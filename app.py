from gevent import monkey
monkey.patch_all(thread=False, ssl=False)

import azure.cognitiveservices.speech as speechsdk
import base64
import os

try:
    import psycogreen.gevent
    psycogreen.gevent.patch_psycopg()
except Exception:
    pass

# ==========================================
# GEVENT VE ASENKRON MOTOR (EN BAŞTA OLMALI)
# ==========================================
from datetime import datetime, timedelta, timezone
import functools
import hashlib
import io
import json
import csv
import logging
import math
import os
import random
from queue import Queue
import re
import threading
import time
import uuid
import html

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from gevent.lock import BoundedSemaphore
import pandas as pd
import pyotp
import requests
from sqlalchemy import func, or_, text
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# Servis Katmanı
from bizimhesap_service import bizimhesap_siparis_aktar
from config import Config

# Yükleme klasörünü garantiye al
UPLOADS_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), "static", "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
SESLER_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), "static", "sesler")
os.makedirs(SESLER_DIR, exist_ok=True)

logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)

# ==========================================
# NGROK / CLOUDFLARE VE WOOCOMMERCE CORS BAŞLIKLARI
# ==========================================
@app.after_request
def ek_guvenlik_ve_cors_ekle(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    return response

# ==========================================
# 10 DÜKKANIN KODLARI VE KURALLARI
# ==========================================
DUKKANLAR = {
    "gumus": {"ad": "Gümüş", "sure": 60, "min_artis": 50.0},
    "antika": {"ad": "Antika", "sure": 60, "min_artis": 100.0},
    "toptan": {"ad": "Toptan", "sure": 45, "min_artis": 100.0},
    "kozmetik": {"ad": "Kozmetik", "sure": 30, "min_artis": 20.0},
    "bijuteri": {"ad": "Bijuteri", "sure": 30, "min_artis": 20.0},
    "hirdavat": {"ad": "Hırdavat & Nalbur", "sure": 45, "min_artis": 50.0},
    "tasarim": {"ad": "Tasarım", "sure": 60, "min_artis": 100.0},
    "modelaraba": {"ad": "Model Oyuncak Arabalar", "sure": 45, "min_artis": 50.0},
    "karma": {"ad": "Karma & Toplu Satış", "sure": 45, "min_artis": 50.0},
    "diger": {"ad": "Diğer - Her Şey", "sure": 30, "min_artis": 25.0},
}

# ==========================================
# VERİTABANI VE UYGULAMA YAPILANDIRMASI
# ==========================================
basedir = os.path.abspath(os.path.dirname(__file__))
database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.strip():
    database_url = database_url.strip()
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    db_url = database_url
else:
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    sqlite_yol = os.path.join(basedir, "instance", "halic_mezat.db").replace("\\", "/")
    db_url = "sqlite:///" + sqlite_yol

IS_SQLITE = db_url.startswith("sqlite")

ADMIN_PASSWORD_HASH = os.environ.get(
    "ADMIN_PASSWORD_HASH",
    generate_password_hash(os.environ.get("ADMIN_PASSWORD", "1453")),
)
ADMIN_2FA_SECRET = os.environ.get("ADMIN_2FA_SECRET", "JBSWY3DPEHPK3PXP")

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "halic_hamid_antik_mezat_gizli_anahtar_1453"
)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

if IS_SQLITE:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"timeout": 30}}
else:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": 25,
        "max_overflow": 50,
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }

app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024

if os.path.exists("/var/data"):
    UPLOAD_FOLDER = "/var/data/uploads"
else:
    UPLOAD_FOLDER = os.path.join(
        os.path.abspath(os.path.dirname(__file__)), "static", "uploads"
    )

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

db = SQLAlchemy(app)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="gevent",
    manage_session=False,
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=8000000,
)

totp = pyotp.TOTP(ADMIN_2FA_SECRET)
BOT_API_URL = "http://127.0.0.1:5005"


# ==========================================
# SES: OpenAI TTS canlı PCM akışı. Diske yazılmaz.
# ==========================================
def get_server_synthesizer():
    return None


def _yerel_ahmet_sesi(metin):
    return None


AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus")

def azure_ahmet_ses_uret_base64(metin):
    return None

_openai_istemci = None
_ses_nesil = 0
SES_ORNEKLEME = 24000
SES_BLOK = 4800
TURKCE_DIREKTIF = "Her koşulda sadece Türkçe telaffuz et, asla yabancı dile geçme"
SES_DIL_KURALI = (
    'language="tr". '
    + TURKCE_DIREKTIF + ". "
    "Yalnızca akıcı İstanbul Türkçesi ile yaz ve seslendir. "
    "Cihaz dili İngilizce veya başka bir dil olsa bile asla yabancı dile geçme. "
    "Hanımefendi sunucu gibi kibar, samimi ve net konuş. Şive taklidi yapma."
)


def ses_metnini_temizle(metin):
    """Ses motoruna yalnızca okunacak Türkçe düz metin gider."""
    s = str(metin or "")
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"\{[\s\S]*?\}", " ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"(?im)^\s*(system|assistant|user|json)\s*:\s*", " ", s)
    s = re.sub(r"[*_#`~|]+", " ", s)
    s = re.sub(r"[$€£¥]|NaN", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(USD|EUR|GBP|VIP|OK|AI|TTS|NFT)\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü\s.,!?:;'%\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:4096]


def _pey_tts_sablonu(metin):
    """Eski sabit fiyat cümlesi okunmaz. Gelen anons olduğu gibi kalır."""
    return ses_metnini_temizle(metin)


def ses_icin_fonetik(metin):
    """Hitap eklerini, ekrandaki yazıyı bozmadan ses motorunun akıcı okuyacağı biçime çevirir."""
    s = str(metin or "")
    for eski, yeni in (
        ("Hanımefendiye", "Hanımefendi'ye"),
        ("Beyefendiye", "Beyefendi'ye"),
        ("Hanımdan", "Hanım'dan"),
        ("Beyden", "Bey'den"),
        ("gardaşımdan", "Bey'den"),
        ("gardaşıma", "Beyefendi'ye"),
        ("babayiğit", "efendim"),
    ):
        s = s.replace(eski, yeni)
    return s


def _openai_ses_istemcisi():
    global _openai_istemci
    anahtar = getattr(Config, "OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not anahtar:
        return None
    if _openai_istemci is None:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        from openai import OpenAI
        _openai_istemci = OpenAI(api_key=anahtar)
    return _openai_istemci


def _mp3_kaydet(veri, nesil):
    os.makedirs(SESLER_DIR, exist_ok=True)
    ad = f"ahmet_{nesil}_{uuid.uuid4().hex[:8]}.mp3"
    with open(os.path.join(SESLER_DIR, ad), "wb") as dosya:
        dosya.write(veri)
    eskiler = sorted(
        (os.path.join(SESLER_DIR, ad) for ad in os.listdir(SESLER_DIR) if ad.endswith(".mp3")),
        key=os.path.getmtime,
    )
    for eski in eskiler[:-40]:
        try:
            os.remove(eski)
        except OSError:
            pass
    return f"/static/sesler/{ad}"


_son_ses_paketi = {"imza": "", "an": 0.0}
_ses_kuyruk = {}
_ses_kuyruk_kilit = threading.Lock()


def _openai_mp3(istemci, model, ses, soylenen):
    """Okunan metin şablondur. tts-1-hd yönerge alanını sesli okuduğu için talimat girdiye yazılmaz."""
    ortak = {"model": model, "voice": ses, "input": soylenen, "response_format": "mp3"}
    if model not in ("tts-1", "tts-1-hd"):
        ortak["instructions"] = 'language="tr". ' + TURKCE_DIREKTIF
    try:
        yanit = istemci.audio.speech.create(**ortak, extra_body={"language": "tr"})
    except Exception:
        ortak.pop("instructions", None)
        yanit = istemci.audio.speech.create(**ortak)
    if hasattr(yanit, "read"):
        return yanit.read()
    return bytes(getattr(yanit, "content", b"") or b"")


def _ses_modeli():
    model = getattr(Config, "OPENAI_TTS_MODEL", "") or "tts-1-hd"
    if model not in ("tts-1", "tts-1-hd"):
        model = "tts-1-hd"
    ses = getattr(Config, "OPENAI_TTS_VOICE", "") or "nova"
    return model, ses


def _sahne_sesini_kes(dukkan):
    """Satış, pas veya yeni sahne: çalan ve bekleyen anonsları düşür."""
    global _ses_nesil
    with _ses_kuyruk_kilit:
        _ses_nesil += 1
        nesil = _ses_nesil
        _ses_kuyruk[dukkan] = {
            "nesil": nesil,
            "isleyici": nesil,
            "calisiyor": False,
            "ogeler": [],
        }
    socketio.emit(
        "ses_kes",
        {"dukkan": dukkan, "nesil": nesil, "tur": "sahne", "zorunlu": True},
        room=dukkan,
    )


def _sahneyi_bosa_al(dukkan, neden):
    """Ürün satılınca veya geri çekilince sahneyi boşalt, sesi kes."""
    oda = odalar.get(dukkan)
    if oda is not None:
        oda["aktif_urun_id"] = None
        oda["durum"]["durum"] = "bosta"
    _sahne_sesini_kes(dukkan)
    mesaj = (
        "Sıradaki ürün hazırlanıyor..."
        if neden == "satildi"
        else "Yeni ürün sahneye bekleniyor..."
    )
    socketio.emit(
        "sahne_bosta",
        {"dukkan": dukkan, "neden": neden, "mesaj": mesaj},
        room=dukkan,
    )


def ahmet_ses_akisi(dukkan, metin, tur="anons", kesinti=False):
    """Tanıtım kesilmeden kuyruğa girer. Zorunlu kesinti satış ve yeni sahne içindir."""
    global _ses_nesil
    temiz = ses_metnini_temizle(ses_icin_fonetik(metin))
    if not temiz:
        return
    imza = f"{dukkan}:{temiz}"
    simdi = time.time()
    if _son_ses_paketi["imza"] == imza and simdi - _son_ses_paketi["an"] < 2:
        return
    _son_ses_paketi["imza"] = imza
    _son_ses_paketi["an"] = simdi
    zorunlu = bool(kesinti) or tur in ("satis", "sahne", "yeni_urun")
    with _ses_kuyruk_kilit:
        _ses_nesil += 1
        nesil = _ses_nesil
        kayit = _ses_kuyruk.get(dukkan) or {
            "nesil": 0, "isleyici": 0, "calisiyor": False, "ogeler": [],
        }
        kayit.setdefault("ogeler", [])
        if zorunlu:
            kayit["ogeler"] = []
            kayit["nesil"] = nesil
            kayit["isleyici"] = nesil
            kayit["calisiyor"] = False
        kayit["ogeler"].append({
            "metin": temiz, "tur": tur, "nesil": nesil, "zorunlu": zorunlu,
        })
        baslat = not kayit.get("calisiyor")
        if baslat:
            kayit["calisiyor"] = True
            kayit["isleyici"] = nesil
        _ses_kuyruk[dukkan] = kayit
        benim = kayit.get("isleyici")
    if zorunlu:
        socketio.emit(
            "ses_kes",
            {"dukkan": dukkan, "nesil": nesil, "tur": tur, "zorunlu": True},
            room=dukkan,
        )
    if baslat:
        socketio.start_background_task(_ses_kuyrugunu_bosalt, dukkan, benim)


def _ses_kuyrugunu_bosalt(dukkan, benim):
    while True:
        with _ses_kuyruk_kilit:
            kayit = _ses_kuyruk.get(dukkan)
            if not kayit or kayit.get("isleyici") != benim:
                return
            if not kayit.get("ogeler"):
                kayit["calisiyor"] = False
                return
            oge = kayit["ogeler"].pop(0)
        istemci = _openai_ses_istemcisi()
        if istemci is None:
            print("[SES] OPENAI_API_KEY tanımlı değil; ses akışı başlamadı.")
            with _ses_kuyruk_kilit:
                kayit = _ses_kuyruk.get(dukkan)
                if kayit and kayit.get("isleyici") == benim:
                    kayit["calisiyor"] = False
            return
        model, ses = _ses_modeli()
        try:
            veri = _openai_mp3(istemci, model, ses, oge["metin"])
        except Exception as hata:
            print(f"[SES HATA] OpenAI akış: {hata}")
            veri = None
        with _ses_kuyruk_kilit:
            kayit = _ses_kuyruk.get(dukkan)
            if not kayit or kayit.get("isleyici") != benim:
                return
            if not veri:
                if not kayit.get("ogeler"):
                    kayit["calisiyor"] = False
                    return
                continue
        adres = _mp3_kaydet(veri, oge["nesil"])
        socketio.emit("ses_oynat", {
            "dukkan": dukkan,
            "nesil": oge["nesil"],
            "tur": oge["tur"],
            "zorunlu": bool(oge.get("zorunlu")),
            "audio_url": adres,
        })
        print(f"[SES] anons yayında, {len(oge['metin'])} karakter, model {model}, ses {ses}")


def _satis_disi_sadelestir(metin, tur):
    """Hayırlı olsun ve bereketini görün yalnızca satış anonsunda kalır."""
    if tur in ("satis", "zafer"):
        return str(metin or "").strip()
    s = str(metin or "")
    s = re.sub(r"hayırlı olsun", "", s, flags=re.IGNORECASE)
    s = re.sub(r"bereketini görün", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+([,.!])", r"\1", s)
    return s.strip(" ,")


def _patron_papaganini_sil(metin):
    s = str(metin or "")
    s = re.sub(r"pardon efendim,?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(
        r"sayın yöneticimin\s*/\s*patronun belirttiği üzere\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"sayın yöneticimin belirttiği üzere\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"patronun belirttiği üzere\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"patronun dediği üzere\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"yöneticimin belirttiği gibi\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ,")


def ahmet_usta_anons_yap(dukkan, metin, yedek_dene=True, tur="anons", kesinti=False):
    temiz = _patron_papaganini_sil(_satis_disi_sadelestir(metin, tur))
    if not temiz:
        return
    socketio.emit("yeni_canli_yorum", {
        "kullanici": "Ahmet Usta",
        "yorum": temiz,
        "mesaj": temiz,
        "dukkan": dukkan,
        "tip": "ahmet_anons",
        "tur": tur,
    }, room=dukkan)
    ahmet_ses_akisi(dukkan, temiz, tur=tur, kesinti=kesinti)


AHMET_NIDALARI = (
    "Aman efendim nefesler tutuldu, şu eserin güzelliğine bakın!",
    "Canlar, peyler açıldı, buyurun efendim!",
    "Hoş geldiniz, bu güzellik kaçmaz, buyurun efendim!",
)
_ahmet_nida_sirasi = 0


def ahmet_nida_sec():
    global _ahmet_nida_sirasi
    metin = AHMET_NIDALARI[_ahmet_nida_sirasi % len(AHMET_NIDALARI)]
    _ahmet_nida_sirasi += 1
    return metin


_TUTAR_BIRLER = {
    "bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4,
    "beş": 5, "bes": 5, "altı": 6, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
    "sıfır": 0, "sifir": 0,
}
_TUTAR_ONLAR = {
    "on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "kirk": 40, "elli": 50,
    "altmış": 60, "altmis": 60, "yetmiş": 70, "yetmis": 70, "seksen": 80, "doksan": 90,
}


def turkce_tutar_coz(metin):
    """Mesajın tamamı bir tutarsa rakama çevirir. 'yedi bin' -> 7000, '10 bin' -> 10000."""
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
        elif k in _TUTAR_BIRLER:
            gecici += _TUTAR_BIRLER[k]
        elif k in _TUTAR_ONLAR:
            gecici += _TUTAR_ONLAR[k]
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


def lira_okunusu(tutar):
    n = int(round(float(tutar or 0)))
    if n >= 1000:
        binler, kalan = divmod(n, 1000)
        if kalan:
            return f"{binler} bin {kalan}"
        return f"{binler} bin"
    return str(n)


def _son_unlu(metin):
    for harf in reversed(str(metin or "").lower()):
        if harf in "aıiuüoöeâîû":
            return harf
    return "a"


def _den_eki(ad):
    son = (ad[-1].lower() if ad else "a")
    kalin = _son_unlu(ad) in "aıouâû"
    sert = son in "çfhkpşst"
    if kalin:
        return "tan" if sert else "dan"
    return "ten" if sert else "den"


def ahmet_kisa_ad(kisi):
    ad = re.sub(r"\[[^\]]*\]", "", str(kisi or "")).strip()
    ad = re.sub(r"\s+(bey|hanım|hanim)$", "", ad, flags=re.IGNORECASE).strip()
    parca = ad.split()
    return parca[0] if parca else "Misafir"


def _hanim_mi(kisi):
    ham = re.sub(r"\[[^\]]*\]", "", str(kisi or ""))
    return bool(re.search(r"hanım|hanim", ham, re.IGNORECASE))


_pey_replik_sirasi = {"ciliz": 0, "dusuk": 0, "orta": 0, "dolgun": 0}


def _pey_adini_temizle(kisi):
    ad = re.sub(r"\[[^\]]*\]", "", str(kisi or "")).strip()
    ad = re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü\s]", "", ad).strip()
    return ad or "Misafir"


def _pey_guc_bandi(tutar, acilis, hemen_al):
    try:
        tutar = float(tutar or 0)
        acilis = float(acilis or 0)
        hemen_al = float(hemen_al or 0)
    except (TypeError, ValueError):
        return "orta"
    deger = hemen_al if hemen_al > acilis else (acilis * 1.8 if acilis else tutar)
    if acilis <= 0:
        return "orta"
    oran_acilis = tutar / acilis if acilis else 1
    oran_deger = tutar / deger if deger else oran_acilis
    if oran_acilis < 1.08 or (deger and tutar < deger * 0.35):
        return "ciliz"
    if oran_deger < 0.55 or oran_acilis < 1.25:
        return "dusuk"
    if oran_deger < 0.85:
        return "orta"
    return "dolgun"


def _pey_ikinci_cumle(band, hitap, eser):
    kaliplar = {
        "ciliz": (
            f"Ah {hitap}, bu nadide parçaya bu teklif kahve parası bile değil, yapmayın gözünüzü seveyim!",
            f"{hitap}, bu kadarcıkla {eser} kimseye kalmaz, bir gayret daha edin!",
        ),
        "dusuk": (
            f"Dostlar bu eserin kıymetini henüz idrak edemedik galiba; işçiliğine, yaşına bir bakın, bu fiyata bu kalite mezatta bir daha denk gelmez!",
            f"{eser} bu kadar ucuza konuşulmaz, el emeğine bir daha bakın, artırın efendim!",
        ),
        "orta": (
            "Eh buna da şükür, bereketiyle geldi ama yetmez, artıran yok mu?",
            f"Güzel, {hitap} kapıyı araladı, fakat eser hâlâ bekliyor, üstüne var mı?",
        ),
        "dolgun": (
            f"Ağzınıza sağlık {hitap}, işte şimdi mezat oldu, salon ayağa kalksın!",
            f"Bravo {hitap}, dolgun pey geldi, haydi bu ateşi söndürmeyin!",
        ),
    }
    secenek = kaliplar.get(band) or kaliplar["orta"]
    sira = _pey_replik_sirasi.get(band, 0)
    _pey_replik_sirasi[band] = sira + 1
    return secenek[sira % len(secenek)]


def _llm_pey_cumlesi(band, hitap, eser, aciklama, tutar):
    try:
        metin = _openai_metin_iste(
            "Sen canlı antika mezatçısısın. Tek cümle yaz. Türkçe konuş. "
            "Güncel fiyat deme, rakamı tekrarlama. En fazla 20 kelime.",
            f"Duygu: {band}. Hitap: {hitap}. Eser: {eser}. "
            f"Kısa not: {(aciklama or '')[:180]}. Teklif: {int(tutar)} TL.",
        )
    except Exception:
        return ""
    temiz = re.sub(r"\s+", " ", str(metin or "")).strip()
    if not temiz or re.search(r"güncel fiyat", temiz, re.IGNORECASE):
        return ""
    cumle = re.split(r"(?<=[.!])\s+", temiz)[0].strip()
    if cumle and cumle[-1] not in ".!":
        cumle += "!"
    return cumle


def ahmet_pey_cumlesi(platform, kisi, tutar, urun_adi="", acilis=0, hemen_al=0, aciklama=""):
    ad = _pey_adini_temizle(kisi)
    fiyat = int(round(float(tutar or 0)))
    ek = _den_eki(ad.split()[-1])
    giris = f"{ad}'{ek} {fiyat} TL geldi!"
    band = _pey_guc_bandi(fiyat, acilis, hemen_al)
    hitap = ahmet_hitap(ad)
    eser = (urun_adi or "eser").strip()
    ikinci = _llm_pey_cumlesi(band, hitap, eser, aciklama, fiyat) or _pey_ikinci_cumle(band, hitap, eser)
    return f"{giris} {ikinci}"


_zafer_sira = 0
ZAFER_KALIPLARI = (
    "Vay vay vay! Harika bir hamle! Sayın {ad} tam on ikiden vurdu! "
    "Gizli pazarlık eşiğimizi yakaladınız, bu şaheser artık sizindir! Helal olsun, güle güle kullanın!",
    "İşte usta gözü, işte koleksiyoner cesareti! Sayın {ad} eşiği tam kalbinden yakaladı ve eseri kaptı! "
    "Salondan koca bir alkış, hayırlı uğurlu olsun!",
    "Alkışlar size Sayın {ad}! Tam on ikiden vurdunuz! "
    "Gizli pazarlık eşiği aşıldı, bu eser artık sizin. Güle güle kullanın!",
)


def ahmet_zafer_anonsu(kisi):
    """Gizli eşik tutunca. Tutar ve taban rakamı söylenmez."""
    global _zafer_sira
    ad = _pey_adini_temizle(kisi)
    metin = ZAFER_KALIPLARI[_zafer_sira % len(ZAFER_KALIPLARI)].format(ad=ad)
    _zafer_sira += 1
    return metin


def ahmet_zaferi_seslendir(dukkan, kisi):
    ahmet_usta_anons_yap(dukkan, ahmet_zafer_anonsu(kisi), tur="zafer", kesinti=True)


def ahmet_pey_reaksiyonunu_seslendir(dukkan, kisi, tutar, urun_adi="", acilis=0, hemen_al=0, aciklama="", kesinti=False):
    def _uret():
        metin = ahmet_pey_cumlesi("", kisi, tutar, urun_adi, acilis, hemen_al, aciklama)
        ahmet_usta_anons_yap(dukkan, metin, tur="anons", kesinti=kesinti)

    socketio.start_background_task(_uret)


def ahmet_satis_cumlesi(lot, urun_adi, kisi):
    ad = ahmet_kisa_ad(kisi)
    eser = (urun_adi or "Eser").strip()
    hitap = "Hanımefendiye" if _hanim_mi(kisi) else "Beyefendiye"
    return (
        f"LOT #{lot} {eser}, {ad} {hitap} hayırlı uğurlu olsun, çok güzel günlerde kullanılsın!"
    )


def ahmet_hitap(isim):
    temiz = re.sub(r"\[[^\]]*\]", "", str(isim or "")).strip() or "Bir beyefendi"
    if re.search(r"\b(bey|hanım|hanim)\b", temiz, re.IGNORECASE):
        return temiz
    return f"{temiz} Bey"


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


def ahmet_pey_anonsu(kullanici, tutar, urun_adi="", onceki=None):
    return ahmet_pey_cumlesi("", kullanici, tutar, urun_adi)


def ahmet_usta_ekranda_yaz(dukkan, metin):
    socketio.emit(
        "yeni_canli_yorum",
        {
            "platform": "Ahmet Usta AI",
            "kullanici": "[Ahmet Usta]",
            "mesaj": metin,
            "zaman": time.strftime("%H:%M:%S"),
            "dukkan": dukkan,
        },
        room=dukkan,
    )


def ahmet_yanitini_yayinla(dukkan, metin):
    """Yalnızca Ahmet'in kendi cümlesini yazar ve seslendirir. Ham izleyici metni okunmaz."""
    temiz = re.sub(r"[\*#_]", "", str(metin or "")).strip().strip('"')
    if not temiz:
        temiz = ahmet_nida_sec()
    ahmet_usta_ekranda_yaz(dukkan, temiz)
    ahmet_usta_anons_yap(dukkan, temiz)


AHMET_SISTEM = (
    "Sen Haliç Antik Mezat'ın tatlı dilli, güler yüzlü, saygılı, hazırcevap ve esprili hanımefendi sunucususun. "
    "Konuşman son derece akıcı, samimi, kibar ve davetkardır. "
    "İzleyicilere nezaketle takıl; 'Efendim', 'Canlar', 'Hoş geldiniz' gibi sıcak bir mezat sohbeti kur. "
    "Yapay şive taklidi yapma. gardaş diye hitap etme. "
    "Normal pey anonsunda asla 'hayırlı olsun' veya 'bereketini görün' deme. Bu iki ifade yalnızca süre bitip satış onaylandığında söylenir. "
    "Pey anonsunu kısa ve rekabeti açık tut: '[İsim] Beyden / Hanımdan [Tutar] TL geldi! Var mı artıran efendim?' "
    "Satış anında: 'LOT #[No] [Ürün Adı], [İsim] Beyefendiye / Hanımefendiye hayırlı uğurlu olsun, çok güzel günlerde kullanılsın!' de ve çekici vur. "
    "Yorumlara tek cümlelik, kibar ve esprili bir hanımefendi cevabı ver. "
    + SES_DIL_KURALI
)

def _patron_sistemi(aktif_urun, patron_komut):
    return f"""
Sen Haliç Antik Mezat'ın profesyonel, coşkulu ve esprili canlı yayın sunucusu Ahmet Usta'sın.
Şu an sahnede olan aktif ürün bilgileri:
- Ürün Adı: {aktif_urun.get('baslik', 'Antika Eser')}
- Açıklama: {aktif_urun.get('aciklama', '')}
- Başlangıç Fiyatı: {aktif_urun.get('baslangic_fiyati', 0)} TL
- Güncel Pey: {aktif_urun.get('anlik_fiyat', 0)} TL

PATRON SANA ŞU ANONS TALİMATINI VERDİ: "{patron_komut}"

KURALLAR:
1. ASLA patronun sözlerini papağan gibi tekrarlama!
2. "Patronun dediği üzere", "Yöneticimin belirttiği gibi", "Pardon efendim" gibi lafları KESİNLİKLE KULLANMA.
3. Patron "fiyatı söyle / kaç para" dediyse doğrudan izleyicilere dönüp güncel pey/fiyatı söyle ve yeni teklif iste.
4. Patron "ürünü tanıt / anlat" dediyse ürünün antika hikayesini, detaylarını öne çıkararak heyecanla tanıt.
5. Doğrudan canlı yayındaki izleyicilere hitap et (Örn: "Evet değerli koleksiyonerler, elimizdeki bu nadide parça...").
6. Yanıtın kısa, net, mezat ortamına yakışır ve en fazla 2-3 cümle olsun.
Yalnızca Türkçe yaz. {TURKCE_DIREKTIF}.
"""


def _sistem_turkce(sistem):
    metin = str(sistem or "").strip()
    if TURKCE_DIREKTIF not in metin:
        metin = f'language="tr". {TURKCE_DIREKTIF}. {metin}'
    return metin


def _metni_cumlede_bitir(metin):
    """Yarım kalan son parçayı at. Tam cümle yoksa metni olduğu gibi bırak."""
    s = re.sub(r"\s+", " ", str(metin or "")).strip().strip('"')
    if not s:
        return ""
    if s[-1] in ".!?…":
        return s
    parcalar = re.split(r"(?<=[\.\!\?…])\s+", s)
    tam = [p.strip() for p in parcalar if p.strip() and p.strip()[-1] in ".!?…"]
    if tam:
        return " ".join(tam).strip()
    return s


def _openai_metin_iste(sistem, kullanici_metni, max_tokens=420):
    istemci = _openai_ses_istemcisi()
    if istemci is None:
        return ""
    yanit = istemci.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.8,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": _sistem_turkce(sistem)},
            {"role": "user", "content": kullanici_metni},
        ],
    )
    ham = re.sub(r"[\*#_]", "", (yanit.choices[0].message.content or "")).strip().strip('"')
    return _metni_cumlede_bitir(ham)


