import itertools
import logging
import os
import random
import shutil
import hashlib
from queue import Empty, PriorityQueue, Queue
import re
import subprocess
import sys
import threading
import time

# Windows Donanim Kutuphanelerini Guvenli Yukleme
try:
    import keyboard
except ImportError:
    keyboard = None

try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

try:
    import ctypes
    from ctypes import wintypes
except Exception:
    ctypes = None
    wintypes = None

try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
except Exception:
    AudioUtilities = None
    ISimpleAudioVolume = None

try:
    import pygame
    pygame.mixer.quit()
    pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=1024)
except Exception:
    try:
        import pygame
        pygame.mixer.init()
    except Exception:
        pygame = None

from flask import Flask, jsonify, request
from google import genai
from google.genai import types
import numpy as np
import requests
import azure.cognitiveservices.speech as speechsdk

try:
    from config import Config
except ImportError:
    class Config:
        GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_KEY")
        AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
        AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION")

if sys.platform == "win32" and ctypes:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# =====================================================================
# 1. AYARLAR VE DÜKKAN YAPILANDIRMASI
# =====================================================================
GEMINI_API_KEY = getattr(Config, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY")
MODEL_ADI = "gemini-3.8-flash"
WEB_SERVER_URL = os.environ.get("WEB_SERVER_URL", "http://127.0.0.1:5000").rstrip("/")

KOK_DIZIN = os.path.abspath(os.path.dirname(__file__))
_ses_kilit = threading.Lock()

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

chat_queue = Queue(maxsize=150)
spiker_kuyrugu = PriorityQueue(maxsize=100)
kuyruk_sayaci = itertools.count()

hafiza_lock = threading.Lock()
ai_speaking = False
stop_signal = False
patron_konusuyor = False
patron_dinleme_aktif = True

DUKKAN_ISIMLERI = {
    "gumus": "Gümüş",
    "antika": "Antika",
    "toptan": "Toptan",
    "kozmetik": "Kozmetik",
    "bijuteri": "Bijuteri",
    "hirdavat": "Hırdavat",
    "tasarim": "Tasarım",
    "modelaraba": "Model Araba",
    "karma": "Karma Satış",
    "diger": "Serbest Mezat",
    "porselen": "Porselen / Seramik",
    "bakir": "Bakır / Pirinç / Madenler",
    "elektronik": "Elektrik / Elektronik",
}

DUKKAN_TABAN_ARTIS = {
    "gumus": 50.0,
    "antika": 100.0,
    "toptan": 100.0,
    "kozmetik": 20.0,
    "bijuteri": 20.0,
    "hirdavat": 50.0,
    "tasarim": 100.0,
    "modelaraba": 50.0,
    "karma": 50.0,
    "diger": 25.0,
    "porselen": 50.0,
    "bakir": 50.0,
    "elektronik": 50.0,
}

DUKKAN_HAFIZALARI = {}
for kod in DUKKAN_ISIMLERI.keys():
    DUKKAN_HAFIZALARI[kod] = {
        "aktif_urun": {"lot": "", "baslik": "", "acilis": 0.0, "hemen_al": 0.0, "aciklama": ""},
        "guncel_pey": 0.0,
        "son_pey_veren": "Kimse",
        "son_pey_zamani": 0.0,
        "bekleyen_hemen_al": {"user": None, "platform": None, "zaman": 0.0},
        "bekleyen_yuksek_pey": {"user": None, "fiyat": 0.0, "platform": None, "zaman": 0.0},
    }

SYSTEM_INSTRUCTION = """
Sen Kapalıçarşı ve Çukurcuma görmüş, ağzı iyi laf yapan, tatlı dilli, şirinlik yapan, izleyiciye hafiften takılan, esprili ve satış kapıcı bir halk mezatçısısın. Adın Ahmet Usta. Resmi ve soğuk bir spiker değilsin.
Kullanıcının sorusuna doğrudan, içten ve esprili cevap ver. Ürün bilgisini lafın içine yedir. En fazla 1-2 cümle, tek nefeste söylenecek canlı nida. Nutuk atma. İzleyicinin cümlesini tekrarlama. Sorudan bağımsız ezber mezat lafı söyleme.
'Kaç parada' derse anlık peyyi coşkuyla söyle. 'Nasılsınız' derse hal hatır sor, sonra esere bağla.
Markdown, emoji ve tırnak kullanma.
"""

AHMET_NIDALARI = (
    "Aman efendim nefesler tutuldu, şu eserin güzelliğine bakın!",
    "Peyler havada uçuşuyor, yetişen alıyor!",
    "Durmayın beyler, bu güzellik kaçmaz, basan alır!",
)
_ahmet_nida_sirasi = 0

def ahmet_nida_sec():
    global _ahmet_nida_sirasi
    metin = AHMET_NIDALARI[_ahmet_nida_sirasi % len(AHMET_NIDALARI)]
    _ahmet_nida_sirasi += 1
    return metin

def lira_okunusu(tutar):
    n = int(round(float(tutar or 0)))
    if n >= 1000:
        binler, kalan = divmod(n, 1000)
        if kalan:
            return f"{binler} bin {kalan}"
        return f"{binler} bin"
    return str(n)

_son_pey_replik = ""
PEY_KALIPLARI = (
    "{hitap}'den harika bir hamle! {soz} lira geldi efendim, sahnede ateş yanıyor!",
    "Bakıyorum salon hareketlendi, {hitap} çıtayı {soz} liraya koydu! Yok mu üzerine çıkan?",
    "{soz} lira sesi geldi! {hitap} eseri gözüne kestirmiş görünüyor, kaptıracak mıyız beyler?",
    "Münadimizden {soz} lira pey! Değerini bilen hemen yapıştırıyor, buyrunuz efendim!",
    "{hitap} eli açık davrandı, {soz} liraya el bastı! Son saniyeler, kaçıran üzülür!",
    "{eser} için {hitap} {soz} lirayı yapıştırdı! Tempo {tempo}, durmak yok!",
    "Duydunuz mu, {soz} lira! {hitap} masayı ısıttı, haydi bir el daha!",
    "{hitap} {tempo} geldi: {soz} lira! {eser} hâlâ sahnede!",
    "Şu {eser} kıpırdadı! {hitap} {soz} lirayla öne geçti, var mı süren?",
    "Aman diyeyim {soz} lirayı kaçırmayın! {hitap} niyeti belli, eser ona mı kalacak?",
    "{hitap} cüzdanı açtı, {soz} lira dedi! {eser} bu fiyata gülüyor, artıran var mı?",
    "Salon bir anda ısındı, {soz} lira! {hitap} rüzgarı kesti, peşinden gelen var mı?",
    "{eser} için yeni ses: {soz} lira, sahibi {hitap}! Gitti gidiyor, vah vah demeyin!",
    "{hitap} {tempo} bir pey bıraktı, tam {soz} lira! Kim tutacak bu eseri?",
    "Bakın bakın, {soz} lira konuştu! {hitap} {eser} için dayandı, üstüne var mı?",
)

def _pey_temposu(tutar, onceki):
    try:
        simdi = float(tutar or 0)
        eski = float(onceki or 0)
    except (TypeError, ValueError):
        return "taze taze"
    if eski <= 0:
        return "açılıştan"
    oran = (simdi - eski) / eski
    if oran >= 0.25:
        return "bir anda sıçrayarak"
    if oran >= 0.10:
        return "peş peşe"
    return "usul usul"

def ahmet_pey_metni(kisi, tutar, urun_adi="", onceki=None):
    global _son_pey_replik
    isim = re.sub(r"\[[^\]]*\]", "", str(kisi or "")).strip() or "Bir beyefendi"
    if not re.search(r"\b(bey|hanım|hanim)\b", isim, re.IGNORECASE):
        isim = f"{isim} Bey"
    soz = lira_okunusu(tutar)
    eser = (urun_adi or "").strip() or "eser"
    tempo = _pey_temposu(tutar, onceki)
    adaylar = [
        kalip.format(hitap=isim, soz=soz, eser=eser, tempo=tempo)
        for kalip in PEY_KALIPLARI
    ]
    havuz = [satir for satir in adaylar if satir != _son_pey_replik] or adaylar
    secim = random.choice(havuz)
    _son_pey_replik = secim
    return secim

def guvenli_arkaplan_post(url, json_data, timeout_sure=1.2):
    def _post():
        try:
            requests.post(url, json=json_data, timeout=timeout_sure)
        except Exception:
            pass
    threading.Thread(target=_post, daemon=True).start()

# =====================================================================
# 2. MÜZİK SES KONTROLÜ (DUCKING)
# =====================================================================
MUZIK_UYGULAMALARI = ["chrome.exe", "spotify.exe", "aimp.exe", "vlc.exe", "msedge.exe", "brave.exe"]
muzik_oturum_hafizasi = {}
ducking_lock = threading.Lock()

def _arka_plan_ses_ayarla(seviye_orani):
    if not ctypes or not AudioUtilities or not ISimpleAudioVolume:
        return
    with ducking_lock:
        ole_init = False
        try:
            ctypes.windll.ole32.CoInitialize(None)
            ole_init = True
            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                proc = session.Process
                if not proc:
                    continue
                p_name = proc.name().lower()
                if any(m in p_name for m in MUZIK_UYGULAMALARI):
                    try:
                        vol = session._ctl.QueryInterface(ISimpleAudioVolume)
                        key = f"{p_name}_{session.ProcessId}"
                        if seviye_orani <= 0.05:
                            cur = vol.GetMasterVolume()
                            if key not in muzik_oturum_hafizasi and cur > 0.05:
                                muzik_oturum_hafizasi[key] = cur
                            vol.SetMasterVolume(0.04, None)
                        else:
                            orig = muzik_oturum_hafizasi.get(key, 1.0)
                            vol.SetMasterVolume(orig, None)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            if ole_init:
                ctypes.windll.ole32.CoUninitialize()

def muzik_kis():
    threading.Thread(target=_arka_plan_ses_ayarla, args=(0.04,), daemon=True).start()

def muzik_ac():
    if not patron_konusuyor and not ai_speaking:
        threading.Thread(target=_arka_plan_ses_ayarla, args=(1.0,), daemon=True).start()
        muzik_oturum_hafizasi.clear()

# =====================================================================
# 3. PEY VE RAKAM ÇÖZÜCÜ (TÜRKÇE YAZIYLA PEY AYRIŞTIRICI)
# =====================================================================
BIRLER = {"bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4, "beş": 5, "bes": 5, "altı": 6, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9}
ONLAR = {"on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "kirk": 40, "elli": 50, "altmış": 60, "altmis": 60, "yetmiş": 70, "yetmis": 70, "seksen": 80, "doksan": 90}

def turkce_tutar_coz(metin):
    """'yedibin' -> 7000, 'yedi bin' -> 7000, 'on bin' -> 10000 formatlarını çözer."""
    if not metin:
        return None
    ham = str(metin).lower().replace("₺", " ")
    for cop in ("liradır", "liradir", "lira", "tl", "peydir", "pey"):
        ham = re.sub(rf"\b{cop}\b", " ", ham)
    ham = ham.replace("yedibin", "yedi bin").replace("sekizyuz", "sekiz yüz").replace("sekizyüz", "sekiz yüz")
    ham = re.sub(r"([a-zçğıöşü])(milyon|bin|yüz|yuz)\b", r"\1 \2", ham)
    ham = re.sub(r"(\d+)\s*bin\b", lambda m: str(int(m.group(1)) * 1000), ham)
    ham = re.sub(r"(\d+)\s*yüz\b", lambda m: str(int(m.group(1)) * 100), ham)
    ham = re.sub(r"(\d+)\s*yuz\b", lambda m: str(int(m.group(1)) * 100), ham)
    ham = re.sub(r"\s+", " ", ham).strip()
    if not ham:
        return None
    if re.fullmatch(r"[\d.,\s]+", ham):
        birlesik = re.sub(r"[^\d]", "", ham)
        return float(birlesik) if birlesik else None
    toplam, gecici = 0, 0
    for k in re.findall(r"[a-zçğıöşü0-9]+", ham):
        if k.isdigit():
            gecici += int(k)
        elif k in BIRLER:
            gecici += BIRLER[k]
        elif k in ONLAR:
            gecici += ONLAR[k]
        elif k in ("yüz", "yuz"):
            gecici = (gecici or 1) * 100
        elif k == "bin":
            toplam += (gecici or 1) * 1000
            gecici = 0
        elif k == "milyon":
            toplam += (gecici or 1) * 1_000_000
            gecici = 0
        else:
            return None
    sonuc = toplam + gecici
    return float(sonuc) if sonuc > 0 else None

def pey_bul(metin):
    if not metin:
        return None
    tam = turkce_tutar_coz(metin)
    if tam and tam >= 20:
        return int(tam)
    ham = str(metin).lower().replace("₺", "").replace("tl", "").replace("lira", " ")
    ham = re.sub(r"(\d+)\s*bin\b", lambda m: str(int(m.group(1)) * 1000), ham)
    for r in re.findall(r"\b\d+\b", ham):
        try:
            val = int(r)
            if val >= 20:
                return val
        except ValueError:
            continue
    return None

def asgari_artis_hesapla(mevcut_fiyat, dukkan="gumus"):
    f = float(mevcut_fiyat or 0)
    taban = DUKKAN_TABAN_ARTIS.get(dukkan, 50.0)
    if f < 1000:
        return taban
    elif f < 2000:
        return max(taban, 100.0)
    elif f < 5000:
        return max(taban, 250.0)
    else:
        return max(taban, 500.0)

def pey_artisi_gecerli_mi(yeni_pey, guncel_pey, dukkan="gumus"):
    if yeni_pey <= guncel_pey:
        return False
    if guncel_pey == 0:
        return yeni_pey >= DUKKAN_TABAN_ARTIS.get(dukkan, 50.0)
    fark = int(yeni_pey - guncel_pey)
    gereken_artis = asgari_artis_hesapla(guncel_pey, dukkan)
    return fark >= gereken_artis

# =====================================================================
# 4. AHMET USTA AZURE IN-MEMORY STREAM (tr-TR-AhmetNeural)
# =====================================================================
AZURE_KEY = getattr(Config, "AZURE_SPEECH_KEY", None) or ""
AZURE_REGION = getattr(Config, "AZURE_SPEECH_REGION", None) or "northeurope"
AZURE_VOICE = "tr-TR-AhmetNeural"
speech_config = None
try:
    speech_config = speechsdk.SpeechConfig(subscription=AZURE_KEY, region=AZURE_REGION)
    speech_config.speech_synthesis_voice_name = AZURE_VOICE
    speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm)
    print(f"[SES] Azure tr-TR-AhmetNeural Aktif ({AZURE_REGION})")
except Exception as e:
    print(f"[SES HATA] Azure yapılandırma hatası: {e}")

def _ses_dosyasini_birak():
    if not pygame or not pygame.mixer.get_init():
        return
    try:
        pygame.mixer.music.stop()
    except Exception:
        pass
    try:
        pygame.mixer.music.unload()
    except Exception:
        pass

def ahmet_sesi_bellege_sentezle(metin):
    return None

def bellekten_ses_cal(audio_bytes):
    return

def kuyrugu_guvenli_bosalt():
    while not spiker_kuyrugu.empty():
        try:
            spiker_kuyrugu.get_nowait()
            spiker_kuyrugu.task_done()
        except (Empty, ValueError):
            break

def aninda_ahmeti_kes():
    global ai_speaking
    if pygame and pygame.mixer.get_init():
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
    ai_speaking = False
    muzik_ac()

def spiker_kuyruk_dinleyici():
    global ai_speaking, stop_signal, patron_konusuyor

    while True:
        try:
            oncelik, _, metin = spiker_kuyrugu.get()

            if stop_signal or patron_konusuyor:
                spiker_kuyrugu.task_done()
                continue

            temiz = re.sub(r"[\*#\"'`\n]", " ", str(metin)).strip()
            temiz = re.sub(r"\s+", " ", temiz)

            if not temiz:
                spiker_kuyrugu.task_done()
                continue

            with _ses_kilit:
                ai_speaking = True
                muzik_kis()

                audio_data = ahmet_sesi_bellege_sentezle(temiz)
                if audio_data and not stop_signal and not patron_konusuyor:
                    print(f'⚡ [Ahmet Usta]: "{temiz}"')
                    bellekten_ses_cal(audio_data)

                ai_speaking = False
                muzik_ac()
            spiker_kuyrugu.task_done()
            time.sleep(0.05)

        except Exception as e:
            ai_speaking = False
            muzik_ac()
            try:
                spiker_kuyrugu.task_done()
            except Exception:
                pass
            time.sleep(0.04)

threading.Thread(target=spiker_kuyruk_dinleyici, daemon=True).start()

def konus(metin, acil_mi=False):
    global stop_signal, patron_konusuyor
    if not metin or stop_signal or patron_konusuyor:
        return

    if acil_mi:
        aninda_ahmeti_kes()
        kuyrugu_guvenli_bosalt()
        spiker_kuyrugu.put((1, next(kuyruk_sayaci), metin))
    else:
        if spiker_kuyrugu.qsize() >= 1:
            kuyrugu_guvenli_bosalt()
        spiker_kuyrugu.put((2, next(kuyruk_sayaci), metin))

def acil_durdur():
    global stop_signal
    stop_signal = not stop_signal
    if stop_signal:
        kuyrugu_guvenli_bosalt()
        aninda_ahmeti_kes()
        print("\n🛑 [FREN] Şalter indi. Bot susturuldu.")
    else:
        print("\n▶️ [DEVAM] Sistem aktif.")

def meydan_patrona():
    global patron_dinleme_aktif, stop_signal
    patron_dinleme_aktif = False
    aninda_ahmeti_kes()
    konus("Meydan sende patronum, satış sende!", True)
    stop_signal = True

def meydan_ahmete():
    global patron_dinleme_aktif, stop_signal
    patron_dinleme_aktif = True
    stop_signal = False
    konus("Buradayım patronum, sendeyim!", True)

# =====================================================================
# 5. SOHBET VE ÇOKLU DÜKKAN YÖNETİCİSİ
# =====================================================================
def dukkan_hafizasi_al(kod):
    k = (kod or "gumus").lower().strip()
    return DUKKAN_HAFIZALARI.get(k, DUKKAN_HAFIZALARI["gumus"])

def yorumdan_dukkan_belirle(yorum_metni, varsayilan="gumus"):
    metin = yorum_metni.lower()
    for kod, isim in DUKKAN_ISIMLERI.items():
        if f"#{kod}" in metin or kod in metin or isim.lower() in metin:
            return kod
    with hafiza_lock:
        for k, h in DUKKAN_HAFIZALARI.items():
            if h["aktif_urun"].get("lot"):
                return k
    return varsayilan

son_ai_yanit_zamani = 0.0

def ortak_chat_yoneticisi():
    global son_ai_yanit_zamani
    print("🤖 10 Dükkanlı Çoklu Chat & Pey Motoru Devrede...")

    while True:
        try:
            paket = chat_queue.get(timeout=0.2)
            if len(paket) == 4:
                platform, user, comment, zorlanan_dukkan = paket
            else:
                platform, user, comment = paket
                zorlanan_dukkan = None
        except Empty:
            continue

        temiz_yorum = comment.strip()
        temiz_kucuk = temiz_yorum.lower()

        if any(y in temiz_kucuk for y in ["katıldı", "beğendi", "paylaştı", "yayında olduğuna"]):
            chat_queue.task_done()
            continue

        simdi = time.time()
        hedef_kod = zorlanan_dukkan if zorlanan_dukkan else yorumdan_dukkan_belirle(temiz_yorum, "gumus")
        hafiza = dukkan_hafizasi_al(hedef_kod)
        dukkan_adi = DUKKAN_ISIMLERI.get(hedef_kod, "Mezat")

        with hafiza_lock:
            sahnede_urun_var = bool(hafiza["aktif_urun"].get("lot"))

        if not sahnede_urun_var:
            if any(s in temiz_kucuk for s in ["selam", "merhaba", "sa", "iyi akşamlar"]):
                konus(f"Aleyküm selam {user}, hoş geldiniz. Sahnede ürün yok, yeni lot hazırlanıyor.", False)
            chat_queue.task_done()
            continue

        onay_kelimesi = any(o in temiz_kucuk for o in ["evet", "onay", "aldım", "benim"])

        # Bekleyen Hemen Al
        hemen_al_tetiklendi = False
        with hafiza_lock:
            b_hemen = hafiza["bekleyen_hemen_al"]
            if b_hemen["user"] and (simdi - b_hemen["zaman"] <= 20.0):
                if b_hemen["user"].lower() == user.lower() and onay_kelimesi:
                    hafiza["son_pey_veren"] = user
                    fiyat = hafiza["aktif_urun"].get("hemen_al") or hafiza["guncel_pey"]
                    hafiza["bekleyen_hemen_al"] = {"user": None, "platform": None, "zaman": 0.0}
                    hemen_al_tetiklendi = True

        if hemen_al_tetiklendi:
            konus(f"Teyit alındı! {dukkan_adi} reyonundan {user} aldı, hayırlı olsun!", True)
            guvenli_arkaplan_post(f"{WEB_SERVER_URL}/api/hemen-al-tetikle", {
                "dukkan": hedef_kod,
                "platform": platform,
                "username": user,
                "fiyat": fiyat
            })
            chat_queue.task_done()
            continue

        # Pey Kontrolü
        fiyat = pey_bul(temiz_yorum)
        if fiyat:
            with hafiza_lock:
                g_pey = hafiza["guncel_pey"]

            if pey_artisi_gecerli_mi(fiyat, g_pey, hedef_kod):
                with hafiza_lock:
                    hafiza["guncel_pey"] = float(fiyat)
                    hafiza["son_pey_veren"] = user
                    hafiza["son_pey_zamani"] = simdi

                with hafiza_lock:
                    eser_adi = hafiza["aktif_urun"].get("baslik", "")
                konus(ahmet_pey_metni(user, fiyat, eser_adi, g_pey), True)
                guvenli_arkaplan_post(f"{WEB_SERVER_URL}/pey-ver", {
                    "dukkan": hedef_kod,
                    "musteri_adi": f"[{platform}] {user}",
                    "miktar": float(fiyat),
                    "islem": "pey"
                })
            elif fiyat <= g_pey:
                gereken = int(g_pey + asgari_artis_hesapla(g_pey, hedef_kod))
                konus(f"Sayın {user}, güncel pey {int(g_pey)} lira. Lütfen en az {gereken} lira yazınız!", True)
            chat_queue.task_done()
            continue

        # AI Yanıt
        if client and len(temiz_yorum) >= 3 and not stop_signal and not patron_konusuyor:
            if simdi - son_ai_yanit_zamani >= 2.0:
                son_ai_yanit_zamani = simdi
                with hafiza_lock:
                    baslik = hafiza['aktif_urun'].get('baslik', 'Eser')
                    aciklama = hafiza['aktif_urun'].get('aciklama', 'Belirtilmedi')
                    acilis = int(float(hafiza['aktif_urun'].get('acilis') or 0))
                    g_pey_deger = int(hafiza['guncel_pey'])

                def _soru_cevapla(p_soru, p_user, p_kod, p_dukkan, p_baslik, p_aciklama, p_acilis, p_pey):
                    prompt = (
                        f"Ürün adı: {p_baslik}. Başlangıç fiyatı: {p_acilis} TL. "
                        f"Anlık pey: {p_pey} TL. Açıklama: {p_aciklama}.\n"
                        f"Kullanıcı Sorusu: {p_soru}"
                    )
                    kutu = {"cevap": ""}

                    def _iste():
                        try:
                            res = client.models.generate_content(
                                model=MODEL_ADI,
                                contents=prompt,
                                config=types.GenerateContentConfig(
                                    system_instruction=SYSTEM_INSTRUCTION,
                                    max_output_tokens=80,
                                    temperature=0.4
                                )
                            )
                            if res.text and not stop_signal and not patron_konusuyor:
                                kutu["cevap"] = re.sub(r"[\*#_]", "", res.text).strip().strip('"')
                        except Exception as e:
                            print(f"Gemini Chat Soru Hatası: {e}")

                    istek = threading.Thread(target=_iste, daemon=True)
                    istek.start()
                    istek.join(1.5)
                    if stop_signal or patron_konusuyor:
                        return
                    cevap = kutu.get("cevap") or f"{p_user}, sizi duydum efendim, eserin başındayız, buyurun!"
                    konus(cevap, True)
                    guvenli_arkaplan_post(f"{WEB_SERVER_URL}/api/bot/ahmet-usta-yanit", {
                        "dukkan": p_kod,
                        "yanit": cevap
                    })

                threading.Thread(
                    target=_soru_cevapla,
                    args=(temiz_yorum, user, hedef_kod, dukkan_adi, baslik, aciklama, acilis, g_pey_deger),
                    daemon=True
                ).start()
        elif len(temiz_yorum) >= 3 and not stop_signal and not patron_konusuyor:
            nida = ahmet_nida_sec()
            konus(nida, False)
            guvenli_arkaplan_post(f"{WEB_SERVER_URL}/api/bot/ahmet-usta-yanit", {
                "dukkan": hedef_kod,
                "yanit": nida
            })

        chat_queue.task_done()

# =====================================================================
# 6. FLASK WEB SERVİSİ (PORT 5005)
# =====================================================================
flask_app = Flask(__name__)

@flask_app.route("/urun_sahneye_aldi", methods=["POST"])
def urun_sahneye_aldi():
    global stop_signal
    stop_signal = False
    data = request.json or {}
    dukkan = (data.get("dukkan") or "gumus").lower()
    hafiza = dukkan_hafizasi_al(dukkan)
    dukkan_adi = DUKKAN_ISIMLERI.get(dukkan, "Mezat")
    tanitim = (data.get("tanitim_yazisi") or data.get("aciklama") or "").strip()

    with hafiza_lock:
        hafiza["bekleyen_hemen_al"] = {"user": None, "platform": None, "zaman": 0.0}
        hafiza["bekleyen_yuksek_pey"] = {"user": None, "fiyat": 0.0, "platform": None, "zaman": 0.0}
        hafiza["aktif_urun"] = {
            "lot": str(data.get("lot", "")),
            "baslik": data.get("baslik", "Nadide Eser"),
            "acilis": float(data.get("acilis", 0)),
            "hemen_al": float(data.get("hemen_al", 0)),
            "aciklama": tanitim,
        }
        hafiza["guncel_pey"] = float(hafiza["aktif_urun"]["acilis"])
        hafiza["son_pey_veren"] = "Açılış"
        guncel_pey_val = int(hafiza["guncel_pey"])
        lot_val = hafiza['aktif_urun']['lot']
        baslik_val = hafiza['aktif_urun']['baslik']

    anons = f"{dukkan_adi} reyonunda Lot {lot_val}, {baslik_val} sahnede! Açılışımız {guncel_pey_val} lira."
    konus(anons, True)

    if tanitim and len(tanitim) > 6:
        konus(f"Eser özellikleri: {tanitim}", False)

    return jsonify({"status": "ok"}), 200

@flask_app.route("/son_dakika_basladi", methods=["POST"])
def son_dakika_basladi():
    data = request.json or {}
    dukkan = (data.get("dukkan") or "gumus").lower()
    hafiza = dukkan_hafizasi_al(dukkan)
    dukkan_adi = DUKKAN_ISIMLERI.get(dukkan, "Mezat")
    with hafiza_lock:
        guncel = int(hafiza['guncel_pey'])
    konus(f"Dikkat! {dukkan_adi} reyonunda son 60 saniye! Güncel pey {guncel} lira, satıyorum!", True)
    return jsonify({"status": "ok"}), 200

@flask_app.route("/yeni_pey_geldi", methods=["POST"])
def yeni_pey_geldi():
    data = request.json or {}
    dukkan = (data.get("dukkan") or "gumus").lower()
    hafiza = dukkan_hafizasi_al(dukkan)
    dukkan_adi = DUKKAN_ISIMLERI.get(dukkan, "Mezat")
    kisi = re.sub(r"\[.*?\]", "", str(data.get("kisi", "Alıcımız"))).strip() or "Alıcımız"
    fiyat = float(data.get("fiyat", 0))

    with hafiza_lock:
        onceki = hafiza["guncel_pey"]
        hafiza["guncel_pey"] = fiyat
        hafiza["son_pey_veren"] = kisi
        hafiza["son_pey_zamani"] = time.time()
        eser_adi = hafiza["aktif_urun"].get("baslik", "")
    konus(ahmet_pey_metni(kisi, fiyat, eser_adi, onceki), True)
    return jsonify({"status": "ok"}), 200

@flask_app.route("/satis_bitti", methods=["POST"])
def satis_bitti():
    data = request.json or {}
    dukkan = (data.get("dukkan") or "gumus").lower()
    hafiza = dukkan_hafizasi_al(dukkan)
    dukkan_adi = DUKKAN_ISIMLERI.get(dukkan, "Mezat")

    with hafiza_lock:
        kazanan = data.get("kazanan", hafiza["son_pey_veren"])
        fiyat = data.get("fiyat", hafiza["guncel_pey"])
        urun = hafiza["aktif_urun"].get("baslik", "Eser")

    if kazanan and kazanan.lower() not in ["yok", "none", "-", ""]:
        konus(f"Sattım! {dukkan_adi} reyonundan {urun}, {int(float(fiyat))} liraya {kazanan} beye hayırlı olsun!", True)
    else:
        konus(f"{dukkan_adi} reyonunda süre doldu, pey gelmediği için eser ayrılmıştır.", True)

    return jsonify({"status": "ok"}), 200

@flask_app.route("/ahmet_sifirla", methods=["POST"])
def ahmet_sifirla():
    data = request.json or {}
    dukkan = (data.get("dukkan") or "gumus").lower()
    hafiza = dukkan_hafizasi_al(dukkan)
    aninda_ahmeti_kes()
    kuyrugu_guvenli_bosalt()
    with hafiza_lock:
        hafiza["aktif_urun"] = {"lot": "", "baslik": "", "acilis": 0.0, "hemen_al": 0.0, "aciklama": ""}
        hafiza["guncel_pey"] = 0.0
        hafiza["son_pey_veren"] = "Kimse"
    return jsonify({"status": "ok"}), 200

@flask_app.route("/yorum_oku", methods=["POST"])
def yorum_oku():
    """Yalnızca hazır anonsu okur. İzleyicinin ham mesajını seslendirmez."""
    data = request.json or {}
    anons = (data.get("anons") or "").strip()
    if anons:
        konus(anons, False)
    return jsonify({"status": "ok"}), 200

@flask_app.route("/canli_yorum", methods=["POST"])
def canli_yorum_al():
    data = request.json or {}
    platform = data.get("platform", "Sosyal Medya")
    user = data.get("user", "Misafir")
    comment = data.get("comment", "")
    dukkan = data.get("dukkan")
    if comment and chat_queue.qsize() < 100:
        chat_queue.put((platform, user, comment, dukkan))
    return jsonify({"status": "ok"}), 200

def web_servisi_baslat():
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    flask_app.run(host="127.0.0.1", port=5005, debug=False, use_reloader=False)

if keyboard:
    try:
        keyboard.add_hotkey("f12", acil_durdur)
        keyboard.add_hotkey("f8", meydan_patrona)
        keyboard.add_hotkey("f9", meydan_ahmete)
    except Exception:
        pass

if __name__ == "__main__":
    print("==================================================")
    print("🚀 HALİÇ ANTİK - OPTİMİZE AHMET USTA BOT")
    print("==================================================")
    print("👉 Model                : gemini-3.8-flash")
    print("👉 Ses Motoru           : Azure In-Memory Stream (tr-TR-AhmetNeural)")
    print("👉 Entegrasyon Portu    : 5005")
    print("==================================================")

    threading.Thread(target=ortak_chat_yoneticisi, daemon=True).start()
    web_servisi_baslat()