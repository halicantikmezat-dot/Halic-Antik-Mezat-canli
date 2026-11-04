import io
import itertools
import logging
import os
from queue import Empty, PriorityQueue, Queue
import re
import sys
import threading
import time
import uuid

# Windows Donanim Kutuphanelerini Guvenli Yukleme (Linux/Render ortaminda cokmez)
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
    pygame.mixer.init(frequency=24000, size=-16, channels=2, buffer=1024)
except Exception:
    try:
        import pygame
        pygame.mixer.init()
    except Exception:
        pygame = None

import azure.cognitiveservices.speech as speechsdk
from flask import Flask, jsonify, request
from google import genai
from google.genai import types
import numpy as np
import requests
from scipy.io import wavfile

# Config sinifi kontrolu
try:
    from config import Config
except ImportError:
    class Config:
        AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
        AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION")
        AZURE_SPEECH_VOICE = "tr-TR-AhmetNeural"

# DPI Farkindaligi (Yalnizca Windows)
if sys.platform == "win32" and ctypes:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# =====================================================================
# 1. AYARLAR VE 10 DUKKAN YAPILANDIRMASI
# =====================================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL_ADI = "gemini-3.8-flash"

TIKTOK_KULLANICI = "halicantikmezat34"
YOUTUBE_VIDEO_ID = ""

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

chat_queue = Queue(maxsize=100)
spiker_kuyrugu = PriorityQueue(maxsize=60)
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
    "hirdavat": "Hırdavat & Nalbur",
    "tasarim": "Tasarım",
    "modelaraba": "Model Araba",
    "karma": "Karma Satış",
    "diger": "Serbest Mezat",
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

SES_CACHE = os.path.join(
    os.path.abspath(os.path.dirname(__file__)), "static", "ses_cache"
)
os.makedirs(SES_CACHE, exist_ok=True)

SYSTEM_INSTRUCTION = """
Sen 'Haliç Antik' canlı mezatının kurdu, 10 farklı dükkânın tezgâhını aynı anda yöneten usta mezatçısısın. Adın Ahmet Usta.

HİTAP KURALLARI:
1. MİKROFONDAKİ KİŞİ VEYA YÖNETİCİ: Tezgâhın tek sahibi ve senin PATRONUNDUR. Mikrofondan veya yönetici panelinden ses/mesaj gelince sadece ona 'Patronum' veya 'Patron' diye hitap et.
2. CANLI SOHBETTEN VEYA SİTEDEN PEY VERENLER: Onlar mezatın alıcılarıdır. Onlara KESİNLİKLE 'Patron' deme! İsimleriyle veya bey/hanım hitabıyla seslen.
3. DİL: Sadece Türkçe, esprili, seri, teşvik edici ve tok mezatçı lisanı.
4. Asla yıldız (*), kare (#), liste veya markdown kullanma. Düz metin olarak TEK VEYA İKİ CÜMLEDE net söyle.
"""

# =====================================================================
# 2. YARDIMCI VE ASENKRON FONKSIYONLAR
# =====================================================================
def guvenli_arkaplan_post(url, json_data, timeout_sure=1.5):
    def _post():
        try:
            requests.post(url, json=json_data, timeout=timeout_sure)
        except Exception as e:
            print(f"⚠️ Arka Plan HTTP İletim Hatası ({url}): {e}")
    threading.Thread(target=_post, daemon=True).start()

# =====================================================================
# 3. MÜZİK SES KONTROLÜ (DUCKING - Sadece Windows)
# =====================================================================
MUZIK_UYGULAMALARI = [
    "chrome.exe", "spotify.exe", "aimp.exe", "vlc.exe",
    "msedge.exe", "brave.exe", "opera.exe", "wmplayer.exe", "firefox.exe"
]
muzik_oturum_hafizasi = {}
ducking_lock = threading.Lock()


def _arka_plan_ses_ayarla(seviye_orani):
    if not ctypes or not AudioUtilities or not ISimpleAudioVolume:
        return
    with ducking_lock:
        ole_initialized = False
        try:
            ctypes.windll.ole32.CoInitialize(None)
            ole_initialized = True
        except Exception:
            pass

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
                    try:
                        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                        oturum_anahtari = f"{p_name}_{session.ProcessId}"

                        if seviye_orani <= 0.05:
                            mevcut = volume.GetMasterVolume()
                            if oturum_anahtari not in muzik_oturum_hafizasi and mevcut > 0.05:
                                muzik_oturum_hafizasi[oturum_anahtari] = mevcut
                            volume.SetMasterVolume(0.03, None)
                        else:
                            orijinal = muzik_oturum_hafizasi.get(oturum_anahtari, 1.0)
                            volume.SetMasterVolume(orijinal, None)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            if ole_initialized:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass


def muzik_kis():
    threading.Thread(target=_arka_plan_ses_ayarla, args=(0.03,), daemon=True).start()


def muzik_ac():
    global muzik_oturum_hafizasi
    if not patron_konusuyor and not ai_speaking:
        threading.Thread(target=_arka_plan_ses_ayarla, args=(1.0,), daemon=True).start()
        muzik_oturum_hafizasi.clear()

