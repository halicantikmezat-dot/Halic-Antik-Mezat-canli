import sys
import time
import threading
import queue
import os
import io
import uuid
import re
import asyncio
import ctypes
from ctypes import wintypes
import pygame
import keyboard
import sounddevice as sd
import numpy as np
from scipy.io import wavfile
from PIL import ImageGrab
import edge_tts
from gtts import gTTS
import requests
from google import genai
from google.genai import types
from flask import Flask, request, jsonify

# Windows ses yönetimi
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

# Windows asyncio Proactor uyumluluğu
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ==================== AYARLAR & YAPILANDIRMA ====================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("⚠️ UYARI: GEMINI_API_KEY ortam değişkeni bulunamadı. Lütfen ayarlayın!")

MODEL_ADI = "gemini-2.5-flash"

TIKTOK_KULLANICI = "halicantikmezat34"
YOUTUBE_VIDEO_ID = ""

client = genai.Client(api_key=GEMINI_API_KEY)
pygame.mixer.init()

chat_queue = queue.Queue(maxsize=100)

# Durum Değişkenleri
ai_speaking = False
stop_signal = False
patron_konusuyor = False
patron_dinleme_aktif = True
GUNCEL_PEY = 0
SON_PEY_VEREN = "Kimse"
son_pey_zamani = 0

AKTIF_URUN = {
    "lot": "",
    "baslik": "",
    "acilis": 0,
    "hemen_al": 0,
    "aciklama": ""
}

tts_lock = threading.Lock()
islem_lock = threading.Lock()

# ==================== MÜZİK SES KONTROLÜ (DUCKING) ====================
MUZIK_UYGULAMALARI = ["chrome.exe", "spotify.exe", "aimp.exe", "vlc.exe", "msedge.exe", "brave.exe", "opera.exe", "wmplayer.exe"]
muzik_oturum_hafizasi = {}

def muzik_sesini_ayarla(seviye_orani):
    try:
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            process = session.Process
            if not process:
                continue

            p_name = process.name().lower()
            if any(yasak in p_name for yasak in ["python", "obs", "mediasdk", "tiktok"]):
                continue

            if any(m in p_name for m in MUZIK_UYGULAMALARI):
                volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                ses_id = str(process.pid)

                if ses_id not in muzik_oturum_hafizasi:
                    mevcut = volume.GetMasterVolume()
                    muzik_oturum_hafizasi[ses_id] = mevcut if mevcut > 0.1 else 1.0

                if seviye_orani <= 0.05:
                    volume.SetMasterVolume(0.03, None)
                else:
                    orijinal = muzik_oturum_hafizasi.get(ses_id, 1.0)
                    volume.SetMasterVolume(orijinal, None)
    except Exception:
        pass

def muzik_kis():
    muzik_sesini_ayarla(0.03)

def muzik_ac():
    if not patron_konusuyor and not ai_speaking:
        muzik_sesini_ayarla(1.0)

# ==================== SİSTEM TALİMATLARI ====================
SYSTEM_INSTRUCTION = """
Sen 'Haliç Antik' canlı mezatının kurdu, tezgâhın usta mezatçısısın. Adın Ahmet Usta.

HİTAP KURALLARI:
1. MİKROFONDAKİ KİŞİ: Tezgâhın tek sahibi ve senin PATRONUNDUR. Mikrofondan ses gelince sadece ona 'Patronum' veya 'Patron' diye hitap et.
2. CANLI SOHBETTEN VEYA SİTEDEN PEY VERENLER: Onlar mezatın izleyicileri ve alıcılarıdır. Onlara KESİNLİKLE 'Patron' deme! Onlara isimleriyle hitap et.
3. DİL: Sadece Türkçe, esprili, seri, teşvik edici ve tok mezatçı lisanı.
4. Asla yıldız (*), kare (#) veya liste kullanma. Düz metin olarak TEK CÜMLE söyle.
"""

# ==================== SES MOTORU (GECİKMESİZ & ANINDA KESMELİ) ====================
def kadin_sesi_yedek(metin, gecici_dosya):
    try:
        tts = gTTS(text=metin, lang='tr')
        tts.save(gecici_dosya)
        return True
    except Exception as e:
        print(f"⚠️ Yedek Kadın Sesi Hatası: {e}")
        return False