# ==========================================
# AHMET USTA YORUM CEVABI (OPENAI)
# ==========================================
def ahmet_usta_yoruma_cevap_ver(dukkan, kullanici, mesaj):
    def _cevapla():
        try:
            print(f"💬 Gelen yorum işleniyor [{dukkan}]: {kullanici} -> {mesaj}")
            oda = odalar.get(dukkan, {})
            aktif_urun_id = oda.get("aktif_urun_id")
            urun_bilgi = "Şu an sahnede aktif ürün bulunmamaktadır."

            with app.app_context():
                if aktif_urun_id:
                    u = db.session.get(Urun, aktif_urun_id)
                    if u:
                        anlik = oda.get("durum", {}).get("pey") or u.guncel_fiyat or u.acilis_fiyati or 0
                        urun_bilgi = (
                            f"Ürün adı: {u.urun_adi}. "
                            f"Anlık pey: {int(float(anlik or 0))} TL. "
                            f"Açıklama: {u.tanitim_yazisi or 'Belirtilmedi'}."
                        )

            cevap = _openai_metin_iste(
                AHMET_SISTEM,
                f"Sahnedeki ürün: {urun_bilgi}\nİzleyici {kullanici} dedi ki: {mesaj}",
            )
            print(f"[Ahmet Usta]: {cevap}")
            ahmet_yanitini_yayinla(dukkan, cevap or ahmet_nida_sec())
        except Exception as e:
            print(f"⚠️ Ahmet Usta Yorum Cevaplama Hatası: {e}")
            ahmet_yanitini_yayinla(dukkan, ahmet_nida_sec())

    socketio.start_background_task(_cevapla)


def minimum_pey_tabani(mevcut_fiyat):
    """Kabul edilen en düşük pey. Üstündeki her tutar geçerlidir, tabana yuvarlanmaz."""
    mevcut = float(mevcut_fiyat or 0)
    if mevcut <= 0:
        return 1
    return int(math.ceil(mevcut * 1.10))


def kademeli_min_artis(fiyat, dukkan="gumus"):
    f = float(fiyat or 0)
    taban = DUKKANLAR.get(dukkan, {}).get("min_artis", 50.0)
    if f < 1000:
        return taban
    elif f < 2000:
        return max(taban, 100.0)
    elif f < 5000:
        return max(taban, 250.0)
    else:
        return max(taban, 500.0)


def bota_haber_ver(endpoint, payload):
    def _gonder():
        try:
            requests.post(f"{BOT_API_URL}/{endpoint}", json=payload, timeout=1.0)
        except Exception:
            pass

    socketio.start_background_task(_gonder)


def suan_utc():
    return datetime.now(timezone.utc)


@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()


# ==========================================
# GÜVENLİK VE YARDIMCI FONKSİYONLAR
# ==========================================
def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("is_admin") is True and session.get("admin_2fa_ok") is True:
            return f(*args, **kwargs)

        req_auth_header = request.headers.get("X-Admin-Password")
        req_json_pass = (
            request.json.get("sifre")
            if request.is_json and request.json
            else None
        )
        incoming_pass = req_auth_header or req_json_pass

        if incoming_pass and check_password_hash(
            ADMIN_PASSWORD_HASH, str(incoming_pass)
        ):
            return f(*args, **kwargs)

        if (
            request.is_json
            or request.path.startswith("/api/")
            or request.method == "POST"
        ):
            return (
                jsonify({
                    "success": False,
                    "mesaj": "Yetkisiz erişim! Yönetici girişi yapınız.",
                }),
                403,
            )

        req_pass = request.args.get("sifre")
        if req_pass and check_password_hash(ADMIN_PASSWORD_HASH, req_pass):
            session["admin_sifre_ok"] = True
            return redirect(url_for("admin_2fa_ekrani"))

        return redirect(url_for("admin_login_ekrani"))

    return decorated_function


def get_client_ip():
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0].split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def get_device_fingerprint():
    ip = get_client_ip()
    user_agent = request.headers.get("User-Agent", "")
    accept_lang = request.headers.get("Accept-Language", "")
    raw_str = f"{ip}-{user_agent}-{accept_lang}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


def kaydet_guvenli_dosya(dosya):
    if not dosya or not dosya.filename:
        return None, None
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    temiz_ad = secure_filename(dosya.filename)
    benzersiz_ad = f"{uuid.uuid4().hex[:10]}_{temiz_ad}"
    dosya_yolu = os.path.join(app.config["UPLOAD_FOLDER"], benzersiz_ad)
    dosya.save(dosya_yolu)
    return f"/static/uploads/{benzersiz_ad}", benzersiz_ad


def saf_isim_temizle(isim):
    if not isim:
        return ""
    temiz = re.sub(r"\[.*?\]", "", str(isim)).strip()
    temiz = (
        temiz.replace("I", "ı")
        .replace("İ", "i")
        .replace("ş", "s")
        .replace("Ş", "s")
        .replace("ğ", "g")
        .replace("Ğ", "g")
        .replace("ü", "u")
        .replace("Ü", "u")
        .replace("ö", "o")
        .replace("Ö", "o")
        .replace("ç", "c")
        .replace("Ç", "c")
        .lower()
    )
    return " ".join(temiz.split())


def kategori_ad_temizle(kat):
    if not kat:
        return "Genel"
    temiz = str(kat).strip()
    temiz = re.sub(r"^kategorilerimiz\s*[»>]\s*", "", temiz, flags=re.IGNORECASE)
    temiz = re.sub(r"^kategoriler\s*[»>]\s*", "", temiz, flags=re.IGNORECASE).strip()
    return temiz.title() if temiz.lower() == "diğer herşey" else temiz


def dukkan_dogrula(kod):
    k = (kod or "").lower().strip()
    return k if k in DUKKANLAR else "gumus"


# ==========================================
# VERİTABANI MODELLERİ
# ==========================================
class Kullanici(db.Model):
    __tablename__ = "kullanici"
    id = db.Column(db.Integer, primary_key=True)
    ad_soyad = db.Column(db.String(100), nullable=False, index=True)
    telefon = db.Column(db.String(20), nullable=True, index=True)
    email = db.Column(db.String(100), nullable=True)
    adres = db.Column(db.Text, nullable=True)
    sifre_hash = db.Column(db.String(255), nullable=True)
    bonus = db.Column(db.Float, default=0.0)
    puan = db.Column(db.Float, default=100.0)
    onayli_mi = db.Column(db.Boolean, default=False)
    is_verified = db.Column(db.Boolean, default=False)
    dogrulama_kodu = db.Column(db.String(6), nullable=True)
    otp_code = db.Column(db.String(6), nullable=True)
    otp_bitis = db.Column(db.DateTime, nullable=True)
    is_banned = db.Column(db.Boolean, default=False, index=True)
    durum = db.Column(db.String(20), default="bekliyor", index=True)
    sozlesme_onay = db.Column(db.Boolean, default=True)
    ip_adresi = db.Column(db.String(50), nullable=True)
    cihaz_kodu = db.Column(db.String(64), nullable=True)
    kayit_tarihi = db.Column(db.DateTime, default=suan_utc)
    silindi_mi = db.Column(db.Boolean, default=False, index=True)
    silinme_tarihi = db.Column(db.DateTime, nullable=True)


class Urun(db.Model):
    __tablename__ = "urun"
    id = db.Column(db.Integer, primary_key=True)
    dukkan_kodu = db.Column(db.String(30), default="gumus", index=True)
    lot_no = db.Column(db.Integer, nullable=False, index=True)
    urun_adi = db.Column(db.String(200), nullable=False, index=True)
    kategori = db.Column(db.String(100), default="Genel", index=True)
    acilis_fiyati = db.Column(db.Float, nullable=False, default=0.0)
    guncel_fiyat = db.Column(db.Float, nullable=False, default=0.0)
    hemen_al_fiyati = db.Column(db.Float, nullable=True, default=0.0)
    tanitim_yazisi = db.Column(db.Text, nullable=True)
    fotograflar = db.Column(db.JSON, default=list)
    video = db.Column(db.String(300), nullable=True, default="")
    ses_dosyasi = db.Column(db.String(300), nullable=True, default="")
    durum = db.Column(db.String(20), default="Aktif", index=True)
    kazanan_adi = db.Column(db.String(100), nullable=True, default="Yok")
    kazanan_id = db.Column(
        db.Integer, db.ForeignKey("kullanici.id"), nullable=True, index=True
    )
    tedarikci_id = db.Column(db.Integer, nullable=True, index=True)
    siparis_durumu = db.Column(db.String(30), default="Odeme_Bekliyor")
    kargo_takip_no = db.Column(db.String(100), nullable=True)
    kargo_firmasi = db.Column(db.String(50), default="Yurtiçi Kargo")
    silindi_mi = db.Column(db.Boolean, default=False, index=True)
    silinme_tarihi = db.Column(db.DateTime, nullable=True)

    def to_dict(self, hafif=False):
        fiyat_val = float(self.acilis_fiyati or 0.0)
        guncel_val = float(
            self.guncel_fiyat if self.guncel_fiyat is not None else fiyat_val
        )
        hemen_val = float(self.hemen_al_fiyati or 0.0)

        foto_listesi = list(self.fotograflar or [])
        if not foto_listesi and self.video:
            foto_listesi = [self.video]

        if hafif:
            return {
                "id": self.id,
                "dukkan": self.dukkan_kodu or "gumus",
                "lot": self.lot_no,
                "ad": self.urun_adi,
                "kategori": self.kategori or "Genel",
                "fiyat": fiyat_val,
                "guncel_fiyat": guncel_val,
                "hemen_al_fiyat": hemen_val,
                "fotograflar": foto_listesi[:1],
                "video": self.video or "",
                "durum": self.durum or "Aktif",
                "tedarikci_id": getattr(self, "tedarikci_id", None),
            }

        return {
            "id": self.id,
            "dukkan": self.dukkan_kodu or "gumus",
            "lot": self.lot_no,
            "ad": self.urun_adi,
            "kategori": self.kategori or "Genel",
            "fiyat": fiyat_val,
            "guncel_fiyat": guncel_val,
            "hemen_al_fiyat": hemen_val,
            "hemen_al_fiyati": hemen_val,
            "tanitim_yazisi": self.tanitim_yazisi or "",
            "fotograflar": foto_listesi,
            "video": self.video or "",
            "ses": self.ses_dosyasi or "",
            "durum": self.durum or "Aktif",
            "kazanan": self.kazanan_adi or "Yok",
            "kazanan_id": self.kazanan_id,
            "tedarikci_id": getattr(self, "tedarikci_id", None),
            "silindi_mi": bool(self.silindi_mi),
        }


class OnTeklif(db.Model):
    __tablename__ = "on_teklif"
    id = db.Column(db.Integer, primary_key=True)
    dukkan_kodu = db.Column(db.String(30), default="gumus", index=True)
    urun_id = db.Column(
        db.Integer, db.ForeignKey("urun.id"), nullable=False, index=True
    )
    musteri_id = db.Column(
        db.Integer, db.ForeignKey("kullanici.id"), nullable=True
    )
    musteri_adi = db.Column(db.String(100), nullable=False)
    teklif = db.Column(db.Float, nullable=False)
    zaman = db.Column(db.String(20), default=lambda: time.strftime("%H:%M:%S"))


class Teklif(db.Model):
    __tablename__ = "teklif"
    id = db.Column(db.Integer, primary_key=True)
    dukkan_kodu = db.Column(db.String(30), default="gumus", index=True)
    urun_id = db.Column(
        db.Integer, db.ForeignKey("urun.id"), nullable=False, index=True
    )
    musteri_id = db.Column(
        db.Integer, db.ForeignKey("kullanici.id"), nullable=True
    )
    musteri_adi = db.Column(db.String(100), nullable=False)
    tutar = db.Column(db.Float, nullable=False)
    ip_adresi = db.Column(db.String(50), nullable=True)
    tarih = db.Column(db.DateTime, default=suan_utc)


class SikayetOneri(db.Model):
    __tablename__ = "sikayet_oneri"
    id = db.Column(db.Integer, primary_key=True)
    musteri_adi = db.Column(db.String(100), nullable=True)
    tur = db.Column(db.String(50), default="Görüş / Tavsiye")
    konu = db.Column(db.String(200), nullable=False)
    mesaj = db.Column(db.Text, nullable=False)
    durum = db.Column(db.String(20), default="Yeni")
    ip_adresi = db.Column(db.String(50), nullable=True)
    tarih = db.Column(db.DateTime, default=suan_utc)


class Elestiri(db.Model):
    __tablename__ = "elestiriler"
    id = db.Column(db.Integer, primary_key=True)
    musteri_adi = db.Column(db.String(100), nullable=True)
    hedef = db.Column(db.String(40), default="Mezat")
    mesaj = db.Column(db.Text, nullable=False)
    durum = db.Column(db.String(20), default="Yeni")
    ip_adresi = db.Column(db.String(50), nullable=True)
    dukkan_kodu = db.Column(db.String(30), default="gumus")
    tarih = db.Column(db.DateTime, default=suan_utc)


class Muzik(db.Model):
    __tablename__ = "muzik"
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(300), nullable=False)


class SepetItem(db.Model):
    __tablename__ = "sepet_item"
    id = db.Column(db.Integer, primary_key=True)
    musteri_id = db.Column(
        db.Integer, db.ForeignKey("kullanici.id"), nullable=False, index=True
    )
    urun_id = db.Column(
        db.Integer, db.ForeignKey("urun.id"), nullable=False, index=True
    )
    eklenme_tarihi = db.Column(db.DateTime, default=suan_utc)


class UrunTakip(db.Model):
    __tablename__ = "urun_takip"
    id = db.Column(db.Integer, primary_key=True)
    musteri_id = db.Column(
        db.Integer, db.ForeignKey("kullanici.id"), nullable=False, index=True
    )
    urun_id = db.Column(
        db.Integer, db.ForeignKey("urun.id"), nullable=False, index=True
    )
    tarih = db.Column(db.DateTime, default=suan_utc)


class UrunAlimSozlesmesi(db.Model):
    __tablename__ = "urun_alim_sozlesmesi"
    id = db.Column(db.Integer, primary_key=True)
    sozlesme_no = db.Column(db.String(50), unique=True, nullable=False)
    teslim_eden_ad = db.Column(db.String(120), nullable=False)
    teslim_eden_tc = db.Column(db.String(20), nullable=False)
    teslim_eden_tel = db.Column(db.String(30), nullable=False)
    teslim_eden_iban = db.Column(db.String(40), nullable=True)
    teslim_eden_adres = db.Column(db.Text, nullable=True)
    urun_adi = db.Column(db.String(200), nullable=False)
    kategori = db.Column(db.String(100), default="Antika")
    ekspertiz_notu = db.Column(db.Text, nullable=True)
    tahmini_deger = db.Column(db.Float, default=0.0)
    komisyon_orani = db.Column(db.Float, default=15.0)
    yasal_taahhut = db.Column(db.Boolean, default=True)
    olusturma_tarihi = db.Column(db.DateTime, default=suan_utc)


def urunu_kazanana_sepete_ekle(urun_id, kazanan_adi, kazanan_id=None):
    if not kazanan_adi or kazanan_adi in ["Yok", "None", "-", ""]:
        return

    if isinstance(kazanan_id, str):
        kazanan_id = kazanan_id.strip()
        kazanan_id = int(kazanan_id) if kazanan_id.isdigit() else None

    hedef_kullanici = None
    if kazanan_id:
        hedef_kullanici = db.session.get(Kullanici, kazanan_id)

    if not hedef_kullanici:
        temiz_ad = saf_isim_temizle(kazanan_adi)
        if not temiz_ad:
            return
        hedef_kullanici = (
            Kullanici.query.filter(
                Kullanici.silindi_mi.is_(False), Kullanici.ad_soyad.ilike(temiz_ad)
            ).first()
        )

    if hedef_kullanici:
        var_mi = SepetItem.query.filter_by(
            musteri_id=hedef_kullanici.id, urun_id=urun_id
        ).first()
        if not var_mi:
            yeni_item = SepetItem(musteri_id=hedef_kullanici.id, urun_id=urun_id)
            db.session.add(yeni_item)
            db.session.commit()

        urun = db.session.get(Urun, urun_id)
        if urun:
            satis_tutari = float(urun.guncel_fiyat or urun.acilis_fiyati or 0)
            socketio.start_background_task(
                bizimhesap_siparis_aktar,
                alici_ad=hedef_kullanici.ad_soyad,
                telefon=hedef_kullanici.telefon,
                email=hedef_kullanici.email,
                adres=hedef_kullanici.adres,
                urun_adi=urun.urun_adi,
                lot_no=urun.lot_no,
                satis_fiyati=satis_tutari,
            )


# ==========================================
# 10 DÜKKAN İÇİN ÇOKLU ODA VE MEZAT MOTORU
# ==========================================
odalar = {}
oda_kilitleri = {}
for kod in DUKKANLAR.keys():
    odalar[kod] = {
        "aktif_urun_id": None,
        "sayac_kalan": 0,
        "sayac_aktif": False,
        "sayac_gorev_id": 0,
        "otonom_aktif": False,
        "izleyici_sayisi": 0,
        "son_teklif_veren_id": None,
        "gizli_pazarlik_aktif": False,
        "gizli_taban_fiyat": None,
        "mevat_asama": "",
        "acilis_tabani": 0,
        "karar_pey_geldi": False,
        "durum": {
            "durum": "Bekliyor",
            "sure_bitis": 0,
            "pey": 0,
            "kazanan": "Yok",
            "kazanan_id": None,
        },
    }
    oda_kilitleri[kod] = BoundedSemaphore(1)


def _baslik_tekrarini_sil(metin, urun_adi):
    temiz = re.sub(r"\s+", " ", str(metin or "")).strip()
    ad = re.sub(r"\s+", " ", str(urun_adi or "")).strip()
    if ad:
        tekrar = re.compile(
            rf"(?:{re.escape(ad)}(?:[\s\.\,\;\:\!\?…\-]*)){{2,}}",
            re.IGNORECASE,
        )
        temiz = tekrar.sub(ad + " ", temiz)
    temiz = re.sub(
        r"Kayıtta malzeme,?\s*el işçiliği ve dönem ayrıca teyit edilmemiş\.?",
        "",
        temiz,
        flags=re.IGNORECASE,
    )
    temiz = re.sub(r"Malzeme, el işçiliği ve dönem kayda göre şöyle:\s*", "", temiz, flags=re.IGNORECASE)
    parcalar = re.split(r"(?<=[\.\!\?])\s+", temiz)
    tek = []
    for parca in parcalar:
        satir = parca.strip()
        if not satir:
            continue
        if tek and tek[-1].lower() == satir.lower():
            continue
        tek.append(satir)
    return " ".join(tek).strip()