# =====================================================================
# 4. PEY AYIKLAYICI VE KADEMELİ KONTROL
# =====================================================================
BIRLER = {
    "bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4,
    "beş": 5, "bes": 5, "altı": 6, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9
}
ONLAR = {
    "on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "kirk": 40,
    "elli": 50, "altmış": 60, "altmis": 60, "yetmiş": 70, "yetmis": 70,
    "seksen": 80, "doksan": 90
}


def yaziyla_sayiyi_coz(kelimeler):
    toplam = 0
    gecici = 0
    bulundu = False

    for k in kelimeler:
        if k in BIRLER:
            gecici += BIRLER[k]
            bulundu = True
        elif k in ONLAR:
            gecici += ONLAR[k]
            bulundu = True
        elif k in ["yüz", "yuz"]:
            gecici = (gecici if gecici > 0 else 1) * 100
            bulundu = True
        elif k == "bin":
            toplam += (gecici if gecici > 0 else 1) * 1000
            gecici = 0
            bulundu = True
        elif k == "milyon":
            toplam += (gecici if gecici > 0 else 1) * 1000000
            gecici = 0
            bulundu = True

    sonuc = toplam + gecici
    return int(sonuc) if bulundu and sonuc > 0 else None


def pey_bul(metin):
    if not metin:
        return None

    ham = (
        str(metin)
        .lower()
        .replace("₺", "")
        .replace("tl", "")
        .replace("lira", "")
        .replace("pey", "")
        .strip()
    )

    def _ondalik_k_coz(match):
        tam = match.group(1)
        ondalik = match.group(2)
        deger = float(f"{tam}.{ondalik}") * 1000
        return str(int(deger))

    ham = re.sub(r"\b(\d+)[.,](\d+)\s*(?:k|bin)\b", _ondalik_k_coz, ham)
    ham = re.sub(r"\b(\d+)\s*(?:k|bin)\b", r"\g<1>000", ham)
    ham = re.sub(r"(\d+)\.(\d{3})\b", r"\1\2", ham)

    rakam_eslesmeleri = re.findall(r"\b\d+\b", ham)
    for r in rakam_eslesmeleri:
        try:
            val = int(r)
            if val >= 50:
                return val
        except ValueError:
            continue

    kelimeler = re.findall(r"[a-zçğıöşü]+", ham)
    parcalanmis = []
    tum_kelime_kokleri = (
        list(BIRLER.keys()) + list(ONLAR.keys()) + ["yüz", "yuz", "bin", "milyon"]
    )

    for kelime in kelimeler:
        kalan = kelime
        while kalan:
            eslesti = False
            for kok in sorted(tum_kelime_kokleri, key=len, reverse=True):
                if kalan.startswith(kok):
                    parcalanmis.append(kok)
                    kalan = kalan[len(kok):]
                    eslesti = True
                    break
            if not eslesti:
                parcalanmis.append(kalan)
                break

    val = yaziyla_sayiyi_coz(parcalanmis)
    if val and val >= 50:
        return val

    return None


def pey_artisi_gecerli_mi(yeni_pey, guncel_pey):
    if yeni_pey <= guncel_pey:
        return False

    if guncel_pey == 0:
        return yeni_pey >= 50

    fark = int(yeni_pey - guncel_pey)

    if guncel_pey < 1000:
        adim = 50
    elif guncel_pey < 2000:
        adim = 100
    else:
        adim = 200

    return fark >= adim


def asgari_gecerli_pey_hesapla(guncel_pey):
    if guncel_pey == 0:
        return 50
    if guncel_pey < 1000:
        adim = 50
    elif guncel_pey < 2000:
        adim = 100
    else:
        adim = 200
    return int(guncel_pey + adim)