def konus(metin, acil_mi=False):
    global ai_speaking, stop_signal, patron_konusuyor
    if not metin or stop_signal or patron_konusuyor:
        return

    # Eğer yeni pey veya satış gibi acil bir anons geldiyse eski konuşmayı anında kes
    if acil_mi:
        aninda_ahmeti_kes()

    with tts_lock:
        if stop_signal or patron_konusuyor:
            return

        ai_speaking = True
        muzik_kis()
        gecici_ses = f"ahmet_{uuid.uuid4().hex[:8]}.mp3"
        try:
            temiz = (
                metin.strip()
                .replace("*", "")
                .replace("#", "")
                .replace('"', "")
                .replace("'", "")
                .replace("`", "")
                .replace("$", "")
                .replace("\n", " ")
            )
            print(f"🔊 [Ahmet Usta]: \"{temiz}\"")

            ses_uretildi = False

            # 1. TERCİH: AHMET USTA (Erkek Sesi - Hızlı ve Tok Mezatçı Temposu: +15%)
            try:
                communicate = edge_tts.Communicate(
                    text=temiz,
                    voice="tr-TR-AhmetNeural",
                    rate="+15%",
                    pitch="+0Hz"
                )
                asyncio.run(communicate.save(gecici_ses))

                if os.path.exists(gecici_ses) and os.path.getsize(gecici_ses) > 0:
                    ses_uretildi = True
            except Exception as err:
                print(f"⚠️ Ahmet Usta erkek sesi üretilemedi ({err}), alternatif deneniyor...")
                ses_uretildi = False

            # 2. TERCİH (ZORDA KALIRSA): YEDEK KADIN SESİ (gTTS)
            if not ses_uretildi:
                print("🚨 Zorda kalındı: Yedek ses devreye girdi...")
                ses_uretildi = kadin_sesi_yedek(temiz, gecici_ses)

            # SESİ ÇALMA
            if ses_uretildi and not stop_signal and not patron_konusuyor:
                pygame.mixer.music.load(gecici_ses)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    if stop_signal or patron_konusuyor:
                        pygame.mixer.music.stop()
                        break
                    time.sleep(0.02)
                pygame.mixer.music.unload()

        except Exception as e:
            print(f"⚠️ Ses Çalma Hatası: {e}")
        finally:
            if os.path.exists(gecici_ses):
                try:
                    os.remove(gecici_ses)
                except Exception:
                    pass
            ai_speaking = False
            muzik_ac()

def aninda_ahmeti_kes():
    global ai_speaking
    if pygame.mixer.music.get_busy():
        pygame.mixer.music.stop()
    ai_speaking = False

def acil_durdur():
    global stop_signal, ai_speaking
    stop_signal = True
    aninda_ahmeti_kes()
    muzik_ac()
    print("\n🛑 [FREN] Şalter indi! Ahmet susturuldu, müzik açıldı.")

def meydan_patrona():
    global patron_dinleme_aktif, stop_signal
    patron_dinleme_aktif = False
    stop_signal = True
    aninda_ahmeti_kes()
    print("\n🔇 [MOD]: Meydan Patronda (Ahmet Dinlemede) - [F8]")
    threading.Thread(target=konus, args=("Meydan sende patronum, satış sende!", True), daemon=True).start()

def meydan_ahmete():
    global patron_dinleme_aktif, stop_signal
    patron_dinleme_aktif = True
    stop_signal = False
    print("\n🔊 [MOD]: Ahmet Devrede - [F9]")
    threading.Thread(target=konus, args=("Buradayım patronum, sendeyim!", True), daemon=True).start()

# ==================== KAMERA EKRAN YAKALAMA ====================
def fotograf_cek():
    user32 = ctypes.windll.user32
    iriun_hwnd = None

    def pencereleri_tara(hwnd, extra):
        nonlocal iriun_hwnd
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            baslik = buff.value.lower()
            if "iriun" in baslik:
                iriun_hwnd = hwnd
                return False
        return True

    CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(CMPFUNC(pencereleri_tara), 0)

    try:
        if iriun_hwnd:
            user32.ShowWindow(iriun_hwnd, 9)
            user32.SetForegroundWindow(iriun_hwnd)
            time.sleep(0.15)

            rect = wintypes.RECT()
            user32.GetWindowRect(iriun_hwnd, ctypes.byref(rect))
            l, t, r, b = rect.left, rect.top, rect.right, rect.bottom

            goruntu = ImageGrab.grab(bbox=(l + 8, t + 35, r - 8, b - 8))
            gw, gh = goruntu.size
            if gw > 200 and gh > 200:
                kesim_sol = int(gw * 0.32)
                kesim_sag = int(gw * 0.68)
                return goruntu.crop((kesim_sol, 0, kesim_sag, gh))
            return goruntu
    except Exception as e:
        print(f"⚠️ Kamera görüntüsü alma hatası: {e}")
    return None