def _hazir_secere(urun, acilis):
    ad = (getattr(urun, "urun_adi", None) or "Eser").strip()
    ham = _baslik_tekrarini_sil((getattr(urun, "tanitim_yazisi", None) or "").strip(), ad)
    if not ham or ham.lower() == ad.lower():
        ham = ""
    alt = int(acilis or 0)
    ust = int(alt * 1.25) if alt else 0
    euro_alt = max(1, alt // 40) if alt else 1
    euro_ust = max(euro_alt, (ust or alt) // 35) if alt else 1
    if ham:
        giris = f"Efendim, şuna bir bakın, {ad}. {ham} "
    else:
        giris = (
            f"Efendim, şuna bir bakın, {ad}. "
            "Duruşu temiz, el emeği üstünde, vitrinde de masada da kendine yer açar. "
        )
    return (
        giris
        + "Koleksiyoncunun gözü bunu süs diye geçiştirmez; evde sohbet açan, ofiste köşeyi tutan bir parçadır. "
        + f"Bizde açılış {alt} lira. Türkiye vitrininde tahminen {alt} ile {ust} lira konuşulur, "
        + f"Avrupa tarafında aynı cins eser kabaca {euro_alt} ile {euro_ust} avro bandında gezer. "
        + "Buyurun efendim, eli değen alsın."
    )


def ahmet_sahne_acilis_metni(urun, acilis_fiyati):
    ad = (getattr(urun, "urun_adi", None) or "Eser").strip()
    tutar = int(round(float(acilis_fiyati or 0)))
    return (
        f"Mezatımız başlıyor efendim! Sıradaki eserimiz {ad}. "
        f"Açılış fiyatımız {tutar} TL. Alıcısına şimdiden hayırlı uğurlu olsun."
    )


def ahmet_secere_metni(ad, aciklama, acilis):
    yedek = (
        f"Efendim, şuna bir bakın, {ad}. "
        f"{(aciklama or 'El emeği, vitrinde de masada da kendine yer açan bir parça.')} "
        f"Bizde açılış {int(round(float(acilis or 0)))} lira, eli değen alsın."
    )
    try:
        metin = _openai_metin_iste(
            SES_DIL_KURALI
            + " Sen tatlı dilli, kibar hanımefendi bir mezat sunucususun. Şive taklidi yapma, gardaş deme. "
            "Ürün başlığı ve açıklamasından 2-3 cümlelik akıcı tanıtım yaz. "
            "Her cümleyi nokta veya ünlemle bitir. Son cümleyi yarım bırakma. "
            "Ne işe yaradığını, madenini veya materyalini, nereden geldiğini, nasıl kullanıldığını, "
            "varsa dikkat edilecek yönünü ve piyasa ile dünya antikacılık değerini özetle. "
            "Uydurma menşe ekleme. Yalnızca verilen kayda dayan.",
            f"Başlık: {ad}\nAçıklama: {aciklama or 'Belirtilmedi'}\nAçılış: {int(round(float(acilis or 0)))} lira",
            max_tokens=520,
        )
        return metin or yedek
    except Exception as hata:
        print(f"[SECERE] {hata}")
        return yedek


def ahmet_kisa_acilis_seslendir(dukkan, ad, acilis):
    """Yeni sahne: yalnız kısa açılış. Uzun tanıtım dokununca okunur."""
    sahte = type("UrunSatir", (), {"urun_adi": ad})()
    ahmet_usta_anons_yap(
        dukkan,
        ahmet_sahne_acilis_metni(sahte, acilis),
        tur="tanitim",
    )


def _gizli_taban_fiyati(urun, oda):
    """Hemen al fiyatının %82'si. Yalnızca oda belleğinde durur, istemciye yazılmaz."""
    hemen_al = float(getattr(urun, "hemen_al_fiyati", 0) or 0) if urun is not None else 0.0
    if hemen_al <= 0:
        return None
    return int(round(hemen_al * 0.82))


def _gizli_esige_vardi(oda, tutar):
    if not oda.get("gizli_pazarlik_aktif"):
        return False
    taban = oda.get("gizli_taban_fiyat")
    if taban is None:
        return False
    try:
        return float(tutar) >= float(taban)
    except (TypeError, ValueError):
        return False


def ahmet_sicak_soguk_metni(kisi, oran):
    ad = _pey_adini_temizle(kisi)
    if oran > 0.25:
        return (
            f"Sayın {ad}, bu teklifle hedefimizden çok uzaktayız! "
            "Bu nadide esere biraz daha cömert yaklaşmak lazım, salonu ısıtalım!"
        )
    if oran >= 0.10:
        return (
            f"Güzel adım Sayın {ad}, hedefimize yaklaştınız! "
            "İbre bize doğru dönüyor, biraz daha gayret!"
        )
    return (
        f"Nefesler tutuldu! Çok ama çok yaklaştınız Sayın {ad}, "
        "bir tık daha dokundunuz mu eser sizindir, ha gayret!"
    )


def ahmet_sicak_soguk_seslendir(dukkan, oda, kisi, tutar):
    """Pazarlıkta eşik altı teklif. Taban rakamı söylenmez ve yayılmaz."""
    if oda.get("mevat_asama") != "pazarlik" or not oda.get("gizli_pazarlik_aktif"):
        return False
    taban = oda.get("gizli_taban_fiyat")
    if taban is None:
        return False
    try:
        taban = float(taban)
        tutar = float(tutar)
    except (TypeError, ValueError):
        return False
    if taban <= 0 or tutar >= taban:
        return False
    oran = (taban - tutar) / taban
    ahmet_usta_anons_yap(
        dukkan,
        ahmet_sicak_soguk_metni(kisi, oran),
        tur="anons",
        kesinti=False,
    )
    return True


def _serbest_pey_modu(oda):
    return oda.get("mevat_asama") == "pazarlik" or bool(oda.get("gizli_pazarlik_aktif"))


def _pazarlik_modu(oda):
    return oda.get("mevat_asama") == "pazarlik" or bool(oda.get("gizli_pazarlik_aktif"))


def _acilis_tabani(oda, urun=None):
    taban = float(oda.get("acilis_tabani") or 0)
    if taban <= 0 and urun is not None:
        taban = float(getattr(urun, "acilis_fiyati", 0) or 0)
    return taban


def _pey_kabul_mu(oda, urun, tutar, mevcut):
    try:
        tutar = float(tutar or 0)
        mevcut = float(mevcut or 0)
    except (TypeError, ValueError):
        return False, "Geçersiz teklif."
    acilis = _acilis_tabani(oda, urun)
    if mevcut > 0 and tutar > mevcut * 5:
        return False, "Güvenlik kilidi: teklif mevcut fiyatın 5 katını aşıyor. Lütfen tutarı kontrol edin."
    if _serbest_pey_modu(oda):
        if acilis > 0 and tutar < acilis:
            return False, f"Teklif açılış fiyatının ({int(acilis)} TL) altına inemez."
        if tutar <= mevcut:
            return False, f"Teklif mevcut fiyattan ({int(mevcut)} TL) yüksek olmalıdır!"
        return True, ""
    if mevcut > 0:
        gereken = minimum_pey_tabani(mevcut)
        if tutar < gereken:
            return False, (
                f"Geçersiz Teklif! En az %10 artışla minimum {gereken} TL verebilirsiniz."
            )
    if tutar <= mevcut:
        return False, f"Teklif mevcut fiyattan ({int(mevcut)} TL) yüksek olmalıdır!"
    return True, ""


def _pazarligi_hazirla(oda, urun):
    acilis = float(getattr(urun, "acilis_fiyati", 0) or 0) if urun is not None else 0.0
    oda["acilis_tabani"] = acilis
    oda["mevat_asama"] = "pazarlik"
    oda["gizli_pazarlik_aktif"] = True
    oda["gizli_taban_fiyat"] = _gizli_taban_fiyati(urun, oda)
    oda["karar_pey_geldi"] = False
    oda["durum"]["durum"] = "Sayim"
    oda["durum"]["sure_bitis"] = time.time() + 180
    oda["sayac_gorev_id"] += 1
    oda["sayac_aktif"] = True
    return oda["sayac_gorev_id"]


def _fiyati_acilisa_cek(dukkan, oda):
    """60 sn kararının başında güncel pey açılış fiyatına döner."""
    urun_id = oda.get("aktif_urun_id")
    acilis = float(oda.get("acilis_tabani") or 0)
    with app.app_context():
        urun = db.session.get(Urun, urun_id) if urun_id else None
        if urun is not None and acilis <= 0:
            acilis = float(urun.acilis_fiyati or 0)
        if urun is not None:
            urun.guncel_fiyat = acilis
            urun.kazanan_adi = "Yok"
            urun.kazanan_id = None
            db.session.add(urun)
            db.session.commit()
    oda["acilis_tabani"] = acilis
    oda["durum"]["pey"] = acilis
    oda["durum"]["kazanan"] = "Yok"
    oda["durum"]["kazanan_id"] = None
    oda["son_teklif_veren_id"] = None
    oda["karar_pey_geldi"] = False
    oda["mevat_asama"] = "karar"
    oda["gizli_pazarlik_aktif"] = False
    oda["gizli_taban_fiyat"] = None
    socketio.emit(
        "pey_guncellendi",
        {
            "dukkan": dukkan,
            "urun_id": urun_id,
            "pey": acilis,
            "kazanan": "Yok",
            "kazanan_id": None,
        },
        room=dukkan,
    )
    duyuru = "Pazarlık bitti, son karar süresi başladı"
    socketio.emit(
        "pazarlik_kapandi",
        {"dukkan": dukkan, "mesaj": duyuru, "pey": acilis, "kazanan": "Yok"},
        room=dukkan,
    )
    ahmet_usta_anons_yap(dukkan, duyuru, tur="sayac")
    return acilis


def _gizli_pazarligi_kapat(oda):
    oda["gizli_pazarlik_aktif"] = False
    oda["gizli_taban_fiyat"] = None
    if oda.get("mevat_asama") == "pazarlik":
        oda["mevat_asama"] = ""


def _pazarlik_erken_sat(dukkan, oda, urun, etiket_ad, kullanici_id, tutar, konusma_adi):
    """Kilidi çağıran tutar. Gizli eşik hiçbir yayına konmaz."""
    oda["sayac_aktif"] = False
    oda["sayac_gorev_id"] += 1
    oda["sayac_kalan"] = 0
    _gizli_pazarligi_kapat(oda)
    oda["durum"]["durum"] = "Satıldı"
    oda["durum"]["sure_bitis"] = 0
    oda["durum"]["kazanan"] = etiket_ad
    oda["durum"]["kazanan_id"] = kullanici_id
    oda["durum"]["pey"] = float(tutar)
    biten_id = oda.get("aktif_urun_id")
    oda["aktif_urun_id"] = None

    if urun is not None:
        urun.durum = "Satıldı"
        urun.kazanan_adi = etiket_ad
        urun.kazanan_id = kullanici_id
        urun.guncel_fiyat = float(tutar)
        db.session.add(urun)
        OnTeklif.query.filter_by(urun_id=urun.id).delete()
        db.session.commit()
        urunu_kazanana_sepete_ekle(urun.id, etiket_ad, kullanici_id)

    socketio.emit(
        "pey_guncellendi",
        {
            "dukkan": dukkan,
            "urun_id": getattr(urun, "id", biten_id),
            "pey": float(tutar),
            "kazanan": etiket_ad,
            "kazanan_id": kullanici_id,
        },
        room=dukkan,
    )
    socketio.emit(
        "urun_satildi_erken",
        {
            "dukkan": dukkan,
            "urun_id": biten_id,
            "kazanan": etiket_ad,
            "fiyat": float(tutar),
            "mesaj": "Tebrikler! Gizli pazarlık tabanı aşıldı, ürün sahibini buldu!",
        },
        room=dukkan,
    )
    bota_haber_ver(
        "satis_bitti",
        {"dukkan": dukkan, "kazanan": etiket_ad, "fiyat": float(tutar)},
    )
    _sahneyi_bosa_al(dukkan, "satildi")
    ahmet_zaferi_seslendir(dukkan, konusma_adi or etiket_ad)
    bota_haber_ver("ahmet_sifirla", {"dukkan": dukkan})


def _uc_dakikalik_pazarlik(dukkan, gorev_id):
    """180 sn: ilk 120 pazarlık, son 60 normal karar. Erken satış sayacı ayrıca durdurur."""
    oda = odalar[dukkan]
    oda["sayac_kalan"] = 180
    oda["sayac_aktif"] = True
    oda["mevat_asama"] = "pazarlik"
    oda["gizli_pazarlik_aktif"] = True

    socketio.emit(
        "tanitim_basladi",
        {"dukkan": dukkan, "sure": 180, "sayac_gorunsun": False, "pazarlik": True},
        room=dukkan,
    )

    son_uyari_yapildi = False
    while oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id and oda["sayac_kalan"] > 0:
        kalan = int(oda["sayac_kalan"])
        if kalan <= 60 and oda.get("mevat_asama") == "pazarlik":
            with oda_kilitleri[dukkan]:
                if oda["sayac_gorev_id"] != gorev_id or not oda["sayac_aktif"]:
                    return False
                if oda["durum"].get("durum") == "Satıldı":
                    return False
                _fiyati_acilisa_cek(dukkan, oda)
                oda["sayac_aktif"] = True
                oda["durum"]["durum"] = "Sayim"
                oda["durum"]["sure_bitis"] = time.time() + kalan
        asama = "karar" if oda.get("mevat_asama") == "karar" else "pazarlik"
        if asama == "karar" and kalan == 15 and not son_uyari_yapildi:
            son_uyari_yapildi = True
            bota_haber_ver("geri_sayim_uyari", {"dukkan": dukkan, "kalan_saniye": 15})
            ahmet_usta_anons_yap(dukkan, "Son 15 saniye beyler, eser elden gidiyor!")
        elif kalan > 15:
            son_uyari_yapildi = False
        socketio.emit(
            "sayac_guncelle",
            {
                "dukkan": dukkan,
                "kalan": kalan,
                "kalan_sure": kalan,
                "toplam": 180,
                "sayac_gorunsun": True,
                "asama": asama,
            },
            room=dukkan,
        )
        socketio.sleep(1)
        if oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id:
            oda["sayac_kalan"] -= 1

    if not (oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id):
        return False
    if oda["durum"].get("durum") == "Satıldı":
        return False
    return True


def dukkan_geri_sayim_gorevi(dukkan, gorev_id, akis="standart"):
    oda = odalar[dukkan]

    if akis == "pazarlik":
        if not _uc_dakikalik_pazarlik(dukkan, gorev_id):
            return
        _gorunur_karar_sayaci(dukkan, gorev_id, giris="pazarlik", yalniz_kapat=True)
        return

    if akis == "karar":
        _gizli_pazarligi_kapat(oda)
        _gorunur_karar_sayaci(dukkan, gorev_id, giris="karar")
        return

    # 1. AŞAMA: GİZLİ 120 SANİYE (Tanıtım & Detay Zoom)
    oda["sayac_kalan"] = 180
    oda["sayac_aktif"] = True

    socketio.emit(
        "tanitim_basladi",
        {"dukkan": dukkan, "sure": 120, "sayac_gorunsun": False},
        room=dukkan,
    )

    gizli_sayac = 120
    while oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id and gizli_sayac > 0:
        socketio.emit(
            "tanitim_sayac",
            {"dukkan": dukkan, "kalan": gizli_sayac, "sayac_gorunsun": False},
            room=dukkan,
        )
        socketio.sleep(1)
        gizli_sayac -= 1
        oda["sayac_kalan"] = 60 + gizli_sayac

    if not (oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id):
        return

    _gorunur_karar_sayaci(dukkan, gorev_id)


def _gorunur_karar_sayaci(dukkan, gorev_id, giris="tanitim", yalniz_kapat=False):
    oda = odalar[dukkan]

    if not yalniz_kapat:
        # 2. AŞAMA: GÖRÜNÜR 60 SANİYE (Pey Karar Sayacı)
        oda["sayac_kalan"] = 60
        bota_haber_ver("son_dakika_basladi", {"dukkan": dukkan})

        guncel_tutar = int(oda["durum"]["pey"])
        if giris == "pazarlik":
            karar_metni = ""
        elif giris == "karar":
            karar_metni = ""
        else:
            karar_metni = (
                f"Tanıtım bitti. Mezat sayacı açıldı. Son 60 saniye! Güncel teklif {guncel_tutar} lira, satıyorum!"
            )
        if karar_metni:
            ahmet_usta_anons_yap(
                dukkan,
                karar_metni,
                tur="sayac",
            )

        socketio.emit(
            "sayac_baslat",
            {"dukkan": dukkan, "kalan": 60, "sayac_gorunsun": True},
            room=dukkan,
        )

        son_uyari_yapildi = False
        while oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id:
            if oda["sayac_kalan"] <= 0:
                break

            if oda["sayac_kalan"] > 15:
                son_uyari_yapildi = False

            if oda["sayac_kalan"] == 15 and not son_uyari_yapildi:
                son_uyari_yapildi = True
                bota_haber_ver("geri_sayim_uyari", {"dukkan": dukkan, "kalan_saniye": 15})
                ahmet_usta_anons_yap(dukkan, "Son 15 saniye beyler, eser elden gidiyor!")

            socketio.emit(
                "sayac_guncelle",
                {"dukkan": dukkan, "kalan": oda["sayac_kalan"], "sayac_gorunsun": True},
                room=dukkan,
            )
            socketio.sleep(1)
            if oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id:
                oda["sayac_kalan"] -= 1

    # 3. AŞAMA: MEZAT KAPANIŞI VE PEY KONTROLÜ
    with oda_kilitleri[dukkan]:
        if oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id:
            oda["sayac_aktif"] = False
            oda["durum"]["sure_bitis"] = 0
            biten_urun_id = oda["aktif_urun_id"]
            oda["aktif_urun_id"] = None

            pey_var_mi = (
                bool(oda.get("karar_pey_geldi"))
                if oda.get("mevat_asama") == "karar"
                else (
                    oda["durum"]["kazanan"] not in ["Yok", "Kimse", None, "", "-"]
                    and float(oda["durum"]["pey"]) > 0
                )
            )
            satis_lot = ""
            satis_ad = "Eser"

            with app.app_context():
                if biten_urun_id:
                    urun = db.session.get(Urun, biten_urun_id)
                    if urun:
                        satis_lot = urun.lot_no
                        satis_ad = urun.urun_adi or "Eser"
                        if pey_var_mi:
                            urun.durum = "Satıldı"
                            urun.kazanan_adi = oda["durum"]["kazanan"]
                            urun.kazanan_id = oda["durum"]["kazanan_id"]
                            urun.guncel_fiyat = float(oda["durum"]["pey"])
                            db.session.add(urun)
                            OnTeklif.query.filter_by(urun_id=biten_urun_id).delete()
                            db.session.commit()
                            urunu_kazanana_sepete_ekle(
                                biten_urun_id,
                                oda["durum"]["kazanan"],
                                oda["durum"]["kazanan_id"],
                            )
                            oda["durum"]["durum"] = "Satıldı"
                        else:
                            urun.durum = "Pas" if oda.get("mevat_asama") == "karar" else "Aktif"
                            urun.kazanan_adi = "Yok"
                            urun.kazanan_id = None
                            db.session.add(urun)
                            db.session.commit()
                            oda["durum"]["durum"] = "Pas"

            socketio.emit(
                "sayac_bitti",
                {
                    "dukkan": dukkan,
                    "urun_id": biten_urun_id,
                    "mesaj": "Süre doldu!",
                    "satildi_mi": pey_var_mi,
                    "kazanan": oda["durum"]["kazanan"] if pey_var_mi else "Yok",
                    "kazanan_id": oda["durum"]["kazanan_id"] if pey_var_mi else None,
                    "fiyat": oda["durum"]["pey"] if pey_var_mi else 0,
                },
                room=dukkan,
            )

            bota_haber_ver(
                "satis_bitti",
                {
                    "dukkan": dukkan,
                    "kazanan": oda["durum"]["kazanan"] if pey_var_mi else "Yok",
                    "fiyat": oda["durum"]["pey"] if pey_var_mi else 0,
                },
            )

            _sahneyi_bosa_al(dukkan, "satildi" if pey_var_mi else "pas")

            if pey_var_mi:
                ahmet_usta_anons_yap(
                    dukkan,
                    ahmet_satis_cumlesi(satis_lot, satis_ad, oda["durum"]["kazanan"]),
                    tur="satis",
                )
            else:
                if oda.get("mevat_asama") == "karar":
                    ahmet_usta_anons_yap(
                        dukkan,
                        "Süre doldu, pey gelmedi. Ürün geri çekildi, pas geçildi.",
                        tur="sayac",
                        kesinti=True,
                    )
                else:
                    ahmet_usta_anons_yap(
                        dukkan,
                        f"{DUKKANLAR[dukkan]['ad']} reyonunda süre doldu, pey gelmediği için eser ayrılmıştır.",
                        kesinti=True,
                    )

            bota_haber_ver("ahmet_sifirla", {"dukkan": dukkan})


def dukkan_otonom_dongusu(dukkan):
    oda = odalar[dukkan]
    kural = DUKKANLAR[dukkan]
    print(f"🤖 [{kural['ad']}] 3 Dakikalık Otonom Mezat Başlatıldı...")

    while oda["otonom_aktif"]:
        with app.app_context():
            if oda["aktif_urun_id"] or oda["durum"]["durum"] in ["Sayim", "Aktif"]:
                socketio.sleep(3)
                continue

            socketio.sleep(14)
            if not oda["otonom_aktif"]:
                break

            secilen_urun = None
            acilis_fiyati = 0
            on_teklif_var = False

            en_iyi_ot = (
                OnTeklif.query.filter_by(dukkan_kodu=dukkan)
                .order_by(OnTeklif.id.asc())
                .first()
            )

            if en_iyi_ot:
                urun = db.session.get(Urun, en_iyi_ot.urun_id)
                if urun and not urun.silindi_mi and urun.durum != "Satıldı":
                    secilen_urun = urun
                    acilis_fiyati = float(en_iyi_ot.teklif)
                    on_teklif_var = True
                else:
                    db.session.delete(en_iyi_ot)
                    db.session.commit()
                    continue

            if not secilen_urun:
                secilen_urun = (
                    Urun.query.filter(
                        Urun.dukkan_kodu == dukkan,
                        Urun.durum == "Aktif",
                        Urun.silindi_mi.is_(False),
                    )
                    .order_by(Urun.lot_no.asc())
                    .first()
                )
                if secilen_urun:
                    acilis_fiyati = float(secilen_urun.acilis_fiyati or 0)
                    on_teklif_var = False

            if not secilen_urun:
                socketio.sleep(8)
                continue

            with oda_kilitleri[dukkan]:
                oda["aktif_urun_id"] = secilen_urun.id
                oda["durum"]["durum"] = "Bekliyor"
                oda["durum"]["pey"] = acilis_fiyati
                oda["durum"]["kazanan"] = (
                    en_iyi_ot.musteri_adi if on_teklif_var else "Yok"
                )
                oda["durum"]["kazanan_id"] = (
                    en_iyi_ot.musteri_id if on_teklif_var else None
                )
                secilen_urun.guncel_fiyat = acilis_fiyati
                db.session.commit()

            takipciler = UrunTakip.query.filter_by(urun_id=secilen_urun.id).all()
            for t in takipciler:
                socketio.emit(
                    "urun_takip_bildirimi",
                    {
                        "urun_id": secilen_urun.id,
                        "mesaj": f"Takip ettiğiniz Lot #{secilen_urun.lot_no} ({secilen_urun.urun_adi}) sahneye alındı!",
                    },
                )

            sahneyi_herkese_yayinla(
                dukkan,
                secilen_urun.to_dict(),
                {
                    "dukkan": dukkan,
                    "urun_id": secilen_urun.id,
                    "pey": acilis_fiyati,
                    "kazanan": oda["durum"]["kazanan"],
                    "kazanan_id": oda["durum"]["kazanan_id"],
                },
                yeni_urun=True,
            )

            bota_haber_ver(
                "urun_sahneye_aldi",
                {
                    "dukkan": dukkan,
                    "lot": secilen_urun.lot_no,
                    "baslik": secilen_urun.urun_adi,
                    "acilis": acilis_fiyati,
                    "guncel_fiyat": acilis_fiyati,
                    "hemen_al": secilen_urun.hemen_al_fiyati or 0,
                    "tanitim_yazisi": secilen_urun.tanitim_yazisi or "",
                },
            )

            ahmet_kisa_acilis_seslendir(dukkan, secilen_urun.urun_adi, acilis_fiyati)

            while oda["otonom_aktif"] and oda.get("aktif_urun_id") == secilen_urun.id:
                socketio.sleep(1)


# VERİTABANI ONARIMI: Tüm eksik kolonları (ip_adresi dahil) PostgreSQL'e otomatik ekler
def veritabani_tablolari_onar():
    with app.app_context():
        try:
            db.create_all()
            kolonlar = [
                ("kullanici", "ip_adresi", "VARCHAR(50)"),
                ("kullanici", "cihaz_kodu", "VARCHAR(64)"),
                ("kullanici", "sifre_hash", "VARCHAR(255)"),
                ("kullanici", "email", "VARCHAR(100)"),
                ("kullanici", "puan", "FLOAT DEFAULT 100.0"),
                ("kullanici", "bonus", "FLOAT DEFAULT 0.0"),
                ("kullanici", "durum", "VARCHAR(20) DEFAULT 'bekliyor'"),
                ("kullanici", "onayli_mi", "BOOLEAN DEFAULT FALSE"),
                ("kullanici", "sozlesme_onay", "BOOLEAN DEFAULT TRUE"),
                ("kullanici", "kayit_tarihi", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
                ("kullanici", "silindi_mi", "BOOLEAN DEFAULT FALSE"),
                ("kullanici", "silinme_tarihi", "TIMESTAMP"),
                ("urun", "dukkan_kodu", "VARCHAR(30) DEFAULT 'gumus'"),
                ("urun", "hemen_al_fiyati", "FLOAT DEFAULT 0.0"),
                ("urun", "kazanan_adi", "VARCHAR(100) DEFAULT 'Yok'"),
                ("urun", "kazanan_id", "INTEGER"),
                ("urun", "guncel_fiyat", "FLOAT DEFAULT 0.0"),
                ("urun", "tedarikci_id", "INTEGER"),
                ("urun", "siparis_durumu", "VARCHAR(30) DEFAULT 'Odeme_Bekliyor'"),
                ("urun", "kargo_takip_no", "VARCHAR(100)"),
                ("urun", "kargo_firmasi", "VARCHAR(50) DEFAULT 'Yurtiçi Kargo'"),
                ("urun", "silindi_mi", "BOOLEAN DEFAULT FALSE"),
                ("urun", "silinme_tarihi", "TIMESTAMP"),
                ("on_teklif", "dukkan_kodu", "VARCHAR(30) DEFAULT 'gumus'"),
                ("on_teklif", "musteri_id", "INTEGER"),
                ("teklif", "dukkan_kodu", "VARCHAR(30) DEFAULT 'gumus'"),
                ("teklif", "musteri_id", "INTEGER"),
                ("teklif", "ip_adresi", "VARCHAR(50)"),
                ("sikayet_oneri", "ip_adresi", "VARCHAR(50)"),
            ]
            try:
                db.session.execute(text(
                    "ALTER TABLE kullanici ADD COLUMN is_verified BOOLEAN DEFAULT FALSE"
                ))
                db.session.commit()
                db.session.execute(text(
                    "UPDATE kullanici SET is_verified = 1 WHERE onayli_mi = 1"
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()
            try:
                db.session.execute(text(
                    "ALTER TABLE kullanici ADD COLUMN dogrulama_kodu VARCHAR(6)"
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()
            for kolon_sql in (
                "ALTER TABLE kullanici ADD COLUMN otp_code VARCHAR(6)",
                "ALTER TABLE kullanici ADD COLUMN otp_bitis TIMESTAMP",
                "ALTER TABLE kullanici ADD COLUMN is_banned BOOLEAN DEFAULT FALSE",
            ):
                try:
                    db.session.execute(text(kolon_sql))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            try:
                db.session.execute(text(
                    "UPDATE kullanici SET is_banned = 1 WHERE durum = 'engellendi'"
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()
            for tablo, kolon, tip in kolonlar:
                try:
                    db.session.execute(
                        text(f"ALTER TABLE {tablo} ADD COLUMN {kolon} {tip};")
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        except Exception:
            pass
        finally:
            db.session.remove()


@app.route("/static/uploads/<path:filename>")
def serve_uploads(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ==========================================
# İZLEYİCİ, VİTRİN VE DÜKKAN ROTALARI
# ==========================================
@app.route("/")
def izleyici_index():
    dukkan = dukkan_dogrula(request.args.get("dukkan", "gumus"))
    return render_template("index.html", secili_dukkan=dukkan, dukkanlar=DUKKANLAR)


@app.route("/dukkan/<string:dukkan_adi>")
def dukkan_sayfasi(dukkan_adi):
    dukkan = dukkan_dogrula(dukkan_adi)
    return render_template("index.html", secili_dukkan=dukkan, dukkanlar=DUKKANLAR)


@app.route("/vitrin")
def vitrin():
    dukkan = dukkan_dogrula(request.args.get("dukkan", "gumus"))
    return render_template("vitrin.html", secili_dukkan=dukkan, dukkanlar=DUKKANLAR)


@app.route("/kurallar")
def kurallar_sayfasi():
    return render_template("kurallar.html")


@app.route("/ahmet-tanitim-geri", methods=["POST"])
def ahmet_tanitim_geri():
    return jsonify({"success": True, "url": AHMET_CIKIS_URL})


@app.route("/ahmet-metin-oku", methods=["POST"])
def ahmet_metin_oku():
    veri = request.json or {}
    dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
    metin = (veri.get("metin") or "").strip()[:1500]
    if not metin:
        return jsonify({"success": False, "mesaj": "Okunacak metin yok."})
    ahmet_usta_anons_yap(dukkan, metin, tur="tanitim")
    return jsonify({"success": True})


@app.route("/overlay")
def overlay_view():
    dukkan = dukkan_dogrula(request.args.get("dukkan", "gumus"))
    return render_template("overlay.html", secili_dukkan=dukkan)


@app.route("/api/dukkanlar", methods=["GET"])
def api_dukkanlar():
    cikti = {}
    for kod, bilgi in DUKKANLAR.items():
        oda = odalar.get(kod) or {}
        sahne = (oda.get("durum") or {}).get("durum")
        canli = bool(oda.get("aktif_urun_id")) and sahne != "Satıldı"
        cikti[kod] = {**bilgi, "canli": canli}
    return jsonify({"success": True, "dukkanlar": cikti})


@app.route("/api/doviz-kurlari", methods=["GET"])
def doviz_kurlari():
    try:
        return jsonify({
            "success": True,
            "kurlar": {"TRY": 1.0, "USD": 34.20, "EUR": 37.50},
        })
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/vitrin-urunler", methods=["GET"])
def vitrin_urunler():
    try:
        dukkan = dukkan_dogrula(request.args.get("dukkan", "gumus"))
        sayfa = max(1, int(request.args.get("sayfa", 1)))
        limit = min(60, max(10, int(request.args.get("limit", 40))))
        kategori = (request.args.get("kategori") or "").strip()
        arama = (request.args.get("arama") or "").strip()

        sorgu = Urun.query.filter(
            Urun.dukkan_kodu == dukkan,
            Urun.durum != "Satıldı",
            Urun.silindi_mi.is_(False),
        )

        if kategori and kategori not in ["Tüm Kategoriler", ""]:
            sorgu = sorgu.filter(
                or_(
                    Urun.kategori == kategori, Urun.kategori.ilike(f"%{kategori}%")
                )
            )

        if arama:
            if arama.isdigit():
                sorgu = sorgu.filter(
                    or_(Urun.lot_no == int(arama), Urun.urun_adi.ilike(f"%{arama}%"))
                )
            else:
                sorgu = sorgu.filter(Urun.urun_adi.ilike(f"%{arama}%"))

        toplam_kayit = sorgu.count()
        toplam_sayfa = (toplam_kayit + limit - 1) // limit
        urunler = (
            sorgu.order_by(Urun.lot_no.asc())
            .offset((sayfa - 1) * limit)
            .limit(limit)
            .all()
        )

        return jsonify({
            "dukkan": dukkan,
            "urunler": [u.to_dict(hafif=True) for u in urunler],
            "toplam_kayit": toplam_kayit,
            "toplam_sayfa": max(1, toplam_sayfa),
            "sayfa": sayfa,
        })
    finally:
        db.session.remove()


@app.route("/admin/urun-ara", methods=["GET"])
def admin_urun_ara_api():
    try:
        dukkan = dukkan_dogrula(request.args.get("dukkan", "gumus"))
        arama = (request.args.get("arama") or "").strip()
        kategori = (request.args.get("kategori") or "").strip()

        sorgu = Urun.query.filter(
            Urun.dukkan_kodu == dukkan,
            Urun.durum != "Satıldı",
            Urun.silindi_mi.is_(False),
        )

        if kategori and kategori != "Tüm Kategoriler":
            sorgu = sorgu.filter(
                or_(
                    Urun.kategori == kategori,
                    Urun.kategori.ilike(f"%{kategori}%"),
                )
            )

        if arama:
            if arama.isdigit():
                sorgu = sorgu.filter(
                    or_(Urun.lot_no == int(arama), Urun.urun_adi.ilike(f"%{arama}%"))
                )
            else:
                sorgu = sorgu.filter(Urun.urun_adi.ilike(f"%{arama}%"))

        urunler = sorgu.order_by(Urun.lot_no.asc()).limit(50).all()
        return jsonify({
            "success": True,
            "dukkan": dukkan,
            "urunler": [u.to_dict(hafif=False) for u in urunler],
        })
    except Exception as e:
        return jsonify({"success": False, "urunler": [], "hata": str(e)}), 200
    finally:
        db.session.remove()


@app.route("/canli-durum", methods=["GET"])
def canli_durum():
    dukkan = dukkan_dogrula(request.args.get("dukkan", "gumus"))
    oda = odalar[dukkan]

    try:
        aktif_urun_obj = (
            db.session.get(Urun, oda["aktif_urun_id"])
            if oda["aktif_urun_id"]
            else None
        )
        return jsonify({
            "dukkan": dukkan,
            "durum": oda["durum"]["durum"],
            "sure_bitis": oda["durum"]["sure_bitis"],
            "pey": oda["durum"]["pey"],
            "kazanan": oda["durum"]["kazanan"],
            "kazanan_id": oda["durum"].get("kazanan_id"),
            "kalan_sayac": oda["sayac_kalan"],
            "aktif_urun": (
                aktif_urun_obj.to_dict()
                if (aktif_urun_obj and not aktif_urun_obj.silindi_mi)
                else None
            ),
        })
    finally:
        db.session.remove()


@app.route("/durum-getir", methods=["GET"])
def durum_getir():
    dukkan = dukkan_dogrula(request.args.get("dukkan", "gumus"))
    oda = odalar[dukkan]

    try:
        aktif_urun_obj = (
            db.session.get(Urun, oda["aktif_urun_id"])
            if oda["aktif_urun_id"]
            else None
        )
        aktif_urun = (
            aktif_urun_obj.to_dict()
            if (aktif_urun_obj and not aktif_urun_obj.silindi_mi)
            else None
        )

        urunler_db = (
            Urun.query.filter(
                Urun.dukkan_kodu == dukkan,
                Urun.silindi_mi.is_(False),
                Urun.durum != "Satıldı",
            )
            .order_by(Urun.lot_no.asc())
            .limit(40)
            .all()
        )
        urunler = [u.to_dict(hafif=True) for u in urunler_db]

        tum_kullanicilar = (
            Kullanici.query.filter(Kullanici.silindi_mi.is_(False))
            .order_by(Kullanici.id.desc())
            .limit(50)
            .all()
        )
        kullanici_tel_map = {k.id: (k.telefon or "-") for k in tum_kullanicilar}

        musteriler = [
            {
                "id": m.id,
                "ad": m.ad_soyad or "İsimsiz",
                "tel": m.telefon or "-",
                "mail": m.email or "-",
                "adres": m.adres or "-",
                "bonus": m.bonus or 0.0,
                "puan": m.puan if m.puan is not None else 100.0,
                "onayli_mi": bool(m.onayli_mi),
                "durum": m.durum or "bekliyor",
                "ip": m.ip_adresi or "-",
            }
            for m in tum_kullanicilar
        ]

        satilan_urunler = (
            Urun.query.filter(
                or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
                Urun.silindi_mi.is_(False),
            )
            .order_by(Urun.id.desc())
            .limit(200)
            .all()
        )

        gruplanmis_dosyalar = {}
        for u in satilan_urunler:
            m_adi = (u.kazanan_adi or "").strip()
            if not m_adi or m_adi.lower() in ["yok", "none", "-", ""]:
                continue

            grup_anahtari = str(u.kazanan_id) if u.kazanan_id else m_adi
            if grup_anahtari not in gruplanmis_dosyalar:
                gruplanmis_dosyalar[grup_anahtari] = {
                    "musteri_id": u.kazanan_id,
                    "musteri_adi": m_adi,
                    "telefon": kullanici_tel_map.get(u.kazanan_id, "-"),
                    "urunler": [],
                    "toplam_tutar": 0.0,
                }

            satiss_fiyati = float(
                u.guncel_fiyat
                if (u.guncel_fiyat and u.guncel_fiyat > 0)
                else (u.acilis_fiyati or 0)
            )
            gruplanmis_dosyalar[grup_anahtari]["urunler"].append({
                "urun_id": u.id,
                "lot": u.lot_no,
                "dukkan": u.dukkan_kodu or "gumus",
                "urun_adi": u.urun_adi,
                "musteri_adi": m_adi,
                "fiyat": satiss_fiyati,
            })
            gruplanmis_dosyalar[grup_anahtari]["toplam_tutar"] += satiss_fiyati

        on_teklif_listesi = (
            OnTeklif.query.filter_by(dukkan_kodu=dukkan)
            .order_by(OnTeklif.id.desc())
            .limit(40)
            .all()
        )
        on_teklif_urun_idler = [ot.urun_id for ot in on_teklif_listesi]
        ilgili_urunler = (
            {
                u.id: u
                for u in Urun.query.filter(Urun.id.in_(on_teklif_urun_idler)).all()
            }
            if on_teklif_urun_idler
            else {}
        )

        on_teklifler = []
        for ot in on_teklif_listesi:
            u = ilgili_urunler.get(ot.urun_id)
            if u and (u.durum == "Satıldı" or u.silindi_mi):
                continue
            on_teklifler.append({
                "id": ot.id,
                "urun_id": ot.urun_id,
                "lot": u.lot_no if u else "-",
                "urun_adi": u.urun_adi if u else "Arşivlenmiş Ürün",
                "musteri_adi": ot.musteri_adi,
                "teklif": ot.teklif,
                "zaman": getattr(ot, "zaman", ""),
            })

        muzikler = [m.url for m in Muzik.query.limit(20).all()]

        return jsonify({
            "dukkan": dukkan,
            "durum": oda["durum"]["durum"],
            "sure_bitis": oda["durum"]["sure_bitis"],
            "pey": oda["durum"]["pey"],
            "kazanan": oda["durum"]["kazanan"],
            "kazanan_id": oda["durum"].get("kazanan_id"),
            "aktif_urun": aktif_urun,
            "urunler": urunler,
            "musteriler": musteriler,
            "satilan_urunler": [u.to_dict(hafif=True) for u in satilan_urunler],
            "gruplanmis_dosyalar": list(gruplanmis_dosyalar.values()),
            "on_teklifler": on_teklifler,
            "muzik_listesi": muzikler,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e), "urunler": []}), 200
    finally:
        db.session.remove()


@app.route("/kategoriler-listesi", methods=["GET"])
def kategoriler_listesi():
    try:
        sonuclar = (
            db.session.query(Urun.kategori)
            .filter(Urun.silindi_mi.is_(False))
            .filter(Urun.kategori.isnot(None))
            .filter(Urun.kategori != "")
            .distinct()
            .all()
        )

        benzersiz = set()
        for k in sonuclar:
            ham = k[0].strip() if (k and k[0]) else ""
            if not ham:
                continue
            temiz = kategori_ad_temizle(ham)
            if temiz:
                benzersiz.add(temiz)

        kategoriler = sorted(list(benzersiz))
        if not kategoriler:
            kategoriler = [
                "Genel", "Antika", "Gümüş", "Koleksiyon", "Tablo", "Hediyelik",
            ]

        return jsonify(kategoriler)
    except Exception:
        db.session.rollback()
        return jsonify(["Genel", "Antika", "Gümüş", "Koleksiyon", "Tablo"])
    finally:
        db.session.remove()


# ==========================================
# CANLI CHAT, SESLİ BAS-KONUŞ VE AHMET USTA KÖPRÜSÜ
# ==========================================
KAYIT_PEY_MESAJI = "Teklif verebilmek için lütfen önce kayıt olun ve giriş yapın"
BAN_MESAJI = "Hesabınız yönetici tarafından askıya alınmıştır. Detaylar için mezat yönetimi ile iletişime geçin"
UYGUNSUZ_KELIMELER = (
    "amk", "aq", "orospu", "orosp", "pic", "piç", "siktir", "sikeyim", "sikerim",
    "yarrak", "yarak", "gotveren", "götveren", "pezevenk", "kahpe", "amina",
    "amına", "amcik", "amcık", "fuck", "shit", "bitch",
)
kullanici_soketleri = {}


def kullanici_banli_mi(kullanici):
    if not kullanici:
        return False
    return bool(getattr(kullanici, "is_banned", False) or kullanici.durum == "engellendi")


def pey_yetkisi(kullanici):
    if not kullanici or kullanici.silindi_mi:
        return False, KAYIT_PEY_MESAJI, {"kayit_gerekli": True}
    if kullanici_banli_mi(kullanici):
        return False, BAN_MESAJI, {"ban": True}
    if not bool(getattr(kullanici, "is_verified", False)):
        return False, KAYIT_PEY_MESAJI, {"kayit_gerekli": True, "dogrulama_gerekli": True}
    return True, "", {}


def metin_uygunsuz_mu(metin):
    ham = str(metin or "").casefold()
    sik = re.sub(r"[^a-z0-9çğıöşü]", "", ham)
    for kelime in UYGUNSUZ_KELIMELER:
        k = kelime.casefold()
        if k in ham or k in sik:
            return True
    return False


def sokete_kullanici_bagla(sid, kullanici_id):
    try:
        kid = int(kullanici_id)
    except (TypeError, ValueError):
        return
    kullanici_soketleri.setdefault(kid, set()).add(sid)


def soketten_kullanici_kopar(sid):
    for sids in kullanici_soketleri.values():
        sids.discard(sid)


def kullanici_soketlerini_kes(kullanici_id):
    for sid in list(kullanici_soketleri.get(int(kullanici_id), ())):
        try:
            socketio.server.disconnect(sid)
        except Exception:
            pass
    kullanici_soketleri.pop(int(kullanici_id), None)


def kullaniciyi_otomatik_banla(kullanici):
    if not kullanici:
        return
    kullanici.is_banned = True
    kullanici.durum = "engellendi"
    kullanici.onayli_mi = False
    db.session.commit()
    socketio.emit(
        "kullanici_engellendi",
        {"kullanici_id": kullanici.id, "ban": True, "mesaj": BAN_MESAJI},
    )
    socketio.emit(
        "yeni_elestiri",
        {
            "kaynak": "ban",
            "tarih": time.strftime("%d.%m.%Y %H:%M"),
            "musteri_adi": kullanici.ad_soyad,
            "tur": "Otomatik Engel",
            "konu": "Uygunsuz davranış",
            "mesaj": "Uygunsuz davranış sebebiyle otomatik engellendi",
            "durum": "Yeni",
        },
        room="admin_room",
    )
    kullanici_soketlerini_kes(kullanici.id)


def otp_kodu_uret():
    return f"{random.randint(0, 999999):06d}"


def otp_eposta_gonder(alici, kod):
    print(f"[TEST-OTP] {kod}")
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host or not alici:
        return
    try:
        import smtplib
        from email.message import EmailMessage
        ileti = EmailMessage()
        ileti["Subject"] = "Haliç Antik doğrulama kodu"
        ileti["From"] = os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USER", "")
        ileti["To"] = alici
        ileti.set_content(
            f"Haliç Antik Mezat doğrulama kodunuz: {kod}\n"
            "Kod 10 dakika geçerlidir."
        )
        port = int(os.environ.get("SMTP_PORT", "587") or 587)
        with smtplib.SMTP(host, port, timeout=12) as smtp:
            if os.environ.get("SMTP_TLS", "1") != "0":
                smtp.starttls()
            kullanici = os.environ.get("SMTP_USER", "")
            sifre = os.environ.get("SMTP_PASSWORD", "")
            if kullanici:
                smtp.login(kullanici, sifre)
            smtp.send_message(ileti)
    except Exception as hata:
        print(f"[OTP] E-posta gönderilemedi: {hata}")


def otp_kaydet(kullanici):
    kod = otp_kodu_uret()
    bitis = suan_utc() + timedelta(minutes=10)
    kullanici.otp_code = kod
    kullanici.dogrulama_kodu = kod
    kullanici.otp_bitis = bitis
    kullanici.is_verified = False
    return kod


@app.route("/api/chat/mesaj-gonder", methods=["POST"])
def chat_mesaj_gonder():
    veri = request.json or {}
    dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
    kullanici_id_raw = veri.get("kullanici_id")
    kullanici_id = None
    if kullanici_id_raw not in (None, "", "null"):
        try:
            kullanici_id = int(kullanici_id_raw)
        except (TypeError, ValueError):
            kullanici_id = None
    kullanici = (veri.get("kullanici") or "Ziyaretçi").strip()
    mesaj = (veri.get("mesaj") or "").strip()
    platform = veri.get("platform", "Web")

    if not mesaj:
        return jsonify({"success": False, "mesaj": "Boş mesaj gönderilemez."}), 400

    if kullanici_id:
        k_obj = db.session.get(Kullanici, kullanici_id)
        if k_obj and kullanici_banli_mi(k_obj):
            return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI}), 403
        if k_obj and metin_uygunsuz_mu(mesaj):
            kullaniciyi_otomatik_banla(k_obj)
            return jsonify({
                "success": False,
                "ban": True,
                "mesaj": "Uygunsuz davranış sebebiyle otomatik engellendi",
            }), 403
    elif metin_uygunsuz_mu(mesaj):
        return jsonify({
            "success": False,
            "mesaj": "Bu mesaj yayın kurallarına aykırı olduğu için iletilmedi.",
        }), 403

    zaman_damgasi = time.strftime("%H:%M:%S")

    # Rakam veya Türkçe yazı tutarı: "7000", "10 bin", "yedi bin", "yedibin".
    teklif_tutari = turkce_tutar_coz(mesaj)

    oda = odalar[dukkan]
    aktif_urun_id = oda.get("aktif_urun_id")

    if teklif_tutari and teklif_tutari > 0 and aktif_urun_id:
        k_obj = db.session.get(Kullanici, kullanici_id) if kullanici_id else None
        uygun, uyari, ek = pey_yetkisi(k_obj)
        if not uygun:
            return jsonify({"success": False, "mesaj": uyari, **ek})
        mevcut_fiyat = float(oda["durum"].get("pey", 0) or 0)
        urun_kontrol = db.session.get(Urun, aktif_urun_id)
        olur, uyari = _pey_kabul_mu(oda, urun_kontrol, teklif_tutari, mevcut_fiyat)
        if not olur:
            return jsonify({"success": False, "mesaj": uyari})

    yorum_verisi = {
        "platform": platform,
        "kullanici": kullanici,
        "mesaj": mesaj,
        "zaman": zaman_damgasi,
        "dukkan": dukkan,
    }
    socketio.emit("yeni_canli_yorum", yorum_verisi, room=dukkan)

    if teklif_tutari and teklif_tutari > 0 and aktif_urun_id:
        try:
            mevcut_fiyat = float(oda["durum"].get("pey", 0) or 0)

            if teklif_tutari > mevcut_fiyat:
                with oda_kilitleri[dukkan]:
                    etiket_ad = f"[{platform}] {kullanici}"
                    oda["durum"]["pey"] = teklif_tutari
                    oda["durum"]["kazanan"] = etiket_ad
                    oda["durum"]["kazanan_id"] = kullanici_id

                    urun = db.session.get(Urun, aktif_urun_id)
                    urun_adi = urun.urun_adi if urun else ""
                    if urun:
                        urun.guncel_fiyat = teklif_tutari
                        urun.kazanan_adi = etiket_ad
                        urun.kazanan_id = kullanici_id
                        db.session.add(urun)

                        yeni_teklif = Teklif(
                            dukkan_kodu=dukkan,
                            urun_id=urun.id,
                            musteri_id=kullanici_id,
                            musteri_adi=etiket_ad,
                            tutar=teklif_tutari,
                        )
                        db.session.add(yeni_teklif)
                        db.session.commit()

                    if oda.get("mevat_asama") == "karar":
                        oda["karar_pey_geldi"] = True
                    elif _gizli_esige_vardi(oda, teklif_tutari):
                        _pazarlik_erken_sat(
                            dukkan,
                            oda,
                            urun,
                            etiket_ad,
                            kullanici_id,
                            teklif_tutari,
                            kullanici,
                        )
                        return jsonify({"success": True, "tip": "pey"})

                    guncelleme = {
                        "dukkan": dukkan,
                        "urun_id": aktif_urun_id,
                        "pey": teklif_tutari,
                        "kazanan": etiket_ad,
                        "kazanan_id": kullanici_id,
                    }
                    socketio.emit("pey_guncellendi", guncelleme, room=dukkan)
                    if (
                        oda.get("mevat_asama") == "karar"
                        and oda["sayac_aktif"]
                        and oda["sayac_kalan"] <= 10
                    ):
                        oda["sayac_kalan"] = 15
                        oda["durum"]["sure_bitis"] = time.time() + 15
                        socketio.emit(
                            "sayac_uzatildi",
                            {
                                "dukkan": dukkan,
                                "kalan": 15,
                                "mesaj": "Son saniye teklifi nedeniyle süre 15 sn uzatıldı!",
                            },
                            room=dukkan,
                        )
                        socketio.emit(
                            "sayac_guncelle",
                            {"dukkan": dukkan, "kalan": 15, "sayac_gorunsun": True, "asama": "karar"},
                            room=dukkan,
                        )

                anons_acilis = _acilis_tabani(oda, urun)
                anons_hemen = float(getattr(urun, "hemen_al_fiyati", 0) or 0) if urun is not None else 0
                if not ahmet_sicak_soguk_seslendir(dukkan, oda, kullanici, teklif_tutari):
                    ahmet_pey_reaksiyonunu_seslendir(
                        dukkan,
                        kullanici,
                        teklif_tutari,
                        urun_adi,
                        anons_acilis,
                        anons_hemen,
                        getattr(urun, "tanitim_yazisi", "") if urun is not None else "",
                    )
                return jsonify({"success": True, "tip": "pey"})
        except Exception as e:
            db.session.rollback()
            print(f"Pey işleme hatası: {e}")

    ahmet_usta_yoruma_cevap_ver(dukkan, kullanici, mesaj)
    return jsonify({"success": True, "tip": "mesaj"})


@app.route("/api/izleyici/bas-konus-ses", methods=["POST"])
def izleyici_bas_konus_ses():
    kullanici_id = request.form.get("kullanici_id")
    dukkan = dukkan_dogrula(request.form.get("dukkan", "gumus"))

    kullanici = db.session.get(Kullanici, int(kullanici_id)) if kullanici_id else None

    if not kullanici or kullanici.silindi_mi:
        return jsonify({"success": False, "mesaj": "Kullanıcı kaydı bulunamadı."}), 403

    if kullanici_banli_mi(kullanici):
        return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI}), 403

    ses_dosyasi = request.files.get("ses")
    if not ses_dosyasi:
        return jsonify({"success": False, "mesaj": "Ses verisi alınamadı."}), 400

    try:
        url, dosya_adi = kaydet_guvenli_dosya(ses_dosyasi)
        tam_yol = os.path.join(app.config["UPLOAD_FOLDER"], dosya_adi)

        def _bota_izleyici_ses_ilet():
            try:
                if os.path.exists(tam_yol):
                    with open(tam_yol, "rb") as f:
                        requests.post(
                            f"{BOT_API_URL}/izleyici_ses_isle",
                            files={"ses": f},
                            data={"dukkan": dukkan, "kullanici": kullanici.ad_soyad},
                            timeout=8.0,
                        )
            except Exception as ex:
                print(f"⚠️ İzleyici Ses İletim Hatası: {ex}")

        socketio.start_background_task(_bota_izleyici_ses_ilet)
        return jsonify({"success": True, "mesaj": "Sesiniz iletildi."})
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/api/admin/bas-konus-ses", methods=["POST"])
@admin_required
def admin_bas_konus_ses():
    ses_dosyasi = request.files.get("ses")
    dukkan = dukkan_dogrula(request.form.get("dukkan", "gumus"))

    if not ses_dosyasi:
        return jsonify({"success": False, "mesaj": "Ses verisi alınamadı."}), 400

    try:
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        url, dosya_adi = kaydet_guvenli_dosya(ses_dosyasi)
        tam_yol = os.path.join(app.config["UPLOAD_FOLDER"], dosya_adi)

        def _bota_ses_ilet():
            try:
                if os.path.exists(tam_yol):
                    with open(tam_yol, "rb") as f:
                        requests.post(
                            f"{BOT_API_URL}/admin_ses_isle",
                            files={"ses": f},
                            data={"dukkan": dukkan},
                            timeout=8.0,
                        )
            except Exception as ex:
                print(f"⚠️ Ses İletim Hatası: {ex}")

        socketio.start_background_task(_bota_ses_ilet)
        return jsonify({"success": True, "mesaj": "Ses Ahmet Usta'ya iletildi."})
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/api/bot/ahmet-usta-yanit", methods=["POST"])
def bot_ahmet_usta_yanit():
    veri = request.json or {}
    dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
    yanit = veri.get("yanit", "")

    if yanit:
        socketio.emit(
            "ahmet_usta_kayan_yazi",
            {"dukkan": dukkan, "metin": yanit, "zaman": time.strftime("%H:%M")},
            room=dukkan,
        )
        socketio.emit(
            "yeni_canli_yorum",
            {
                "platform": "Ahmet Usta AI",
                "kullanici": "[Ahmet Usta]",
                "mesaj": yanit,
                "zaman": time.strftime("%H:%M:%S"),
                "dukkan": dukkan,
            },
            room=dukkan,
        )
        ahmet_usta_anons_yap(dukkan, yanit)

    return jsonify({"success": True})


# ==========================================
# YASAL ÜRÜN ALIM & KONSİNYE SÖZLEŞMESİ ROTALARI
# ==========================================
@app.route("/admin/sozlesme-ekle", methods=["POST"])
@admin_required
def admin_sozlesme_ekle():
    try:
        veri = request.json or {}
        teslim_eden = (veri.get("ad_soyad") or "").strip()
        tc = (veri.get("tc") or "").strip()
        tel = (veri.get("tel") or "").strip()
        urun_ad = (veri.get("urun_adi") or "").strip()

        if not teslim_eden or not tc or not urun_ad:
            return jsonify({
                "success": False,
                "mesaj": "Teslim eden adı, TC ve Ürün adı zorunludur!",
            }), 400

        yeni_no = f"HLCS-{int(time.time())}"
        yeni_sozlesme = UrunAlimSozlesmesi(
            sozlesme_no=yeni_no,
            teslim_eden_ad=teslim_eden,
            teslim_eden_tc=tc,
            teslim_eden_tel=tel,
            teslim_eden_iban=veri.get("iban", ""),
            teslim_eden_adres=veri.get("adres", ""),
            urun_adi=urun_ad,
            kategori=veri.get("kategori", "Antika"),
            ekspertiz_notu=veri.get("ekspertiz_notu", ""),
            tahmini_deger=float(veri.get("tahmini_deger") or 0.0),
            komisyon_orani=float(veri.get("komisyon") or 15.0),
            yasal_taahhut=True,
        )
        db.session.add(yeni_sozlesme)
        db.session.commit()
        return jsonify({
            "success": True,
            "mesaj": "Sözleşme başarıyla kaydedildi.",
            "sozlesme_no": yeni_no,
            "id": yeni_sozlesme.id,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/admin/sozlesmeler-listele", methods=["GET"])
@admin_required
def admin_sozlesmeler_listele():
    try:
        liste = (
            UrunAlimSozlesmesi.query.order_by(UrunAlimSozlesmesi.id.desc())
            .limit(100)
            .all()
        )
        return jsonify({
            "success": True,
            "sozlesmeler": [
                {
                    "id": s.id,
                    "no": s.sozlesme_no,
                    "ad": s.teslim_eden_ad,
                    "tc": s.teslim_eden_tc,
                    "tel": s.teslim_eden_tel,
                    "urun": s.urun_adi,
                    "deger": s.tahmini_deger,
                    "komisyon": s.komisyon_orani,
                    "tarih": (
                        s.olusturma_tarihi.strftime("%d.%m.%Y %H:%M")
                        if s.olusturma_tarihi
                        else "-"
                    ),
                }
                for s in liste
            ],
        })
    except Exception as e:
        return jsonify({"success": False, "sozlesmeler": [], "hata": str(e)})


@app.route("/admin/sozlesme-detay/<int:sozlesme_id>", methods=["GET"])
@admin_required
def admin_sozlesme_detay(sozlesme_id):
    s = db.session.get(UrunAlimSozlesmesi, sozlesme_id)
    if not s:
        return jsonify({"success": False, "mesaj": "Belge bulunamadı."}), 404
    return jsonify({
        "success": True,
        "sozlesme": {
            "id": s.id,
            "no": s.sozlesme_no,
            "ad": s.teslim_eden_ad,
            "tc": s.teslim_eden_tc,
            "tel": s.teslim_eden_tel,
            "iban": s.teslim_eden_iban,
            "adres": s.teslim_eden_adres,
            "urun": s.urun_adi,
            "kategori": s.kategori,
            "ekspertiz_notu": s.ekspertiz_notu,
            "deger": s.tahmini_deger,
            "komisyon": s.komisyon_orani,
            "tarih": (
                s.olusturma_tarihi.strftime("%d.%m.%Y")
                if s.olusturma_tarihi
                else "-"
            ),
        },
    })


# ==========================================
# SEPET, SİPARİŞ VE ÖDEME ROTALARI
# ==========================================
@app.route("/sepete-ekle", methods=["POST"])
def sepete_ekle():
    veri = request.json or {}
    musteri_id = veri.get("musteri_id")
    urun_id = veri.get("urun_id")

    if not musteri_id or not urun_id:
        return jsonify({
            "success": False, "mesaj": "Müşteri ve ürün bilgisi zorunludur.",
        })

    kullanici = db.session.get(Kullanici, int(musteri_id))
    urun = db.session.get(Urun, int(urun_id))

    if not kullanici or not urun:
        return jsonify({
            "success": False, "mesaj": "Kullanıcı veya ürün bulunamadı.",
        })

    var_mi = SepetItem.query.filter_by(
        musteri_id=kullanici.id, urun_id=urun.id
    ).first()
    if var_mi:
        return jsonify({
            "success": False, "mesaj": "Bu ürün zaten sepetinizde ekli.",
        })

    yeni_item = SepetItem(musteri_id=kullanici.id, urun_id=urun.id)
    db.session.add(yeni_item)
    db.session.commit()
    return jsonify({"success": True, "mesaj": "Ürün sepetinize eklendi."})


@app.route("/sepetten-cikar", methods=["POST"])
def sepetten_cikar():
    veri = request.json or {}
    musteri_id = veri.get("musteri_id")
    urun_id = veri.get("urun_id")
    sepet_id = veri.get("sepet_id")

    item = None
    if sepet_id:
        item = db.session.get(SepetItem, int(sepet_id))
    elif musteri_id and urun_id:
        item = SepetItem.query.filter_by(
            musteri_id=int(musteri_id), urun_id=int(urun_id)
        ).first()

    if item:
        db.session.delete(item)
        db.session.commit()
        return jsonify({"success": True, "mesaj": "Ürün sepetten çıkarıldı."})

    return jsonify({"success": False, "mesaj": "Ürün sepetinizde bulunamadı."})


@app.route("/sepet-getir/<int:musteri_id>", methods=["GET"])
def sepet_getir(musteri_id):
    kullanici = db.session.get(Kullanici, musteri_id) if musteri_id > 0 else None
    url_ad = request.args.get("ad", "").strip()

    if not kullanici and url_ad:
        saf_ad = saf_isim_temizle(url_ad)
        kullanici = (
            Kullanici.query.filter(
                Kullanici.silindi_mi.is_(False), Kullanici.ad_soyad.ilike(saf_ad)
            ).first()
        )

    items = []
    if kullanici:
        items = SepetItem.query.filter_by(musteri_id=kullanici.id).all()

    ekli_urun_idler = set()
    sepet_listesi = []
    ara_toplam = 0.0

    for it in items:
        u = db.session.get(Urun, it.urun_id)
        if u and not u.silindi_mi:
            ekli_urun_idler.add(u.id)
            fiyat = float(
                u.guncel_fiyat
                if (u.guncel_fiyat and u.guncel_fiyat > 0)
                else (u.acilis_fiyati or 0)
            )
            ara_toplam += fiyat
            sepet_listesi.append({
                "sepet_id": it.id,
                "dukkan": u.dukkan_kodu,
                "urun_id": u.id,
                "lot": u.lot_no,
                "ad": u.urun_adi,
                "fiyat": fiyat,
                "gorsel": (
                    u.fotograflar[0]
                    if (u.fotograflar and len(u.fotograflar) > 0)
                    else (u.video or "")
                ),
            })

    if kullanici:
        kazanilan_satilanlar = Urun.query.filter(
            or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
            Urun.silindi_mi.is_(False),
            Urun.kazanan_id == kullanici.id,
        ).all()

        for u in kazanilan_satilanlar:
            if u.id not in ekli_urun_idler:
                fiyat = float(
                    u.guncel_fiyat
                    if (u.guncel_fiyat and u.guncel_fiyat > 0)
                    else (u.acilis_fiyati or 0)
                )
                ara_toplam += fiyat
                sepet_listesi.append({
                    "sepet_id": 0,
                    "dukkan": u.dukkan_kodu,
                    "urun_id": u.id,
                    "lot": u.lot_no,
                    "ad": u.urun_adi,
                    "fiyat": fiyat,
                    "gorsel": (
                        u.fotograflar[0]
                        if (u.fotograflar and len(u.fotograflar) > 0)
                        else (u.video or "")
                    ),
                })

    kdv_tutari = round(ara_toplam * 0.20, 2)
    kargo_ucreti = 150.0 if ara_toplam > 0 else 0.0
    genel_toplam = round(ara_toplam + kdv_tutari + kargo_ucreti, 2)

    return jsonify({
        "success": True,
        "urunler": sepet_listesi,
        "ara_toplam": ara_toplam,
        "kdv_tutari": kdv_tutari,
        "kargo_ucreti": kargo_ucreti,
        "genel_toplam": genel_toplam,
    })


@app.route("/odeme-baslat", methods=["POST"])
def odeme_baslat():
    veri = request.json or {}
    musteri_id = veri.get("musteri_id")
    ad = (veri.get("ad") or "").strip()
    tel = (veri.get("tel") or "").strip()
    adres = (veri.get("adres") or "").strip()

    if not musteri_id and (not ad or not tel):
        return jsonify({
            "success": False,
            "mesaj": "Ödemeyi tamamlamak için lütfen ad ve telefon bilgilerinizi giriniz.",
        })

    kullanici = None
    if musteri_id:
        kullanici = db.session.get(Kullanici, int(musteri_id))
    elif tel:
        kullanici = Kullanici.query.filter_by(telefon=tel).first()

    if not kullanici and ad and tel:
        kullanici = Kullanici(
            ad_soyad=ad,
            telefon=tel,
            adres=adres,
            durum="onayli",
            onayli_mi=True,
            cihaz_kodu=get_device_fingerprint(),
        )
        db.session.add(kullanici)
        db.session.commit()

    return jsonify({
        "success": True,
        "musteri_id": kullanici.id if kullanici else None,
        "mesaj": "Ödeme ekranına yönlendiriliyorsunuz (3D Secure).",
        "yonlendir": "/pos",
    })


# ==========================================
# ÇOKLU ODA PEY VERME VE HEMEN AL
# ==========================================
@app.route("/pey-ver", methods=["POST"])
def pey_ver():
    veri = request.json or {}
    client_ip = get_client_ip()

    dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
    oda = odalar[dukkan]

    musteri_id = veri.get("musteri_id")
    musteri_adi = (veri.get("musteri_adi") or veri.get("isim") or "").strip()
    telefon = (veri.get("telefon") or "").strip()

    kullanici = None
    if musteri_id:
        kullanici = db.session.get(Kullanici, int(musteri_id))
    elif telefon:
        kullanici = Kullanici.query.filter_by(telefon=telefon).first()
    elif musteri_adi:
        saf_istek = saf_isim_temizle(musteri_adi)
        kullanici = (
            Kullanici.query.filter(
                Kullanici.silindi_mi.is_(False), Kullanici.ad_soyad.ilike(saf_istek)
            ).first()
        )

    if not kullanici or kullanici.silindi_mi:
        return jsonify({
            "success": False,
            "kayit_gerekli": True,
            "mesaj": KAYIT_PEY_MESAJI,
        })

    if kullanici_banli_mi(kullanici):
        return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI}), 403

    if not bool(getattr(kullanici, "is_verified", False)):
        return jsonify({
            "success": False,
            "kayit_gerekli": True,
            "dogrulama_gerekli": True,
            "mesaj": KAYIT_PEY_MESAJI,
        })

    etiket_ad = f"[Web] {kullanici.ad_soyad}"
    raw_uid = veri.get("urun_id") or oda["aktif_urun_id"]
    try:
        urun_id = int(raw_uid) if raw_uid else oda["aktif_urun_id"]
    except Exception:
        urun_id = oda["aktif_urun_id"]

    if not urun_id:
        return jsonify({
            "success": False, "mesaj": "Bu dükkanda şu an aktif bir mezat yok!",
        })

    with oda_kilitleri[dukkan]:
        try:
            sorgu = Urun.query
            if not IS_SQLITE:
                sorgu = sorgu.with_for_update()
            urun = sorgu.filter_by(id=urun_id).first()

            if (
                not urun
                or urun.silindi_mi
                or urun.durum == "Satıldı"
                or oda["durum"].get("durum") == "Satıldı"
            ):
                return jsonify({
                    "success": False,
                    "mesaj": "⚠️ Bu ürün satılmıştır veya aktif mezatta değildir!",
                })

            islem = veri.get("islem", "pey")

            if islem == "hemen_al":
                statik_hemen_al = float(urun.hemen_al_fiyati or 0.0)
                mevcut_pey = float(
                    oda["durum"].get("pey", 0.0)
                    or urun.guncel_fiyat
                    or urun.acilis_fiyati
                    or 0.0
                )

                if statik_hemen_al > 0 and mevcut_pey >= statik_hemen_al:
                    artis = max(100.0, round(mevcut_pey * 0.10))
                    hemen_al_fiyat = round(mevcut_pey + artis)
                elif statik_hemen_al > 0:
                    hemen_al_fiyat = statik_hemen_al
                else:
                    artis = max(100.0, round(mevcut_pey * 0.10))
                    hemen_al_fiyat = round(mevcut_pey + artis)

                oda["sayac_aktif"] = False
                _gizli_pazarligi_kapat(oda)
                urun.durum = "Satıldı"
                urun.guncel_fiyat = hemen_al_fiyat
                urun.kazanan_adi = etiket_ad
                urun.kazanan_id = kullanici.id

                oda["durum"]["durum"] = "Satıldı"
                oda["durum"]["sure_bitis"] = 0
                oda["durum"]["kazanan"] = etiket_ad
                oda["durum"]["kazanan_id"] = kullanici.id
                oda["durum"]["pey"] = hemen_al_fiyat
                biten_id = oda["aktif_urun_id"]
                oda["aktif_urun_id"] = None

                yeni_teklif = Teklif(
                    dukkan_kodu=dukkan,
                    urun_id=urun.id,
                    musteri_id=kullanici.id,
                    musteri_adi=etiket_ad,
                    tutar=hemen_al_fiyat,
                )
                db.session.add(yeni_teklif)
                db.session.add(urun)
                OnTeklif.query.filter_by(urun_id=urun.id).delete()
                db.session.commit()

                urunu_kazanana_sepete_ekle(urun.id, etiket_ad, kullanici.id)

                socketio.emit(
                    "pey_guncellendi",
                    {
                        "dukkan": dukkan,
                        "urun_id": urun.id,
                        "pey": hemen_al_fiyat,
                        "kazanan": etiket_ad,
                        "kazanan_id": kullanici.id,
                    },
                    room=dukkan,
                )
                socketio.emit(
                    "sayac_bitti",
                    {
                        "dukkan": dukkan,
                        "urun_id": biten_id,
                        "satildi_mi": True,
                        "kazanan": etiket_ad,
                        "kazanan_id": kullanici.id,
                        "fiyat": hemen_al_fiyat,
                    },
                    room=dukkan,
                )
                bota_haber_ver(
                    "satis_bitti",
                    {
                        "dukkan": dukkan,
                        "kazanan": etiket_ad,
                        "fiyat": hemen_al_fiyat,
                    },
                )
                _sahneyi_bosa_al(dukkan, "satildi")
                ahmet_usta_anons_yap(
                    dukkan,
                    ahmet_satis_cumlesi(urun.lot_no, urun.urun_adi, kullanici.ad_soyad),
                    tur="satis",
                )
                bota_haber_ver("ahmet_sifirla", {"dukkan": dukkan})

                return jsonify({
                    "success": True,
                    "guncel_fiyat": hemen_al_fiyat,
                    "kazanan": etiket_ad,
                })

            if islem == "pey":
                mevcut_fiyat = float(
                    oda["durum"]["pey"]
                    if (oda["durum"].get("pey") and float(oda["durum"]["pey"]) > 0)
                    else (urun.guncel_fiyat or urun.acilis_fiyati or 0.0)
                )

                miktar = float(veri.get("miktar", 0))

                if miktar <= 0:
                    if _serbest_pey_modu(oda):
                        taban = _acilis_tabani(oda, urun)
                        miktar = round(max(mevcut_fiyat, taban) + 1)
                    else:
                        yuzde_on = round(mevcut_fiyat * 0.10)
                        min_artis = kademeli_min_artis(mevcut_fiyat, dukkan)
                        artis_tutari = max(min_artis, float(yuzde_on))
                        miktar = round(mevcut_fiyat + artis_tutari)

                olur, uyari = _pey_kabul_mu(oda, urun, miktar, mevcut_fiyat)
                if not olur:
                    if not _serbest_pey_modu(oda):
                        ahmet_usta_anons_yap(dukkan, uyari, tur="anons", kesinti=True)
                    return jsonify({"success": False, "mesaj": uyari})

                onceki_kazanan_id = oda.get("son_teklif_veren_id")
                if onceki_kazanan_id and onceki_kazanan_id != kullanici.id:
                    socketio.emit(
                        "pey_gecildi_uyarisi",
                        {
                            "urun_id": urun.id,
                            "yeni_fiyat": miktar,
                            "yeni_kisi": etiket_ad,
                            "mesaj": f"Peyiniz geçildi! Güncel teklif: {int(miktar)} TL",
                        },
                    )

                oda["son_teklif_veren_id"] = kullanici.id
                oda["durum"]["pey"] = miktar
                oda["durum"]["kazanan"] = etiket_ad
                oda["durum"]["kazanan_id"] = kullanici.id
                urun.guncel_fiyat = miktar
                urun.kazanan_adi = etiket_ad
                urun.kazanan_id = kullanici.id

                yeni_teklif = Teklif(
                    dukkan_kodu=dukkan,
                    urun_id=urun.id,
                    musteri_id=kullanici.id,
                    musteri_adi=etiket_ad,
                    tutar=miktar,
                )
                db.session.add(yeni_teklif)
                db.session.add(urun)
                db.session.commit()

                if oda.get("mevat_asama") == "karar":
                    oda["karar_pey_geldi"] = True
                elif _gizli_esige_vardi(oda, miktar):
                    _pazarlik_erken_sat(
                        dukkan,
                        oda,
                        urun,
                        etiket_ad,
                        kullanici.id,
                        miktar,
                        kullanici.ad_soyad,
                    )
                    return jsonify({
                        "success": True,
                        "guncel_fiyat": miktar,
                        "kazanan": etiket_ad,
                    })

                bota_haber_ver(
                    "yeni_pey_geldi",
                    {"dukkan": dukkan, "kisi": etiket_ad, "fiyat": miktar},
                )
                platform = (veri.get("platform") or "Web").strip()
                if platform not in ("Web", "TikTok", "Instagram"):
                    platform = "Web"
                if not ahmet_sicak_soguk_seslendir(dukkan, oda, kullanici.ad_soyad, miktar):
                    ahmet_pey_reaksiyonunu_seslendir(
                        dukkan,
                        kullanici.ad_soyad,
                        miktar,
                        urun.urun_adi,
                        _acilis_tabani(oda, urun),
                        float(urun.hemen_al_fiyati or 0),
                        urun.tanitim_yazisi or "",
                    )

                if (
                    oda["sayac_aktif"]
                    and not oda.get("gizli_pazarlik_aktif")
                    and oda["sayac_kalan"] <= 10
                ):
                    oda["sayac_kalan"] = 15
                    oda["durum"]["sure_bitis"] = time.time() + 15
                    socketio.emit(
                        "sayac_uzatildi",
                        {
                            "dukkan": dukkan,
                            "kalan": 15,
                            "mesaj": "Son saniye teklifi nedeniyle süre 15 sn uzatıldı!",
                        },
                        room=dukkan,
                    )
                    socketio.emit(
                        "sayac_guncelle",
                        {"dukkan": dukkan, "kalan": 15, "sayac_gorunsun": True, "asama": "karar"},
                        room=dukkan,
                    )

                platform = (veri.get("platform") or "Web").strip()
                if platform not in ("Web", "TikTok", "Instagram"):
                    platform = "Web"
                socketio.emit(
                    "pey_guncellendi",
                    {
                        "dukkan": dukkan,
                        "urun_id": urun.id,
                        "pey": miktar,
                        "kazanan": etiket_ad,
                        "kazanan_id": kullanici.id,
                        "platform": platform,
                        "ad": kullanici.ad_soyad,
                    },
                    room=dukkan,
                )
                return jsonify({
                    "success": True,
                    "guncel_fiyat": miktar,
                    "kazanan": etiket_ad,
                })
        except Exception as e:
            db.session.rollback()
            return jsonify({
                "success": False, "mesaj": f"Veritabanı hatası: {str(e)}",
            }), 500
        finally:
            db.session.remove()


# ==========================================
# ÜRÜN TAKİP VE ABONELİK ROTALARI
# ==========================================
@app.route("/urun-takip-et", methods=["POST"])
def urun_takip_et():
    veri = request.json or {}
    musteri_id = veri.get("musteri_id")
    urun_id = veri.get("urun_id")

    if not musteri_id or not urun_id:
        return jsonify({
            "success": False, "mesaj": "Müşteri ve ürün bilgisi zorunludur.",
        })

    var_mi = UrunTakip.query.filter_by(
        musteri_id=int(musteri_id), urun_id=int(urun_id)
    ).first()
    if var_mi:
        return jsonify({
            "success": True, "mesaj": "Bu ürün zaten takip listenizde.",
        })

    yeni_takip = UrunTakip(musteri_id=int(musteri_id), urun_id=int(urun_id))
    db.session.add(yeni_takip)
    db.session.commit()
    return jsonify({
        "success": True,
        "mesaj": "⭐ Ürün takibe alındı. Sahneye çıktığında anlık bildirim alacaksınız.",
    })


# ==========================================
# ÖN TEKLİF VE KAYIT ROTALARI
# ==========================================
@app.route("/kayit-ol", methods=["POST"])
def kayit_ol():
    veri = request.json or {}
    ad = (veri.get("ad") or "").strip()
    tel = (veri.get("tel") or "").strip()
    mail = (veri.get("mail") or veri.get("email") or "").strip()
    adres = (veri.get("adres") or "").strip()
    sozlesme = bool(veri.get("sozlesme", True))

    if not ad:
        return jsonify({"success": False, "mesaj": "Ad Soyad alanı zorunludur."})
    if not tel:
        return jsonify({
            "success": False, "mesaj": "Telefon numarası zorunludur.",
        })

    client_ip = get_client_ip()
    fingerprint = get_device_fingerprint()

    kullanici = Kullanici.query.filter_by(telefon=tel).first()
    if kullanici and kullanici_banli_mi(kullanici):
        return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI})

    if kullanici:
        kullanici.ad_soyad = ad
        kullanici.email = mail or None
        kullanici.adres = adres or None
        kullanici.sozlesme_onay = sozlesme
        kullanici.cihaz_kodu = fingerprint
        kullanici.ip_adresi = client_ip
        kullanici.silindi_mi = False
        kullanici.silinme_tarihi = None
    else:
        kullanici = Kullanici(
            ad_soyad=ad,
            telefon=tel,
            email=mail or None,
            adres=adres or None,
            sozlesme_onay=sozlesme,
            cihaz_kodu=fingerprint,
            ip_adresi=client_ip,
        )
        db.session.add(kullanici)

    kullanici.onayli_mi = True
    kullanici.durum = "onayli"
    kullanici.is_verified = True
    kullanici.is_banned = False
    kullanici.otp_code = None
    kullanici.dogrulama_kodu = None
    kullanici.otp_bitis = None

    try:
        db.session.commit()
    except Exception as hata:
        db.session.rollback()
        print(f"Kayıt hatası: {hata}")
        return jsonify({"success": False, "mesaj": "Kayıt kaydedilemedi. Bilgileri kontrol edip yeniden deneyin."})

    return jsonify({
        "success": True,
        "musteri_id": kullanici.id,
        "durum": kullanici.durum,
        "onayli_mi": True,
        "is_verified": True,
        "dogrulama_gerekli": False,
        "mesaj": "Kayıt tamamlandı. Mezata hoş geldiniz.",
    })


@app.route("/profil-guncelle", methods=["POST"])
def profil_guncelle():
    veri = request.json or {}
    ad = (veri.get("ad") or "").strip()
    tel = (veri.get("tel") or "").strip()
    mail = (veri.get("mail") or veri.get("email") or "").strip()
    adres = (veri.get("adres") or "").strip()
    if not ad or not tel:
        return jsonify({"success": False, "mesaj": "Ad ve telefon zorunludur."})
    try:
        kullanici = db.session.get(Kullanici, int(veri.get("musteri_id") or 0))
    except (TypeError, ValueError):
        kullanici = None
    if not kullanici or kullanici.silindi_mi:
        return jsonify({"success": False, "mesaj": "Profil bulunamadı."})
    if kullanici_banli_mi(kullanici):
        return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI})
    kullanici.ad_soyad = ad
    kullanici.telefon = tel
    kullanici.email = mail or None
    kullanici.adres = adres or None
    try:
        db.session.commit()
    except Exception as hata:
        db.session.rollback()
        print(f"Profil güncelleme hatası: {hata}")
        return jsonify({"success": False, "mesaj": "Profil kaydedilemedi."})
    return jsonify({"success": True, "mesaj": "Profil başarıyla güncellendi."})


@app.route("/profil-sil", methods=["POST"])
def profil_sil():
    veri = request.json or {}
    try:
        kullanici = db.session.get(Kullanici, int(veri.get("musteri_id") or 0))
    except (TypeError, ValueError):
        kullanici = None
    if kullanici and not kullanici.silindi_mi:
        kullanici.silindi_mi = True
        kullanici.silinme_tarihi = suan_utc()
        kullanici.cihaz_kodu = None
        try:
            db.session.commit()
        except Exception as hata:
            db.session.rollback()
            print(f"Profil silme hatası: {hata}")
            return jsonify({"success": False, "mesaj": "Hesap sıfırlanamadı."})
    return jsonify({"success": True, "mesaj": "Hesap sıfırlandı."})


@app.route("/dogrulama-durum", methods=["GET"])
def dogrulama_durum():
    try:
        kullanici = db.session.get(Kullanici, int(request.args.get("id") or 0))
    except Exception:
        kullanici = None
    if not kullanici or kullanici.silindi_mi:
        return jsonify({"success": False, "is_verified": False})
    return jsonify({
        "success": True,
        "is_verified": bool(kullanici.is_verified),
        "onayli_mi": bool(kullanici.onayli_mi),
        "is_banned": kullanici_banli_mi(kullanici),
        "mesaj": BAN_MESAJI if kullanici_banli_mi(kullanici) else "",
    })


@app.route("/dogrulama-tamamla", methods=["POST"])
def dogrulama_tamamla():
    veri = request.json or {}
    kod = "".join(ch for ch in str(veri.get("kod") or "") if ch.isdigit())
    try:
        kullanici = db.session.get(Kullanici, int(veri.get("musteri_id") or 0))
    except Exception:
        kullanici = None
    if not kullanici or kullanici.silindi_mi:
        return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})
    if kullanici_banli_mi(kullanici):
        return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI})
    bitis = getattr(kullanici, "otp_bitis", None)
    if bitis is not None:
        if bitis.tzinfo is None:
            bitis = bitis.replace(tzinfo=timezone.utc)
        if bitis < suan_utc():
            return jsonify({"success": False, "mesaj": "Doğrulama kodunun süresi doldu. Yeniden kayıt olun."})
    beklenen = (getattr(kullanici, "otp_code", None) or kullanici.dogrulama_kodu or "")
    if not beklenen or kod != beklenen:
        return jsonify({"success": False, "mesaj": "Doğrulama kodu hatalı."})
    kullanici.is_verified = True
    kullanici.otp_code = None
    kullanici.dogrulama_kodu = None
    kullanici.otp_bitis = None
    db.session.commit()
    return jsonify({
        "success": True,
        "is_verified": True,
        "mesaj": "Telefon ve e-posta doğrulamanız tamamlandı.",
    })