# =====================================================================
# 5. RESMİ AHMET USTA SES SİSTEMİ (AZURE tr-TR-AhmetNeural - TEK MOTOR)
# =====================================================================
def ahmet_sesi_indir_mp3(metin, dosya_yolu):
    try:
        azure_key = getattr(Config, "AZURE_SPEECH_KEY", None) or os.environ.get("AZURE_SPEECH_KEY")
        azure_region = getattr(Config, "AZURE_SPEECH_REGION", None) or os.environ.get("AZURE_SPEECH_REGION")

        if not azure_key or not azure_region:
            print("⚠️ Azure API Key veya Region eksik! Lütfen Environment ayarlarını kontrol edin.")
            return False

        speech_config = speechsdk.SpeechConfig(subscription=azure_key, region=azure_region)
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
        )

        ssml = f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='tr-TR'>
            <voice name='tr-TR-AhmetNeural'>
                <prosody rate='+15%' pitch='-2Hz'>
                    {metin}
                </prosody>
            </voice>
        </speak>"""

        audio_config = speechsdk.audio.AudioOutputConfig(filename=dosya_yolu)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
        result = synthesizer.speak_ssml_async(ssml).get()

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return os.path.exists(dosya_yolu) and os.path.getsize(dosya_yolu) > 500
        else:
            print(f"⚠️ Azure Sentez Hatası: {result.reason}")
            return False
    except Exception as e:
        print(f"⚠️ Azure TTS Bağlantı Hatası: {e}")
        return False


def kuyrugu_guvenli_bosalt():
    while not spiker_kuyrugu.empty():
        try:
            spiker_kuyrugu.get_nowait()
            spiker_kuyrugu.task_done()
        except (Empty, ValueError):
            break


def ses_cal_donanimdan(dosya_yolu):
    if not pygame or not pygame.mixer.get_init():
        return
    try:
        dosya_tam = os.path.abspath(dosya_yolu)
        if not os.path.exists(dosya_tam):
            return

        pygame.mixer.music.load(dosya_tam)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            if stop_signal or patron_konusuyor:
                pygame.mixer.music.stop()
                break
            time.sleep(0.04)

        try:
            pygame.mixer.music.unload()
        except Exception:
            pass

    except Exception as e:
        print(f"⚠️ Hoparlör Hatası: {e}")


def spiker_kuyruk_dinleyici():
    global ai_speaking, stop_signal, patron_konusuyor

    while True:
        try:
            oncelik, _, metin = spiker_kuyrugu.get()

            if stop_signal or patron_konusuyor:
                spiker_kuyrugu.task_done()
                continue

            temiz = (
                str(metin)
                .strip()
                .replace("*", "")
                .replace("#", "")
                .replace('"', "")
                .replace("'", "")
                .replace("`", "")
                .replace("\n", " ")
            )

            if not temiz:
                spiker_kuyrugu.task_done()
                continue

            dosya_mp3 = os.path.join(SES_CACHE, f"ahmet_{uuid.uuid4().hex[:6]}.mp3")

            ai_speaking = True
            muzik_kis()
            time.sleep(0.04)

            # SADECE VE SADECE AHMET USTA ÇALIŞIR
            if ahmet_sesi_indir_mp3(temiz, dosya_mp3):
                print(f'⚡ [Ahmet Usta (Azure)]: "{temiz}"')
                if not stop_signal and not patron_konusuyor:
                    ses_cal_donanimdan(dosya_mp3)
            else:
                print(f'⚠️ [Ahmet Usta Sentezlenemedi - Yabancı Ses Devreye Sokulmadı]: "{temiz}"')

            # Windows dosya kilidi onlemi ve gecici dosya temizligi
            time.sleep(0.05)
            if os.path.exists(dosya_mp3):
                try:
                    os.remove(dosya_mp3)
                except OSError:
                    pass

            ai_speaking = False
            muzik_ac()
            spiker_kuyrugu.task_done()
            time.sleep(0.03)

        except Exception as e:
            ai_speaking = False
            muzik_ac()
            try:
                spiker_kuyrugu.task_done()
            except Exception:
                pass
            time.sleep(0.05)


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
        spiker_kuyrugu.put((2, next(kuyruk_sayaci), metin))


def aninda_ahmeti_kes():
    global ai_speaking
    if pygame and pygame.mixer.get_init():
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass
    ai_speaking = False
    muzik_ac()


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
    print("\n🔇 [MOD F8]: Meydan Patronda (Bot Beklemede)")


def meydan_ahmete():
    global patron_dinleme_aktif, stop_signal
    patron_dinleme_aktif = True
    stop_signal = False
    print("\n🔊 [MOD F9]: Ahmet Devrede")
    konus("Buradayım patronum, sendeyim!", True)

# =====================================================================
# 6. GÖRSEL ANALİZ & KAMERA (Windows Masaüstü)
# =====================================================================
def fotograf_cek():
    if not ctypes or not wintypes or not ImageGrab:
        return None

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
            if "iriun" in buff.value.lower():
                iriun_hwnd = hwnd
                return False
        return True

    CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(CMPFUNC(pencereleri_tara), 0)

    try:
        if iriun_hwnd:
            user32.ShowWindow(iriun_hwnd, 9)
            user32.SetForegroundWindow(iriun_hwnd)
            time.sleep(0.12)

            rect = wintypes.RECT()
            user32.GetWindowRect(iriun_hwnd, ctypes.byref(rect))
            l, t, r, b = rect.left, rect.top, rect.right, rect.bottom

            goruntu = ImageGrab.grab(bbox=(l + 8, t + 35, r - 8, b - 8))
            gw, gh = goruntu.size
            if gw > 200 and gh > 200:
                kesim_sol = int(gw * 0.25)
                kesim_sag = int(gw * 0.75)
                return goruntu.crop((kesim_sol, 0, kesim_sag, gh))
            return goruntu
    except Exception as e:
        print(f"⚠️ Kamera Hatası: {e}")
    return None


def antika_ekspertiz_yap():
    if not client:
        konus("Patronum Gemini API anahtarı girilmemiş.", True)
        return

    print("\n📸 [EKSPERTİZ]: Sahne taranıyor...")
    konus("Gözüm tezgâhta patronum, parçayı inceliyorum...", True)

    img = fotograf_cek()
    if img is None:
        konus("Patronum kamera penceresinden görüntü alamadım.", True)
        return

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    image_part = types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")

    prompt = (
        "Sen antika uzmanı mezatçı Ahmet Usta'sın. Karşındaki kişi senin Patronundur. "
        "Fotoğraftaki tezgâha koyulan nesneyi analiz et: "
        "Malzemesini ve dönemini tahmin et. 'Patronum' diyerek esprili, tok mezatçı lisanıyla TEK CÜMLEDE tanıt."
    )

    try:
        res = client.models.generate_content(
            model=MODEL_ADI,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
        )
        if res.text and not stop_signal and not patron_konusuyor:
            cevap = res.text.strip()
            print(f"🏷️ [EKSPERTİZ]: {cevap}")
            konus(cevap, True)
    except Exception as e:
        print(f"⚠️ Görsel Analiz Hatası: {e}")
        konus("Patronum parça biraz parladı, net göremedim.", True)

# =====================================================================
# 7. MİKROFON İŞLEME (Windows Masaüstü VAD)
# =====================================================================
islem_lock = threading.Lock()


def patron_sesini_isle(wav_bytes):
    global stop_signal, patron_dinleme_aktif
    if not client or not islem_lock.acquire(blocking=False):
        return

    try:
        audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")
        prompt = (
            "Sen canlı mezatta tezgâhtaki mezatçı Ahmet Usta'sın. Mikrofondaki ses senin PATRONUNDUR.\n"
            "KOMUT KURALLARI:\n"
            "1. Patron 'bu nedir', 'tanıt', 'anlat', 'fiyatı nedir', 'ekspertiz' diyorsa: SADECE 'EKSPERTIZ_YAP' yaz.\n"
            "2. Patron 'dur', 'sus', 'kes', 'şalter' diyorsa: SADECE 'DUR' yaz.\n"
            "3. Patron 'meydan bende', 'bendesin' diyorsa: SADECE 'MEYDAN_PATRON' yaz.\n"
            "4. Patron 'sendeyim', 'devreye gir', 'ahmet gel' diyorsa: SADECE 'MEYDAN_AHMET' yaz.\n"
            "5. Diğer durumlarda: Canlı mezatta patronla düet yap, esprili ve saygılı TEK CÜMLE söyle."
        )

        res = client.models.generate_content(
            model=MODEL_ADI,
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
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
            print(f"🎤 [AHMET DÜET]: {yanit}")
            konus(yanit, True)

    except Exception as e:
        print(f"⚠️ Mikrofon İşleme Hatası: {e}")
    finally:
        if islem_lock.locked():
            islem_lock.release()


def patron_dinleyici():
    if not sd:
        return
    global stop_signal, patron_konusuyor, ai_speaking, patron_dinleme_aktif
    fs = 16000
    blok_boyutu = int(0.25 * fs)
    SESSIZLIK_SURESI = 0.85

    ses_parcalari = []
    son_ses_zamani = 0.0

    print("🎙️ Akıllı Mikrofon Kalibrasyonu Yapılıyor...")
    try:
        kalibrasyon = sd.rec(int(1.0 * fs), samplerate=fs, channels=1, dtype="int16")
        sd.wait()
        taban_gurultu = np.max(np.abs(kalibrasyon))
    except Exception:
        taban_gurultu = 1200

    DINAMIK_ESIK = max(5500, int(taban_gurultu * 2.4))
    print(f"✅ Kalibrasyon Tamam: Dinamik Eşik -> {DINAMIK_ESIK}")

    try:
        with sd.InputStream(samplerate=fs, channels=1, dtype="int16", blocksize=blok_boyutu) as stream:
            while True:
                try:
                    recording, overflowed = stream.read(blok_boyutu)
                    ses_gucu = np.max(np.abs(recording))

                    if ses_gucu >= DINAMIK_ESIK:
                        if not patron_konusuyor:
                            patron_konusuyor = True
                            aninda_ahmeti_kes()
                            muzik_kis()

                        son_ses_zamani = time.time()
                        ses_parcalari.append(recording.copy())

                    elif patron_konusuyor:
                        ses_parcalari.append(recording.copy())
                        if time.time() - son_ses_zamani > SESSIZLIK_SURESI:
                            patron_konusuyor = False
                            muzik_ac()

                            if len(ses_parcalari) >= 3:
                                butun_ses = np.concatenate(ses_parcalari, axis=0)
                                ses_parcalari.clear()

                                wav_io = io.BytesIO()
                                wavfile.write(wav_io, fs, butun_ses)
                                threading.Thread(
                                    target=patron_sesini_isle,
                                    args=(wav_io.getvalue(),),
                                    daemon=True,
                                ).start()
                            else:
                                ses_parcalari.clear()
                except Exception:
                    time.sleep(0.04)
    except Exception as e:
        print(f"⚠️ Yerel Mikrofon Aygıtı Açılamadı (Yalnızca Web Bas-Konuş aktif): {e}")

# =====================================================================
# 8. SOHBET VE ÇOKLU DÜKKAN PEY YÖNETİCİSİ
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


def ortak_chat_yoneticisi():
    print("🤖 10 Dükkanlı Çoklu Chat & Pey Motoru Devrede...")

    while True:
        try:
            paket = chat_queue.get(timeout=0.25)
            if len(paket) == 4:
                platform, user, comment, zorlanan_dukkan = paket
            else:
                platform, user, comment = paket
                zorlanan_dukkan = None
        except Empty:
            continue

        temiz_yorum = comment.strip()
        temiz_kucuk = temiz_yorum.lower()

        # Sistem bildirimlerini atla
        if any(
            yasak in temiz_kucuk
            for yasak in [
                "hedefleri hedefler", "istatistiklerini görebilir",
                "yayında olduğuna göre", "dağıtımı veya",
                "araçlar ekleyebilirsin", "katıldı", "beğendi"
            ]
        ):
            chat_queue.task_done()
            continue

        simdi = time.time()
        hedef_kod = zorlanan_dukkan if zorlanan_dukkan else yorumdan_dukkan_belirle(temiz_yorum, "gumus")
        hafiza = dukkan_hafizasi_al(hedef_kod)
        dukkan_adi = DUKKAN_ISIMLERI.get(hedef_kod, "Mezat")

        with hafiza_lock:
            sahnede_urun_var = bool(hafiza["aktif_urun"].get("lot"))

        if not sahnede_urun_var:
            if any(s in temiz_kucuk for s in ["selam", "merhaba", "sa", "s.a", "iyi akşamlar", "kolay gelsin"]):
                cevap = f"Aleyküm selam {user}, hoş geldiniz. Sahnede şu an ürün yok efendim, az sonra yeni lot geliyor takipte kalın."
                konus(cevap, False)
            elif any(p in temiz_kucuk for p in ["fiyat", "kaç", "pey", "aldım", "aldim", "satıldı mı", "hemen al"]):
                cevap = f"{user} efendim, az önceki parça satıldı bitti! Yeni eser hazırlanıyor, gözünüz ekranda olsun."
                konus(cevap, False)
            chat_queue.task_done()
            continue

        onay_kelimesi = any(o in temiz_kucuk for o in ["evet", "onay", "onaylıyorum", "aldım", "aldim", "benim"])

        # Bekleyen Hemen Al Kontrolü
        hemen_al_islemi = False
        with hafiza_lock:
            b_hemen = hafiza["bekleyen_hemen_al"]
            if b_hemen["user"] and (simdi - b_hemen["zaman"] <= 20.0):
                if b_hemen["user"].lower() == user.lower() and onay_kelimesi:
                    hafiza["son_pey_veren"] = user
                    hafiza["son_pey_zamani"] = simdi
                    fiyat = hafiza["aktif_urun"].get("hemen_al") or hafiza["guncel_pey"]
                    hafiza["bekleyen_hemen_al"] = {"user": None, "platform": None, "zaman": 0.0}
                    hemen_al_islemi = True
            elif b_hemen["zaman"] and (simdi - b_hemen["zaman"] > 20.0):
                hafiza["bekleyen_hemen_al"] = {"user": None, "platform": None, "zaman": 0.0}

        if hemen_al_islemi:
            konus(f"Teyit alındı! {dukkan_adi} reyonundan Sayın {user} aldı, hayırlı uğurlu olsun!", True)
            guvenli_arkaplan_post(
                "http://127.0.0.1:5000/api/hemen-al-tetikle",
                {"dukkan": hedef_kod, "platform": platform, "username": user, "fiyat": fiyat}
            )
            chat_queue.task_done()
            continue

        # Bekleyen Uçuk Pey Kontrolü
        yuksek_pey_islemi = False
        with hafiza_lock:
            b_pey = hafiza["bekleyen_yuksek_pey"]
            if b_pey["user"] and (simdi - b_pey["zaman"] <= 20.0):
                if b_pey["user"].lower() == user.lower() and onay_kelimesi:
                    fiyat = b_pey["fiyat"]
                    hafiza["guncel_pey"] = float(fiyat)
                    hafiza["son_pey_veren"] = user
                    hafiza["son_pey_zamani"] = simdi
                    hafiza["bekleyen_yuksek_pey"] = {"user": None, "fiyat": 0.0, "platform": None, "zaman": 0.0}
                    yuksek_pey_islemi = True
            elif b_pey["zaman"] and (simdi - b_pey["zaman"] > 20.0):
                hafiza["bekleyen_yuksek_pey"] = {"user": None, "fiyat": 0.0, "platform": None, "zaman": 0.0}

        if yuksek_pey_islemi:
            mesaj = f"Teyit verildi! {dukkan_adi} mezatında {user} bey tam {int(fiyat)} lira verdi! Var mı artıran?"
            konus(mesaj, True)
            guvenli_arkaplan_post(
                "http://127.0.0.1:5000/pey-ver",
                {"dukkan": hedef_kod, "musteri_adi": user, "miktar": float(fiyat), "islem": "pey"}
            )
            chat_queue.task_done()
            continue

        # Yeni Hemen Al Talebi
        if any(h in temiz_kucuk for h in ["hemen al", "aldım", "aldim", "aldık", "bana ver"]):
            with hafiza_lock:
                hafiza["bekleyen_hemen_al"] = {"user": user, "platform": platform, "zaman": simdi}
                lot_no = hafiza['aktif_urun'].get('lot', '')
            teyit_uyari = (
                f"Sayın {user}, {dukkan_adi} reyonundaki Lot {lot_no} için hemen al talebinizi duydum! "
                "Lütfen 'onay' yazarak teyit ediniz efendim!"
            )
            konus(teyit_uyari, True)
            chat_queue.task_done()
            continue

        # Pey Kontrolü
        fiyat = pey_bul(temiz_yorum)
        if fiyat:
            with hafiza_lock:
                g_pey = hafiza["guncel_pey"]
                bekleyen_user = hafiza["bekleyen_yuksek_pey"]["user"]

            ucuk_artis = False
            if g_pey > 0:
                if (fiyat >= g_pey * 5) or ((fiyat - g_pey) >= 5000):
                    ucuk_artis = True
            elif fiyat >= 5000:
                ucuk_artis = True

            if ucuk_artis and (bekleyen_user != user):
                with hafiza_lock:
                    hafiza["bekleyen_yuksek_pey"] = {
                        "user": user,
                        "fiyat": float(fiyat),
                        "platform": platform,
                        "zaman": simdi
                    }
                teyit_uyari = (
                    f"Sayın {user}, {dukkan_adi} tezgâhında tam {int(fiyat)} lira yazdınız! "
                    "Teklifiniz geçerli ise lütfen 'onay' yazarak teyit ediniz efendim!"
                )
                konus(teyit_uyari, True)
                chat_queue.task_done()
                continue

            if pey_artisi_gecerli_mi(fiyat, g_pey):
                with hafiza_lock:
                    hafiza["guncel_pey"] = float(fiyat)
                    hafiza["son_pey_veren"] = user
                    hafiza["son_pey_zamani"] = simdi

                mesaj = f"{dukkan_adi} reyonunda {user} bey {int(fiyat)} lira verdi! Var mı artıran?"
                konus(mesaj, True)
                guvenli_arkaplan_post(
                    "http://127.0.0.1:5000/pey-ver",
                    {"dukkan": hedef_kod, "musteri_adi": user, "miktar": float(fiyat), "islem": "pey"}
                )
                chat_queue.task_done()
                continue
            elif fiyat > g_pey:
                gerekli_asgari = asgari_gecerli_pey_hesapla(g_pey)
                uyari_mesaji = f"Sayın {user}, artış kuralı gereği en az {gerekli_asgari} lira vermeniz gerekir efendim!"
                konus(uyari_mesaji, True)
                chat_queue.task_done()
                continue
            elif fiyat <= g_pey:
                uyari_mesaji = f"Sayın {user}, sahnede güncel pey zaten {int(g_pey)} lira, lütfen üzerini yazınız!"
                konus(uyari_mesaji, True)
                chat_queue.task_done()
                continue

        # Normal Chat / Soru-Cevap
        if not fiyat:
            cevap = None
            if any(s in temiz_kucuk for s in ["selam", "merhaba", "iyi akşamlar", "hayırlı", "kolay gelsin"]):
                cevap = f"Aleyküm selam {user}, {dukkan_adi} mezatımıza hoş geldiniz efendim."
            elif any(s in temiz_kucuk for s in ["kargo", "nerede", "yeriniz", "neresi"]):
                cevap = f"{user}, dükkanımız İstanbul Balat'ta efendim, her yere güvenli kargomuz var."

            if cevap:
                konus(cevap, False)
                guvenli_arkaplan_post(
                    "http://127.0.0.1:5000/api/bot/ahmet-usta-yanit",
                    {"dukkan": hedef_kod, "yanit": cevap}
                )
            elif client and len(temiz_yorum) >= 3 and not stop_signal and not patron_konusuyor:
                with hafiza_lock:
                    baslik = hafiza['aktif_urun'].get('baslik', 'Eser')
                    aciklama = hafiza['aktif_urun'].get('aciklama', 'Kusursuz kondisyonda nadide parça')
                    g_pey_deger = int(hafiza['guncel_pey'])

                prompt = (
                    f"Canlı mezattayız. Dükkan: {dukkan_adi}. Tezgâhta mezatçı Ahmet Usta sensin.\n"
                    f"İzleyici ({user}) şunu sordu veya yazdı: '{temiz_yorum}'.\n"
                    f"Sahnedeki aktif ürün: {baslik}.\n"
                    f"Ürün künyesi ve açıklaması: {aciklama}.\n"
                    f"Güncel pey: {g_pey_deger} TL.\n"
                    "Ona mezatçı bilgeliğiyle, tok, esprili, saygılı ve TEK CÜMLEDE net cevap ver."
                )

                def _ai_chat(p_prompt, p_user, p_kod):
                    try:
                        res = client.models.generate_content(
                            model=MODEL_ADI,
                            contents=p_prompt,
                            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
                        )
                        if res.text and not stop_signal and not patron_konusuyor:
                            ai_yanit = res.text.strip().replace("*", "").replace("#", "")
                            konus(ai_yanit, False)
                            guvenli_arkaplan_post(
                                "http://127.0.0.1:5000/api/bot/ahmet-usta-yanit",
                                {"dukkan": p_kod, "yanit": ai_yanit}
                            )
                    except Exception as err:
                        print(f"⚠️ Gemini Chat Hatası: {err}")

                threading.Thread(target=_ai_chat, args=(prompt, user, hedef_kod), daemon=True).start()

        chat_queue.task_done()

# =====================================================================
# 9. TIKTOK & YOUTUBE DINLEYICILERI
# =====================================================================
def tiktok_dinleyici():
    while True:
        try:
            from TikTokLive import TikTokLiveClient
            from TikTokLive.events import CommentEvent

            print(f"🔗 TikTok Bağlanılıyor: @{TIKTOK_KULLANICI}")
            client_tt = TikTokLiveClient(unique_id=TIKTOK_KULLANICI)

            @client_tt.on(CommentEvent)
            async def on_comment(event: CommentEvent):
                user = getattr(event.user, "nickname", None) or getattr(
                    event.user, "unique_id", "Misafir"
                )
                comment = getattr(event, "comment", "")
                if comment and chat_queue.qsize() < 30:
                    chat_queue.put(("TikTok", user, comment))

            client_tt.run()
        except Exception as e:
            print(f"⚠️ TikTok Bağlantı Hatası: {e}")
            time.sleep(10)


def youtube_dinleyici():
    if not YOUTUBE_VIDEO_ID:
        return
    while True:
        try:
            import pytchat

            print(f"🔗 YouTube Chat Bağlanılıyor (ID: {YOUTUBE_VIDEO_ID})...")
            chat = pytchat.create(video_id=YOUTUBE_VIDEO_ID)
            while chat.is_alive():
                for c in chat.get().sync_items():
                    if chat_queue.qsize() < 30:
                        chat_queue.put(("YouTube", c.author.name, c.message))
                time.sleep(1)
        except Exception as e:
            print(f"⚠️ YouTube Bağlantı Hatası: {e}")
            time.sleep(12)

# =====================================================================
# 10. FLASK ENTEGRASYON KÖPRÜSÜ (10 DÜKKAN UYUMLU)
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

    tanitim = data.get("tanitim_yazisi") or data.get("aciklama") or ""

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

    anons_giris = (
        f"{dukkan_adi} tezgâhında Lot {lot_val}, {baslik_val} sahneye çıktı! "
        f"Açılışımız {guncel_pey_val} lira. Eserimizi detaylarıyla inceleyelim efendim."
    )
    konus(anons_giris, True)

    if tanitim and len(tanitim.strip()) > 8:
        konus(f"Eserin özellikleri şöyle efendim: {tanitim.strip()}", False)
    elif client and not stop_signal:
        def _urun_detay_uret():
            try:
                p = (
                    f"Sen antika ve müzayede uzmanı Ahmet Usta'sın. Dükkan: {dukkan_adi}. "
                    f"Sahnedeki ürün: Lot {lot_val} - {baslik_val}. "
                    "Bu eserin işçiliği, kondisyonu, damgası ve estetiği üzerine canlı mezatta alıcıları cezbedecek, "
                    "tok mezatçı lisanıyla 2 cümlelik usta tanıtımı yap. Asla madde işareti koyma."
                )
                res = client.models.generate_content(
                    model=MODEL_ADI,
                    contents=p,
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
                )
                if res.text and not stop_signal and not patron_konusuyor:
                    konus(res.text.strip().replace("*", "").replace("#", ""), False)
            except Exception as e:
                print(f"⚠️ AI Tanıtım Hatası: {e}")

        threading.Thread(target=_urun_detay_uret, daemon=True).start()

    return jsonify({"status": "ok"}), 200


@flask_app.route("/son_dakika_basladi", methods=["POST"])
def son_dakika_basladi():
    data = request.json or {}
    dukkan = (data.get("dukkan") or "gumus").lower()
    hafiza = dukkan_hafizasi_al(dukkan)
    dukkan_adi = DUKKAN_ISIMLERI.get(dukkan, "Mezat")

    with hafiza_lock:
        guncel_pey_val = int(hafiza['guncel_pey'])

    uyari = (
        f"Dikkat efendim! {dukkan_adi} reyonunda son 60 saniye başladı! "
        f"Peylerinizi hazırlayın, karar anı! Güncel pey {guncel_pey_val} lira, satıyorum!"
    )
    konus(uyari, True)
    return jsonify({"status": "ok"}), 200


@flask_app.route("/yeni_pey_geldi", methods=["POST"])
def yeni_pey_geldi():
    data = request.json or {}
    dukkan = (data.get("dukkan") or "gumus").lower()
    hafiza = dukkan_hafizasi_al(dukkan)
    dukkan_adi = DUKKAN_ISIMLERI.get(dukkan, "Mezat")

    kisi = data.get("kisi", "Bir alıcı")
    kisi_temiz = re.sub(r"\[.*?\]", "", str(kisi)).strip() or "Alıcımız"
    try:
        fiyat = float(data.get("fiyat", 0))
    except Exception:
        fiyat = 0.0

    with hafiza_lock:
        if not pey_artisi_gecerli_mi(fiyat, hafiza["guncel_pey"]):
            return jsonify({"status": "gecersiz_pey"}), 200

        hafiza["guncel_pey"] = fiyat
        hafiza["son_pey_veren"] = kisi_temiz
        hafiza["son_pey_zamani"] = time.time()

    mesaj = f"{dukkan_adi} reyonunda {kisi_temiz} bey {int(fiyat)} lira verdi! Var mı artıran?"
    konus(mesaj, True)
    return jsonify({"status": "ok"}), 200


@flask_app.route("/geri_sayim_uyari", methods=["POST"])
def geri_sayim_uyari():
    data = request.json or {}
    dukkan = (data.get("dukkan") or "gumus").lower()
    dukkan_adi = DUKKAN_ISIMLERI.get(dukkan, "Mezat")
    saniye = data.get("kalan_saniye", 15)
    mesaj = f"{dukkan_adi} reyonunda son {saniye} saniye! Satıyorum efendim, kaçırmayın, gidiyor!"
    konus(mesaj, True)
    return jsonify({"status": "ok"}), 200


@flask_app.route("/satis_bitti", methods=["POST"])
def satis_bitti():
    data = request.json or {}
    dukkan = (data.get("dukkan") or "gumus").lower()
    hafiza = dukkan_hafizasi_al(dukkan)
    dukkan_adi = DUKKAN_ISIMLERI.get(dukkan, "Mezat")

    with hafiza_lock:
        kazanan = data.get("kazanan", hafiza["son_pey_veren"])
        kazanan_temiz = re.sub(r"\[.*?\]", "", str(kazanan)).strip() or hafiza["son_pey_veren"]
        fiyat = data.get("fiyat", hafiza["guncel_pey"])
        urun_adi = hafiza["aktif_urun"].get("baslik", "Ürün")

    if kazanan_temiz and kazanan_temiz.lower() not in ["yok", "none", "-", ""]:
        mesaj = f"Sattım! {dukkan_adi} reyonundan {urun_adi}, {int(float(fiyat))} liraya {kazanan_temiz} beye hayırlı olsun!"
    else:
        mesaj = f"{dukkan_adi} reyonunda süre doldu, pey gelmediği için parça geri çekilmiştir!"

    konus(mesaj, True)
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
        hafiza["bekleyen_hemen_al"] = {"user": None, "platform": None, "zaman": 0.0}
        hafiza["bekleyen_yuksek_pey"] = {"user": None, "fiyat": 0.0, "platform": None, "zaman": 0.0}

    print(f"🧹 [{dukkan.upper()}] Ahmet Usta kuyruğu ve hafızası tamamen sıfırlandı.")
    return jsonify({"status": "ok"}), 200


@flask_app.route("/admin_ses_isle", methods=["POST"])
def admin_ses_isle():
    ses_dosyasi = request.files.get("ses")
    dukkan = (request.form.get("dukkan") or "gumus").lower()
    hafiza = dukkan_hafizasi_al(dukkan)
    dukkan_adi = DUKKAN_ISIMLERI.get(dukkan, "Mezat")

    if not ses_dosyasi or not client:
        return jsonify({"status": "no_audio"}), 400

    try:
        wav_bytes = ses_dosyasi.read()
        audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/webm")
        with hafiza_lock:
            baslik = hafiza['aktif_urun'].get('baslik', 'Eser')
            aciklama = hafiza['aktif_urun'].get('aciklama', '')

        prompt = (
            f"Sen canlı mezatta tezgâhtaki mezatçı Ahmet Usta'sın. Dükkan: {dukkan_adi}.\n"
            f"Aktif ürün: {baslik}.\n"
            f"Açıklama: {aciklama}.\n"
            "Mikrofonda konuşan kişi senin tek PATRONUNDUR. Sana ürünle veya mezatla ilgili soru sordu veya talimat verdi.\n"
            "Ona sadece 'Patronum' diye hitap ederek esprili, tok, saygılı ve TEK CÜMLEDE net cevap ver."
        )
        res = client.models.generate_content(
            model=MODEL_ADI,
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
        )
        if res.text:
            yanit = res.text.strip().replace("*", "").replace("#", "")
            konus(yanit, True)
            guvenli_arkaplan_post(
                "http://127.0.0.1:5000/api/bot/ahmet-usta-yanit",
                {"dukkan": dukkan, "yanit": yanit}
            )
            return jsonify({"status": "ok", "yanit": yanit}), 200
    except Exception as e:
        print(f"⚠️ Yönetici Ses Analiz Hatası: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "empty"}), 200


@flask_app.route("/canli_yorum", methods=["POST"])
def canli_yorum_al():
    data = request.json or {}
    platform = data.get("platform", "Sosyal Medya")
    user = data.get("user", "Misafir")
    comment = data.get("comment", "")
    dukkan = data.get("dukkan")
    if comment and chat_queue.qsize() < 30:
        chat_queue.put((platform, user, comment, dukkan))
    return jsonify({"status": "ok"}), 200


def web_servisi_baslat():
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    flask_app.run(host="127.0.0.1", port=5005, debug=False, use_reloader=False)

# =====================================================================
# 11. TETİKLEYİCİLER & KISAYOLLAR (Windows Masaüstü)
# =====================================================================
if keyboard:
    try:
        keyboard.add_hotkey("f12", acil_durdur)
        keyboard.add_hotkey("f8", meydan_patrona)
        keyboard.add_hotkey("f9", meydan_ahmete)
    except Exception:
        pass

if __name__ == "__main__":
    print("==================================================")
    print("🚀 HALİÇ ANTİK - 10 DÜKKANLI MEZAT SES ROBOTU")
    print("==================================================")
    print("👉 Tek Ses Motoru      : Ahmet Usta (Resmi Azure Speech SDK)")
    print("👉 Entegrasyon Servisi : http://127.0.0.1:5005")
    print(f"👉 TikTok Dinleyici    : @{TIKTOK_KULLANICI}")
    print(f"👉 YouTube Dinleyici   : {'Aktif' if YOUTUBE_VIDEO_ID else 'Kapalı'}")
    print(f"👉 Model               : {MODEL_ADI}")
    print(f"👉 Yapay Zekâ (Gemini) : {'Hazır' if GEMINI_API_KEY else 'API Key Yok'}")
    print("==================================================")

    if sd:
        threading.Thread(target=patron_dinleyici, daemon=True).start()
    threading.Thread(target=tiktok_dinleyici, daemon=True).start()
    threading.Thread(target=youtube_dinleyici, daemon=True).start()
    threading.Thread(target=ortak_chat_yoneticisi, daemon=True).start()

    web_servisi_baslat()