def antika_ekspertiz_yap():
    global stop_signal, patron_konusuyor
    try:
        print("\n📸 [EKSPERTİZ]: Sahnedeki ürün taranıyor...")
        threading.Thread(target=konus, args=("Gözüm tezgâhta patronum, hemen inceliyorum...", True), daemon=True).start()

        img = fotograf_cek()
        if img is None:
            konus("Patronum Iriun penceresinden görüntü alamadım, kameraya bir bak.", True)
            return

        prompt = (
            "Sen antika uzmanı mezatçı Ahmet Usta'sın. Karşındaki kişi senin Patronundur. "
            "SANA GÖNDERİLEN BU FOTOĞRAFTAKİ TEZGÂHA KOYULAN YENİ NESNEYİ ANALİZ ET: "
            "1. Malzemesini ve olası dönemini söyle. "
            "2. 'Patronum' diye hitap ederek esprili, mezatçı diliyle TEK VEYA İKİ CÜMLEDE tanıt ve pey toplamaya çağır. "
            "3. Asla madde işareti, yıldız veya başlık kullanma."
        )

        res = client.models.generate_content(
            model=MODEL_ADI,
            contents=[prompt, img],
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
        )

        if res.text and not stop_signal and not patron_konusuyor:
            cevap = res.text.strip()
            print(f"\n🏷️ [AHMET ÜRÜN TANITIMI]:\n{cevap}\n")
            konus(cevap, True)

    except Exception as e:
        print(f"⚠️ Görsel Analiz Hatası: {e}")
        konus("Patronum parça biraz parladı, hafif çevir de net göreyim.", True)

# ==================== MİKROFON İŞLEME ====================
def patron_sesini_isle(wav_bytes):
    global stop_signal, patron_dinleme_aktif
    if not islem_lock.acquire(blocking=False):
        return

    try:
        audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")

        prompt = (
            "Sen canlı mezatta tezgâhtaki mezatçı Ahmet Usta'sın. "
            "Mikrofondaki ses senin TEK BÜYÜĞÜN VE PATRONUNDUR.\n"
            "KOMUT KURALLARI:\n"
            "1. Patron 'bu nedir', 'tanıt', 'anlat', 'fiyatı nedir', 'ekspertiz' diyorsa: SADECE 'EKSPERTIZ_YAP' yaz.\n"
            "2. Patron 'dur', 'sus', 'kes', 'şalter' diyorsa: SADECE 'DUR' yaz.\n"
            "3. Patron 'meydan bende', 'bendesin' diyorsa: SADECE 'MEYDAN_PATRON' yaz.\n"
            "4. Patron 'sendeyim', 'devreye gir', 'ahmet gel' diyorsa: SADECE 'MEYDAN_AHMET' yaz.\n"
            "5. Diğer tüm konuşmalarda: Patronla canlı mezatta tam düet yap. Tok mezatçı diliyle 'Patronum' diyerek TEK CÜMLE söyle."
        )

        res = client.models.generate_content(
            model=MODEL_ADI,
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
        )

        if not res.text or stop_signal or patron_konusuyor:
            return

        yanit = res.text.strip()

        if "EKSPERTIZ_YAP" in yanit:
            threading.Thread(target=antika_ekspertiz_yap, daemon=True).start()
        elif "DUR" in yanit:
            acil_durdur()
        elif "MEYDAN_PATRON" in yanit:
            meydan_patrona()
        elif "MEYDAN_AHMET" in yanit:
            meydan_ahmete()
        elif patron_dinleme_aktif:
            print(f"\n🎤 [AHMET DÜET CEVABI]: {yanit}")
            konus(yanit, True)

    except Exception as e:
        print(f"⚠️ Ses Analiz Hatası: {e}")
    finally:
        if islem_lock.locked():
            islem_lock.release()

# ==================== CHAT & PEY İŞLEME ====================
def pey_bul(metin):
    rakamlar = re.findall(r'\b\d+\b', metin.replace(".", "").replace(",", ""))
    if rakamlar:
        try:
            return int(rakamlar[0])
        except ValueError:
            return None
    return None