@app.route("/on-teklif-ver", methods=["POST"])
def on_teklif_ver():
    veri = request.json or {}
    raw_uid = veri.get("urun_id")
    musteri_id = veri.get("musteri_id")
    musteri_adi = (veri.get("musteri_adi") or "").strip()
    teklif_tutari = float(veri.get("teklif", 0))

    try:
        urun_id = int(raw_uid)
    except Exception:
        return jsonify({"success": False, "mesaj": "Geçersiz ürün ID!"})

    kullanici = None
    if musteri_id:
        kullanici = db.session.get(Kullanici, int(musteri_id))
        if kullanici:
            musteri_adi = kullanici.ad_soyad
    elif musteri_adi:
        saf_istek = saf_isim_temizle(musteri_adi)
        kullanici = (
            Kullanici.query.filter(
                Kullanici.silindi_mi.is_(False), Kullanici.ad_soyad.ilike(saf_istek)
            ).first()
        )

    if not kullanici or not kullanici.email or not kullanici.telefon:
        return jsonify({
            "success": False,
            "kayit_gerekli": True,
            "mesaj": "⚠️ Ön teklif verebilmek için Ad, Telefon ve E-posta ile kayıt olmanız zorunludur!",
        })
    uygun, uyari, ek = pey_yetkisi(kullanici)
    if not uygun:
        return jsonify({"success": False, "mesaj": uyari, **ek})

    urun = db.session.get(Urun, urun_id)
    if not urun or urun.silindi_mi or urun.durum == "Satıldı":
        return jsonify({"success": False, "mesaj": "⚠️ Bu ürün teklife kapalıdır!"})

    acilis = float(urun.acilis_fiyati or 0)
    if teklif_tutari < acilis:
        return jsonify({
            "success": False,
            "mesaj": f"Ön teklif açılış fiyatından ({acilis} TL) düşük olamaz!",
        })

    en_yuksek = (
        OnTeklif.query.filter_by(urun_id=urun_id)
        .order_by(OnTeklif.teklif.desc())
        .first()
    )
    if en_yuksek and teklif_tutari <= en_yuksek.teklif:
        return jsonify({
            "success": False,
            "mesaj": f"Teklifiniz mevcut en yüksek ön tekliften ({en_yuksek.teklif} TL) büyük olmalıdır!",
        })

    yeni_ot = OnTeklif(
        dukkan_kodu=urun.dukkan_kodu,
        urun_id=urun_id,
        musteri_id=kullanici.id,
        musteri_adi=musteri_adi,
        teklif=teklif_tutari,
    )
    db.session.add(yeni_ot)
    urun.guncel_fiyat = teklif_tutari
    db.session.add(urun)
    db.session.commit()

    return jsonify({"success": True, "mesaj": "✅ Ön teklifiniz başarıyla kaydedildi."})


# ==========================================
# ÖN TEKLİF SİLME VE DÜZENLEME ROTALARI
# ==========================================
@app.route("/on-teklif-sil", methods=["POST"])
@admin_required
def on_teklif_sil():
    veri = request.json or {}
    ot_id = veri.get("id")

    if not ot_id:
        return jsonify({"success": False, "mesaj": "Ön teklif ID belirtilmedi."}), 400

    try:
        on_teklif = db.session.get(OnTeklif, int(ot_id))
        if on_teklif:
            urun_id = on_teklif.urun_id
            db.session.delete(on_teklif)
            db.session.commit()

            kalan_en_yuksek = (
                OnTeklif.query.filter_by(urun_id=urun_id)
                .order_by(OnTeklif.teklif.desc())
                .first()
            )
            urun = db.session.get(Urun, urun_id)
            if urun:
                urun.guncel_fiyat = (
                    float(kalan_en_yuksek.teklif)
                    if kalan_en_yuksek
                    else float(urun.acilis_fiyati or 0.0)
                )
                db.session.commit()

            return jsonify({"success": True, "mesaj": "Ön teklif başarıyla silindi."})

        return jsonify({"success": False, "mesaj": "Ön teklif bulunamadı."}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/on-teklif-guncelle", methods=["POST"])
@admin_required
def on_teklif_guncelle():
    veri = request.json or {}
    ot_id = veri.get("id")
    musteri = (veri.get("musteri_adi") or "").strip()
    teklif_val = veri.get("teklif")

    if not ot_id or not musteri or teklif_val is None:
        return jsonify({"success": False, "mesaj": "Tüm alanları doldurunuz."}), 400

    try:
        on_teklif = db.session.get(OnTeklif, int(ot_id))
        if on_teklif:
            on_teklif.musteri_adi = musteri
            on_teklif.teklif = float(teklif_val)
            db.session.commit()

            urun = db.session.get(Urun, on_teklif.urun_id)
            if urun:
                en_yuksek = (
                    OnTeklif.query.filter_by(urun_id=urun.id)
                    .order_by(OnTeklif.teklif.desc())
                    .first()
                )
                if en_yuksek:
                    urun.guncel_fiyat = float(en_yuksek.teklif)
                    db.session.commit()

            return jsonify({"success": True, "mesaj": "Ön teklif güncellendi."})

        return jsonify({"success": False, "mesaj": "Ön teklif bulunamadı."}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)}), 500