def ortak_chat_yoneticisi():
    global GUNCEL_PEY, SON_PEY_VEREN, ai_speaking, patron_konusuyor, patron_dinleme_aktif, stop_signal, son_pey_zamani
    print("🤖 Çoklu Chat & Pey Motoru Devrede (TikTok, YT, IG)...")

    while True:
        if not patron_dinleme_aktif or patron_konusuyor or ai_speaking:
            time.sleep(0.2)
            continue

        try:
            platform, user, comment = chat_queue.get(timeout=0.3)
        except queue.Empty:
            continue

        temiz_yorum = comment.strip()
        temiz_kucuk = temiz_yorum.lower()

        # HEMEN AL KONTROLÜ
        if "hemen al" in temiz_kucuk or "aldım" in temiz_kucuk:
            SON_PEY_VEREN = user
            son_pey_zamani = time.time()
            fiyat = AKTIF_URUN.get("hemen_al") or GUNCEL_PEY
            konus(f"Hemen al geldi! {user} aldı, hayırlı olsun!", True)
            try:
                requests.post("http://127.0.0.1:5000/api/hemen-al-tetikle", json={
                    "platform": platform, "username": user, "fiyat": fiyat
                }, timeout=1)
            except Exception:
                pass
            chat_queue.queue.clear()
            chat_queue.task_done()
            continue

        fiyat = pey_bul(temiz_yorum)

        # NORMAL PEY KONTROLÜ (Hızlı ve Seri Anons)
        if fiyat and fiyat >= 50 and fiyat > GUNCEL_PEY:
            GUNCEL_PEY = fiyat
            SON_PEY_VEREN = user
            son_pey_zamani = time.time()
            mesaj = f"{user} {int(fiyat)} lira! Var mı artıran?"
            print(f"\n💰 [{platform} PEY]: {user} -> {fiyat} TL")
            konus(mesaj, True)
            chat_queue.queue.clear()

            try:
                requests.post("http://127.0.0.1:5000/api/tiktok-web-chat", json={
                    "platform": platform,
                    "username": user,
                    "message": f"{fiyat} TL"
                }, timeout=1)
            except Exception as e:
                print(f"⚠️ Web Panel İletim Hatası: {e}")

        # GENEL SOHBET
        elif len(temiz_yorum) >= 3 and not fiyat:
            if time.time() - son_pey_zamani > 4.0:
                son_pey_zamani = time.time()
                prompt = (
                    f"Canlı mezatta bir İZLEYİCİ ({user}) sohbete şunu yazdı: '{temiz_yorum}'.\n"
                    f"Şu an sahnedeki son pey: {GUNCEL_PEY} TL.\n"
                    f"Bu izleyiciye mezatçı ağzıyla esprili, ÇOK KISA TEK CÜMLE bir cevap ver."
                )
                try:
                    res = client.models.generate_content(
                        model=MODEL_ADI,
                        contents=prompt,
                        config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
                    )
                    if res.text and not stop_signal and not patron_konusuyor:
                        konus(res.text.strip(), False)
                        chat_queue.queue.clear()
                except Exception as e:
                    print(f"⚠️ Chat Yapay Zeka Hatası: {e}")

        chat_queue.task_done()

# ==================== CANLI PLATFORM DİNLEYİCİLERİ ====================
def tiktok_dinleyici():
    while True:
        try:
            from TikTokLive import TikTokLiveClient
            from TikTokLive.events import CommentEvent

            print(f"🔗 TikTok Canlı Yayınına Bağlanılıyor: @{TIKTOK_KULLANICI}")
            client_tt = TikTokLiveClient(unique_id=TIKTOK_KULLANICI)

            @client_tt.on(CommentEvent)
            async def on_comment(event: CommentEvent):
                user = getattr(event.user, 'nickname', None) or getattr(event.user, 'unique_id', 'Misafir')
                comment = getattr(event, 'comment', '')
                if comment and chat_queue.qsize() < 15:
                    chat_queue.put(("TikTok", user, comment))

            client_tt.run()
        except Exception:
            time.sleep(15)

def youtube_dinleyici():
    if not YOUTUBE_VIDEO_ID:
        return
    while True:
        try:
            import pytchat
            print(f"🔗 YouTube Canlı Sohbetine Bağlanılıyor (ID: {YOUTUBE_VIDEO_ID})...")
            chat = pytchat.create(video_id=YOUTUBE_VIDEO_ID)
            while chat.is_alive():
                for c in chat.get().sync_items():
                    if chat_queue.qsize() < 15:
                        chat_queue.put(("YouTube", c.author.name, c.message))
                time.sleep(1)
        except Exception:
            time.sleep(10)