# ==========================================
# YÖNETİCİ GİRİŞİ, 2FA VE MERKEZİ PANEL
# ==========================================
LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Haliç Antik - Yönetici Girişi</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .box { background: #1e293b; padding: 30px; border-radius: 12px; border: 1px solid #334155; width: 340px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; }
        h2 { color: #f59e0b; margin-top: 0; font-size: 20px; }
        input { width: 100%; padding: 12px; margin: 12px 0; border: 1px solid #475569; border-radius: 6px; background: #0f172a; color: #fff; box-sizing: border-box; text-align: center; font-size: 16px; }
        button { width: 100%; padding: 12px; background: #f59e0b; color: #000; font-weight: bold; border: none; border-radius: 6px; cursor: pointer; font-size: 15px; }
        button:hover { background: #d97706; }
        .hata { color: #ef4444; font-size: 14px; margin-top: 10px; display: none; }
    </style>
</head>
<body>
    <div class="box">
        <h2>🏛️ Yönetici Girişi</h2>
        <p style="font-size: 13px; color: #94a3b8;">Lütfen panel şifrenizi giriniz.</p>
        <form id="loginForm">
            <input type="password" id="sifre" placeholder="Şifrenizi Girin" required autofocus>
            <button type="submit">Devam Et</button>
        </form>
        <div id="hata" class="hata"></div>
    </div>
    <script>
        document.getElementById('loginForm').onsubmit = async (e) => {
            e.preventDefault();
            const res = await fetch('/admin-login-post', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ sifre: document.getElementById('sifre').value })
            });
            const data = await res.json();
            if (data.success) {
                window.location.href = '/admin-2fa';
            } else {
                const h = document.getElementById('hata');
                h.innerText = data.mesaj;
                h.style.display = 'block';
            }
        };
    </script>
</body>
</html>
"""

TWO_FA_PAGE_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Haliç Antik - 2FA Doğrulama</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .box { background: #1e293b; padding: 30px; border-radius: 12px; border: 1px solid #334155; width: 340px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; }
        h2 { color: #f59e0b; margin-top: 0; font-size: 20px; }
        input { width: 100%; padding: 12px; margin: 12px 0; border: 1px solid #475569; border-radius: 6px; background: #0f172a; color: #fff; box-sizing: border-box; text-align: center; font-size: 20px; letter-spacing: 4px; }
        button { width: 100%; padding: 12px; background: #f59e0b; color: #000; font-weight: bold; border: none; border-radius: 6px; cursor: pointer; font-size: 15px; }
        button:hover { background: #d97706; }
        .hata { color: #ef4444; font-size: 14px; margin-top: 10px; display: none; }
    </style>
</head>
<body>
    <div class="box">
        <h2>🔒 İki Aşamalı Doğrulama</h2>
        <p style="font-size: 13px; color: #94a3b8;">Authenticator uygulamanızdaki 6 haneli kodu giriniz.</p>
        <form id="tfaForm">
            <input type="text" id="kod" placeholder="000000" maxlength="6" pattern="[0-9]*" required autofocus>
            <button type="submit">Onayla ve Giriş Yap</button>
        </form>
        <div id="hata" class="hata"></div>
    </div>
    <script>
        document.getElementById('tfaForm').onsubmit = async (e) => {
            e.preventDefault();
            const res = await fetch('/admin-2fa-dogrula', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ kod: document.getElementById('kod').value })
            });
            const data = await res.json();
            if (data.success) {
                window.location.href = '/admin';
            } else {
                const h = document.getElementById('hata');
                h.innerText = data.mesaj;
                h.style.display = 'block';
            }
        };
    </script>
</body>
</html>
"""


@app.route("/admin-login", methods=["GET"])
def admin_login_ekrani():
    if session.get("is_admin") and session.get("admin_2fa_ok"):
        return redirect(url_for("admin_panel"))
    return LOGIN_PAGE_HTML


@app.route("/admin-login-post", methods=["POST"])
def admin_login_post():
    veri = request.json or {}
    girilen_sifre = str(veri.get("sifre", ""))
    if check_password_hash(ADMIN_PASSWORD_HASH, girilen_sifre):
        session["admin_sifre_ok"] = True
        return jsonify({
            "success": True, "mesaj": "Şifre doğrulandı, 2FA kodunu giriniz.",
        })
    return jsonify({"success": False, "mesaj": "Hatalı şifre!"}), 401


@app.route("/admin-2fa", methods=["GET"])
def admin_2fa_ekrani():
    if not session.get("admin_sifre_ok"):
        return redirect(url_for("admin_login_ekrani"))
    return TWO_FA_PAGE_HTML


@app.route("/admin-2fa-dogrula", methods=["POST"])
def admin_2fa_dogrula():
    if not session.get("admin_sifre_ok"):
        return jsonify({
            "success": False, "mesaj": "Yetkisiz oturum! Önce şifrenizi girin.",
        }), 401

    veri = request.json or {}
    girilen_kod = str(veri.get("kod", "")).strip()

    if totp.verify(girilen_kod, valid_window=1):
        session["is_admin"] = True
        session["admin_2fa_ok"] = True
        session.pop("admin_sifre_ok", None)
        return jsonify({
            "success": True,
            "mesaj": "Doğrulama başarılı, panele yönlendiriliyorsunuz.",
        })

    return jsonify({
        "success": False, "mesaj": "Geçersiz veya süresi dolmuş 2FA kodu!",
    }), 400


@app.route("/admin", methods=["GET"])
@admin_required
def admin_panel():
    dukkan = dukkan_dogrula(request.args.get("dukkan", "gumus"))
    try:
        en_yuksek_lot = (
            db.session.query(func.max(Urun.lot_no))
            .filter(Urun.dukkan_kodu == dukkan, Urun.silindi_mi.is_(False))
            .scalar()
        )
        siradaki_lot = (en_yuksek_lot + 1) if en_yuksek_lot else 1
    except Exception:
        siradaki_lot = 1
    return render_template(
        "admin.html",
        siradaki_lot=siradaki_lot,
        secili_dukkan=dukkan,
        dukkanlar=DUKKANLAR,
    )


@app.route("/admin-cikis-yap", methods=["POST", "GET"])
def admin_cikis_yap():
    session.pop("is_admin", None)
    session.pop("admin_2fa_ok", None)
    session.pop("admin_sifre_ok", None)
    return redirect(url_for("izleyici_index"))


# ==========================================
# ÇOKLU ODA YÖNETİM İŞLEMLERİ
# ==========================================
@app.route("/sahneye-al", methods=["POST"])
@admin_required
def sahneye_al():
    veri = request.json or {}
    raw_id = veri.get("urun_id")
    try:
        urun_id = int(raw_id)
    except Exception:
        return jsonify({"success": False, "mesaj": "Geçersiz ürün ID!"})

    urun = db.session.get(Urun, urun_id)
    if not urun or urun.silindi_mi:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})

    dukkan = urun.dukkan_kodu or "gumus"
    oda = odalar[dukkan]

    with oda_kilitleri[dukkan]:
        oda["sayac_aktif"] = False
        oda["sayac_gorev_id"] += 1
        oda["sayac_kalan"] = 0
        _gizli_pazarligi_kapat(oda)
        oda["aktif_urun_id"] = urun.id
        oda["durum"]["durum"] = "Bekliyor"
        oda["durum"]["sure_bitis"] = 0
        urun.durum = "Aktif"

        en_yuksek_ot = (
            OnTeklif.query.filter_by(dukkan_kodu=dukkan, urun_id=urun_id)
            .order_by(OnTeklif.teklif.desc())
            .first()
        )
        if en_yuksek_ot:
            oda["durum"]["pey"] = float(en_yuksek_ot.teklif)
            oda["durum"]["kazanan"] = en_yuksek_ot.musteri_adi
            oda["durum"]["kazanan_id"] = en_yuksek_ot.musteri_id
            urun.guncel_fiyat = float(en_yuksek_ot.teklif)
            urun.kazanan_adi = en_yuksek_ot.musteri_adi
            urun.kazanan_id = en_yuksek_ot.musteri_id
        else:
            oda["durum"]["pey"] = float(urun.acilis_fiyati or 0)
            oda["durum"]["kazanan"] = "Yok"
            oda["durum"]["kazanan_id"] = None
            urun.guncel_fiyat = float(urun.acilis_fiyati or 0)
            urun.kazanan_adi = "Yok"
            urun.kazanan_id = None

        db.session.add(urun)
        db.session.commit()

    takipciler = UrunTakip.query.filter_by(urun_id=urun.id).all()
    urun_paket = urun.to_dict()
    takip_metin = f"Takip ettiğiniz Lot #{urun.lot_no} ({urun.urun_adi}) sahneye alındı!"
    db.session.remove()

    sahneyi_herkese_yayinla(
        dukkan,
        urun_paket,
        {
            "dukkan": dukkan,
            "urun_id": urun_paket["id"],
            "pey": oda["durum"]["pey"],
            "kazanan": oda["durum"]["kazanan"],
            "kazanan_id": oda["durum"]["kazanan_id"],
        },
        yeni_urun=True,
    )
    for t in takipciler:
        socketio.emit(
            "urun_takip_bildirimi",
            {
                "urun_id": urun_paket["id"],
                "mesaj": takip_metin,
            },
        )

    bota_haber_ver(
        "urun_sahneye_aldi",
        {
            "dukkan": dukkan,
            "lot": urun_paket["lot"],
            "baslik": urun_paket["ad"],
            "acilis": urun_paket["fiyat"],
            "guncel_fiyat": oda["durum"]["pey"],
            "hemen_al": urun_paket["hemen_al_fiyat"],
            "tanitim_yazisi": urun_paket["tanitim_yazisi"],
        },
    )

    ahmet_kisa_acilis_seslendir(dukkan, urun_paket["ad"], oda["durum"]["pey"])

    return jsonify({"success": True, "urun": urun_paket})


@app.route("/mezat-baslat", methods=["POST"])
@admin_required
def mezat_baslat():
    veri = request.json or {}
    dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
    oda = odalar[dukkan]

    urun_id = veri.get("urun_id", oda["aktif_urun_id"])
    try:
        sure = int(veri.get("sure") or 180)
    except (TypeError, ValueError):
        sure = 180

    if not urun_id:
        return jsonify({"success": False, "mesaj": "Bu dükkanda ürün seçilmedi!"})

    if sure <= 15:
        with oda_kilitleri[dukkan]:
            if not oda["sayac_aktif"]:
                return jsonify({"success": False, "mesaj": "Uzatılacak aktif sayaç yok."})
            oda["sayac_kalan"] = int(oda["sayac_kalan"] or 0) + 15
            oda["durum"]["sure_bitis"] = time.time() + oda["sayac_kalan"]
            kalan = int(oda["sayac_kalan"])
            pazarlik = bool(oda.get("gizli_pazarlik_aktif"))
        paket = {
            "dukkan": dukkan,
            "kalan": kalan,
            "kalan_sure": kalan,
            "sayac_gorunsun": True,
        }
        if pazarlik:
            paket["asama"] = "pazarlik"
        else:
            socketio.emit(
                "sayac_uzatildi",
                {
                    "dukkan": dukkan,
                    "kalan": kalan,
                    "mesaj": "Süre 15 sn uzatıldı!",
                },
                room=dukkan,
            )
        socketio.emit("sayac_guncelle", paket, room=dukkan)
        return jsonify({"success": True, "dukkan": dukkan})

    with oda_kilitleri[dukkan]:
        oda["aktif_urun_id"] = urun_id
        oda["durum"]["durum"] = "Sayim"
        oda["sayac_gorev_id"] += 1
        oda["sayac_aktif"] = True
        gorev = oda["sayac_gorev_id"]
        urun = db.session.get(Urun, int(urun_id))
        if sure >= 180:
            gorev = _pazarligi_hazirla(oda, urun)
            akis = "pazarlik"
        else:
            _fiyati_acilisa_cek(dukkan, oda)
            oda["sayac_aktif"] = True
            oda["sayac_kalan"] = 60
            akis = "karar"

    socketio.start_background_task(dukkan_geri_sayim_gorevi, dukkan, gorev, akis)
    socketio.emit("mezat_basladi_muzik", room=dukkan)
    return jsonify({"success": True, "dukkan": dukkan})


@app.route("/satis-bitir", methods=["POST"])
@admin_required
def satis_bitir():
    veri = request.json or {}
    dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
    oda = odalar[dukkan]

    with oda_kilitleri[dukkan]:
        oda["sayac_aktif"] = False
        oda["sayac_gorev_id"] += 1
        oda["durum"]["durum"] = "Satıldı"
        oda["durum"]["sure_bitis"] = 0
        biten_urun_id = oda["aktif_urun_id"]
        oda["aktif_urun_id"] = None

        satis_lot = ""
        satis_ad = "Eser"
        if biten_urun_id:
            urun = db.session.get(Urun, biten_urun_id)
            if urun:
                satis_lot = urun.lot_no
                satis_ad = urun.urun_adi or "Eser"
                urun.durum = "Satıldı"
                urun.kazanan_adi = oda["durum"]["kazanan"]
                urun.kazanan_id = oda["durum"].get("kazanan_id")
                urun.guncel_fiyat = (
                    float(oda["durum"]["pey"])
                    if float(oda["durum"]["pey"]) > 0
                    else float(urun.acilis_fiyati or 0)
                )
                db.session.add(urun)
                OnTeklif.query.filter_by(urun_id=biten_urun_id).delete()
                db.session.commit()
                urunu_kazanana_sepete_ekle(
                    biten_urun_id,
                    oda["durum"]["kazanan"],
                    oda["durum"].get("kazanan_id"),
                )

        socketio.emit(
            "sayac_bitti",
            {
                "dukkan": dukkan,
                "urun_id": biten_urun_id,
                "satildi_mi": True,
                "kazanan": oda["durum"]["kazanan"],
                "kazanan_id": oda["durum"].get("kazanan_id"),
                "fiyat": oda["durum"]["pey"],
            },
            room=dukkan,
        )
        bota_haber_ver(
            "satis_bitti",
            {
                "dukkan": dukkan,
                "kazanan": oda["durum"]["kazanan"],
                "fiyat": oda["durum"]["pey"],
            },
        )
        _sahneyi_bosa_al(dukkan, "satildi")
        ahmet_usta_anons_yap(
            dukkan,
            ahmet_satis_cumlesi(satis_lot, satis_ad, oda["durum"]["kazanan"]),
            tur="satis",
        )
        bota_haber_ver("ahmet_sifirla", {"dukkan": dukkan})
    return jsonify({"success": True, "dukkan": dukkan})


@app.route("/son-peyi-iptal-et", methods=["POST"])
@admin_required
def son_peyi_iptal_et():
    veri = request.json or {}
    dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
    oda = odalar[dukkan]

    if not oda["aktif_urun_id"]:
        return jsonify({"success": False, "mesaj": "Sahnede aktif ürün yok!"})

    with oda_kilitleri[dukkan]:
        son_teklif = (
            Teklif.query.filter_by(dukkan_kodu=dukkan, urun_id=oda["aktif_urun_id"])
            .order_by(Teklif.id.desc())
            .first()
        )
        if son_teklif:
            db.session.delete(son_teklif)
            db.session.commit()

        bir_onceki = (
            Teklif.query.filter_by(dukkan_kodu=dukkan, urun_id=oda["aktif_urun_id"])
            .order_by(Teklif.id.desc())
            .first()
        )
        urun = db.session.get(Urun, oda["aktif_urun_id"])
        if bir_onceki:
            oda["durum"]["pey"] = float(bir_onceki.tutar)
            oda["durum"]["kazanan"] = bir_onceki.musteri_adi
            oda["durum"]["kazanan_id"] = bir_onceki.musteri_id
            if urun:
                urun.guncel_fiyat = float(bir_onceki.tutar)
                urun.kazanan_adi = bir_onceki.musteri_adi
                urun.kazanan_id = bir_onceki.musteri_id
        else:
            oda["durum"]["pey"] = float(urun.acilis_fiyati or 0) if urun else 0
            oda["durum"]["kazanan"] = "Yok"
            oda["durum"]["kazanan_id"] = None
            if urun:
                urun.guncel_fiyat = float(urun.acilis_fiyati or 0)
                urun.kazanan_adi = "Yok"
                urun.kazanan_id = None

        if urun:
            db.session.add(urun)
            db.session.commit()

        socketio.emit(
            "pey_guncellendi",
            {
                "dukkan": dukkan,
                "urun_id": oda["aktif_urun_id"],
                "pey": oda["durum"]["pey"],
                "kazanan": oda["durum"]["kazanan"],
                "kazanan_id": oda["durum"].get("kazanan_id"),
            },
            room=dukkan,
        )
    return jsonify({"success": True})


@app.route("/tek-urun-iptal-et", methods=["POST"])
@admin_required
def tek_urun_iptal_et():
    veri = request.json or {}
    urun_id = veri.get("urun_id")

    if not urun_id:
        return jsonify({"success": False, "mesaj": "Ürün ID belirtilmedi."})

    urun = db.session.get(Urun, int(urun_id))
    if not urun:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."})

    dukkan = urun.dukkan_kodu or "gumus"
    oda = odalar[dukkan]

    try:
        with oda_kilitleri[dukkan]:
            Teklif.query.filter_by(urun_id=urun.id).delete()
            OnTeklif.query.filter_by(urun_id=urun.id).delete()
            SepetItem.query.filter_by(urun_id=urun.id).delete()

            urun.durum = "Aktif"
            urun.kazanan_adi = "Yok"
            urun.kazanan_id = None
            urun.guncel_fiyat = float(urun.acilis_fiyati or 0)

            db.session.add(urun)
            db.session.commit()

            if oda["aktif_urun_id"] == urun.id:
                oda["sayac_aktif"] = False
                oda["sayac_gorev_id"] += 1
                _gizli_pazarligi_kapat(oda)
                oda["aktif_urun_id"] = None
                oda["durum"]["durum"] = "Bekliyor"
                oda["durum"]["sure_bitis"] = 0
                oda["durum"]["pey"] = float(urun.acilis_fiyati or 0)
                oda["durum"]["kazanan"] = "Yok"
                oda["durum"]["kazanan_id"] = None
                socketio.emit(
                    "pey_guncellendi",
                    {
                        "dukkan": dukkan,
                        "urun_id": urun.id,
                        "pey": oda["durum"]["pey"],
                        "kazanan": "Yok",
                        "kazanan_id": None,
                    },
                    room=dukkan,
                )

        return jsonify({
            "success": True,
            "mesaj": f"Lot #{urun.lot_no} ({urun.urun_adi}) satışı iptal edildi.",
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/musteri-dosya-sil", methods=["POST"])
@admin_required
def musteri_dosya_sil():
    veri = request.json or {}
    ham_ad = (veri.get("musteri_adi") or "").strip()
    musteri_id = veri.get("musteri_id")

    if not ham_ad and not musteri_id:
        return jsonify({
            "success": False, "mesaj": "Müşteri bilgisi belirtilmedi.",
        })

    try:
        hedef_kullanici = None
        if musteri_id:
            hedef_kullanici = db.session.get(Kullanici, int(musteri_id))

        if not hedef_kullanici and ham_ad:
            saf_ad = saf_isim_temizle(ham_ad)
            hedef_kullanici = (
                Kullanici.query.filter(
                    Kullanici.silindi_mi.is_(False),
                    Kullanici.ad_soyad.ilike(saf_ad),
                ).first()
            )

        if hedef_kullanici:
            SepetItem.query.filter_by(musteri_id=hedef_kullanici.id).delete()
            satilanlar = Urun.query.filter(
                or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
                Urun.silindi_mi.is_(False),
                Urun.kazanan_id == hedef_kullanici.id,
            ).all()

            for u in satilanlar:
                u.durum = "Aktif"
                u.kazanan_adi = "Yok"
                u.kazanan_id = None
                u.guncel_fiyat = float(u.acilis_fiyati or 0)
                Teklif.query.filter_by(urun_id=u.id).delete()
                OnTeklif.query.filter_by(urun_id=u.id).delete()
                SepetItem.query.filter_by(urun_id=u.id).delete()
                db.session.add(u)

                d_kod = u.dukkan_kodu or "gumus"
                if odalar[d_kod]["aktif_urun_id"] == u.id:
                    odalar[d_kod]["aktif_urun_id"] = None
                    odalar[d_kod]["durum"]["pey"] = float(u.acilis_fiyati or 0)
                    odalar[d_kod]["durum"]["kazanan"] = "Yok"
                    odalar[d_kod]["durum"]["kazanan_id"] = None

            db.session.commit()

        return jsonify({
            "success": True,
            "mesaj": "Müşterinin tüm dosyası sıfırlandı ve ürünler havuza alındı.",
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/urun-ekle", methods=["POST"])
@admin_required
def urun_ekle():
    try:
        dukkan = dukkan_dogrula(request.form.get("dukkan", "gumus"))
        dosyalar = (
            request.files.getlist("dosyalar")
            or request.files.getlist("medyalar")
            or []
        )

        fotograflar = []
        video_url = ""
        ses_url = ""

        for dosya in dosyalar:
            url, temiz_ad = kaydet_guvenli_dosya(dosya)
            if url and temiz_ad:
                ext = temiz_ad.lower()
                if ext.endswith((
                    ".mp4", ".mov", ".avi", ".webm", ".mkv", ".3gp", ".m4v"
                )):
                    if not video_url:
                        video_url = url
                    fotograflar.append(url)
                elif ext.endswith((".mp3", ".wav", ".ogg", ".m4a")):
                    if not ses_url:
                        ses_url = url
                    fotograflar.append(url)
                else:
                    fotograflar.append(url)

        lot_raw = request.form.get("lot")
        if lot_raw and str(lot_raw).strip().isdigit():
            lot_no = int(lot_raw)
        else:
            en_yuksek_lot = (
                db.session.query(func.max(Urun.lot_no))
                .filter(Urun.dukkan_kodu == dukkan, Urun.silindi_mi.is_(False))
                .scalar()
            )
            lot_no = (en_yuksek_lot + 1) if en_yuksek_lot else 1

        ted_id_raw = request.form.get("tedarikci_id")
        secili_tedarikci_id = (
            int(ted_id_raw) if ted_id_raw and str(ted_id_raw).isdigit() else None
        )

        yeni_urun = Urun(
            dukkan_kodu=dukkan,
            lot_no=lot_no,
            urun_adi=request.form.get("ad", "İsimsiz Ürün"),
            kategori=kategori_ad_temizle(request.form.get("kategori", "Genel")),
            acilis_fiyati=float(request.form.get("fiyat") or 0),
            guncel_fiyat=float(request.form.get("fiyat") or 0),
            hemen_al_fiyati=float(request.form.get("hemen_al_fiyat") or 0),
            tanitim_yazisi=request.form.get("tanitim_yazisi", ""),
            fotograflar=fotograflar,
            video=video_url,
            ses_dosyasi=ses_url,
            durum="Aktif",
            silindi_mi=False,
            tedarikci_id=secili_tedarikci_id,
        )
        db.session.add(yeni_urun)
        db.session.commit()

        socketio.emit(
            "yeni_urun_bildirimi",
            {
                "dukkan": dukkan,
                "lot": lot_no,
                "ad": yeni_urun.urun_adi,
                "fiyat": yeni_urun.acilis_fiyati,
                "mesaj": f"Yeni Eser Yayında: Lot #{lot_no} {yeni_urun.urun_adi} kataloğa eklendi!",
            },
        )

        return jsonify({"success": True, "dukkan": dukkan, "lot_no": lot_no})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/urun-guncelle", methods=["POST"])
@admin_required
def urun_guncelle():
    veri = request.json or {}
    urun_id = veri.get("id")
    urun = db.session.get(Urun, urun_id)
    if not urun or urun.silindi_mi:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})

    eski_dukkan = urun.dukkan_kodu or "gumus"
    yeni_dukkan = dukkan_dogrula(veri.get("dukkan", eski_dukkan))
    oda = odalar[eski_dukkan]

    try:
        urun.dukkan_kodu = yeni_dukkan
        urun.lot_no = int(veri.get("lot", urun.lot_no))
        urun.urun_adi = veri.get("ad", urun.urun_adi)
        urun.kategori = kategori_ad_temizle(veri.get("kategori", urun.kategori))
        urun.acilis_fiyati = float(veri.get("fiyat", urun.acilis_fiyati))
        urun.hemen_al_fiyati = float(
            veri.get("hemen_al_fiyat", urun.hemen_al_fiyati)
        )
        urun.tanitim_yazisi = veri.get("tanitim_yazisi", urun.tanitim_yazisi)

        ted_val = veri.get("tedarikci_id")
        urun.tedarikci_id = (
            int(ted_val) if ted_val and str(ted_val).isdigit() else None
        )

        if oda["aktif_urun_id"] == urun.id:
            if eski_dukkan != yeni_dukkan:
                oda["aktif_urun_id"] = None
                oda["durum"]["durum"] = "Bekliyor"
                oda["durum"]["sure_bitis"] = 0
                oda["durum"]["pey"] = 0
                oda["durum"]["kazanan"] = "Yok"
                oda["durum"]["kazanan_id"] = None
            elif oda["durum"]["pey"] == 0:
                oda["durum"]["pey"] = urun.acilis_fiyati
                urun.guncel_fiyat = urun.acilis_fiyati

        db.session.add(urun)
        db.session.commit()

        if oda["aktif_urun_id"] == urun.id and eski_dukkan == yeni_dukkan:
            sahneyi_herkese_yayinla(eski_dukkan, urun.to_dict())
        return jsonify({"success": True, "mesaj": "Ürün başarıyla güncellendi."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/urun-sil", methods=["POST"])
@admin_required
def urun_sil():
    veri = request.json or {}
    urun_id = veri.get("id")
    urun = db.session.get(Urun, urun_id)
    if urun:
        dukkan = urun.dukkan_kodu or "gumus"
        urun.silindi_mi = True
        urun.silinme_tarihi = suan_utc()

        if odalar[dukkan]["aktif_urun_id"] == urun.id:
            odalar[dukkan]["aktif_urun_id"] = None
            odalar[dukkan]["durum"]["durum"] = "Bekliyor"
            odalar[dukkan]["durum"]["sure_bitis"] = 0
            odalar[dukkan]["durum"]["pey"] = 0
            odalar[dukkan]["durum"]["kazanan"] = "Yok"
            odalar[dukkan]["durum"]["kazanan_id"] = None

        db.session.commit()
        return jsonify({"success": True, "mesaj": "Ürün çöp kutusuna taşındı."})
    return jsonify({"success": False, "mesaj": "Ürün bulunamadı."})


# YÖNETİCİ MÜŞTERİ DURUM GÜNCELLEME (ANLIK BAN/SUSTURMA SİNYALİ)
@app.route("/musteri-durum-guncelle", methods=["POST"])
@admin_required
def musteri_durum_guncelle():
    veri = request.json or {}
    kullanici_id = veri.get("kullanici_id")
    yeni_durum = veri.get("durum")

    kullanici = db.session.get(Kullanici, int(kullanici_id))
    if kullanici:
        kullanici.durum = yeni_durum
        kullanici.onayli_mi = yeni_durum == "onayli"
        if yeni_durum == "engellendi":
            kullanici.is_banned = True
            kullanici.onayli_mi = False
        elif yeni_durum == "onayli":
            kullanici.is_banned = False
        db.session.commit()

        if yeni_durum == "engellendi":
            socketio.emit("kullanici_engellendi", {
                "kullanici_id": kullanici.id,
                "ban": True,
                "mesaj": BAN_MESAJI,
            })
            kullanici_soketlerini_kes(kullanici.id)
            
        return jsonify({"success": True, "mesaj": f"Müşteri durumu {yeni_durum} olarak güncellendi."})
    return jsonify({"success": False, "mesaj": "Kullanıcı bulunamadı."})


@app.route("/musteri-sil", methods=["POST"])
@admin_required
def musteri_sil():
    veri = request.json or {}
    kullanici_id = veri.get("kullanici_id")
    kullanici = db.session.get(Kullanici, int(kullanici_id)) if kullanici_id else None
    if kullanici:
        kullanici.silindi_mi = True
        kullanici.silinme_tarihi = suan_utc()
        db.session.commit()
        return jsonify({"success": True, "mesaj": "Müşteri çöp kutusuna taşındı."})
    return jsonify({"success": False, "mesaj": "Müşteri bulunamadı."})


# ==========================================
# ÇÖP KUTUSU İŞLEMLERİ
# ==========================================
@app.route("/cop-kutusu-listele", methods=["GET"])
@admin_required
def cop_kutusu_listele():
    try:
        silinen_urunler = (
            Urun.query.filter_by(silindi_mi=True)
            .order_by(Urun.silinme_tarihi.desc())
            .limit(100)
            .all()
        )
        silinen_musteriler = (
            Kullanici.query.filter_by(silindi_mi=True)
            .order_by(Kullanici.silinme_tarihi.desc())
            .limit(100)
            .all()
        )

        return jsonify({
            "success": True,
            "urunler": [
                {
                    "id": u.id,
                    "lot": u.lot_no,
                    "ad": u.urun_adi,
                    "fiyat": u.acilis_fiyati,
                    "silinme_tarihi": (
                        u.silinme_tarihi.strftime("%d.%m.%Y %H:%M")
                        if u.silinme_tarihi
                        else "-"
                    ),
                }
                for u in silinen_urunler
            ],
            "musteriler": [
                {
                    "id": m.id,
                    "ad": m.ad_soyad,
                    "tel": m.telefon,
                    "silinme_tarihi": (
                        m.silinme_tarihi.strftime("%d.%m.%Y %H:%M")
                        if m.silinme_tarihi
                        else "-"
                    ),
                }
                for m in silinen_musteriler
            ],
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "urunler": [], "musteriler": []})
    finally:
        db.session.remove()


@app.route("/cop-kutusundan-geri-yukle", methods=["POST"])
@admin_required
def cop_kutusundan_geri_yukle():
    veri = request.json or {}
    tur = veri.get("tur")
    kayit_id = veri.get("id")

    if tur == "urun":
        urun = db.session.get(Urun, kayit_id)
        if urun:
            urun.silindi_mi = False
            urun.silinme_tarihi = None
            db.session.commit()
            return jsonify({"success": True, "mesaj": "Ürün geri yüklendi."})
    elif tur == "musteri":
        kullanici = db.session.get(Kullanici, kayit_id)
        if kullanici:
            kullanici.silindi_mi = False
            kullanici.silinme_tarihi = None
            db.session.commit()
            return jsonify({"success": True, "mesaj": "Müşteri geri yüklendi."})

    return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})


@app.route("/cop-kutusundan-kalici-sil", methods=["POST"])
@admin_required
def cop_kutusundan_kalici_sil():
    veri = request.json or {}
    tur = veri.get("tur")
    kayit_id = veri.get("id")

    try:
        if tur == "urun":
            urun = db.session.get(Urun, kayit_id)
            if urun:
                dukkan = urun.dukkan_kodu or "gumus"
                Teklif.query.filter_by(urun_id=urun.id).delete()
                OnTeklif.query.filter_by(urun_id=urun.id).delete()
                SepetItem.query.filter_by(urun_id=urun.id).delete()
                UrunTakip.query.filter_by(urun_id=urun.id).delete()

                if odalar[dukkan]["aktif_urun_id"] == urun.id:
                    odalar[dukkan]["aktif_urun_id"] = None
                    odalar[dukkan]["durum"]["durum"] = "Bekliyor"
                    odalar[dukkan]["durum"]["sure_bitis"] = 0
                    odalar[dukkan]["durum"]["pey"] = 0
                    odalar[dukkan]["durum"]["kazanan"] = "Yok"
                    odalar[dukkan]["durum"]["kazanan_id"] = None

                db.session.delete(urun)
                db.session.commit()
                return jsonify({
                    "success": True, "mesaj": "Ürün kalıcı olarak silindi.",
                })
        elif tur == "musteri":
            kullanici = db.session.get(Kullanici, kayit_id)
            if kullanici:
                Teklif.query.filter_by(musteri_id=kullanici.id).delete()
                OnTeklif.query.filter_by(musteri_id=kullanici.id).delete()
                SepetItem.query.filter_by(musteri_id=kullanici.id).delete()
                UrunTakip.query.filter_by(musteri_id=kullanici.id).delete()
                db.session.delete(kullanici)
                db.session.commit()
                return jsonify({
                    "success": True, "mesaj": "Müşteri kalıcı olarak silindi.",
                })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)}), 500

    return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})


# ==========================================
# 7/24 OTONOM MEZAT ÇOKLU MOTOR YÖNETİMİ
# ==========================================
@app.route("/api/admin/otonom-mezat-kontrol", methods=["POST"])
@admin_required
def otonom_mezat_kontrol():
    veri = request.json or {}
    hedef = veri.get("dukkan", "tumu")
    durum = bool(veri.get("aktif", False))

    hedefler = list(DUKKANLAR.keys()) if hedef == "tumu" else [dukkan_dogrula(hedef)]

    for d in hedefler:
        oda = odalar[d]
        if durum and not oda["otonom_aktif"]:
            oda["otonom_aktif"] = True
            socketio.start_background_task(dukkan_otonom_dongusu, d)
            socketio.emit(
                "otonom_durum_degisti", {"dukkan": d, "aktif": True}, room=d
            )
        elif not durum and oda["otonom_aktif"]:
            oda["otonom_aktif"] = False
            socketio.emit(
                "otonom_durum_degisti", {"dukkan": d, "aktif": False}, room=d
            )

    return jsonify({
        "success": True,
        "mesaj": f"Otonom mezat durumu güncellendi: {hedef.upper()} -> {'AÇIK' if durum else 'KAPALI'}",
        "durumlar": {d: odalar[d]["otonom_aktif"] for d in DUKKANLAR.keys()},
    })


@app.route("/api/admin/muzik-link-guncelle", methods=["POST"])
@admin_required
def admin_muzik_link_guncelle():
    veri = request.json or {}
    video_id = veri.get("video_id", "j12l7mQANFU").strip()

    if not video_id:
        return jsonify({"success": False, "mesaj": "Geçersiz video ID!"}), 400

    socketio.emit("canli_muzik_degisti", {"video_id": video_id})
    return jsonify({"success": True, "video_id": video_id})


@app.route("/excel-indir", methods=["GET"])
@admin_required
def excel_indir():
    try:
        kayitli = _rapor_yolu()
        if os.path.isfile(kayitli):
            return send_file(
                kayitli,
                as_attachment=True,
                download_name="mezat_raporu.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        satilanlar = Urun.query.filter(Urun.durum == "Satıldı", Urun.silindi_mi.is_(False)).all()
        musteriler = Kullanici.query.filter(Kullanici.silindi_mi.is_(False)).all()

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            if satilanlar:
                df_sat = [
                    {
                        "Dükkan": s.dukkan_kodu,
                        "Lot No": s.lot_no,
                        "Ürün Adı": s.urun_adi,
                        "Satış Fiyatı (TL)": s.guncel_fiyat,
                        "Alan Müşteri": s.kazanan_adi,
                    }
                    for s in satilanlar
                ]
                pd.DataFrame(df_sat).to_excel(
                    writer, sheet_name="Satilan_Urunler", index=False
                )
            if musteriler:
                df_mus = [
                    {
                        "Ad Soyad": getattr(m, "ad_soyad", "-"),
                        "Telefon": getattr(m, "telefon", "-"),
                        "E-posta": getattr(m, "email", "-"),
                        "Adres": getattr(m, "adres", "-"),
                        "Durum": getattr(m, "durum", "-"),
                    }
                    for m in musteriler
                ]
                pd.DataFrame(df_mus).to_excel(
                    writer, sheet_name="Musteriler", index=False
                )

        output.seek(0)
        dosya_adi = f"mezat_raporu_{suan_utc().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=dosya_adi,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return str(e), 500


def _rapor_yolu():
    os.makedirs(app.instance_path, exist_ok=True)
    return os.path.join(app.instance_path, "mezat_raporu.xlsx")


def _bos_rapor_yaz(yol):
    pd.DataFrame(
        columns=["Dükkan", "Lot No", "Ürün Adı", "Satış Fiyatı (TL)", "Alan Müşteri"]
    ).to_excel(yol, index=False)


_xml_besleme = {"link": "", "kuyruk": []}


@app.route("/excel-rapor-sil", methods=["POST"])
@admin_required
def excel_rapor_sil():
    yol = _rapor_yolu()
    if os.path.isfile(yol):
        os.remove(yol)
    return jsonify({"success": True, "mesaj": "Excel raporu silindi."})


@app.route("/excel-rapor-sifirla", methods=["POST"])
@admin_required
def excel_rapor_sifirla():
    _bos_rapor_yaz(_rapor_yolu())
    return jsonify({"success": True, "mesaj": "Excel raporu sıfırlandı."})


@app.route("/excel-rapor-yukle", methods=["POST"])
@admin_required
def excel_rapor_yukle():
    dosya = request.files.get("dosya")
    if not dosya or not dosya.filename:
        return jsonify({"success": False, "mesaj": "Excel dosyası seçilmedi."})
    yol = _rapor_yolu()
    dosya.save(yol)
    return jsonify({"success": True, "mesaj": "Excel raporu yeni dosyayla güncellendi."})


@app.route("/xml-link-temizle", methods=["POST"])
@admin_required
def xml_link_temizle():
    _xml_besleme["link"] = ""
    _xml_besleme["kuyruk"] = []
    return jsonify({"success": True, "mesaj": "XML linki ve geçici kuyruk temizlendi."})


@app.route("/toplu-urun-yukle", methods=["POST"])
@admin_required
def toplu_urun_yukle():
    url = (request.form.get("xml_url") or "").strip()
    if url:
        _xml_besleme["link"] = url
        try:
            cevap = requests.get(url, timeout=12)
            cevap.raise_for_status()
            parcalar = re.findall(
                r"<(?:item|urun|product|entry)\b[\s\S]*?</(?:item|urun|product|entry)>",
                cevap.text,
                flags=re.IGNORECASE,
            )
            _xml_besleme["kuyruk"] = parcalar[:500]
        except Exception as hata:
            _xml_besleme["kuyruk"] = []
            return jsonify({"success": False, "mesaj": f"XML okunamadı: {hata}"})
        return jsonify({
            "success": True,
            "mesaj": f"XML linki alındı. Geçici kuyrukta {len(_xml_besleme['kuyruk'])} kayıt var.",
        })
    if request.files.get("dosya"):
        return jsonify({
            "success": False,
            "mesaj": "Ürün dosyası kataloğa yazılmadı. Rapor için Excel Raporu yanındaki üzerine yazmayı kullanın.",
        })
    return jsonify({"success": False, "mesaj": "XML linki veya dosya yok."})


# ==========================================
# BİZİMHESAP PERAKENDE SATIŞ CSV AKTARIM ROTASI
# ==========================================
@app.route("/admin/bizimhesap-aktarim-excel", methods=["GET"])
def bizimhesap_aktarim_excel():
    kuyruk_dosyasi = "bizimhesap_kuyruk.json"
    satirlar = []

    if os.path.exists(kuyruk_dosyasi):
        try:
            with open(kuyruk_dosyasi, "r", encoding="utf-8") as f:
                satirlar = json.load(f)
        except Exception:
            satirlar = []

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["Tarih", "Belge No", "Müşteri Adı", "Telefon", "Ürün Adı", "Miktar", "Birim Fiyat", "KDV Oranı", "Açıklama"])

    if not satirlar:
        writer.writerow([
            datetime.now().strftime("%d.%m.%Y"),
            "LOT-ORNEK",
            "Örnek Alıcı",
            "05555555555",
            "Mezat Antika Lot",
            "1",
            "100.00",
            "20",
            "Haliç Antik Mezat Satışı",
        ])
    else:
        for item in satirlar:
            writer.writerow([
                item.get("tarih", datetime.now().strftime("%d.%m.%Y")),
                f"LOT-{item.get('lot_no', '0')}",
                item.get("alici", "Misafir"),
                item.get("telefon", "05555555555"),
                item.get("urun_adi", "Mezat Ürünü"),
                "1",
                f"{item.get('tutar', 0):.2f}",
                "20",
                f"Lot #{item.get('lot_no', '')} Mezat Satışı",
            ])

    csv_data = output.getvalue().encode("utf-8-sig")
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=bizimhesap_satis_aktarim.csv"},
    )