# ==================== FLASK KÖPRÜSÜ (PANEL & MEZAT TETİKLEYİCİLERİ) ====================
flask_app = Flask(__name__)

@flask_app.route('/canli_yorum', methods=['POST'])
def canli_yorum_al():
    data = request.json or {}
    platform = data.get("platform", "Instagram")
    user = data.get("user", "Misafir")
    comment = data.get("comment", "")

    if comment and chat_queue.qsize() < 15:
        chat_queue.put((platform, user, comment))
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "gecersiz"}), 400

# 1. YENİ ÜRÜN SAHNEYE GELDİĞİNDE TETİKLENEN ROTA
@flask_app.route('/urun_sahneye_aldi', methods=['POST'])
def urun_sahneye_aldi():
    global AKTIF_URUN, GUNCEL_PEY, SON_PEY_VEREN
    data = request.json or {}
    AKTIF_URUN = {
        "lot": data.get("lot", ""),
        "baslik": data.get("baslik", "Nadide Parça"),
        "acilis": data.get("acilis", 0),
        "hemen_al": data.get("hemen_al", 0),
        "aciklama": data.get("aciklama", "")
    }
    GUNCEL_PEY = float(data.get("acilis", 0))
    SON_PEY_VEREN = "Açılış"

    prompt = (
        f"Mezatta yeni ürün sahneye çıktı! Ürün: {AKTIF_URUN['baslik']}. "
        f"Açılış Fiyatı: {AKTIF_URUN['acilis']} TL, Hemen Al Fiyatı: {AKTIF_URUN['hemen_al']} TL. "
        f"GÖREV: Hızlı, canlı ve esprili TEK CÜMLEDE ürünün açılış fiyatını duyur ve pey toplamaya çağır."
    )
    def anons():
        try:
            res = client.models.generate_content(
                model=MODEL_ADI, contents=prompt,
                config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
            )
            metin = res.text.strip() if res.text else (
                f"Sıradaki parçamız {AKTIF_URUN['baslik']}! Açılış {int(AKTIF_URUN['acilis'])} lira, hemen al {int(AKTIF_URUN['hemen_al'])} lira. Mezat başladı!"
            )
            konus(metin, True)
        except Exception:
            konus(f"Sıradaki parçamız {AKTIF_URUN['baslik']}! Açılış {int(AKTIF_URUN['acilis'])} lira efendim!", True)

    threading.Thread(target=anons, daemon=True).start()
    return jsonify({"status": "ok"}), 200

# 2. YENİ PEY GELDİĞİNDE ANINDA SESLENDİRME (GECİKMEYİ ÖNLEYEN HIZLI MOD)
@flask_app.route('/yeni_pey_geldi', methods=['POST'])
def yeni_pey_geldi():
    global GUNCEL_PEY, SON_PEY_VEREN, son_pey_zamani
    data = request.json or {}
    kisi = data.get("kisi", "Bir alıcı")
    kisi_temiz = re.sub(r'\[.*?\]', '', str(kisi)).strip() or "Alıcımız"
    try:
        fiyat = float(data.get("fiyat", 0))
    except Exception:
        fiyat = 0.0

    GUNCEL_PEY = fiyat
    SON_PEY_VEREN = kisi_temiz
    son_pey_zamani = time.time()

    fiyat_tam = int(round(fiyat))
    # Kuyruk oluşmaması için kısa ve seri anons
    mesaj = f"{kisi_temiz} {fiyat_tam} lira! Var mı artıran?"
    threading.Thread(target=konus, args=(mesaj, True), daemon=True).start()
    return jsonify({"status": "ok"}), 200

# 3. GERİ SAYIM SON SANİYELER UYARISI (SERİ MOD)
@flask_app.route('/geri_sayim_uyari', methods=['POST'])
def geri_sayim_uyari():
    data = request.json or {}
    saniye = data.get("kalan_saniye", 10)
    mesaj = f"Son {saniye} saniye! Kaçırmayın, bitiyor!"
    threading.Thread(target=konus, args=(mesaj, True), daemon=True).start()
    return jsonify({"status": "ok"}), 200