# ==========================================
# BİZİMHESAP 7/24 OTOMATİK SİPARİŞ XML MOTORU
# ==========================================
@app.route("/api/bizimhesap/siparisler.xml", methods=["GET"])
def bizimhesap_otomatik_xml():
    with app.app_context():
        satilanlar = Urun.query.filter(
            or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
            Urun.silindi_mi.is_(False)
        ).order_by(Urun.id.desc()).limit(100).all()

        xml_lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<orders>'
        ]

        for u in satilanlar:
            alici = db.session.get(Kullanici, u.kazanan_id) if u.kazanan_id else None
            alici_ad = alici.ad_soyad if alici else (u.kazanan_adi or "Misafir Alıcı")
            alici_tel = alici.telefon if alici else "05555555555"
            alici_adres = alici.adres if alici else "Adres Bilgisi Alınacak"
            alici_mail = alici.email if alici else "info@halicantik.com"
            fiyat = float(u.guncel_fiyat or u.acilis_fiyati or 0.0)

            xml_lines.append(f"""  <order>
    <id>{u.id}</id>
    <order_number>HLCMZT-{u.lot_no}-{u.id}</order_number>
    <date>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</date>
    <customer>
      <name>{alici_ad}</name>
      <phone>{alici_tel}</phone>
      <email>{alici_mail}</email>
      <address>{alici_adres}</address>
    </customer>
    <items>
      <item>
        <code>LOT-{u.lot_no}</code>
        <name>{u.urun_adi}</name>
        <quantity>1</quantity>
        <price>{fiyat:.2f}</price>
        <tax_rate>20</tax_rate>
      </item>
    </items>
    <status>Approved</status>
  </order>""")

        xml_lines.append('</orders>')
        xml_icerik = "\n".join(xml_lines)
        return Response(xml_icerik, mimetype="application/xml; charset=utf-8")


# ==========================================
# BİZİMHESAP WOOCOMMERCE REST API TAM UYUMLULUK
# ==========================================
@app.route('/wp-json', methods=['GET', 'POST', 'OPTIONS'])
@app.route('/wp-json/', methods=['GET', 'POST', 'OPTIONS'])
def wc_base_index():
    return jsonify({
        "name": "Haliç Antik Mezat",
        "description": "Canlı Mezat Portalı",
        "url": request.host_url.rstrip('/'),
        "home": request.host_url.rstrip('/'),
        "namespaces": ["wc/v1", "wc/v2", "wc/v3"],
        "authentication": [],
        "routes": {
            "/wp-json": {"namespace": ""},
            "/wp-json/wc/v3": {"namespace": "wc/v3"},
            "/wp-json/wc/v3/orders": {"namespace": "wc/v3"},
            "/wp-json/wc/v3/system_status": {"namespace": "wc/v3"}
        }
    }), 200


@app.route('/wp-json/wc/v3', methods=['GET', 'POST', 'OPTIONS'])
@app.route('/wp-json/wc/v3/', methods=['GET', 'POST', 'OPTIONS'])
def wc_v3_root():
    return jsonify({
        "namespace": "wc/v3",
        "routes": {
            "/wc/v3/orders": {"methods": ["GET", "POST"]},
            "/wc/v3/system_status": {"methods": ["GET"]}
        }
    }), 200


@app.route('/wp-json/wc/v3/system_status', methods=['GET', 'POST', 'OPTIONS'])
def wc_system_status():
    return jsonify({
        "environment": {
            "home_url": request.host_url.rstrip('/'),
            "site_url": request.host_url.rstrip('/'),
            "version": "8.5.0",
            "log_directory": "/tmp/",
            "log_directory_writable": True,
            "wp_version": "6.4.3",
            "wp_multisite": False,
            "wp_memory_limit": 268435456,
            "wp_debug_mode": False,
            "wp_cron": True,
            "language": "tr_TR",
            "server_info": "Apache/2.4.41",
            "php_version": "8.1.0",
            "php_post_max_size": 134217728,
            "php_max_execution_time": 300,
            "php_max_input_vars": 10000,
            "curl_version": "7.68.0",
            "suhosin_installed": False,
            "max_upload_size": 134217728,
            "mysql_version": "8.0.35",
            "mysql_version_string": "8.0.35",
            "default_timezone": "UTC",
            "fsockopen_or_curl_enabled": True,
            "soapclient_enabled": True,
            "domdocument_enabled": True,
            "gzip_enabled": True,
            "mbstring_enabled": True,
            "remote_post_successful": True,
            "remote_post_response": 200,
            "remote_get_successful": True,
            "remote_get_response": 200
        },
        "database": {"wc_database_version": "8.5.0"},
        "active_plugins": [],
        "theme": {"name": "Storefront", "version": "4.5.0"},
        "settings": {"api_enabled": True, "force_ssl": True, "currency": "TRY"},
        "security": {"secure_connection": True, "hide_errors": True}
    }), 200


@app.route('/wp-json/wc/v3/orders', methods=['GET', 'POST', 'OPTIONS'])
def wc_orders():
    with app.app_context():
        satilanlar = Urun.query.filter(
            or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
            Urun.silindi_mi.is_(False)
        ).order_by(Urun.id.desc()).limit(100).all()

        orders_data = []
        for urun in satilanlar:
            alici = db.session.get(Kullanici, urun.kazanan_id) if urun.kazanan_id else None
            alici_ad = alici.ad_soyad if alici else (urun.kazanan_adi or "Misafir")
            fiyat = float(urun.guncel_fiyat or urun.acilis_fiyati or 0.0)

            orders_data.append({
                "id": urun.id,
                "number": f"HLCMZT-{urun.lot_no}-{urun.id}",
                "status": "completed",
                "date_created": datetime.now().isoformat(),
                "total": str(fiyat),
                "billing": {
                    "first_name": alici_ad,
                    "last_name": "Alıcısı",
                    "email": alici.email if alici else "info@halicantik.com",
                    "phone": alici.telefon if alici else "05555555555",
                    "address_1": alici.adres if alici else "Balat / İstanbul",
                    "city": "İstanbul",
                    "country": "TR"
                },
                "line_items": [
                    {
                        "id": urun.id,
                        "name": urun.urun_adi,
                        "product_id": urun.id,
                        "quantity": 1,
                        "subtotal": str(fiyat),
                        "total": str(fiyat),
                        "sku": f"LOT-{urun.lot_no}"
                    }
                ]
            })

        return jsonify(orders_data), 200


# ==========================================
# SOCKET.IO HANDLERS (ODA AYRIMI)
# ==========================================
@socketio.on("connect")
def handle_connect():
    try:
        aktif_izleyiciler.add(request.sid)
        izleyici_sayisini_yayinla()
        emit(
            "izleyici_sayisi_guncelle",
            {"sayi": len(aktif_izleyiciler), "dukkan": "gumus"},
        )
    finally:
        db.session.remove()


@socketio.on("disconnect")
def handle_disconnect():
    try:
        aktif_izleyiciler.discard(request.sid)
        izleyici_adlari.pop(request.sid, None)
        soketten_kullanici_kopar(request.sid)
        izleyici_sayisini_yayinla()
    finally:
        db.session.remove()


@socketio.on("join_admin")
def handle_join_admin():
    if session.get("is_admin"):
        join_room("admin_room")


_son_duzeltme_ani = {}


def _patron_yazi_sade(metin):
    s = str(metin or "").casefold()
    for eski, yeni in (
        ("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"), ("â", "a"),
    ):
        s = s.replace(eski, yeni)
    return s


def _patron_emir_turu(metin):
    s = _patron_yazi_sade(metin)
    if any(k in s for k in (
        "3 dakika", "uc dakika", "sureyi ac", "sureyi baslat",
        "sayaci baslat", "sayac baslat", "sayaci ac", "sayaci ac",
    )):
        return "sayac"
    if any(k in s for k in ("kac para", "fiyat", "ne kadar", "kactan", "kaca", "kac lir")):
        return "fiyat"
    if any(k in s for k in (
        "tanit", "bastan anlat", "bir daha anlat", "tekrar anlat", "hikaye", "ozellig",
    )):
        return "tanitim"
    return "genel"


def _sahne_urun_bilgisi(dukkan):
    oda = odalar.get(dukkan) or {}
    urun_id = oda.get("aktif_urun_id")
    urun = db.session.get(Urun, urun_id) if urun_id else None
    try:
        pey = float((oda.get("durum") or {}).get("pey") or 0)
    except (TypeError, ValueError):
        pey = 0
    acilis = float(getattr(urun, "acilis_fiyati", 0) or 0) if urun else 0
    guncel = pey if pey > 0 else acilis
    ad = (getattr(urun, "urun_adi", None) or "Eser").strip() if urun else ""
    aciklama = (getattr(urun, "tanitim_yazisi", None) or "").strip() if urun else ""
    return urun, ad, aciklama, int(round(acilis)), int(round(guncel))


def _sayaci_tetikle(dukkan):
    oda = odalar[dukkan]
    if not oda.get("aktif_urun_id"):
        return False
    with oda_kilitleri[dukkan]:
        urun = db.session.get(Urun, oda["aktif_urun_id"])
        gorev = _pazarligi_hazirla(oda, urun)
    socketio.start_background_task(dukkan_geri_sayim_gorevi, dukkan, gorev, "pazarlik")
    socketio.emit("mezat_basladi_muzik", room=dukkan)
    return True


def _patron_emrini_uygula(dukkan, metin):
    with app.app_context():
        try:
            patron_komut = _patron_papaganini_sil(metin).strip()
            if len(patron_komut) < 2:
                return
            if _patron_emir_turu(patron_komut) == "sayac":
                _sayaci_tetikle(dukkan)
            _urun, ad, aciklama, acilis, guncel = _sahne_urun_bilgisi(dukkan)
            aktif_urun = {
                "baslik": ad or "Antika Eser",
                "aciklama": aciklama or "",
                "baslangic_fiyati": acilis,
                "anlik_fiyat": guncel,
            }
            try:
                yanit = _openai_metin_iste(
                    _patron_sistemi(aktif_urun, patron_komut),
                    "Canlı yayındaki izleyicilere söyleyeceğin anonsu yaz.",
                )
            except Exception as hata:
                print(f"[PATRON] {hata}")
                yanit = ""
            yanit = _patron_papaganini_sil(yanit)
            if not yanit or yanit.casefold() == patron_komut.casefold():
                return
            ahmet_usta_anons_yap(dukkan, yanit, tur="anons")
        finally:
            db.session.remove()


@socketio.on("yonetici_duzeltmesi")
def yonetici_duzeltmesi(veri):
    """Patronun emrini sahneye uygular. Söylenen cümleyi aynen okumaz."""
    try:
        if not session.get("is_admin"):
            return
        veri = veri or {}
        metin = _patron_papaganini_sil(ses_metnini_temizle(veri.get("metin")))
        if len(metin) < 2:
            return
        dukkan = dukkan_dogrula(veri.get("dukkan") or "gumus")
        anahtar = f"{dukkan}:{metin.casefold()}"
        simdi = time.time()
        if simdi - _son_duzeltme_ani.get(anahtar, 0) < 8:
            return
        _son_duzeltme_ani[anahtar] = simdi
        socketio.start_background_task(_patron_emrini_uygula, dukkan, metin)
    finally:
        db.session.remove()


def _patron_sesini_metne_cevir(yol):
    istemci = _openai_ses_istemcisi()
    if istemci is None:
        print("[PATRON SESİ] Ses anlaşılmadı, OpenAI anahtarı yok.")
        return ""
    with open(yol, "rb") as dosya:
        cevap = istemci.audio.transcriptions.create(
            model="whisper-1",
            file=dosya,
            language="tr",
        )
    return str(getattr(cevap, "text", "") or "").strip()


def _patron_sesini_isle(dukkan, ses_b64, uzanti):
    import tempfile
    yol = ""
    try:
        try:
            ham = base64.b64decode(ses_b64)
        except Exception:
            print("[PATRON SESİ] Ses anlaşılmadı, kayıt bozuk.")
            return
        if len(ham) < 80:
            print("[PATRON SESİ] Ses anlaşılmadı, kayıt çok kısa.")
            return
        fd, yol = tempfile.mkstemp(prefix="temp_patron_voice_", suffix="." + uzanti)
        os.close(fd)
        with open(yol, "wb") as dosya:
            dosya.write(ham)
        metin = _patron_papaganini_sil(_patron_sesini_metne_cevir(yol))
        if len(metin) < 2:
            print("[PATRON SESİ] Ses anlaşılmadı, metin boş.")
            return
        print(f"[PATRON SESİ METNE ÇEVRİLDİ]: '{metin}'")
        _patron_emrini_uygula(dukkan, metin)
    except Exception as hata:
        print(f"[PATRON SESİ] Ses anlaşılmadı: {hata}")
    finally:
        if yol and os.path.exists(yol):
            try:
                os.remove(yol)
            except OSError:
                pass


@socketio.on("patron_ses_komut")
def patron_ses_komut(veri):
    """Bas-konuş kaydını Türkçe metne çevirir ve emri sahneye uygular."""
    try:
        if not session.get("is_admin"):
            print("[PATRON SESİ] Yönetici oturumu yok, ses işlenmedi.")
            return
        veri = veri or {}
        ses = str(veri.get("audio") or "")
        if not ses or len(ses) > 8000000:
            print("[PATRON SESİ] Ses anlaşılmadı, paket boş.")
            return
        mime = str(veri.get("mime") or "audio/webm")
        uzanti = "wav" if "wav" in mime else "webm"
        dukkan = dukkan_dogrula(veri.get("room") or veri.get("dukkan") or "gumus")
        socketio.start_background_task(_patron_sesini_isle, dukkan, ses, uzanti)
    finally:
        db.session.remove()


@socketio.on("moderator_ses_parcasi")
def moderator_ses_parcasi(veri):
    """Moderatörün canlı mikrofon parçasını odadaki izleyicilere iletir."""
    try:
        if not session.get("is_admin"):
            return
        veri = veri or {}
        parca = str(veri.get("parca") or "")
        if not parca or len(parca) > 400000:
            return
        dukkan = dukkan_dogrula(veri.get("dukkan") or "gumus")
        try:
            sira = int(veri.get("seq") if veri.get("seq") is not None else (veri.get("sira") or 0))
        except (TypeError, ValueError):
            sira = 0
        try:
            ornek_hizi = int(veri.get("rate") or 16000)
        except (TypeError, ValueError):
            ornek_hizi = 16000
        if ornek_hizi < 8000 or ornek_hizi > 48000:
            ornek_hizi = 16000
        emit(
            "izleyici_moderator_sesi",
            {
                "dukkan": dukkan,
                "parca": parca,
                "mime": str(veri.get("mime") or "audio/webm;codecs=opus")[:80],
                "rate": ornek_hizi,
                "seq": sira,
                "sira": sira,
            },
            to=dukkan,
            broadcast=True,
            include_self=False,
        )
    finally:
        db.session.remove()


@socketio.on("admin_voice_broadcast")
def admin_voice_broadcast(veri):
    """Bas-konuş bitince gelen tek ses kaydını bütün izleyicilere yollar."""
    try:
        veri = veri or {}
        ses = str(veri.get("audio") or "")
        if not ses or len(ses) > 8000000:
            return
        if not session.get("is_admin"):
            print("[MIC SOKET] Yönetici oturumu yok, ses iletilmedi.")
            return
        emit("play_admin_audio_blob", veri, broadcast=True)
        print("[MIC SOKET] Yönetici sesi izleyicilere iletildi.")
    finally:
        db.session.remove()


@socketio.on("admin_voice_chunk")
def admin_voice_chunk(veri):
    """Yönetici mikrofon parçasını gecikmesiz olarak bütün izleyicilere yollar."""
    try:
        if not session.get("is_admin"):
            return
        veri = veri or {}
        parca = veri.get("parca") or veri.get("chunk") or veri.get("data") or ""
        if isinstance(parca, bytes):
            parca = base64.b64encode(parca).decode("ascii")
        parca = str(parca or "")
        if not parca or len(parca) > 400000:
            return
        try:
            sira = int(veri.get("seq") or 0)
        except (TypeError, ValueError):
            sira = 0
        emit(
            "play_admin_voice",
            {
                "parca": parca,
                "mime": str(veri.get("mime") or "audio/webm;codecs=opus")[:80],
                "seq": sira,
            },
            broadcast=True,
            include_self=False,
        )
    finally:
        db.session.remove()


@socketio.on("admin_voice_end")
def admin_voice_end(veri=None):
    try:
        if not session.get("is_admin"):
            return
        emit("play_admin_voice", {"bitti": True}, broadcast=True, include_self=False)
    finally:
        db.session.remove()


@socketio.on("join_dukkan")
def handle_join_dukkan(data):
    try:
        veri = data or {}
        dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
        ad = str(veri.get("isim") or "").strip()[:40] or "Misafir"
        izleyici_adlari[request.sid] = ad
        sokete_kullanici_bagla(request.sid, veri.get("kullanici_id"))
        join_room(dukkan)
        izleyici_sayisini_yayinla()
        emit(
            "izleyici_sayisi_guncelle",
            {
                "dukkan": dukkan,
                "sayi": len(aktif_izleyiciler),
                "isimler": list(dict.fromkeys(izleyici_adlari.values())),
            },
        )
    finally:
        db.session.remove()


@socketio.on("leave_dukkan")
def handle_leave_dukkan(data):
    dukkan = dukkan_dogrula((data or {}).get("dukkan", "gumus"))
    leave_room(dukkan)


@socketio.on("ahmet_ses_iste")
def ahmet_ses_iste(veri):
    veri = veri or {}
    dukkan = dukkan_dogrula(veri.get("dukkan") or "gumus")
    oda = odalar.get(dukkan) or {}
    durum = (oda.get("durum") or {}).get("durum")
    if not oda.get("aktif_urun_id") or durum in ("bosta", "Satıldı", "Pas"):
        return
    metin = str(veri.get("metin") or "").strip()
    if metin:
        ahmet_ses_akisi(dukkan, metin, tur="tanitim", kesinti=False)


@socketio.on('admin_sesli_komut')
def handle_admin_sesli_komut(data):
    dukkan = data.get('dukkan', 'gumus')
    metin = data.get('metin', '').strip()
    if metin:
        # Yönetici sesli konuştuğunda hem yorum olarak bas hem Ahmet Usta anonsunu tetikle
        ahmet_usta_anons_yap(dukkan, metin)


def _kayit_tarih_yaz(kayit):
    tarih = getattr(kayit, "tarih", None)
    if not tarih:
        return "-"
    try:
        return tarih.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(tarih)


@app.route("/elestiri-gonder", methods=["POST"])
def elestiri_gonder():
    veri = request.json or {}
    mesaj = (veri.get("mesaj") or "").strip()
    if len(mesaj) < 3:
        return jsonify({"success": False, "mesaj": "Eleştiri metni çok kısa."})
    try:
        kullanici = db.session.get(Kullanici, int(veri.get("musteri_id") or 0))
    except Exception:
        kullanici = None
    if not kullanici or kullanici.silindi_mi or not bool(getattr(kullanici, "is_verified", False)):
        return jsonify({
            "success": False,
            "kayit_gerekli": True,
            "mesaj": "Görüş bildirmek için giriş yapmalısınız",
        })
    if kullanici_banli_mi(kullanici):
        return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI})
    if metin_uygunsuz_mu(mesaj):
        kullaniciyi_otomatik_banla(kullanici)
        return jsonify({
            "success": False,
            "ban": True,
            "mesaj": "Uygunsuz davranış sebebiyle otomatik engellendi",
        })

    hedef = (veri.get("hedef") or "Mezat").strip()
    if hedef not in ("Ürün", "Satıcı", "Mezat"):
        hedef = "Mezat"
    dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
    musteri = (veri.get("musteri") or "Misafir").strip()[:100]

    kayit = Elestiri(
        musteri_adi=musteri,
        hedef=hedef,
        mesaj=mesaj[:2000],
        durum="Yeni",
        ip_adresi=get_client_ip(),
        dukkan_kodu=dukkan,
    )
    db.session.add(kayit)
    db.session.commit()

    paket = {
        "id": kayit.id,
        "kaynak": "elestiri",
        "tarih": _kayit_tarih_yaz(kayit),
        "musteri_adi": kayit.musteri_adi,
        "tur": "Eleştiri",
        "konu": kayit.hedef,
        "mesaj": kayit.mesaj,
        "ip": kayit.ip_adresi or "-",
        "durum": kayit.durum,
        "dukkan": dukkan,
    }
    socketio.emit("yeni_elestiri", paket, room="admin_room")
    return jsonify({"success": True, "mesaj": "Eleştiriniz yönetici arşivine iletildi."})


@app.route("/sikayet-oneri-listele", methods=["GET"])
@admin_required
def sikayet_oneri_listele():
    satirlar = []
    for kayit in SikayetOneri.query.order_by(SikayetOneri.id.desc()).limit(80).all():
        satirlar.append({
            "id": kayit.id,
            "kaynak": "sikayet",
            "tarih": _kayit_tarih_yaz(kayit),
            "musteri_adi": kayit.musteri_adi or "Misafir",
            "tur": kayit.tur or "Görüş",
            "konu": kayit.konu or "-",
            "mesaj": kayit.mesaj or "",
            "ip": kayit.ip_adresi or "-",
            "durum": kayit.durum or "Yeni",
        })
    for kayit in Elestiri.query.order_by(Elestiri.id.desc()).limit(80).all():
        satirlar.append({
            "id": kayit.id,
            "kaynak": "elestiri",
            "tarih": _kayit_tarih_yaz(kayit),
            "musteri_adi": kayit.musteri_adi or "Misafir",
            "tur": "Eleştiri",
            "konu": kayit.hedef or "Mezat",
            "mesaj": kayit.mesaj or "",
            "ip": kayit.ip_adresi or "-",
            "durum": kayit.durum or "Yeni",
        })
    return jsonify(satirlar)


@app.route("/sikayet-oneri-sil", methods=["POST"])
@admin_required
def sikayet_oneri_sil():
    veri = request.json or {}
    kaynak = veri.get("kaynak") or "sikayet"
    try:
        kayit_id = int(veri.get("id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})
    model = Elestiri if kaynak == "elestiri" else SikayetOneri
    kayit = db.session.get(model, kayit_id)
    if not kayit:
        return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})
    db.session.delete(kayit)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/itiraz-duzelt-bildir", methods=["POST"])
@admin_required
def itiraz_duzelt_bildir():
    veri = request.json or {}
    kaynak = veri.get("kaynak") or "sikayet"
    try:
        kayit_id = int(veri.get("id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})
    model = Elestiri if kaynak == "elestiri" else SikayetOneri
    kayit = db.session.get(model, kayit_id)
    if not kayit:
        return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})
    kayit.durum = "Düzeltildi"
    db.session.commit()
    return jsonify({"success": True})


aktif_izleyiciler = set()
izleyici_adlari = {}


def sahneyi_herkese_yayinla(dukkan, urun_paket, pey_paket=None, yeni_urun=False):
    """Sahne değişimini odaya ve bağlı tüm istemcilere duyurur."""
    if yeni_urun:
        _sahne_sesini_kes(dukkan)
        socketio.emit(
            "yeni_urun_sahneye_alindi",
            {"dukkan": dukkan, "urun_id": (urun_paket or {}).get("id")},
            room=dukkan,
        )
    socketio.emit("yeni_sahne_urunu", urun_paket, room=dukkan)
    socketio.emit("yeni_sahne_urunu", urun_paket)
    if pey_paket:
        socketio.emit("pey_guncellendi", pey_paket, room=dukkan)
        socketio.emit("pey_guncellendi", pey_paket)


def izleyici_sayisini_yayinla():
    sayi = len(aktif_izleyiciler)
    isimler = []
    for ad in izleyici_adlari.values():
        temiz = str(ad or "").strip()
        if temiz and temiz not in isimler:
            isimler.append(temiz)
    for kod in DUKKANLAR:
        socketio.emit(
            "izleyici_sayisi_guncelle",
            {"dukkan": kod, "sayi": sayi, "isimler": isimler},
            room=kod,
        )


def _arkaplan_agir_baslangic():
    """Model, veritabanı onarımı ve ses hazırlığı dinlemeyi kilitlemesin."""
    try:
        veritabani_tablolari_onar()
    except Exception as hata:
        print(f"Arka plan veritabanı onarımı: {hata}")
    if getattr(Config, "OPENAI_API_KEY", ""):
        print(f"Canlı ses akışı hazır: OpenAI {Config.OPENAI_TTS_VOICE}.")
    else:
        print("[SES] OPENAI_API_KEY tanımlı değil.")

# ==========================================
# PAZARYERİ VE TEDARİKÇİ MODÜLÜ BAĞLANTISI
# ==========================================
try:
    from pazaryeri import init_pazaryeri, pazaryeri_bp
    init_pazaryeri(app, db, admin_required)
    app.register_blueprint(pazaryeri_bp, url_prefix="/admin/pazaryeri")
except Exception:
    pass

# ==========================================
# SUNUCUYU BAŞLATMA
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    threading.Thread(
        target=_arkaplan_agir_baslangic,
        daemon=True,
        name="halic-baslangic",
    ).start()
    print(f"🚀 Haliç Antik Çok Dükkanlı Mezat Portalı http://127.0.0.1:{port} adresinde başlatılıyor...")
    socketio.run(app, host="0.0.0.0", port=port, debug=True, use_reloader=False)