# 4. SATIŞ TAMAMLANDIĞINDA ANINDA TEBRİK
@flask_app.route('/satis_bitti', methods=['POST'])
def satis_bitti():
    global SON_PEY_VEREN, GUNCEL_PEY
    data = request.json or {}
    kazanan = data.get("kazanan", SON_PEY_VEREN)
    kazanan_temiz = re.sub(r'\[.*?\]', '', str(kazanan)).strip() or SON_PEY_VEREN
    fiyat = data.get("fiyat", GUNCEL_PEY)
    urun_adi = AKTIF_URUN.get("baslik", "Ürün")

    fiyat_tam = int(round(float(fiyat)))
    mesaj = f"Sattım! {urun_adi}, {fiyat_tam} liraya {kazanan_temiz} kişisine kalmıştır. Hayırlı olsun!"
    threading.Thread(target=konus, args=(mesaj, True), daemon=True).start()
    return jsonify({"status": "ok"}), 200

def web_servisi_baslat():
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    flask_app.run(host="127.0.0.1", port=5005, debug=False, use_reloader=False)

# ==================== AKILLI MİKROFON DİNLEYİCİ ====================
def patron_dinleyici():
    global stop_signal, patron_konusuyor, ai_speaking, patron_dinleme_aktif
    fs = 16000
    parca_suresi = 0.4
    SESSIZLIK_SURESI = 1.0

    ses_parcalari = []
    son_ses_zamani = 0

    print("🎙️ Akıllı Mikrofon Hazır... Kalibrasyon yapılıyor...")
    try:
        kalibrasyon_verisi = sd.rec(int(1.0 * fs), samplerate=fs, channels=1, dtype='int16')
        sd.wait()
        taban_gurultu = np.max(np.abs(kalibrasyon_verisi))
    except Exception:
        taban_gurultu = 1200

    DINAMIK_ESIK = max(6500, int(taban_gurultu * 2.8))
    print(f"✅ Kalibrasyon Tamam: Gürültü Tabanı ~{taban_gurultu} | Eşik: {DINAMIK_ESIK}")

    while True:
        try:
            recording = sd.rec(int(parca_suresi * fs), samplerate=fs, channels=1, dtype='int16')
            sd.wait()
            ses_gucu = np.max(np.abs(recording))

            if ses_gucu >= DINAMIK_ESIK:
                if not patron_konusuyor:
                    patron_konusuyor = True
                    aninda_ahmeti_kes()
                    muzik_kis()

                son_ses_zamani = time.time()
                ses_parcalari.append(recording)

            elif patron_konusuyor:
                ses_parcalari.append(recording)
                if time.time() - son_ses_zamani > SESSIZLIK_SURESI:
                    patron_konusuyor = False
                    muzik_ac()

                    if len(ses_parcalari) >= 3:
                        butun_ses = np.concatenate(ses_parcalari, axis=0)
                        ses_parcalari.clear()

                        wav_io = io.BytesIO()
                        wavfile.write(wav_io, fs, butun_ses)
                        wav_data = wav_io.getvalue()
                        threading.Thread(target=patron_sesini_isle, args=(wav_data,), daemon=True).start()
                    else:
                        ses_parcalari.clear()
        except Exception:
            time.sleep(0.05)

# Kısayollar
keyboard.add_hotkey('f12', acil_durdur)
keyboard.add_hotkey('f8', meydan_patrona)
keyboard.add_hotkey('f9', meydan_ahmete)

if __name__ == '__main__':
    print("==================================================")
    print("🚀 HALİÇ ANTİK - CANLI MEZAT DÜET & CHAT BOTU")
    print("==================================================")
    print(f"👉 Model: {MODEL_ADI}")
    print(f"👉 TikTok: @{TIKTOK_KULLANICI}")
    print(f"👉 YouTube: {'Aktif' if YOUTUBE_VIDEO_ID else 'Boş'}")
    print("👉 Entegrasyon Servisi: http://127.0.0.1:5005")
    print("🛑 F12: Şalter | F8: Meydan Patronda | F9: Ahmet Devrede")
    print("==================================================")

    threading.Thread(target=patron_dinleyici, daemon=True).start()
    threading.Thread(target=tiktok_dinleyici, daemon=True).start()
    threading.Thread(target=youtube_dinleyici, daemon=True).start()
    threading.Thread(target=web_servisi_baslat, daemon=True).start()
    threading.Thread(target=ortak_chat_yoneticisi, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nSistem kapatıldı.")
        sys.exit(0)