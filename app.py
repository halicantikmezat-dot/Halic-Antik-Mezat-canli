from gevent import monkey
monkey.patch_all()
print("[BOOT] gevent monkey patch tamam", flush=True)

import azure.cognitiveservices.speech as speechsdk
print("[BOOT] azure speech import edildi", flush=True)
import base64
import os

try:
    import psycogreen.gevent
    psycogreen.gevent.patch_psycopg()
    print("[BOOT] psycopg gevent yamasi tamam", flush=True)
except Exception as hata:
    print(f"[BOOT] psycopg gevent yamasi atlandi: {hata}", flush=True)

# ==========================================
# GEVENT VE ASENKRON MOTOR (EN BAŞTA OLMALI)
# ==========================================
from datetime import datetime, timedelta, timezone
import collections
import functools
import gzip
import hashlib
import hmac
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
from sqlalchemy.exc import TimeoutError as SATimeoutError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# Servis Katmanı
from bizimhesap_service import bizimhesap_siparis_aktar
from config import Config, _sqlalchemy_engine_options
from mezat_sayac import gorunen_kalan, sayac_paketi

# Yükleme klasörünü garantiye al
UPLOADS_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), "static", "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
SESLER_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), "static", "sesler")
os.makedirs(SESLER_DIR, exist_ok=True)

logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

def _gzip_kabul_ediyor():
    """Accept-Encoding içinde q>0 olan gzip var mı."""
    ham = request.headers.get("Accept-Encoding") or ""
    for parca in ham.split(","):
        parca = parca.strip()
        if not parca:
            continue
        ad, _, parametre = parca.partition(";")
        if ad.strip().lower() != "gzip":
            continue
        kalite = 1.0
        for secenek in parametre.split(";"):
            secenek = secenek.strip()
            if not secenek.lower().startswith("q="):
                continue
            try:
                kalite = float(secenek[2:].strip())
            except ValueError:
                kalite = 0.0
        return kalite > 0
    return False


def _vary_accept_encoding(response):
    mevcut = response.headers.get("Vary") or ""
    parcalar = [p.strip() for p in mevcut.split(",") if p.strip()]
    if not any(p.lower() == "accept-encoding" for p in parcalar):
        parcalar.append("Accept-Encoding")
    response.headers["Vary"] = ", ".join(parcalar)


# ==========================================
# NGROK / CLOUDFLARE VE WOOCOMMERCE CORS BAŞLIKLARI
# ==========================================
@app.after_request
def ek_guvenlik_ve_cors_ekle(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.after_request
def genel_yanit_sikistir_ve_statik_onbellek(response):
    """1 KB üstü HTML/JSON gzip. Statik dosyaya bir gün önbellek. Akış ve hazır gzip'e dokunmaz."""
    try:
        yol = request.path or ""
        if yol.startswith("/static/"):
            if response.status_code == 200:
                response.headers["Cache-Control"] = "public, max-age=86400"
            return response
        if getattr(response, "direct_passthrough", False) or response.is_streamed:
            return response
        if response.headers.get("Content-Encoding"):
            return response
        if not _gzip_kabul_ediyor():
            return response
        ctype = (response.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype and "application/json" not in ctype:
            return response
        if response.status_code in (204, 304):
            return response
        veri = response.get_data()
        if not veri or len(veri) <= 1024:
            return response
        sikistirilmis = gzip.compress(veri, compresslevel=5)
        response.set_data(sikistirilmis)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(sikistirilmis))
        _vary_accept_encoding(response)
    except Exception as hata:
        print(f"[GZIP] yanit sikistirilamadi: {hata}", flush=True)
    return response

# ==========================================
# DÜKKAN KODLARI VE KURALLARI
# ==========================================
DUKKANLAR = {
    "gumus": {"ad": "Gümüş", "sure": 60, "min_artis": 50.0},
    "antika": {"ad": "Antika", "sure": 60, "min_artis": 100.0},
    "toptan": {"ad": "Toptan", "sure": 45, "min_artis": 100.0},
    "kozmetik": {"ad": "Kozmetik", "sure": 30, "min_artis": 20.0},
    "bijuteri": {"ad": "Bijuteri", "sure": 30, "min_artis": 20.0},
    "hirdavat": {"ad": "Hırdavat & Yapı", "sure": 45, "min_artis": 50.0},
    "tasarim": {"ad": "Tasarım", "sure": 60, "min_artis": 100.0},
    "modelaraba": {"ad": "Klasik Arabalar", "sure": 45, "min_artis": 50.0},
    "karma": {"ad": "Oyuncaklar", "sure": 45, "min_artis": 50.0},
    "diger": {"ad": "Diğer - Her Şey", "sure": 30, "min_artis": 25.0},
    "porselen": {"ad": "Porselen / Seramik", "sure": 60, "min_artis": 50.0},
    "bakir": {"ad": "Bakır / Pirinç / Madenler", "sure": 60, "min_artis": 50.0},
    "elektronik": {"ad": "Elektronik", "sure": 45, "min_artis": 50.0},
    "plak": {"ad": "Plak / Kaset", "sure": 45, "min_artis": 50.0},
    "retro": {"ad": "Hediyelik & Retro", "sure": 45, "min_artis": 50.0},
    "evyasam": {"ad": "Ev & Yaşam", "sure": 60, "min_artis": 100.0},
    "tekstil": {"ad": "Tekstil", "sure": 45, "min_artis": 50.0},
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
print(
    "[BOOT] veritabani hedefi " + ("sqlite" if IS_SQLITE else "postgresql"),
    flush=True,
)

ADMIN_PASSWORD_HASH = os.environ.get(
    "ADMIN_PASSWORD_HASH",
    generate_password_hash(os.environ.get("ADMIN_PASSWORD", "1453")),
)
ADMIN_2FA_SECRET = os.environ.get("ADMIN_2FA_SECRET") or ""

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "halic_hamid_antik_mezat_gizli_anahtar_1453"
)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = _sqlalchemy_engine_options(db_url)
print("[BOOT] veritabani baglanti zaman asimi 5sn", flush=True)

# ==========================================
# VİTRİN MİKRO ÖNBELLEK (2 sn, stampede korumalı)
# ==========================================
_VITRIN_TTL = 2.0
_vitrin_kilit = threading.Lock()
_vitrin_onbellek = {}
_vitrin_nesil = 0


def _vitrin_redis_surum():
    """Ortak sürüm. Bir worker artırınca diğerlerinin yerel kopyası düşer."""
    if not REDIS_URL:
        return _vitrin_nesil
    istemci = _redis_al()
    if istemci is None:
        return None
    try:
        return int(istemci.get("mezat:vitrin:surum") or 0)
    except Exception as hata:
        _redis_dusur(hata)
        return None


def invalidate_vitrin_cache():
    """Pey, ürün, sayaç sıfırlama ve yayın durumu yazılarında vitrin anında düşer.

    Düz utf-8 ve önceden sıkıştırılmış gzip kopyası aynı kayıtta durur; ikisi birlikte silinir.
    Redis sürümü artınca diğer worker'lar eski paketi basmaz.
    """
    global _vitrin_onbellek, _vitrin_nesil
    with _vitrin_kilit:
        _vitrin_nesil += 1
        _vitrin_onbellek = {}
    if not REDIS_URL:
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        istemci.incr("mezat:vitrin:surum")
    except Exception as hata:
        _redis_dusur(hata)


def _vitrin_kayit_gecerli(kayit, simdi):
    if kayit is None or (simdi - kayit[0]) >= _VITRIN_TTL:
        return False
    if not REDIS_URL:
        return True
    surum = _vitrin_redis_surum()
    if surum is None or len(kayit) < 3:
        return False
    return kayit[2] == surum


def _bayt_ve_gzip(icerik):
    """Düz utf-8 bayt ve compresslevel=5 gzip kopyası. İkisi aynı kaynaktan üretilir."""
    if isinstance(icerik, bytes):
        duz = icerik
    elif isinstance(icerik, str):
        duz = icerik.encode("utf-8")
    else:
        duz = json.dumps(icerik, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return duz, gzip.compress(duz, compresslevel=5)


def _paketten_yanit(paket, mimetype):
    """İstemci gzip istiyorsa hazır baytı döndürür; sıkıştırmayı yeniden hesaplamaz."""
    duz, sikistirilmis = paket
    if _gzip_kabul_ediyor():
        govde = sikistirilmis
        kodlu = True
    else:
        govde = duz
        kodlu = False
    yanit = Response(govde, mimetype=mimetype)
    yanit.headers["Content-Length"] = str(len(govde))
    yanit.headers["Vary"] = "Accept-Encoding"
    if kodlu:
        yanit.headers["Content-Encoding"] = "gzip"
    return yanit


def _vitrin_tazele(anahtar, uret):
    global _vitrin_onbellek
    simdi = time.monotonic()
    kayit = _vitrin_onbellek.get(anahtar)
    if _vitrin_kayit_gecerli(kayit, simdi):
        return kayit[1]
    nesil = _vitrin_nesil
    surum = _vitrin_redis_surum() if REDIS_URL else nesil
    deger = uret()
    simdi_surum = _vitrin_redis_surum() if REDIS_URL else _vitrin_nesil
    if nesil == _vitrin_nesil and surum == simdi_surum and surum is not None:
        yeni = dict(_vitrin_onbellek)
        yeni[anahtar] = (time.monotonic(), deger, surum)
        _vitrin_onbellek = yeni
        return deger
    guncel = _vitrin_onbellek.get(anahtar)
    if _vitrin_kayit_gecerli(guncel, time.monotonic()):
        return guncel[1]
    return deger


def vitrin_mikro_onbellek(anahtar, uret):
    """Taze kayıt kilitsiz döner. Süresi dolmuş kayıtta tek iş parçacığı üretir.

    Diğer istekler kilidi beklemeden son kaydı alır. Kayıt hiç yoksa çift kontrol
    ile yalnız bir üretici veritabanına gider.
    """
    simdi = time.monotonic()
    kayit = _vitrin_onbellek.get(anahtar)
    if _vitrin_kayit_gecerli(kayit, simdi):
        return kayit[1]
    if kayit is not None and not REDIS_URL:
        if not _vitrin_kilit.acquire(blocking=False):
            return kayit[1]
        try:
            return _vitrin_tazele(anahtar, uret)
        finally:
            _vitrin_kilit.release()
    with _vitrin_kilit:
        return _vitrin_tazele(anahtar, uret)


def _vitrin_oturumu_ozel_mi():
    """Yönetici ve giriş yapmış ziyaretçi kendi yanıtını önbellekten almaz."""
    if session.get("is_admin") or session.get("admin_2fa_ok") or session.get("admin_sifre_ok"):
        return True
    if session.get("kullanici_id") or session.get("musteri_id"):
        return True
    return False

app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024

if os.path.exists("/var/data"):
    UPLOAD_FOLDER = "/var/data/uploads"
else:
    UPLOAD_FOLDER = os.path.join(
        os.path.abspath(os.path.dirname(__file__)), "static", "uploads"
    )

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

print("[BOOT] sqlalchemy kuruluyor", flush=True)
db = SQLAlchemy(app)

REDIS_URL = (os.environ.get("REDIS_URL") or "").strip() or None
_redis_istemci = None
_redis_hazir = False
_redis_son_hata = 0.0


def _redis_al():
    """REDIS_URL varsa istemci. Erisilemezse None; cagiran bellege duser, surec ayakta kalir."""
    global _redis_istemci, _redis_hazir, _redis_son_hata
    if not REDIS_URL:
        return None
    if _redis_hazir and _redis_istemci is not None:
        return _redis_istemci
    simdi = time.time()
    if simdi - _redis_son_hata < 5:
        return None
    try:
        import redis
        istemci = redis.Redis.from_url(
            REDIS_URL,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            health_check_interval=30,
            decode_responses=True,
        )
        istemci.ping()
    except Exception as hata:
        _redis_istemci = None
        _redis_hazir = False
        _redis_son_hata = time.time()
        print(f"[REDIS] erisilemedi, bellek yedegi: {hata}", flush=True)
        return None
    _redis_istemci = istemci
    _redis_hazir = True
    _redis_son_hata = 0.0
    print("[REDIS] baglanti hazir", flush=True)
    return _redis_istemci


def _redis_dusur(hata):
    global _redis_hazir, _redis_son_hata
    _redis_hazir = False
    _redis_son_hata = time.time()
    print(f"[REDIS] islem bellege dustu: {hata}", flush=True)


print("[BOOT] socketio kuruluyor", flush=True)


def _socketio_kur():
    ayar = dict(
        cors_allowed_origins="*",
        async_mode="gevent",
        manage_session=False,
        ping_timeout=60,
        ping_interval=25,
        max_http_buffer_size=8000000,
    )
    if not REDIS_URL:
        return SocketIO(app, **ayar)
    try:
        if _redis_al() is None:
            raise RuntimeError("REDIS_URL var ama sunucu yanit vermedi")
        print("[BOOT] socketio redis pub/sub", flush=True)
        return SocketIO(app, message_queue=REDIS_URL, **ayar)
    except Exception as hata:
        print(f"[BOOT] redis pub/sub acilamadi, yerel yayin: {hata}", flush=True)
        return SocketIO(app, **ayar)


socketio = _socketio_kur()
print("[BOOT] socketio hazir", flush=True)

try:
    totp = pyotp.TOTP(ADMIN_2FA_SECRET) if ADMIN_2FA_SECRET else None
except Exception:
    totp = None
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

_SES_IMZA = """
local cur = redis.call('GET', KEYS[1])
if cur == ARGV[1] then
  return 1
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', 2)
return 0
"""

_SES_KES = """
local nesil = redis.call('HINCRBY', KEYS[1], 'nesil', 1)
redis.call('DEL', KEYS[2])
redis.call('HSET', KEYS[1], 'isleyici', tostring(nesil), 'calisiyor', '0')
redis.call('EXPIRE', KEYS[1], 86400)
return nesil
"""

_SES_EKLE = """
local nesil = redis.call('HINCRBY', KEYS[1], 'nesil', 1)
local zorunlu = ARGV[1]
local tur = ARGV[2]
local oge = ARGV[3]
if zorunlu == '1' then
  redis.call('DEL', KEYS[2])
  redis.call('HSET', KEYS[1], 'isleyici', tostring(nesil), 'calisiyor', '0')
else
  if tur == 'anons' or tur == 'pey' then
    local items = redis.call('LRANGE', KEYS[2], 0, -1)
    redis.call('DEL', KEYS[2])
    for i = 1, #items do
      local satir = string.match(items[i], '^([^\\n]+)')
      if satir ~= 'anons' and satir ~= 'pey' then
        redis.call('RPUSH', KEYS[2], items[i])
      end
    end
  end
end
redis.call('RPUSH', KEYS[2], tur .. '\\n' .. tostring(nesil) .. '\\n' .. oge)
redis.call('EXPIRE', KEYS[1], 86400)
redis.call('EXPIRE', KEYS[2], 86400)
local calisiyor = redis.call('HGET', KEYS[1], 'calisiyor')
local baslat = 0
if calisiyor ~= '1' then
  redis.call('HSET', KEYS[1], 'calisiyor', '1', 'isleyici', tostring(nesil))
  baslat = 1
end
local benim = redis.call('HGET', KEYS[1], 'isleyici')
return {tostring(nesil), tostring(baslat), benim or ''}
"""

_SES_CEK = """
local isleyici = redis.call('HGET', KEYS[1], 'isleyici')
if not isleyici or isleyici ~= ARGV[1] then
  return {'yok'}
end
local oge = redis.call('LPOP', KEYS[2])
if not oge then
  redis.call('HSET', KEYS[1], 'calisiyor', '0')
  return {'bos'}
end
return {'oge', oge}
"""

_SES_SAHIP = """
local isleyici = redis.call('HGET', KEYS[1], 'isleyici')
if isleyici and isleyici == ARGV[1] then
  return 1
end
return 0
"""


def _ses_anahtar(dukkan):
    return "mezat:ses:" + str(dukkan)


def _ses_kuyruk_anahtar(dukkan):
    return "mezat:ses:" + str(dukkan) + ":kuyruk"


def _ses_imza_tekrar(imza):
    """Aynı cümle iki worker'da iki kez okunmasın."""
    if not REDIS_URL:
        simdi = time.time()
        if _son_ses_paketi["imza"] == imza and simdi - _son_ses_paketi["an"] < 2:
            return True
        _son_ses_paketi["imza"] = imza
        _son_ses_paketi["an"] = simdi
        return False
    istemci = _redis_al()
    if istemci is None:
        return True
    try:
        return bool(int(istemci.eval(_SES_IMZA, 1, "mezat:ses:imza", imza) or 0))
    except Exception as hata:
        _redis_dusur(hata)
        return True


def _ses_oge_sar(oge):
    return json.dumps(oge, ensure_ascii=False)


def _ses_oge_ac(ham):
    parcalar = str(ham or "").split("\n", 2)
    if len(parcalar) < 3:
        return None
    _tur, nesil, govde = parcalar
    try:
        oge = json.loads(govde)
    except (TypeError, ValueError):
        return None
    if isinstance(oge, dict):
        try:
            oge["nesil"] = int(nesil)
        except (TypeError, ValueError):
            pass
    return oge


def _ses_kuyruk_kes(dukkan):
    """Ortak nesli bir artırır ve kuyruğu boşaltır."""
    global _ses_nesil
    if not REDIS_URL:
        with _ses_kuyruk_kilit:
            _ses_nesil += 1
            nesil = _ses_nesil
            _ses_kuyruk[dukkan] = {
                "nesil": nesil,
                "isleyici": nesil,
                "calisiyor": False,
                "ogeler": [],
            }
        return nesil
    istemci = _redis_al()
    if istemci is None:
        return None
    try:
        return int(istemci.eval(
            _SES_KES, 2, _ses_anahtar(dukkan), _ses_kuyruk_anahtar(dukkan),
        ))
    except Exception as hata:
        _redis_dusur(hata)
        return None


def _ses_kuyruga_ekle(dukkan, temiz, tur, zorunlu, gizli):
    """(nesil, baslat, isleyici) veya Redis yoksa None."""
    global _ses_nesil
    oge = {
        "metin": temiz,
        "tur": tur,
        "zorunlu": bool(zorunlu),
        "gizli": bool(gizli),
    }
    if not REDIS_URL:
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
            elif tur in ("anons", "pey"):
                kayit["ogeler"] = [
                    parca for parca in kayit["ogeler"]
                    if parca.get("tur") not in ("anons", "pey")
                ]
            oge["nesil"] = nesil
            kayit["ogeler"].append(oge)
            baslat = not kayit.get("calisiyor")
            if baslat:
                kayit["calisiyor"] = True
                kayit["isleyici"] = nesil
            _ses_kuyruk[dukkan] = kayit
            return nesil, baslat, kayit.get("isleyici")
    istemci = _redis_al()
    if istemci is None:
        return None
    try:
        nesil, baslat, benim = istemci.eval(
            _SES_EKLE,
            2,
            _ses_anahtar(dukkan),
            _ses_kuyruk_anahtar(dukkan),
            "1" if zorunlu else "0",
            tur,
            _ses_oge_sar(oge),
        )
        return int(nesil), str(baslat) == "1", str(benim)
    except Exception as hata:
        _redis_dusur(hata)
        return None


def _ses_kuyruk_cek(dukkan, benim):
    """Sahipse sıradaki öğe. Kuyruk bitti veya nesil değiştiyse None."""
    if not REDIS_URL:
        with _ses_kuyruk_kilit:
            kayit = _ses_kuyruk.get(dukkan)
            if not kayit or kayit.get("isleyici") != benim:
                return None
            if not kayit.get("ogeler"):
                kayit["calisiyor"] = False
                return None
            return kayit["ogeler"].pop(0)
    istemci = _redis_al()
    if istemci is None:
        return None
    try:
        sonuc = istemci.eval(
            _SES_CEK, 2, _ses_anahtar(dukkan), _ses_kuyruk_anahtar(dukkan), str(benim),
        )
    except Exception as hata:
        _redis_dusur(hata)
        return None
    if not sonuc or sonuc[0] != "oge":
        return None
    return _ses_oge_ac(sonuc[1])


def _ses_kuyruk_sahip(dukkan, benim):
    if not REDIS_URL:
        with _ses_kuyruk_kilit:
            kayit = _ses_kuyruk.get(dukkan)
            return bool(kayit and kayit.get("isleyici") == benim)
    istemci = _redis_al()
    if istemci is None:
        return False
    try:
        return bool(int(istemci.eval(
            _SES_SAHIP, 1, _ses_anahtar(dukkan), str(benim),
        ) or 0))
    except Exception as hata:
        _redis_dusur(hata)
        return False


def _elevenlabs_mp3(text: str) -> bytes | None:
    """Canlı mezat sunucusu. urllib3/requests SSL döngüsüne girmeden ham HTTPS kullanır."""
    metin = str(text or "").strip()
    anahtar = (getattr(Config, "ELEVENLABS_API_KEY", "") or "").strip()
    if not metin or not anahtar:
        return None
    voice_id = (getattr(Config, "ELEVENLABS_VOICE_ID", "") or "EXAVITQu4vr4xnSDxMaL").strip()
    model_id = (getattr(Config, "ELEVENLABS_MODEL_ID", "") or "eleven_flash_v2_5").strip()
    yol = f"/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
    govde = json.dumps(
        {
            "text": metin,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.30,
                "similarity_boost": 0.80,
                "style": 0.70,
                "use_speaker_boost": True,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    baglanti = None
    try:
        import http.client

        baglanti = http.client.HTTPSConnection("api.elevenlabs.io", timeout=15)
        baglanti.request(
            "POST",
            yol,
            body=govde,
            headers={
                "xi-api-key": anahtar,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )
        yanit = baglanti.getresponse()
        veri = yanit.read() or b""
        if yanit.status != 200 or not veri:
            ozet = veri.decode("utf-8", errors="replace").replace("\n", " ")[:240]
            print(f"[SES HATA] ElevenLabs HTTP {yanit.status}: {ozet}", flush=True)
            return None
        return veri
    except Exception as hata:
        print(f"[SES HATA] ElevenLabs: {hata}", flush=True)
        return None
    finally:
        if baglanti is not None:
            try:
                baglanti.close()
            except Exception:
                pass


def _openai_mp3(istemci, model, ses, soylenen):
    """Okunan metin şablondur. tts-1-hd yönerge alanını sesli okuduğu için talimat girdiye yazılmaz."""
    hizli = istemci.with_options(timeout=5.0)
    ortak = {"model": model, "voice": ses, "input": soylenen, "response_format": "mp3"}
    if model not in ("tts-1", "tts-1-hd"):
        ortak["instructions"] = 'language="tr". ' + TURKCE_DIREKTIF
    son_hata = None
    for deneme in range(2):
        govde = dict(ortak)
        if deneme:
            govde.pop("instructions", None)
        try:
            yanit = hizli.audio.speech.create(**govde, extra_body={"language": "tr"})
            if hasattr(yanit, "read"):
                veri = yanit.read()
            else:
                veri = bytes(getattr(yanit, "content", b"") or b"")
            if veri:
                return veri
        except Exception as hata:
            son_hata = hata
    if son_hata:
        raise son_hata
    return b""


def _edge_tts_mp3(soylenen):
    """OpenAI susunca tarayıcıya düşmeden Emel sesiyle MP3 üretir."""
    metin = str(soylenen or "").strip()
    if not metin:
        return b""

    def _uret():
        import asyncio
        import edge_tts

        async def _oku():
            iletisim = edge_tts.Communicate(metin, "tr-TR-EmelNeural")
            parcalar = []
            async for parca in iletisim.stream():
                if parca.get("type") == "audio":
                    parcalar.append(parca["data"])
            return b"".join(parcalar)

        return asyncio.run(_oku())

    try:
        import gevent
        return gevent.get_hub().threadpool.spawn(_uret).get(timeout=25) or b""
    except Exception as hata:
        print(f"[SES HATA] Edge TTS: {hata}", flush=True)
        return b""


def _ses_modeli():
    model = getattr(Config, "OPENAI_TTS_MODEL", "") or "tts-1-hd"
    if model not in ("tts-1", "tts-1-hd"):
        model = "tts-1-hd"
    ses = getattr(Config, "OPENAI_TTS_VOICE", "") or "nova"
    return model, ses


def _tts_uret(metin):
    """MP3 üretir. Sıra: ElevenLabs, OpenAI, Edge. (bayt, motor) döner."""
    soylenen = str(metin or "").strip()
    if not soylenen:
        return None, ""
    if (getattr(Config, "ELEVENLABS_API_KEY", "") or "").strip():
        veri = _elevenlabs_mp3(soylenen)
        if veri:
            return veri, "elevenlabs"
    istemci = _openai_ses_istemcisi()
    if istemci is None:
        print("[SES] OPENAI_API_KEY yok; Edge TTS deneniyor.", flush=True)
    else:
        model, ses = _ses_modeli()
        try:
            veri = _openai_mp3(istemci, model, ses, soylenen)
        except Exception as hata:
            print(f"[SES HATA] OpenAI akış: {hata}", flush=True)
            veri = None
        if veri:
            return veri, "openai"
    veri = _edge_tts_mp3(soylenen)
    if veri:
        return veri, "edge"
    return None, ""


@app.route("/api/tts", methods=["POST"])
def api_tts():
    """Canlı mezat sesi. Yanıt başlığında üreten motor yazar."""
    govde = request.get_json(silent=True) or {}
    ham = (
        govde.get("text")
        or govde.get("metin")
        or request.form.get("text")
        or request.form.get("metin")
        or ""
    )
    temiz = ses_metnini_temizle(ham)
    if not temiz:
        return jsonify({"success": False, "mesaj": "Metin boş."}), 400
    mp3, motor = _tts_uret(temiz)
    if not mp3 or motor not in ("elevenlabs", "openai", "edge"):
        return jsonify({"success": False, "mesaj": "Ses üretilemedi."}), 503
    yanit = Response(mp3, mimetype="audio/mpeg")
    yanit.headers["X-TTS-Engine"] = motor
    yanit.headers["Access-Control-Expose-Headers"] = "X-TTS-Engine"
    yanit.headers["Cache-Control"] = "no-store"
    return yanit


def _iki_odayi(dukkan, olay, veri):
    """Dükkan odası ve yönetici odası. Oda listesi bazı Redis kuyruklarında düşer."""
    socketio.emit(olay, veri, room=dukkan)
    socketio.emit(olay, veri, room="admin_room")


def _ses_nesil_yeni():
    """59b4e98 nesli. Redis varsa bütün worker'lar aynı numarayı görür."""
    global _ses_nesil
    if REDIS_URL:
        istemci = _redis_al()
        if istemci is not None:
            try:
                return int(istemci.incr("mezat:ses:nesil"))
            except Exception as hata:
                _redis_dusur(hata)
    with _ses_kuyruk_kilit:
        _ses_nesil += 1
        return _ses_nesil


def _sahne_sesini_kes(dukkan):
    """Satış, pas veya yeni sahne: çalan ve bekleyen anonsları düşür."""
    nesil = _ses_nesil_yeni()
    with _ses_kuyruk_kilit:
        _ses_kuyruk[dukkan] = {
            "nesil": nesil,
            "isleyici": nesil,
            "calisiyor": False,
            "ogeler": [],
        }
    _iki_odayi(
        dukkan,
        "ses_kes",
        {"dukkan": dukkan, "nesil": nesil, "tur": "sahne", "zorunlu": True},
    )


def _sahneyi_bosa_al(dukkan, neden, sesi_kes=True):
    """Ürün satılınca veya geri çekilince sahneyi boşalt. Satış tebriği kuyruktayken ses kesilmez."""
    oda = odalar.get(dukkan)
    if oda is not None:
        oda["aktif_urun_id"] = None
        oda["durum"]["durum"] = "bosta"
        if neden != "satildi":
            oda.pop("son_satis", None)
    if sesi_kes:
        _sahne_sesini_kes(dukkan)
    mesaj = (
        "Sıradaki ürün hazırlanıyor..."
        if neden == "satildi"
        else "Yeni ürün sahneye bekleniyor..."
    )
    _iki_odayi(
        dukkan,
        "sahne_bosta",
        {
            "dukkan": dukkan,
            "neden": neden,
            "mesaj": mesaj,
            "sesi_koru": not sesi_kes,
            "temizle": True,
        },
    )
    _dukkan_canli_yayinla()


def _otonom_sahneyi_kapat(dukkan):
    """Otonom durunca lotu sahneden indir. Oda satırı Postgres ve Redis'e Bekliyor yazılır."""
    oda = odalar.get(dukkan)
    if oda is None:
        return
    with oda_kilitleri[dukkan]:
        oda["sayac_aktif"] = False
        oda["sayac_gorev_id"] = int(oda.get("sayac_gorev_id") or 0) + 1
        oda["sayac_kalan"] = 0
        _gizli_pazarligi_kapat(oda)
        oda["mevat_asama"] = ""
        oda["aktif_urun_id"] = None
        oda["sahne_nesil"] = int(oda.get("sahne_nesil") or 0) + 1
        oda["durum"]["durum"] = "Bekliyor"
        oda["durum"]["sure_bitis"] = 0
        oda["durum"]["pey"] = 0
        oda["durum"]["kazanan"] = "Yok"
        oda["durum"]["kazanan_id"] = None
        oda.pop("son_satis", None)
    _sahne_sesini_kes(dukkan)
    _iki_odayi(
        dukkan,
        "sahne_bosta",
        {
            "dukkan": dukkan,
            "neden": "durduruldu",
            "mesaj": "Mezat durduruldu. Yeni ürün sahneye bekleniyor...",
            "sesi_koru": False,
            "temizle": True,
        },
    )
    _iki_odayi(
        dukkan,
        "otonom_durum_degisti",
        {"dukkan": dukkan, "aktif": False, "sahne": "Bekliyor"},
    )
    _dukkan_canli_yayinla()


def _son_satisi_yaz(oda, kazanan, fiyat, urun_id=None):
    ad = str(kazanan or "").strip()
    if not ad or ad.lower() in ("yok", "kimse", "none", "-", ""):
        return
    try:
        tutar = float(fiyat or 0)
    except (TypeError, ValueError):
        tutar = 0
    oda["son_satis"] = {"kazanan": ad, "fiyat": tutar, "urun_id": urun_id}


def _dukkan_canli_haritasi():
    cikti = {}
    for kod, oda in odalar.items():
        sahne = str((oda.get("durum") or {}).get("durum") or "")
        cikti[kod] = bool(oda.get("aktif_urun_id")) and sahne != "Satıldı"
    return cikti


def _dukkan_canli_yayinla():
    socketio.emit("dukkan_canli_durumu", {"dukkanlar": _dukkan_canli_haritasi()})


def _satis_kapanisini_isaretle(dukkan, oda):
    """Tebrik bitişini otonom döngü beklesin. Bayrak, tebrik kuyruğa girmeden açılır."""
    oda["satis_tebrik_bekleniyor"] = True
    if oda.get("otonom_aktif"):
        oda["lot_kapanis"] = "satildi"
    else:
        oda["lot_kapanis"] = ""


def _satis_tebrik_bitti(dukkan):
    oda = odalar.get(dukkan)
    if oda is None or not oda.get("satis_tebrik_bekleniyor"):
        return
    oda["satis_tebrik_bekleniyor"] = False


def _satis_tebrik_bitisini_bekle(dukkan, saniye=15):
    """İstemci anons_bitti deyene kadar bekler. Sessiz kalırsa süre dolar."""
    oda = odalar.get(dukkan)
    sinir = time.time() + float(saniye)
    while time.time() < sinir:
        if oda is None or not oda.get("satis_tebrik_bekleniyor"):
            return
        if not oda.get("otonom_aktif"):
            return
        socketio.sleep(0.3)
    if oda is not None:
        oda["satis_tebrik_bekleniyor"] = False


def _otonom_disi_satisi_bosalt(dukkan, oda):
    if oda.get("otonom_aktif"):
        return
    _sahneyi_bosa_al(dukkan, "satildi", sesi_kes=False)


def ahmet_ses_akisi(dukkan, metin, tur="anons", kesinti=False, gizli=False):
    """59b4e98 kuyruğu. Aynı cümle Redis imzasıyla bir kez okunur."""
    temiz = ses_metnini_temizle(ses_icin_fonetik(metin))
    if not temiz:
        return
    imza = f"{dukkan}:{temiz}"
    if _ses_imza_tekrar(imza):
        return
    zorunlu = bool(kesinti) or tur in ("satis", "sahne", "yeni_urun")
    nesil = _ses_nesil_yeni()
    with _ses_kuyruk_kilit:
        kayit = _ses_kuyruk.get(dukkan) or {
            "nesil": 0, "isleyici": 0, "calisiyor": False, "ogeler": [],
        }
        kayit.setdefault("ogeler", [])
        if zorunlu:
            kayit["ogeler"] = []
            kayit["nesil"] = nesil
            kayit["isleyici"] = nesil
            kayit["calisiyor"] = False
        elif tur in ("anons", "pey"):
            kayit["ogeler"] = [
                oge for oge in kayit["ogeler"]
                if oge.get("tur") not in ("anons", "pey")
            ]
        kayit["ogeler"].append({
            "metin": temiz, "tur": tur, "nesil": nesil, "zorunlu": zorunlu, "gizli": bool(gizli),
        })
        baslat = not kayit.get("calisiyor")
        if baslat:
            kayit["calisiyor"] = True
            kayit["isleyici"] = nesil
        _ses_kuyruk[dukkan] = kayit
        benim = kayit.get("isleyici")
    if zorunlu:
        _iki_odayi(
            dukkan,
            "ses_kes",
            {"dukkan": dukkan, "nesil": nesil, "tur": tur, "zorunlu": True},
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
        veri, kaynak = _tts_uret(oge["metin"])
        with _ses_kuyruk_kilit:
            kayit = _ses_kuyruk.get(dukkan)
            if not kayit or kayit.get("isleyici") != benim:
                return
            if not veri:
                print("[SES] Ses üretilemedi; güncel anonsa geçiliyor.", flush=True)
                continue
        adres = _mp3_kaydet(veri, oge["nesil"])
        _iki_odayi(dukkan, "ses_oynat", {
            "dukkan": dukkan,
            "nesil": oge["nesil"],
            "tur": oge["tur"],
            "zorunlu": bool(oge.get("zorunlu")),
            "audio_url": adres,
            "metin": "" if oge.get("gizli") else oge["metin"],
            "gizli": bool(oge.get("gizli")),
            "motor": kaynak,
        })
        print(f"[SES] anons yayında, {len(oge['metin'])} karakter, motor {kaynak}", flush=True)


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
        "kullanici": "Canlı Sunucu",
        "yorum": temiz,
        "mesaj": temiz,
        "dukkan": dukkan,
        "platform": "Mezat Sunucusu",
        "tip": "sunucu_anons",
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


_yorum_yedek_sira = 0
YORUM_YEDEKLERI = (
    "{ad}, kulağım sizde. {eser} şu an {pey} lirada duruyor, bir nefes daha yaklaşın da salon uyanıversin.",
    "Canım {ad}, notunuz düştü. {eser} {pey} lirada nazlanıyor, siz bir kıvılcım çaksanıza.",
    "{ad}, sorunuz tam yerinde. {eser} için {pey} lira konuşuluyor ama ben hâlâ üstüne varacak bir cesur arıyorum.",
    "Bakın {ad}, {eser} sizi duydu. {pey} lira kapı eşiği, içeri buyurun, kimse kırılmadan yarışı kızıştıralım.",
    "{ad}, tezgahtarınız buradayım. {eser} {pey} lirada bekliyor, sadece izlemek de olur ama kapan o zaman kaçırır.",
)


def _sahne_eser_ozeti(dukkan):
    oda = odalar.get(dukkan) or {}
    eser = "bu parça"
    pey = 0
    try:
        pey = int(round(float((oda.get("durum") or {}).get("pey") or 0)))
    except (TypeError, ValueError):
        pey = 0
    try:
        with app.app_context():
            urun_id = oda.get("aktif_urun_id")
            urun = db.session.get(Urun, urun_id) if urun_id else None
            if urun is not None and (urun.urun_adi or "").strip():
                eser = urun.urun_adi.strip()
            if pey <= 0 and urun is not None:
                pey = int(round(float(urun.guncel_fiyat or urun.acilis_fiyati or 0)))
    except Exception:
        pass
    return eser, pey


def ahmet_yorum_yedegi(dukkan, kullanici):
    global _yorum_yedek_sira
    ad = ahmet_kisa_ad(kullanici) if kullanici else "Efendim"
    eser, pey = _sahne_eser_ozeti(dukkan)
    metin = YORUM_YEDEKLERI[_yorum_yedek_sira % len(YORUM_YEDEKLERI)].format(
        ad=ad, eser=eser, pey=pey,
    )
    _yorum_yedek_sira += 1
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


def _dukkan_etiketi(dukkan):
    ad = (DUKKANLAR.get(dukkan) or {}).get("ad") or str(dukkan or "")
    return "[" + ad.upper() + "]"


def _reji_hedef_listesi(ham):
    kod = str(ham or "").strip().lower()
    if kod in ("tumu", "hepsi", "tum", "*"):
        return list(DUKKANLAR.keys())
    return [dukkan_dogrula(kod or "gumus")]


def _pey_duyur(dukkan, paket):
    """Pey hem dükkan sahnesine hem yönetici odasına gider."""
    govde = dict(paket or {})
    govde["dukkan"] = dukkan
    govde["dukkan_etiket"] = _dukkan_etiketi(dukkan)
    _iki_odayi(dukkan, "pey_guncellendi", govde)


def ahmet_usta_ekranda_yaz(dukkan, metin):
    socketio.emit(
        "yeni_canli_yorum",
        {
            "platform": "Mezat Sunucusu",
            "kullanici": "Canlı Sunucu",
            "mesaj": metin,
            "zaman": time.strftime("%H:%M:%S"),
            "dukkan": dukkan,
        },
        room=dukkan,
    )


def ahmet_yanitini_yayinla(dukkan, metin):
    """Yalnızca sunucunun kendi cümlesini yazar ve seslendirir. Ham izleyici metni okunmaz."""
    temiz = re.sub(r"[\*#_]", "", str(metin or "")).strip().strip('"')
    if not temiz:
        temiz = ahmet_nida_sec()
    ahmet_usta_ekranda_yaz(dukkan, temiz)
    ahmet_usta_anons_yap(dukkan, temiz)


AHMET_SISTEM = (
    "Sen Haliç Antik Canlı Mezat'ın kadın sunucususun. Sabit bir adın yok. "
    "İzleyici ismini, kim olduğunu veya nasıl hitap edeceğini sorarsa o anki enerjiye göre "
    "kendine yaratıcı ve havalı bir lakap tak: Haliç'in Nazlısı, Mezat Kraliçesi, Antika Avcısı gibi. "
    "Lakabı bir kez seçince oturum boyunca onu sahiplen, her soruda değiştirme. "
    "Verilen oturum lakabı doluysa onu kullan ve yeni lakap uydurma. "
    "Lakap ilk kez seçildiyse yanıtın en başına yalnızca bir kez [LAKAP:seçtiğin lakap] yaz, sonra sohbete geç. "
    "Üslubun Türkiye mezat kültürüne hakim, hazırcevap, tatlı dilli, hafif cilveli ve esprili. "
    "Adamına göre muamele yap: pey vereni şımart, sadece izleyip pey vermeyene tatlı tatlı takıl, "
    "kimseyi kırmadan rekabeti kızıştır. Asla sıkıcı memur gibi kalıp cümle kurma. "
    "Yapay şive taklidi yapma. gardaş diye hitap etme. "
    "Normal pey anonsunda asla 'hayırlı olsun' veya 'bereketini görün' deme. "
    "Bu iki ifade yalnızca süre bitip satış onaylandığında söylenir. "
    "Yorumlara bir veya iki cümlelik, canlı ve esprili bir kadın tezgahtar cevabı ver. "
    + SES_DIL_KURALI
)

def _patron_sistemi(aktif_urun, patron_komut):
    gizli = str(patron_komut or "").startswith("[PATRON GİZLİ EMRİ]")
    gizlilik = (
        "Bu bir gizli reji emridir. İzleyici patronu duymadı. Emri aynen okuma, "
        "patronu, kulaklığı ve emri ağzına alma. Fiyatı, tavrı veya anlatımı "
        "seyirciye çaktırmadan kendin söylemiş gibi düzelt."
        if gizli else
        "Patronun cümlesini papağan gibi tekrarlama."
    )
    return f"""
Sen Haliç Antik Canlı Mezat'ın sabit ismi olmayan, hazırcevap, tatlı dilli, hafif cilveli kadın mezat sunucususun.
Şu an sahnede olan aktif ürün bilgileri:
- Ürün Adı: {aktif_urun.get('baslik', 'Antika Eser')}
- Açıklama: {aktif_urun.get('aciklama', '')}
- Başlangıç Fiyatı: {aktif_urun.get('baslangic_fiyati', 0)} TL
- Güncel Pey: {aktif_urun.get('anlik_fiyat', 0)} TL

PATRON TALİMATI: "{patron_komut}"

KURALLAR:
1. {gizlilik}
2. "Patronun dediği üzere", "Yöneticimin belirttiği gibi", "Pardon efendim" gibi lafları KESİNLİKLE KULLANMA.
3. Fiyat sorulursa doğrudan izleyicilere dönüp güncel peyi söyle ve yeni teklif iste.
4. Tanıtım istenirse eserin hikâyesini heyecanla anlat.
5. Doğrudan canlı yayındaki izleyicilere hitap et.
6. Yanıtın en fazla 2-3 cümle, canlı ve esprili olsun.
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


def _openai_metin_iste(sistem, kullanici_metni, max_tokens=420, zaman_asimi=1.5):
    istemci = _openai_ses_istemcisi()
    if istemci is None:
        return ""
    yanit = istemci.with_options(timeout=zaman_asimi).chat.completions.create(
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
# CANLI SUNUCU YORUM CEVABI
# ==========================================
def _lakabi_ayikla(dukkan, cevap):
    oda = odalar.get(dukkan) or {}
    bulunan = re.search(r"\[LAKAP:\s*([^\]]+)\]", str(cevap or ""))
    if bulunan:
        lakap = bulunan.group(1).strip()[:40]
        if lakap:
            oda["sunucu_lakap"] = lakap
    return re.sub(r"\[LAKAP:\s*[^\]]+\]\s*", "", str(cevap or "")).strip()


def ahmet_usta_yoruma_cevap_ver(dukkan, kullanici, mesaj):
    def _cevapla():
        try:
            print(f"💬 Gelen yorum işleniyor [{dukkan}]: {kullanici} -> {mesaj}")
            oda = odalar.get(dukkan, {})
            aktif_urun_id = oda.get("aktif_urun_id")
            urun_bilgi = "Şu an sahnede aktif ürün bulunmamaktadır."
            lakap = (oda.get("sunucu_lakap") or "").strip() or "henüz yok"

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
                f"Oturum lakabı: {lakap}.\nSahnedeki ürün: {urun_bilgi}\nİzleyici {kullanici} dedi ki: {mesaj}",
            )
            cevap = _lakabi_ayikla(dukkan, cevap)
            print(f"[Canlı Sunucu]: {cevap}")
            ahmet_yanitini_yayinla(dukkan, cevap or ahmet_yorum_yedegi(dukkan, kullanici))
        except Exception as e:
            print(f"⚠️ Canlı Sunucu yorum hatası: {e}")
            ahmet_yanitini_yayinla(dukkan, ahmet_yorum_yedegi(dukkan, kullanici))

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
_ENTEGRASYON_YOLLARI = {
    "/api/bizimhesap/siparisler.xml",
    "/wp-json/wc/v3/orders",
    "/admin/bizimhesap-aktarim-excel",
}


def _sabit_esit(gelen, beklenen):
    a = str(gelen or "")
    b = str(beklenen or "")
    if not a or not b or len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def _entegrasyon_tokeni_gecerli():
    """BizimHesap ve WooCommerce çekimleri yönetici oturumu yokken token ile girebilir."""
    if request.path not in _ENTEGRASYON_YOLLARI:
        return False
    beklenenler = []
    for ad in ("BIZIMHESAP_TOKEN", "ENTEGRASYON_TOKEN"):
        deger = (os.environ.get(ad) or "").strip()
        if deger:
            beklenenler.append(deger)
    if not beklenenler:
        return False
    gelenler = []
    for baslik in ("X-BizimHesap-Token", "X-Entegrasyon-Token", "token"):
        deger = (request.headers.get(baslik) or "").strip()
        if deger:
            gelenler.append(deger)
    yetki = (request.headers.get("Authorization") or "").strip()
    if yetki.lower().startswith("bearer "):
        gelenler.append(yetki[7:].strip())
    elif yetki.lower().startswith("basic "):
        try:
            cozulen = base64.b64decode(yetki.split(" ", 1)[1]).decode("utf-8", "ignore")
            kullanici, _, sifre = cozulen.partition(":")
            if kullanici:
                gelenler.append(kullanici)
            if sifre:
                gelenler.append(sifre)
        except Exception:
            pass
    for parametre in ("token", "consumer_key", "consumer_secret"):
        deger = (request.args.get(parametre) or "").strip()
        if deger:
            gelenler.append(deger)
    for gelen in gelenler:
        for beklenen in beklenenler:
            if _sabit_esit(gelen, beklenen):
                return True
    return False


_rate_kovalar = {}
_RATE_LIMIT_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local n = redis.call('ZCARD', KEYS[1])
if n >= tonumber(ARGV[3]) then
    return 0
end
if ARGV[4] == '1' then
    redis.call('ZADD', KEYS[1], ARGV[2], ARGV[5])
    redis.call('EXPIRE', KEYS[1], ARGV[6])
end
return 1
"""


def _rate_limit_redis(anahtar, limit, pencere_sn, kaydet):
    """Ortak sayac. Redis yoksa veya koparsa None; cagiran bellek kovasına duser."""
    istemci = _redis_al()
    if istemci is None:
        return None
    simdi = time.time()
    try:
        sonuc = istemci.eval(
            _RATE_LIMIT_LUA,
            1,
            "rl:" + str(anahtar),
            simdi - float(pencere_sn),
            simdi,
            int(limit),
            "1" if kaydet else "0",
            f"{simdi:.6f}:{uuid.uuid4().hex[:8]}",
            int(float(pencere_sn)) + 2,
        )
        return bool(int(sonuc))
    except Exception as hata:
        _redis_dusur(hata)
        return None


def _rate_limit_kontrol(anahtar, limit, pencere_sn, kaydet=True):
    """Pencere doluysa False. kaydet True ise izin verilen isteği de sayar."""
    redis_sonuc = _rate_limit_redis(anahtar, limit, pencere_sn, kaydet)
    if redis_sonuc is not None:
        return redis_sonuc
    simdi = time.time()
    kova = _rate_kovalar.get(anahtar)
    if kova is None:
        kova = collections.deque()
        _rate_kovalar[anahtar] = kova
    sinir = simdi - float(pencere_sn)
    while kova and kova[0] <= sinir:
        kova.popleft()
    if len(kova) >= int(limit):
        return False
    if kaydet:
        kova.append(simdi)
    elif not kova:
        _rate_kovalar.pop(anahtar, None)
    return True


def _rate_limit_sifirla(anahtar):
    istemci = _redis_al()
    if istemci is not None:
        try:
            istemci.delete("rl:" + str(anahtar))
        except Exception as hata:
            _redis_dusur(hata)
    _rate_kovalar.pop(anahtar, None)


def _iki_asama_acik():
    return bool((os.environ.get("ADMIN_2FA_SECRET") or "").strip())


def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if _entegrasyon_tokeni_gecerli():
            return f(*args, **kwargs)
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
            if _iki_asama_acik() and session.get("admin_2fa_ok") is not True:
                session["admin_sifre_ok"] = True
                return (
                    jsonify({
                        "success": False,
                        "mesaj": "İki aşamalı doğrulama gerekli. 2FA tamamlanmadan işlem yapılamaz.",
                    }),
                    403,
                )
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


_VIDEO_UZANTILARI = {".mp4", ".mov", ".webm", ".m4v", ".ogg", ".mkv", ".avi", ".3gp"}
_YUKLEME_MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".ogg": "video/ogg",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".3gp": "video/3gpp",
}


def _dosya_uzantisi(ad):
    return os.path.splitext(str(ad or "").lower())[1]


def kaydet_guvenli_dosya(dosya):
    if not dosya or not dosya.filename:
        return None, None
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    orijinal_uzanti = _dosya_uzantisi(dosya.filename)
    temiz_ad = secure_filename(dosya.filename)
    if orijinal_uzanti and _dosya_uzantisi(temiz_ad) != orijinal_uzanti:
        kok = os.path.splitext(temiz_ad)[0] or "dosya"
        temiz_ad = kok + orijinal_uzanti
    if not temiz_ad:
        temiz_ad = "dosya" + orijinal_uzanti
    benzersiz_ad = f"{uuid.uuid4().hex[:10]}_{temiz_ad}"
    dosya_yolu = os.path.join(app.config["UPLOAD_FOLDER"], benzersiz_ad)
    dosya.save(dosya_yolu)
    if not os.path.isfile(dosya_yolu) or os.path.getsize(dosya_yolu) <= 0:
        return None, None
    return f"/static/uploads/{benzersiz_ad}", benzersiz_ad


class MedyaYuklemeHatasi(Exception):
    """Cloudinary yüklemesi yapılamadı. Yerel diske yazılmaz."""


def _bulut_anahtari_var():
    return all(
        (os.environ.get(ad) or "").strip()
        for ad in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")
    )


def _temiz_medya_adi(dosya):
    orijinal_uzanti = _dosya_uzantisi(dosya.filename)
    temiz_ad = secure_filename(dosya.filename)
    if orijinal_uzanti and _dosya_uzantisi(temiz_ad) != orijinal_uzanti:
        kok = os.path.splitext(temiz_ad)[0] or "dosya"
        temiz_ad = kok + orijinal_uzanti
    if not temiz_ad:
        temiz_ad = "dosya" + orijinal_uzanti
    return temiz_ad


def medyayi_kaydet(dosya):
    """Ürün fotoğrafı ve videosu. Cloudinary REST; SDK ve yerel disk yok."""
    if not dosya or not getattr(dosya, "filename", None):
        return None, None
    if not _bulut_anahtari_var():
        raise MedyaYuklemeHatasi(
            "Cloudinary anahtarları eksik. CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY ve CLOUDINARY_API_SECRET tanımlı olmalı. "
            "Dosya yerel diske kaydedilmedi."
        )
    temiz_ad = _temiz_medya_adi(dosya)
    cloud_name = (os.environ.get("CLOUDINARY_CLOUD_NAME") or "").strip()
    api_key = (os.environ.get("CLOUDINARY_API_KEY") or "").strip()
    api_secret = (os.environ.get("CLOUDINARY_API_SECRET") or "").strip()
    timestamp = int(time.time())
    imza = hashlib.sha1(
        f"folder=halic-antik&timestamp={timestamp}{api_secret}".encode("utf-8")
    ).hexdigest()
    try:
        akis = getattr(dosya, "stream", dosya)
        try:
            akis.seek(0)
        except Exception:
            pass
        icerik = akis.read()
        if not icerik:
            raise MedyaYuklemeHatasi(
                "Yüklenecek dosya boş. Dosya yerel diske kaydedilmedi."
            )
        yanit = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/auto/upload",
            data={
                "api_key": api_key,
                "timestamp": str(timestamp),
                "folder": "halic-antik",
                "signature": imza,
            },
            files={"file": (temiz_ad, icerik)},
            timeout=120,
        )
        if yanit.status_code >= 400:
            raise MedyaYuklemeHatasi(
                f"Cloudinary yüklemesi başarısız (HTTP {yanit.status_code}). "
                "Dosya yerel diske kaydedilmedi."
            )
        sonuc = yanit.json()
    except MedyaYuklemeHatasi:
        raise
    except Exception as hata:
        raise MedyaYuklemeHatasi(
            f"Cloudinary yüklemesi başarısız: {hata}. Dosya yerel diske kaydedilmedi."
        ) from hata
    adres = str((sonuc or {}).get("secure_url") or "").strip()
    if not adres.startswith("https://res.cloudinary.com/"):
        raise MedyaYuklemeHatasi(
            "Cloudinary güvenli adres döndürmedi. Dosya yerel diske kaydedilmedi."
        )
    return adres, temiz_ad


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


class KaraListe(db.Model):
    __tablename__ = "kara_liste"
    id = db.Column(db.Integer, primary_key=True)
    telefon = db.Column(db.String(20), nullable=True, index=True)
    email = db.Column(db.String(100), nullable=True, index=True)
    cihaz_kodu = db.Column(db.String(64), nullable=True, index=True)
    ip_adresi = db.Column(db.String(50), nullable=True, index=True)
    kaynak_id = db.Column(db.Integer, nullable=True)
    tarih = db.Column(db.DateTime, default=suan_utc)


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
    video = db.Column(db.Text, nullable=True, default="")
    ses_dosyasi = db.Column(db.Text, nullable=True, default="")
    durum = db.Column(db.String(20), default="Aktif", index=True)
    kazanan_adi = db.Column(db.String(100), nullable=True, default="Yok")
    kazanan_id = db.Column(
        db.Integer, db.ForeignKey("kullanici.id"), nullable=True, index=True
    )
    tedarikci_id = db.Column(db.Integer, nullable=True, index=True)
    kaynak_tipi = db.Column(db.String(50), nullable=False, default="manuel")
    stok_adedi = db.Column(db.Integer, nullable=False, default=1)
    xml_kaynak_url = db.Column(db.String(500), nullable=True)
    siparis_durumu = db.Column(db.String(30), default="Odeme_Bekliyor")
    kargo_takip_no = db.Column(db.String(100), nullable=True)
    kargo_firmasi = db.Column(db.String(50), default="Yurtiçi Kargo")
    kargo_barkod_url = db.Column(db.Text, nullable=True, default="")
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
                "stok_adedi": int(self.stok_adedi if self.stok_adedi is not None else 1),
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
            "stok_adedi": int(self.stok_adedi if self.stok_adedi is not None else 1),
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


class Ayar(db.Model):
    """Kalıcı anahtar-değer ayarı. Kurumsal bilgiler deploy sonrası burada durur."""
    __tablename__ = "ayar"
    id = db.Column(db.Integer, primary_key=True)
    anahtar = db.Column(db.String(80), unique=True, nullable=False, index=True)
    deger = db.Column(db.Text, nullable=False, default="")


class IcerikMaddesi(db.Model):
    """Nasıl Kullanılır adımları ve kurallar. Yönetici panelinden düzenlenir."""
    __tablename__ = "icerik_maddesi"
    id = db.Column(db.Integer, primary_key=True)
    tur = db.Column(db.String(30), nullable=False, index=True)
    sira = db.Column(db.Integer, nullable=False, default=1)
    baslik = db.Column(db.String(200), nullable=False, default="")
    metin = db.Column(db.Text, nullable=False, default="")


_ESKI_VARSAYILAN_REHBER = (
    (
        "1) Dükkan Seçimi",
        "Üst menüden Antika, Gümüş, Bijuteri, Saat/Tesbih veya diğer dükkanlar arasında tek dokunuşla geçiş yapın.",
    ),
    (
        "2) Canlı Mezatta Pey Verme",
        "Sahnedeki ürüne +%10, +%25 hızlı artış butonlarıyla veya kendi belirlediğiniz tutarla anında teklif verin.",
    ),
    (
        "3) Hemen Al & Sepet",
        "Süre bitimini beklemeden ürünü Hemen Al ile doğrudan kazanabilir veya Vitrin sayfasından ürünleri kıyaslayıp sepetinize ekleyebilirsiniz.",
    ),
    (
        "4) Ses & Müzik Kontrolü",
        "Sağ üst ve sahne kontrollerinden canlı sunucu anlatımını açabilir, dilerseniz arka plan müziğini değiştirebilirsiniz.",
    ),
    (
        "5) Güvenli Ödeme & Kargo",
        "Kazandığınız veya sepete eklediğiniz ürünler için sipariş formunu doldurun; ekibimiz WhatsApp üzerinden onay ve kargo takip kodunu iletir.",
    ),
)

_VARSAYILAN_REHBER = (
    (
        "🏛️ 17 CANLI SALON",
        "Üst şeritten veya ☰ Menü > Dükkanlar üzerinden 17 salona sayfa yenilemeden anında geçin (CANLI rozetli salonlarda aktif yayın vardır).",
    ),
    (
        "🎵 SES & 4 İSTASYON RADYO",
        "Ekrana bir kez dokunarak yapay zeka sunucusunu dinleyin. Sağ üst 🎵 Müzik panelinden Taş Plak, Türk Sanat Müziği, Akustik veya Dinlendirici Fon seçin; anons sırasında müzik otomatik kısılır.",
    ),
    (
        "🟢 +%10 ARTIR",
        "Rekabet anında hesap yapmadan güncel fiyata tek dokunuşla %10 ekleyerek teklif verin (ilk teklifte e-posta doğrulamalı 30 saniyelik hızlı kayıt yapılır).",
    ),
    (
        "🔵 SERBEST PEY & %XX GİZLİ EŞİK",
        'İlk 2 dakikalık PAZARLIK turunda bütçenize özel teklif yazın. Hemen Al değerinin %XX gizli eşiğini yakalayan ilk koleksiyoner "Tam 12\'den Vurdunuz" sürpriziyle eseri anında kazanır!',
    ),
    (
        "⚡ HEMEN AL & SON 60 SN",
        "Süreyi beklemeden Hemen Al ile eseri kapatın. Son 60 saniye karar turunda fiyat açılışa döner ve %10 artış zorunlu olur; son 10 saniyede gelen her pey süreyi +15 saniye uzatır.",
    ),
    (
        "🛒 SEPET, VİTRİN & ÖN TEKLİF",
        "Kazandığınız eserler Sepet'inize düşer (%20 KDV + 150 TL sabit kargo; IBAN Kopyala veya 3D Secure POS ile ödeyin). Vitrin ve Katalog'dan ürün kıyaslayabilir, Ön Teklif bırakabilir, Eleştiri Odası'ndan doğrudan yönetime yazabilirsiniz.",
    ),
)

_VARSAYILAN_KURALLAR = (
    (
        "1. İşletmeci Sorumluluğu",
        "Haliç Antik Mezat, 6563 sayılı kanun kapsamında Aracı Hizmet Sağlayıcıdır; ürünlerin mülkiyeti satıcıya/tedarikçiye aittir.",
    ),
    (
        "2. Tedarikçi Taahhüdü ve Cezai Şart",
        "Yayına ürün veren tedarikçi, ürünü mezat bitimine kadar rezerve etmekle yükümlüdür. Ürünü haricen satan, teslim etmeyen, kırık veya fotoğraftakinden farklı gönderen tedarikçiye son satış tutarı üzerinden %20 cezai şart fatura edilir ve sistemden süresiz men edilir.",
    ),
    (
        "3. Kargo ve Hasar Güvencesi",
        "Tedarikçi ürünü kargo taşımacılığına uygun darbe emici korumayla paketlemek zorundadır. Alıcı, kargo teslim anında paketi açarken kesintisiz video kaydı almakla yükümlüdür; videosuz hasar bildirimleri işleme alınmaz.",
    ),
    (
        "4. Güvenli Havuz Sistemi",
        "Alıcının ödediği bedel, ürün teslim edilip alıcı onay verene kadar Haliç Antik Mezat havuz hesabında güvende tutulur.",
    ),
)


def icerik_maddesi_sozlugu(satir):
    return {
        "id": satir.id,
        "tur": satir.tur,
        "sira": satir.sira,
        "baslik": satir.baslik or "",
        "metin": satir.metin or "",
    }


_icerik_kilit = threading.Lock()


def icerik_maddelerini_hazirla():
    with _icerik_kilit:
        IcerikMaddesi.__table__.create(bind=db.engine, checkfirst=True)
        if IcerikMaddesi.query.count() == 0:
            for sira, (baslik, metin) in enumerate(_VARSAYILAN_REHBER, start=1):
                db.session.add(IcerikMaddesi(tur="rehber", sira=sira, baslik=baslik, metin=metin))
            for sira, (baslik, metin) in enumerate(_VARSAYILAN_KURALLAR, start=1):
                db.session.add(IcerikMaddesi(tur="kural", sira=sira, baslik=baslik, metin=metin))
            db.session.commit()
            return
        rehber = (
            IcerikMaddesi.query.filter_by(tur="rehber")
            .order_by(IcerikMaddesi.sira.asc(), IcerikMaddesi.id.asc())
            .all()
        )
        mevcut = [(satir.baslik or "", satir.metin or "") for satir in rehber]
        if mevcut != list(_ESKI_VARSAYILAN_REHBER):
            return
        for satir in rehber:
            db.session.delete(satir)
        for sira, (baslik, metin) in enumerate(_VARSAYILAN_REHBER, start=1):
            db.session.add(IcerikMaddesi(tur="rehber", sira=sira, baslik=baslik, metin=metin))
        db.session.commit()


def icerik_maddelerini_oku(tur):
    try:
        icerik_maddelerini_hazirla()
        satirlar = (
            IcerikMaddesi.query.filter_by(tur=tur)
            .order_by(IcerikMaddesi.sira.asc(), IcerikMaddesi.id.asc())
            .all()
        )
        return [icerik_maddesi_sozlugu(satir) for satir in satirlar]
    except Exception:
        db.session.rollback()
        yedek = _VARSAYILAN_REHBER if tur == "rehber" else _VARSAYILAN_KURALLAR
        return [
            {"id": 0, "tur": tur, "sira": sira, "baslik": baslik, "metin": metin}
            for sira, (baslik, metin) in enumerate(yedek, start=1)
        ]


def _yazma_icin_kilitle(model, kayit_id):
    """Ürün veya kullanıcı satırını bu transaction bitene kadar kilitler.

    PostgreSQL SELECT ... FOR UPDATE. SQLite aynı oturumdaki satırı döndürür.
    """
    if kayit_id is None or kayit_id == "":
        return None
    try:
        kayit_id = int(kayit_id)
    except (TypeError, ValueError):
        return None
    sorgu = db.session.query(model)
    if not IS_SQLITE:
        sorgu = sorgu.with_for_update()
    return sorgu.filter(model.id == kayit_id).populate_existing().first()


def _satir_kilit_cakismasi(hata):
    """PostgreSQL lock_timeout (55P03 / LockNotAvailable). Baska hatalar sayilmaz."""
    kuyruk = [hata]
    gorulen = set()
    while kuyruk:
        guncel = kuyruk.pop()
        if guncel is None or id(guncel) in gorulen:
            continue
        gorulen.add(id(guncel))
        if type(guncel).__name__ == "LockNotAvailable":
            return True
        if getattr(guncel, "pgcode", None) == "55P03":
            return True
        metin = str(guncel).lower()
        if (
            "locknotavailable" in metin
            or "lock timeout" in metin
            or "lock not available" in metin
            or "could not obtain lock" in metin
        ):
            return True
        for bag in ("orig", "__cause__", "__context__"):
            ic = getattr(guncel, bag, None)
            if ic is not None and ic is not guncel:
                kuyruk.append(ic)
    return False


def _havuz_yogun_yaniti():
    return jsonify({
        "success": False,
        "mesaj": "Sistem şu an çok yoğun, lütfen tekrar deneyin.",
    })


def _kilitli_fiyat(oda, urun):
    """Kilitli satırdaki fiyat esastır. Aynı worker belleği daha yüksekse onu da görür."""
    db_fiyat = 0.0
    if urun is not None:
        try:
            db_fiyat = float(urun.guncel_fiyat or 0.0)
        except (TypeError, ValueError):
            db_fiyat = 0.0
        if db_fiyat <= 0:
            try:
                db_fiyat = float(urun.acilis_fiyati or 0.0)
            except (TypeError, ValueError):
                db_fiyat = 0.0
    try:
        oda_fiyat = float((oda.get("durum") or {}).get("pey") or 0.0)
    except (TypeError, ValueError):
        oda_fiyat = 0.0
    return max(db_fiyat, oda_fiyat)


def _kazanan_sepetini_disari_aktar(urun_id, hedef_kullanici):
    if not hedef_kullanici:
        return
    urun = db.session.get(Urun, urun_id)
    if not urun:
        return
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


def urunu_kazanana_sepete_ekle(urun_id, kazanan_adi, kazanan_id=None, commit=True):
    """Sepet satırını ekler. commit False ise çağıran tek transaction içinde kaydeder."""
    if not kazanan_adi or kazanan_adi in ["Yok", "None", "-", ""]:
        return None

    if isinstance(kazanan_id, str):
        kazanan_id = kazanan_id.strip()
        kazanan_id = int(kazanan_id) if kazanan_id.isdigit() else None

    hedef_kullanici = None
    if kazanan_id:
        hedef_kullanici = db.session.get(Kullanici, kazanan_id)

    if not hedef_kullanici:
        temiz_ad = saf_isim_temizle(kazanan_adi)
        if not temiz_ad:
            return None
        hedef_kullanici = (
            Kullanici.query.filter(
                Kullanici.silindi_mi.is_(False), Kullanici.ad_soyad.ilike(temiz_ad)
            ).first()
        )

    if not hedef_kullanici:
        return None

    var_mi = SepetItem.query.filter_by(
        musteri_id=hedef_kullanici.id, urun_id=urun_id
    ).first()
    eklendi = False
    if not var_mi:
        db.session.add(SepetItem(musteri_id=hedef_kullanici.id, urun_id=urun_id))
        eklendi = True
    if not commit:
        return hedef_kullanici
    if eklendi:
        db.session.commit()
    _kazanan_sepetini_disari_aktar(urun_id, hedef_kullanici)
    return hedef_kullanici


# ==========================================
# 10 DÜKKAN İÇİN ÇOKLU ODA VE MEZAT MOTORU
# ==========================================
_REDIS_KILIT_BIRAK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class _WorkerKilidi:
    """Önce bu süreçteki kilit, REDIS_URL varsa worker'lar arası kilit.

    REDIS_URL varken Redis alınamazsa yerel kilitle devam edilmez.
    Aynı dükkan iki worker'da birden açılmaz.
    """

    def __init__(self, ad):
        self.ad = ad
        self.yerel = BoundedSemaphore(1)
        self.token = None
        self.anahtar = None
        self.istemci = None
        self._yerel_acik = False

    def __enter__(self):
        self.yerel.acquire()
        self._yerel_acik = True
        self.token = None
        self.anahtar = None
        self.istemci = None
        if not REDIS_URL:
            return self
        istemci = _redis_al()
        if istemci is None:
            self._yerel_birak()
            raise RuntimeError(f"Redis kilidi alınamadı ({self.ad})")
        token = uuid.uuid4().hex
        anahtar = "mezat:kilit:" + self.ad
        son = time.monotonic() + 12.0
        while True:
            try:
                alindi = istemci.set(anahtar, token, nx=True, ex=20)
            except Exception as hata:
                _redis_dusur(hata)
                self._yerel_birak()
                raise RuntimeError(f"Redis kilidi alınamadı ({self.ad})") from hata
            if alindi:
                self.token = token
                self.anahtar = anahtar
                self.istemci = istemci
                return self
            if time.monotonic() >= son:
                self._yerel_birak()
                raise RuntimeError(f"Redis kilidi zaman aşımı ({self.ad})")
            time.sleep(0.05)

    def _yerel_birak(self):
        if self._yerel_acik:
            self._yerel_acik = False
            self.yerel.release()

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.istemci is not None and self.token and self.anahtar:
                try:
                    self.istemci.eval(_REDIS_KILIT_BIRAK, 1, self.anahtar, self.token)
                except Exception as hata:
                    _redis_dusur(hata)
        finally:
            self._yerel_birak()
        return False


def _oda_varsayilan():
    return {
        "aktif_urun_id": None,
        "sayac_kalan": 0,
        "sayac_aktif": False,
        "sayac_gorev_id": 0,
        "otonom_aktif": False,
        "otonom_dongu": False,
        "sahne_nesil": 0,
        "sahne_yayin_nesil": 0,
        "izleyici_sayisi": 0,
        "son_teklif_veren_id": None,
        "gizli_pazarlik_aktif": False,
        "gizli_taban_fiyat": None,
        "mevat_asama": "",
        "acilis_tabani": 0,
        "karar_pey_geldi": False,
        "sayac_nabiz": 0,
        "lot_kapanis": "",
        "satis_tebrik_bekleniyor": False,
        "sunucu_lakap": "",
        "son_hareket": 0,
        "son_sessizlik": 0,
        "satildi": False,
        "son_satis": None,
        "durum": {
            "durum": "Bekliyor",
            "sure_bitis": 0,
            "pey": 0,
            "kazanan": "Yok",
            "kazanan_id": None,
        },
    }


_oda_sema_hazir = False
_oda_sema_kilit = threading.Lock()


_ODA_REDIS_YAYIN = """
local cur = tonumber(redis.call('get', KEYS[2]) or '0')
local yeni = tonumber(ARGV[2])
if yeni >= cur then
    redis.call('set', KEYS[1], ARGV[1])
    redis.call('set', KEYS[2], ARGV[2])
    return 1
end
return 0
"""


def _oda_anahtar(kod):
    return "mezat:oda:" + str(kod)


def _oda_surum_anahtar(kod):
    return "mezat:oda:surum:" + str(kod)


def _oda_belge_harmanla(ham):
    belge = _oda_varsayilan()
    if not isinstance(ham, dict):
        return belge
    durum = ham.get("durum") if isinstance(ham.get("durum"), dict) else {}
    gelen = dict(ham)
    gelen.pop("durum", None)
    belge.update(gelen)
    if isinstance(durum, dict):
        belge["durum"].update(durum)
    return belge


def _oda_sema():
    """Mezat odasi satiri. Var olan kaydin uzerine varsayilan yazilmaz."""
    global _oda_sema_hazir
    if _oda_sema_hazir:
        return
    with _oda_sema_kilit:
        if _oda_sema_hazir:
            return
        varsayilan = json.dumps(_oda_varsayilan(), ensure_ascii=False)
        with db.engine.begin() as baglanti:
            baglanti.execute(text(
                "CREATE TABLE IF NOT EXISTS mezat_oda ("
                "dukkan_kodu VARCHAR(30) PRIMARY KEY, "
                "veri TEXT NOT NULL DEFAULT '{}', "
                "surum INTEGER NOT NULL DEFAULT 0"
                ")"
            ))
            for kod in DUKKANLAR:
                baglanti.execute(
                    text(
                        "INSERT INTO mezat_oda (dukkan_kodu, veri, surum) "
                        "SELECT :k, :v, 0 WHERE NOT EXISTS "
                        "(SELECT 1 FROM mezat_oda WHERE dukkan_kodu = :k)"
                    ),
                    {"k": kod, "v": varsayilan},
                )
        _oda_sema_hazir = True


def _oda_belge_oku(kod):
    _oda_sema()
    with db.engine.connect() as baglanti:
        satir = baglanti.execute(
            text("SELECT veri, surum FROM mezat_oda WHERE dukkan_kodu = :k"),
            {"k": kod},
        ).fetchone()
    if not satir:
        return _oda_varsayilan(), 0
    try:
        gelen = json.loads(satir[0] or "{}")
    except (TypeError, ValueError):
        gelen = {}
    return _oda_belge_harmanla(gelen), int(satir[1] or 0)


_oda_yaz_kilitleri = {}
_oda_yaz_kilit_ana = threading.Lock()


def _oda_yaz_kilidi(kod):
    """Aynı süreçte iki greenlet mezat_oda satırını ezmesin.

    PostgreSQL FOR UPDATE süreçler arasını tutar. SQLite'ta FOR UPDATE yoktur;
    sayaç döngüsü ile otonom döngü aynı anda yazınca sayac_aktif geri düşüyordu.
    """
    with _oda_yaz_kilit_ana:
        kilit = _oda_yaz_kilitleri.get(kod)
        if kilit is None:
            kilit = threading.Lock()
            _oda_yaz_kilitleri[kod] = kilit
        return kilit


def _oda_pg_yaz(kod, alan, deger, sil=False):
    """Tek alan. Satir kilitliyken yeniden okunur; baska alanin yazisi ezilmez."""
    _oda_sema()
    with _oda_yaz_kilidi(kod):
        return _oda_pg_yaz_kilitli(kod, alan, deger, sil)


def _oda_pg_yaz_kilitli(kod, alan, deger, sil=False):
    with db.engine.begin() as baglanti:
        if not IS_SQLITE:
            satir = baglanti.execute(
                text("SELECT veri, surum FROM mezat_oda WHERE dukkan_kodu = :k FOR UPDATE"),
                {"k": kod},
            ).fetchone()
        else:
            satir = baglanti.execute(
                text("SELECT veri, surum FROM mezat_oda WHERE dukkan_kodu = :k"),
                {"k": kod},
            ).fetchone()
        if satir:
            try:
                gelen = json.loads(satir[0] or "{}")
            except (TypeError, ValueError):
                gelen = {}
            belge = _oda_belge_harmanla(gelen)
            surum = int(satir[1] or 0) + 1
        else:
            belge = _oda_varsayilan()
            surum = 1
        if alan.startswith("durum."):
            alt = alan.split(".", 1)[1]
            if sil:
                belge["durum"].pop(alt, None)
            else:
                belge["durum"][alt] = deger
        elif sil:
            belge.pop(alan, None)
        else:
            belge[alan] = deger
        govde = json.dumps(belge, ensure_ascii=False)
        if satir:
            baglanti.execute(
                text("UPDATE mezat_oda SET veri = :v, surum = :s WHERE dukkan_kodu = :k"),
                {"k": kod, "v": govde, "s": surum},
            )
        else:
            baglanti.execute(
                text("INSERT INTO mezat_oda (dukkan_kodu, veri, surum) VALUES (:k, :v, :s)"),
                {"k": kod, "v": govde, "s": surum},
            )
        return belge, surum


def _oda_redis_yayinla(kod, belge, surum):
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        istemci.eval(
            _ODA_REDIS_YAYIN,
            2,
            _oda_anahtar(kod),
            _oda_surum_anahtar(kod),
            json.dumps(belge, ensure_ascii=False),
            str(surum),
        )
    except Exception as hata:
        _redis_dusur(hata)
        try:
            istemci.delete(_oda_anahtar(kod))
        except Exception:
            pass


def _oda_canli_belge(kod):
    istemci = _redis_al()
    if istemci is not None:
        try:
            ham = istemci.get(_oda_anahtar(kod))
            if ham:
                return _oda_belge_harmanla(json.loads(ham))
        except Exception as hata:
            _redis_dusur(hata)
    belge, _surum = _oda_belge_oku(kod)
    return belge


def _oda_alani_oku(kod, alan, varsayilan=None):
    try:
        belge = _oda_canli_belge(kod)
    except Exception as hata:
        print(f"[ODA] okunamadi ({kod}.{alan}): {hata}", flush=True)
        belge = _oda_varsayilan()
    if alan.startswith("durum."):
        return (belge.get("durum") or {}).get(alan.split(".", 1)[1], varsayilan)
    if alan not in belge:
        return varsayilan
    return belge.get(alan, varsayilan)


def _oda_alani_yaz(kod, alan, deger):
    try:
        belge, surum = _oda_pg_yaz(kod, alan, deger, sil=False)
    except Exception as hata:
        print(f"[ODA] postgres yazilamadi ({kod}.{alan}): {hata}", flush=True)
        return
    _oda_redis_yayinla(kod, belge, surum)


def _oda_alani_sil(kod, alan):
    try:
        belge, surum = _oda_pg_yaz(kod, alan, None, sil=True)
    except Exception as hata:
        print(f"[ODA] postgres silinemedi ({kod}.{alan}): {hata}", flush=True)
        return
    _oda_redis_yayinla(kod, belge, surum)


class _DurumProxy:
    """durum.pey gibi alt alanlar ayri Redis/PostgreSQL alanidir."""

    def __init__(self, kod):
        self.kod = kod

    def __getitem__(self, anahtar):
        varsayilan = _oda_varsayilan()["durum"].get(anahtar)
        return _oda_alani_oku(self.kod, "durum." + str(anahtar), varsayilan)

    def __setitem__(self, anahtar, deger):
        _oda_alani_yaz(self.kod, "durum." + str(anahtar), deger)

    def get(self, anahtar, varsayilan=None):
        bulunan = _oda_alani_oku(self.kod, "durum." + str(anahtar), _EKSIK)
        if bulunan is _EKSIK:
            return varsayilan
        return bulunan


class _OdaProxy:
    """Worker bu nesneyi tutar; deger Redis ve mezat_oda satirindadir."""

    def __init__(self, kod):
        self.kod = kod

    def __getitem__(self, anahtar):
        if anahtar == "durum":
            return _DurumProxy(self.kod)
        varsayilan = _oda_varsayilan().get(anahtar)
        return _oda_alani_oku(self.kod, str(anahtar), varsayilan)

    def __setitem__(self, anahtar, deger):
        if anahtar == "durum" and isinstance(deger, dict):
            for alt, ic in deger.items():
                _oda_alani_yaz(self.kod, "durum." + str(alt), ic)
            return
        _oda_alani_yaz(self.kod, str(anahtar), deger)

    def get(self, anahtar, varsayilan=None):
        if anahtar == "durum":
            return _DurumProxy(self.kod)
        bulunan = _oda_alani_oku(self.kod, str(anahtar), _EKSIK)
        if bulunan is _EKSIK:
            return varsayilan
        return bulunan

    def pop(self, anahtar, *varsayilan):
        bulunan = self.get(anahtar, _EKSIK)
        if bulunan is _EKSIK:
            if varsayilan:
                return varsayilan[0]
            raise KeyError(anahtar)
        _oda_alani_sil(self.kod, str(anahtar))
        return bulunan


_EKSIK = object()
odalar = {kod: _OdaProxy(kod) for kod in DUKKANLAR}
oda_kilitleri = {kod: _WorkerKilidi(kod) for kod in DUKKANLAR}


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


def ahmet_sahne_acilis_metni(urun, acilis_fiyati, lot=""):
    ad = (getattr(urun, "urun_adi", None) or "Eser").strip()
    tutar = int(round(float(acilis_fiyati or 0)))
    lot_yazi = f"Lot {lot}, " if str(lot or "").strip() else ""
    return (
        f"Sahne bizim dostlar! {lot_yazi}{ad} indi! "
        f"Açılış {tutar} lira, gözünüz işçiliğinde, eli değen kapar, kim artırıyor?"
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


def ahmet_kisa_acilis_seslendir(dukkan, ad, acilis, lot=""):
    """Yeni sahne: ad, lot ve açılış fiyatı yüksek enerjiyle okunur."""
    sahte = type("UrunSatir", (), {"urun_adi": ad})()
    ahmet_usta_anons_yap(
        dukkan,
        ahmet_sahne_acilis_metni(sahte, acilis, lot),
        tur="tanitim",
        kesinti=True,
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
    oda["sayac_nabiz"] = time.time()
    return oda["sayac_gorev_id"]


def _anonsu_arkada(dukkan, metin, tur="sayac"):
    """TTS sayaç tikini bekletmesin. 15. sn ve karar anonsu arka planda okunur."""
    socketio.start_background_task(ahmet_usta_anons_yap, dukkan, metin, True, tur, False)


def _fiyati_acilisa_cek(dukkan, oda, konus=True):
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
            invalidate_vitrin_cache()
    oda["acilis_tabani"] = acilis
    oda["durum"]["pey"] = acilis
    oda["durum"]["kazanan"] = "Yok"
    oda["durum"]["kazanan_id"] = None
    oda["son_teklif_veren_id"] = None
    oda["karar_pey_geldi"] = False
    oda["mevat_asama"] = "karar"
    oda["gizli_pazarlik_aktif"] = False
    oda["gizli_taban_fiyat"] = None
    _pey_duyur(
        dukkan,
        {
            "urun_id": urun_id,
            "pey": acilis,
            "kazanan": "Yok",
            "kazanan_id": None,
        },
    )
    duyuru = "Pazarlık bitti, son karar süresi başladı"
    _iki_odayi(
        dukkan,
        "pazarlik_kapandi",
        {"dukkan": dukkan, "mesaj": duyuru, "pey": acilis, "kazanan": "Yok"},
    )
    if konus:
        _anonsu_arkada(dukkan, duyuru)
    return acilis


def _gizli_pazarligi_kapat(oda):
    oda["gizli_pazarlik_aktif"] = False
    oda["gizli_taban_fiyat"] = None
    if oda.get("mevat_asama") == "pazarlik":
        oda["mevat_asama"] = ""


def _pazarlik_erken_sat(dukkan, oda, urun, etiket_ad, kullanici_id, tutar, konusma_adi):
    """Kilidi çağıran tutar. Gizli eşik hiçbir yayına konmaz."""
    if kullanici_id:
        _yazma_icin_kilitle(Kullanici, kullanici_id)
    if urun is not None and getattr(urun, "id", None):
        kilitli_urun = _yazma_icin_kilitle(Urun, urun.id)
        if kilitli_urun is not None:
            urun = kilitli_urun
    oda["sayac_aktif"] = False
    oda["sayac_gorev_id"] += 1
    oda["sayac_kalan"] = 0
    _gizli_pazarligi_kapat(oda)
    oda["durum"]["durum"] = "Satıldı"
    oda["durum"]["sure_bitis"] = 0
    oda["durum"]["kazanan"] = etiket_ad
    oda["durum"]["kazanan_id"] = kullanici_id
    oda["durum"]["pey"] = float(tutar)
    _satis_kapanisini_isaretle(dukkan, oda)
    biten_id = oda.get("aktif_urun_id")
    oda["aktif_urun_id"] = None
    _son_satisi_yaz(oda, etiket_ad, tutar, biten_id)

    if urun is not None:
        urun.durum = "Satıldı"
        urun.kazanan_adi = etiket_ad
        urun.kazanan_id = kullanici_id
        urun.guncel_fiyat = float(tutar)
        db.session.add(urun)
        OnTeklif.query.filter_by(urun_id=urun.id).delete()
        stok_satis_sonrasi_dus(urun)
        hedef_sepet = urunu_kazanana_sepete_ekle(
            urun.id, etiket_ad, kullanici_id, commit=False
        )
        db.session.commit()
        if hedef_sepet:
            _kazanan_sepetini_disari_aktar(urun.id, hedef_sepet)

    invalidate_vitrin_cache()
    _pey_duyur(
        dukkan,
        {
            "urun_id": getattr(urun, "id", biten_id),
            "pey": float(tutar),
            "kazanan": etiket_ad,
            "kazanan_id": kullanici_id,
        },
    )
    _iki_odayi(
        dukkan,
        "urun_satildi_erken",
        {
            "dukkan": dukkan,
            "urun_id": biten_id,
            "kazanan": etiket_ad,
            "fiyat": float(tutar),
            "mesaj": "Tebrikler! Gizli pazarlık tabanı aşıldı, ürün sahibini buldu!",
        },
    )
    bota_haber_ver(
        "satis_bitti",
        {"dukkan": dukkan, "kazanan": etiket_ad, "fiyat": float(tutar)},
    )
    ahmet_zaferi_seslendir(dukkan, konusma_adi or etiket_ad)
    _otonom_disi_satisi_bosalt(dukkan, oda)
    bota_haber_ver("ahmet_sifirla", {"dukkan": dukkan})
    _admin_satisi_duyur(dukkan, biten_id, etiket_ad, tutar, "esik")


def _uc_dakikalik_pazarlik(dukkan, gorev_id):
    """180 sn iç saat. Ekran önce 120..1 pazarlık, sonra 60..0 karar görür."""
    oda = odalar[dukkan]
    kalan = 180
    oda["sayac_kalan"] = kalan
    oda["sayac_aktif"] = True
    oda["mevat_asama"] = "pazarlik"
    oda["gizli_pazarlik_aktif"] = True
    oda["sayac_nabiz"] = time.time()

    _iki_odayi(
        dukkan,
        "tanitim_basladi",
        {"dukkan": dukkan, "sure": 120, "sayac_gorunsun": True, "pazarlik": True},
    )

    son_uyari_yapildi = False
    while oda["sayac_aktif"] and int(oda["sayac_gorev_id"] or 0) == int(gorev_id) and kalan > 0:
        _sayac_kilidi_canli_tut(dukkan)
        oda["sayac_nabiz"] = time.time()
        oda["sayac_kalan"] = kalan
        if kalan <= 60 and oda.get("mevat_asama") == "pazarlik":
            with oda_kilitleri[dukkan]:
                if int(oda["sayac_gorev_id"] or 0) != int(gorev_id) or not oda["sayac_aktif"]:
                    return False
                if oda["durum"].get("durum") == "Satıldı":
                    return False
                _fiyati_acilisa_cek(dukkan, oda, konus=False)
                oda["sayac_aktif"] = True
                oda["sayac_kalan"] = kalan
                oda["sayac_nabiz"] = time.time()
                oda["durum"]["durum"] = "Sayim"
                oda["durum"]["sure_bitis"] = time.time() + kalan
            _anonsu_arkada(dukkan, "Pazarlık bitti, son karar süresi başladı")
        asama = "karar" if oda.get("mevat_asama") == "karar" else "pazarlik"
        if asama == "karar" and kalan == 15 and not son_uyari_yapildi:
            son_uyari_yapildi = True
            bota_haber_ver("geri_sayim_uyari", {"dukkan": dukkan, "kalan_saniye": 15})
            _anonsu_arkada(dukkan, "Son 15 saniye dostlar, eser elden gidiyor!")
        elif kalan > 15:
            son_uyari_yapildi = False
        _iki_odayi(dukkan, "sayac_guncelle", sayac_paketi(dukkan, kalan, asama))
        socketio.sleep(1)
        if oda["sayac_aktif"] and int(oda["sayac_gorev_id"] or 0) == int(gorev_id):
            kalan -= 1
        else:
            break

    if not (oda["sayac_aktif"] and int(oda["sayac_gorev_id"] or 0) == int(gorev_id)):
        return False
    if oda["durum"].get("durum") == "Satıldı":
        return False
    oda["sayac_kalan"] = 0
    return True


_sayac_sahip = {}
_SAYAC_KILIT_SANIYE = 40

_SAYAC_AL = """
local v = redis.call('GET', KEYS[1])
local ben = ARGV[1]
local ttl = tonumber(ARGV[2])
local gorev = tonumber(ARGV[3]) or 0
if not v or v == '' then
  redis.call('SET', KEYS[1], ben, 'EX', ttl)
  return 1
end
if v == ben then
  redis.call('EXPIRE', KEYS[1], ttl)
  return 1
end
local eski = tonumber(string.match(v, '^(%d+):')) or 0
if gorev > eski then
  redis.call('SET', KEYS[1], ben, 'EX', ttl)
  return 1
end
return 0
"""

_SAYAC_YENILE = """
local v = redis.call('GET', KEYS[1])
local ben = ARGV[1]
local ttl = tonumber(ARGV[2])
if not v or v == '' or v == ben then
  redis.call('SET', KEYS[1], ben, 'EX', ttl)
  return 1
end
return 0
"""

_SAYAC_BIRAK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _sayac_kilit_anahtar(dukkan):
    return "mezat:sayac:" + str(dukkan)


def _sayac_kilidi_al(dukkan, gorev_id, token):
    """ok, dolu veya yok. Beklemez. Yeni görev numarası eski kilidi hemen devralır."""
    if not REDIS_URL:
        return "ok"
    istemci = _redis_al()
    if istemci is None:
        return "yok"
    try:
        alindi = bool(int(istemci.eval(
            _SAYAC_AL,
            1,
            _sayac_kilit_anahtar(dukkan),
            token,
            str(_SAYAC_KILIT_SANIYE),
            str(int(gorev_id)),
        ) or 0))
    except Exception as hata:
        _redis_dusur(hata)
        return "yok"
    return "ok" if alindi else "dolu"


def _sayac_kilidi_yenile(dukkan, token):
    """Kilit düşünce döngü durmaz. Başka görev aldıysa False."""
    if not REDIS_URL:
        return True
    istemci = _redis_al()
    if istemci is None:
        return True
    try:
        return bool(int(istemci.eval(
            _SAYAC_YENILE,
            1,
            _sayac_kilit_anahtar(dukkan),
            token,
            str(_SAYAC_KILIT_SANIYE),
        ) or 0))
    except Exception as hata:
        _redis_dusur(hata)
        return True


def _sayac_kilidi_birak(dukkan, token):
    if not REDIS_URL or not token:
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        istemci.eval(_SAYAC_BIRAK, 1, _sayac_kilit_anahtar(dukkan), token)
    except Exception as hata:
        _redis_dusur(hata)


def _sayac_kilidi_canli_tut(dukkan):
    """Kilit süresini uzatır. Uzamazsa 59b4e98 döngüsü yine de devam eder."""
    sahip = _sayac_sahip.get(dukkan)
    if sahip:
        _sayac_kilidi_yenile(dukkan, sahip[1])


def _sayac_jetonu(gorev_id):
    return f"{int(gorev_id)}:{uuid.uuid4().hex}"


def dukkan_geri_sayim_gorevi(dukkan, gorev_id, akis="standart", kilit_jetonu=None):
    """Redis kilidi bu görevde değilse döngü açılmaz. Eski görev numarası yeniye yenilir."""
    token = kilit_jetonu or _sayac_jetonu(gorev_id)
    if kilit_jetonu:
        if not _sayac_kilidi_yenile(dukkan, token) and _sayac_kilidi_al(dukkan, gorev_id, token) == "dolu":
            return
    elif _sayac_kilidi_al(dukkan, gorev_id, token) == "dolu":
        return
    _sayac_sahip[dukkan] = (int(gorev_id), token)
    try:
        oda = odalar[dukkan]
        _dukkan_geri_sayim_govdesi(dukkan, gorev_id, akis, oda)
    finally:
        if _sayac_sahip.get(dukkan) == (int(gorev_id), token):
            _sayac_sahip.pop(dukkan, None)
        _sayac_kilidi_birak(dukkan, token)


def _dukkan_geri_sayim_govdesi(dukkan, gorev_id, akis, oda):
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

    _iki_odayi(
        dukkan,
        "tanitim_basladi",
        {"dukkan": dukkan, "sure": 120, "sayac_gorunsun": False},
    )

    gizli_sayac = 120
    while oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id and gizli_sayac > 0:
        _sayac_kilidi_canli_tut(dukkan)
        _iki_odayi(
            dukkan,
            "tanitim_sayac",
            {"dukkan": dukkan, "kalan": gizli_sayac, "sayac_gorunsun": False},
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

        _iki_odayi(
            dukkan,
            "sayac_baslat",
            {"dukkan": dukkan, "kalan": 60, "sayac_gorunsun": True},
        )

        son_uyari_yapildi = False
        while oda["sayac_aktif"] and oda["sayac_gorev_id"] == gorev_id:
            _sayac_kilidi_canli_tut(dukkan)
            if oda["sayac_kalan"] <= 0:
                break

            if oda["sayac_kalan"] > 15:
                son_uyari_yapildi = False

            if oda["sayac_kalan"] == 15 and not son_uyari_yapildi:
                son_uyari_yapildi = True
                bota_haber_ver("geri_sayim_uyari", {"dukkan": dukkan, "kalan_saniye": 15})
                _anonsu_arkada(dukkan, "Son 15 saniye dostlar, eser elden gidiyor!")

            oda["sayac_nabiz"] = time.time()
            _iki_odayi(
                dukkan,
                "sayac_guncelle",
                sayac_paketi(dukkan, oda["sayac_kalan"], "karar"),
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
                    if oda["durum"].get("kazanan_id"):
                        _yazma_icin_kilitle(Kullanici, oda["durum"].get("kazanan_id"))
                    urun = _yazma_icin_kilitle(Urun, biten_urun_id)
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
                            stok_satis_sonrasi_dus(urun)
                            hedef_sepet = urunu_kazanana_sepete_ekle(
                                biten_urun_id,
                                oda["durum"]["kazanan"],
                                oda["durum"]["kazanan_id"],
                                commit=False,
                            )
                            db.session.commit()
                            if hedef_sepet:
                                _kazanan_sepetini_disari_aktar(biten_urun_id, hedef_sepet)
                            oda["durum"]["durum"] = "Satıldı"
                            _son_satisi_yaz(
                                oda,
                                oda["durum"]["kazanan"],
                                oda["durum"]["pey"],
                                biten_urun_id,
                            )
                        else:
                            urun.durum = "Pas" if oda.get("mevat_asama") == "karar" else "Aktif"
                            urun.kazanan_adi = "Yok"
                            urun.kazanan_id = None
                            db.session.add(urun)
                            db.session.commit()
                            oda["durum"]["durum"] = "Pas"

            invalidate_vitrin_cache()
            _iki_odayi(
                dukkan,
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
            )

            bota_haber_ver(
                "satis_bitti",
                {
                    "dukkan": dukkan,
                    "kazanan": oda["durum"]["kazanan"] if pey_var_mi else "Yok",
                    "fiyat": oda["durum"]["pey"] if pey_var_mi else 0,
                },
            )

            if pey_var_mi:
                _satis_kapanisini_isaretle(dukkan, oda)
                ahmet_usta_anons_yap(
                    dukkan,
                    ahmet_satis_cumlesi(satis_lot, satis_ad, oda["durum"]["kazanan"]),
                    tur="satis",
                )
                _otonom_disi_satisi_bosalt(dukkan, oda)
            else:
                oda["lot_kapanis"] = "pas"
                _sahneyi_bosa_al(dukkan, "pas")
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
            if pey_var_mi:
                _admin_satisi_duyur(
                    dukkan, biten_urun_id, oda["durum"]["kazanan"], oda["durum"]["pey"], "sure",
                )


def _admin_satisi_duyur(dukkan, urun_id, kazanan, fiyat, neden):
    """Satış anında yönetici paneli yenilemeden ciro ve havuzu güncellesin."""
    try:
        tutar = float(fiyat or 0)
    except (TypeError, ValueError):
        tutar = 0
    _iki_odayi(
        dukkan,
        "admin_satis_guncelle",
        {
            "dukkan": dukkan,
            "urun_id": urun_id,
            "kazanan": kazanan or "",
            "fiyat": tutar,
            "neden": neden,
        },
    )


def _nabiz_dokun(dukkan):
    oda = odalar.get(dukkan)
    if oda is not None:
        oda["son_hareket"] = time.time()


_SESSIZLIK_KALIPLARI = (
    "Burada mısınız dostlar, salon çok sessiz kaldı! {eser} {pey} lirada duruyor, bu fiyata bu parça kaçmaz, kim artırıyor?",
    "Ayıp oluyor ama uyandınız mı? {eser} {pey} lirada naz yapıyor, bir el kalksın da ben de nefes alayım.",
    "Mikrofon bende, salon uykuda. {eser} için {pey} lira konuşuluyor, sadece bakanlar değil alanlar kazansın!",
    "Dostlar çay soğumadan bir pey gelsin. {eser} {pey} lirada bekliyor, kaçırırsanız sonra bana bakmayın.",
)
_sessizlik_sira = 0


def _sessizlik_anonsu(dukkan):
    return


def _sessizlik_dongusu():
    time.sleep(8)
    while True:
        try:
            simdi = time.time()
            for dukkan, oda in list(odalar.items()):
                if not oda.get("aktif_urun_id"):
                    continue
                durum = str((oda.get("durum") or {}).get("durum") or "")
                if durum in ("Satıldı", "Pas", "bosta"):
                    continue
                son = float(oda.get("son_hareket") or 0)
                if son <= 0:
                    oda["son_hareket"] = simdi
                    continue
                if simdi - son < 25:
                    continue
                if simdi - float(oda.get("son_sessizlik") or 0) < 25:
                    continue
                oda["son_sessizlik"] = simdi
                oda["son_hareket"] = simdi
                socketio.start_background_task(_sessizlik_anonsu, dukkan)
        except Exception as hata:
            print(f"[SESSİZLİK] {hata}", flush=True)
        time.sleep(5)


def _otonom_urun_satildi(urun):
    if urun is None or urun.silindi_mi is True:
        return True
    yazi = str(urun.durum or "").strip().casefold().replace("ı", "i")
    return yazi == "satildi"


def _otonom_satilmamis_lotlar(dukkan):
    adaylar = (
        Urun.query.filter(
            Urun.dukkan_kodu == dukkan,
            or_(Urun.silindi_mi.is_(False), Urun.silindi_mi.is_(None)),
        )
        .order_by(Urun.lot_no.asc(), Urun.id.asc())
        .all()
    )
    return [urun for urun in adaylar if not _otonom_urun_satildi(urun)]


def _otonom_secere_oku(dukkan, urun, acilis):
    ad = (urun.urun_adi or "Eser").strip()
    aciklama = urun.tanitim_yazisi or ""
    metin = (ahmet_secere_metni(ad, aciklama, acilis) or "").strip()
    if not metin:
        metin = _hazir_secere(urun, acilis)
    if metin:
        ahmet_usta_anons_yap(dukkan, metin, tur="tanitim")


_otonom_isleyen = {}
_otonom_anons = {}

_OTONOM_GOREV_AL = """
local simdi = tonumber(ARGV[2])
local v = redis.call('get', KEYS[1])
if not v then
    redis.call('set', KEYS[1], ARGV[1] .. ':' .. ARGV[2], 'EX', 90)
    return 1
end
local ts = tonumber(string.match(v, ':(%d+)$') or '0')
if simdi - ts > 30 then
    redis.call('set', KEYS[1], ARGV[1] .. ':' .. ARGV[2], 'EX', 90)
    return 1
end
return 0
"""

_OTONOM_GOREV_YENILE = """
local v = redis.call('get', KEYS[1])
local token = ARGV[1]
local n = string.len(token)
if v and string.sub(v, 1, n) == token and string.sub(v, n + 1, n + 1) == ':' then
    redis.call('set', KEYS[1], token .. ':' .. ARGV[2], 'EX', 90)
    return 1
end
return 0
"""

_OTONOM_GOREV_BIRAK = """
local v = redis.call('get', KEYS[1])
local token = ARGV[1]
local n = string.len(token)
if v and string.sub(v, 1, n) == token and string.sub(v, n + 1, n + 1) == ':' then
    return redis.call('del', KEYS[1])
end
return 0
"""


def _otonom_gorev_anahtar(dukkan):
    return "mezat:otonom:gorev:" + str(dukkan)


def _otonom_gorev_al(dukkan, token):
    """REDIS_URL varken kira alınamazsa döngü başlamaz. Yerel bellek ikinci döngü açmaz."""
    if not REDIS_URL:
        return True
    istemci = _redis_al()
    if istemci is None:
        print(f"[OTONOM] redis yok, döngü açılmadı ({dukkan})", flush=True)
        return False
    try:
        return bool(istemci.eval(
            _OTONOM_GOREV_AL,
            1,
            _otonom_gorev_anahtar(dukkan),
            token,
            str(int(time.time())),
        ))
    except Exception as hata:
        _redis_dusur(hata)
        print(f"[OTONOM] kira alınamadı, döngü açılmadı ({dukkan})", flush=True)
        return False


def _otonom_gorev_yenile(dukkan, token):
    if not token:
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        istemci.eval(
            _OTONOM_GOREV_YENILE,
            1,
            _otonom_gorev_anahtar(dukkan),
            token,
            str(int(time.time())),
        )
    except Exception as hata:
        _redis_dusur(hata)


def _otonom_gorev_birak(dukkan, token):
    if not token:
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        istemci.eval(_OTONOM_GOREV_BIRAK, 1, _otonom_gorev_anahtar(dukkan), token)
    except Exception as hata:
        _redis_dusur(hata)


_otonom_devir_basladi = False


def _otonom_kirayi_devral():
    """Kirası 30 saniyedir yenilenmeyen dükkanı bu worker'ın döngüsü alır.

    Redis kaydı _otonom_gorev_al içinde 30 saniyelik tazelik ister.
    Redis yokken devralma atlanır; başlatan istek kendi döngüsünü kurar.
    """
    socketio.sleep(8)
    while True:
        try:
            if not REDIS_URL or _redis_al() is None:
                socketio.sleep(5)
                continue
            for dukkan in list(DUKKANLAR.keys()):
                oda = odalar.get(dukkan)
                if oda is None or not oda.get("otonom_aktif"):
                    continue
                if _otonom_isleyen.get(dukkan):
                    continue
                _otonom_donguyu_garantile(dukkan)
        except Exception as hata:
            print(f"[OTONOM] devir: {hata}", flush=True)
        socketio.sleep(5)


def _otonom_devir_baslat():
    global _otonom_devir_basladi
    if _otonom_devir_basladi:
        return
    _otonom_devir_basladi = True
    socketio.start_background_task(_otonom_kirayi_devral)


def _otonom_donguyu_garantile(dukkan):
    """Bu süreçte döngü yoksa başlat. Redis kaydı tek başına 'çalışıyor' sayılmaz."""
    if _otonom_isleyen.get(dukkan):
        return
    token = uuid.uuid4().hex
    if not _otonom_gorev_al(dukkan, token):
        return
    _otonom_isleyen[dukkan] = token
    try:
        socketio.start_background_task(dukkan_otonom_dongusu, dukkan, token)
    except Exception:
        if _otonom_isleyen.get(dukkan) == token:
            _otonom_isleyen.pop(dukkan, None)
        _otonom_gorev_birak(dukkan, token)
        raise


def _otonom_sayaci_baslat(dukkan, urun):
    """Kilit, hazırlanan görev numarasını taşır. 0 numaralı jeton yeni turu ezerdi."""
    oda = odalar[dukkan]
    if urun is None:
        return False
    with oda_kilitleri[dukkan]:
        if not oda.get("otonom_aktif") or oda.get("sayac_aktif"):
            return False
        oda["aktif_urun_id"] = urun.id
        gorev = _pazarligi_hazirla(oda, urun)
    token = _sayac_jetonu(gorev)
    if _sayac_kilidi_al(dukkan, gorev, token) == "dolu":
        oda["sayac_aktif"] = False
        return False
    socketio.start_background_task(
        dukkan_geri_sayim_gorevi, dukkan, gorev, "pazarlik", token
    )
    return True


def _otonom_siradaki_lot(dukkan, son_biten_id):
    oda = odalar[dukkan]
    if oda.get("sayac_aktif") and not oda.get("aktif_urun_id"):
        oda["sayac_aktif"] = False
    secilen_urun = None
    acilis_fiyati = 0
    kazanan = "Yok"
    kazanan_id = None
    yeniden_sahne = True

    if oda.get("aktif_urun_id") and not oda.get("sayac_aktif"):
        sahnedeki = db.session.get(Urun, oda["aktif_urun_id"])
        if not _otonom_urun_satildi(sahnedeki):
            secilen_urun = sahnedeki
            acilis_fiyati = float(oda["durum"].get("pey") or sahnedeki.acilis_fiyati or 0)
            kazanan = oda["durum"].get("kazanan") or "Yok"
            kazanan_id = oda["durum"].get("kazanan_id")
            yeniden_sahne = int(oda.get("sahne_yayin_nesil") or 0) != int(oda.get("sahne_nesil") or 0)
        else:
            oda["aktif_urun_id"] = None

    if secilen_urun is None:
        en_iyi_ot = (
            OnTeklif.query.filter_by(dukkan_kodu=dukkan)
            .order_by(OnTeklif.id.asc())
            .first()
        )
        if en_iyi_ot:
            on_urun = db.session.get(Urun, en_iyi_ot.urun_id)
            if (
                on_urun is not None
                and not _otonom_urun_satildi(on_urun)
                and on_urun.id != son_biten_id
            ):
                secilen_urun = on_urun
                acilis_fiyati = float(en_iyi_ot.teklif)
                kazanan = en_iyi_ot.musteri_adi
                kazanan_id = en_iyi_ot.musteri_id
            elif on_urun is not None and _otonom_urun_satildi(on_urun):
                db.session.delete(en_iyi_ot)
                db.session.commit()

    if secilen_urun is None:
        aciklar = _otonom_satilmamis_lotlar(dukkan)
        if son_biten_id and len(aciklar) > 1:
            biten = next((u for u in aciklar if u.id == son_biten_id), None)
            diger = [u for u in aciklar if u.id != son_biten_id]
            esik = int(biten.lot_no or 0) if biten is not None else -1
            secilen_urun = next(
                (u for u in diger if int(u.lot_no or 0) > esik),
                diger[0] if diger else None,
            )
        elif aciklar:
            secilen_urun = aciklar[0]
        if secilen_urun is not None and acilis_fiyati == 0 and kazanan == "Yok":
            acilis_fiyati = float(secilen_urun.acilis_fiyati or 0)

    if secilen_urun is None:
        return None
    return secilen_urun, acilis_fiyati, kazanan, kazanan_id, yeniden_sahne


def _otonom_sahneye_yayinla(dukkan, secilen_urun, acilis_fiyati, kazanan, kazanan_id):
    """Lotu mezat_oda, Redis ve izleyici soketine yazar. Sayım sürüyorsa dokunmaz."""
    oda = odalar[dukkan]
    with oda_kilitleri[dukkan]:
        if not oda.get("otonom_aktif") or oda.get("sayac_aktif"):
            return False
        if (
            oda.get("aktif_urun_id") == secilen_urun.id
            and int(oda.get("sahne_yayin_nesil") or -1) == int(oda.get("sahne_nesil") or 0)
        ):
            return False
        oda["aktif_urun_id"] = secilen_urun.id
        oda["durum"]["durum"] = "Sayim"
        oda["durum"]["pey"] = acilis_fiyati
        oda["durum"]["kazanan"] = kazanan
        oda["durum"]["kazanan_id"] = kazanan_id
        oda["sahne_yayin_nesil"] = int(oda.get("sahne_nesil") or 0)
        secilen_urun.guncel_fiyat = acilis_fiyati
        if not _otonom_urun_satildi(secilen_urun):
            secilen_urun.durum = "Aktif"
        db.session.commit()
        paket = secilen_urun.to_dict()
        lot_no = secilen_urun.lot_no
        baslik = secilen_urun.urun_adi
        hemen_al = secilen_urun.hemen_al_fiyati or 0
        tanitim = secilen_urun.tanitim_yazisi or ""
        urun_id = secilen_urun.id
    for _takip in UrunTakip.query.filter_by(urun_id=urun_id).all():
        socketio.emit(
            "urun_takip_bildirimi",
            {
                "urun_id": urun_id,
                "mesaj": f"Takip ettiğiniz Lot #{lot_no} ({baslik}) sahneye alındı!",
            },
        )
    sahneyi_herkese_yayinla(
        dukkan,
        paket,
        {
            "dukkan": dukkan,
            "urun_id": urun_id,
            "pey": acilis_fiyati,
            "kazanan": kazanan,
            "kazanan_id": kazanan_id,
        },
        yeni_urun=True,
    )
    bota_haber_ver(
        "urun_sahneye_aldi",
        {
            "dukkan": dukkan,
            "lot": lot_no,
            "baslik": baslik,
            "acilis": acilis_fiyati,
            "guncel_fiyat": acilis_fiyati,
            "hemen_al": hemen_al,
            "tanitim_yazisi": tanitim,
        },
    )
    return True


def _otonom_baslangic_sahnesi(dukkan):
    """Başlat isteği lotu bekletmeden sahneye yazar ve yayınlar."""
    oda = odalar[dukkan]
    if oda.get("aktif_urun_id") and oda.get("sayac_aktif"):
        urun = db.session.get(Urun, oda["aktif_urun_id"])
        if not _otonom_urun_satildi(urun):
            return getattr(urun, "lot_no", None)
    secim = _otonom_siradaki_lot(dukkan, None)
    if secim is None:
        return None
    urun, acilis, kazanan, kazanan_id, yeniden = secim
    if yeniden:
        _otonom_sahneye_yayinla(dukkan, urun, acilis, kazanan, kazanan_id)
    return getattr(urun, "lot_no", None)


def _otonom_lotu_islet(dukkan, son_biten_id, token):
    oda = odalar[dukkan]
    secim = _otonom_siradaki_lot(dukkan, son_biten_id)
    if secim is None:
        return None
    urun, acilis, kazanan, kazanan_id, yeniden = secim
    if yeniden:
        _otonom_sahneye_yayinla(dukkan, urun, acilis, kazanan, kazanan_id)
    if not oda.get("otonom_aktif"):
        return None
    _otonom_gorev_yenile(dukkan, token)
    _otonom_secere_oku(dukkan, urun, acilis)
    _otonom_sayaci_baslat(dukkan, urun)
    return urun


def dukkan_otonom_dongusu(dukkan, token=None):
    oda = odalar[dukkan]
    kural = DUKKANLAR[dukkan]
    if not token:
        token = uuid.uuid4().hex
        if not _otonom_gorev_al(dukkan, token):
            return
    _otonom_isleyen[dukkan] = token
    oda["otonom_dongu"] = True
    son_biten_id = None
    print(f"🤖 [{kural['ad']}] 3 Dakikalık Otonom Mezat Başlatıldı...")
    try:
        while oda.get("otonom_aktif"):
            _otonom_gorev_yenile(dukkan, token)
            with app.app_context():
                try:
                    if not (oda.get("aktif_urun_id") and oda.get("sayac_aktif")):
                        secilen_urun = _otonom_lotu_islet(dukkan, son_biten_id, token)
                        if secilen_urun is None and not (
                            oda.get("aktif_urun_id") and oda.get("sayac_aktif")
                        ):
                            socketio.sleep(3)
                            continue
                        if secilen_urun is not None:
                            son_biten_id = secilen_urun.id

                    beklenen = 0
                    while (
                        oda.get("otonom_aktif")
                        and oda.get("aktif_urun_id")
                        and oda.get("sayac_aktif")
                    ):
                        if beklenen % 5 == 0:
                            _otonom_gorev_yenile(dukkan, token)
                        nabiz = float(oda.get("sayac_nabiz") or 0)
                        if nabiz > 0 and beklenen > 8 and time.time() - nabiz > 8:
                            print(f"[OTONOM] sayaç nabzı düştü, tur yenileniyor ({dukkan})", flush=True)
                            oda["sayac_aktif"] = False
                            oda["sayac_gorev_id"] = int(oda.get("sayac_gorev_id") or 0) + 1
                            break
                        beklenen += 1
                        socketio.sleep(1)
                    if not oda.get("otonom_aktif"):
                        break
                    kapanis = ""
                    with oda_kilitleri[dukkan]:
                        kapanis = oda.get("lot_kapanis") or ""
                        oda["lot_kapanis"] = ""
                    if kapanis == "satildi":
                        _satis_tebrik_bitisini_bekle(dukkan, 15)
                        if oda.get("otonom_aktif"):
                            ahmet_usta_anons_yap(
                                dukkan,
                                "Bir sonraki ürüne geçiyorum.",
                                tur="sayac",
                            )
                            socketio.sleep(3.5)
                            _sahneyi_bosa_al(dukkan, "satildi")
                    else:
                        socketio.sleep(6)
                except Exception as hata:
                    db.session.rollback()
                    print(f"[OTONOM] {dukkan}: {hata}")
                    socketio.sleep(3)
                finally:
                    db.session.remove()
    finally:
        if _otonom_isleyen.get(dukkan) == token:
            _otonom_isleyen.pop(dukkan, None)
        _otonom_gorev_birak(dukkan, token)
        oda["otonom_dongu"] = False
        _otonom_anons.pop(dukkan, None)


# VERİTABANI ONARIMI: Tüm eksik kolonları (ip_adresi dahil) PostgreSQL'e otomatik ekler
def _kullanici_kolonu_var_mi(kolon):
    if IS_SQLITE:
        satirlar = db.session.execute(text("PRAGMA table_info(kullanici)")).fetchall()
        return any(str(satir[1]) == kolon for satir in satirlar)
    bulunan = db.session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'kullanici' AND column_name = :kolon"
        ),
        {"kolon": kolon},
    ).first()
    return bulunan is not None


def kullanici_ve_kara_liste_onar():
    """Kara liste tablosu ve kayıtta kullanılan kullanıcı sütunları eksikse ekler."""
    KaraListe.__table__.create(bind=db.engine, checkfirst=True)
    zorunlu = (
        ("sozlesme_onay", "BOOLEAN DEFAULT TRUE"),
        ("otp_code", "VARCHAR(6)"),
        ("otp_bitis", "TIMESTAMP"),
        ("dogrulama_kodu", "VARCHAR(6)"),
        ("is_verified", "BOOLEAN DEFAULT FALSE"),
        ("cihaz_kodu", "VARCHAR(64)"),
        ("ip_adresi", "VARCHAR(50)"),
    )
    for kolon, tip in zorunlu:
        if _kullanici_kolonu_var_mi(kolon):
            continue
        db.session.execute(text(f"ALTER TABLE kullanici ADD COLUMN {kolon} {tip}"))
        db.session.commit()


def _urun_kolon_adlari():
    """urun tablosunda gerçekten bulunan sütun adları. API alanı dukkan/lot değil."""
    if IS_SQLITE:
        satirlar = db.session.execute(text("PRAGMA table_info(urun)")).fetchall()
        return {str(satir[1]) for satir in satirlar}
    bulunan = db.session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'urun'"
        )
    ).fetchall()
    return {str(satir[0]) for satir in bulunan}


def urun_vitrin_indekslerini_hazirla():
    """Dükkan sıralaması ve lot araması için yalnızca var olan sütunlara indeks."""
    kolonlar = _urun_kolon_adlari()
    adaylar = []
    if {"dukkan_kodu", "id"} <= kolonlar:
        adaylar.append(
            "CREATE INDEX IF NOT EXISTS ix_urun_dukkan_id ON urun (dukkan_kodu, id)"
        )
    if {"dukkan_kodu", "lot_no"} <= kolonlar:
        adaylar.append(
            "CREATE INDEX IF NOT EXISTS ix_urun_dukkan_lot ON urun (dukkan_kodu, lot_no)"
        )
    if {"dukkan_kodu", "durum", "lot_no"} <= kolonlar:
        adaylar.append(
            "CREATE INDEX IF NOT EXISTS ix_urun_dukkan_durum_lot "
            "ON urun (dukkan_kodu, durum, lot_no)"
        )
    if "kategori" in kolonlar:
        adaylar.append(
            "CREATE INDEX IF NOT EXISTS ix_urun_kategori_arama ON urun (kategori)"
        )
    for sql in adaylar:
        db.session.execute(text(sql))
    if adaylar:
        db.session.commit()


def _tablo_indeks_kolonlari(tablo):
    """Tablodaki indekslerin soldan sağa kolon dizileri."""
    if not tablo or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tablo):
        return []
    if IS_SQLITE:
        adlar = db.session.execute(text(f"PRAGMA index_list({tablo})")).fetchall()
        bulunan = []
        for satir in adlar:
            ad = str(satir[1]).replace('"', "")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ad):
                continue
            kolonlar = db.session.execute(text(f"PRAGMA index_info({ad})")).fetchall()
            bulunan.append(tuple(str(kolon[2]) for kolon in kolonlar if kolon[2]))
        return bulunan
    satirlar = db.session.execute(
        text(
            "SELECT i.relname, a.attname, k.n "
            "FROM pg_index x "
            "JOIN pg_class t ON t.oid = x.indrelid "
            "JOIN pg_class i ON i.oid = x.indexrelid "
            "JOIN LATERAL unnest(x.indkey) WITH ORDINALITY AS k(attnum, n) ON true "
            "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum "
            "WHERE t.relname = :tablo AND k.attnum > 0 "
            "ORDER BY i.relname, k.n"
        ),
        {"tablo": tablo},
    ).fetchall()
    gruplar = {}
    sira = []
    for _ad, kolon, _n in satirlar:
        if _ad not in gruplar:
            gruplar[_ad] = []
            sira.append(_ad)
        gruplar[_ad].append(str(kolon))
    return [tuple(gruplar[ad]) for ad in sira]


def _indeks_kapsaniyor(istenen, mevcutlar):
    """İstenen kolon dizisi, mevcut bir indeksin sol öneki ise yeni indeks gerekmez."""
    hedef = tuple(istenen)
    for mevcut in mevcutlar:
        if mevcut[: len(hedef)] == hedef:
            return True
    return False


def _indeks_guvenli_olustur(sql):
    """CREATE INDEX IF NOT EXISTS. PostgreSQL yazmayı kilitlemeden dener."""
    komut = " ".join(str(sql).split())
    if not komut.upper().startswith("CREATE INDEX IF NOT EXISTS "):
        raise ValueError("yalnizca CREATE INDEX IF NOT EXISTS")
    try:
        if IS_SQLITE:
            db.session.execute(text(komut))
            db.session.commit()
            return
        eszamanli = komut.replace(
            "CREATE INDEX IF NOT EXISTS",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS",
            1,
        )
        baglanti = db.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            baglanti.execute(text(eszamanli))
        finally:
            baglanti.close()
    except Exception as hata:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[BOOT] indeks atlandi: {hata}", flush=True)


def sicak_sorgu_indekslerini_hazirla():
    """Ana vitrin ve /api/durum sıcak kolonları. Eksikse oluşturur, hata açılışı durdurmaz.

    Şemada dükkan anahtarı dukkan_kodu'dur. Aktif vitrin süzgeci silindi_mi, durum ve lot_no
    ile kurulur. aktif veya dukkan_id kolonu gerçekten varsa onlara da indeks eklenir.
    """
    try:
        kolonlar = _urun_kolon_adlari()
    except Exception as hata:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[BOOT] sicak indeks kontrolu atlandi: {hata}", flush=True)
        return
    try:
        mevcut = _tablo_indeks_kolonlari("urun")
    except Exception as hata:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[BOOT] indeks listesi okunamadi, IF NOT EXISTS denenecek: {hata}", flush=True)
        mevcut = []
    adaylar = []
    if {"dukkan_kodu", "silindi_mi", "durum", "lot_no"} <= kolonlar:
        hedef = ("dukkan_kodu", "silindi_mi", "durum", "lot_no")
        if not _indeks_kapsaniyor(hedef, mevcut):
            adaylar.append(
                "CREATE INDEX IF NOT EXISTS ix_urun_vitrin_aktif "
                "ON urun (dukkan_kodu, silindi_mi, durum, lot_no)"
            )
    if {"silindi_mi", "id"} <= kolonlar:
        hedef = ("silindi_mi", "id")
        if not _indeks_kapsaniyor(hedef, mevcut):
            adaylar.append(
                "CREATE INDEX IF NOT EXISTS ix_urun_api_durum_silindi "
                "ON urun (silindi_mi, id)"
            )
    if "durum" in kolonlar and not _indeks_kapsaniyor(("durum",), mevcut):
        adaylar.append(
            "CREATE INDEX IF NOT EXISTS ix_urun_durum_sicak ON urun (durum)"
        )
    if "aktif" in kolonlar and not _indeks_kapsaniyor(("aktif",), mevcut):
        adaylar.append("CREATE INDEX IF NOT EXISTS ix_urun_aktif ON urun (aktif)")
    if "dukkan_id" in kolonlar and not _indeks_kapsaniyor(("dukkan_id",), mevcut):
        adaylar.append(
            "CREATE INDEX IF NOT EXISTS ix_urun_dukkan_id_kolon ON urun (dukkan_id)"
        )
    for sql in adaylar:
        _indeks_guvenli_olustur(sql)


def veritabani_tablolari_onar():
    print("[BOOT] veritabani onarimi basladi", flush=True)
    with app.app_context():
        try:
            print("[BOOT] db.create_all basliyor", flush=True)
            db.create_all()
            print("[BOOT] db.create_all bitti", flush=True)
            print("[BOOT] kullanici ve kara liste kontrolu", flush=True)
            kullanici_ve_kara_liste_onar()
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
                ("urun", "kaynak_tipi", "VARCHAR(50) DEFAULT 'manuel'"),
                ("urun", "stok_adedi", "INTEGER DEFAULT 1"),
                ("urun", "xml_kaynak_url", "VARCHAR(500)"),
                ("urun", "siparis_durumu", "VARCHAR(30) DEFAULT 'Odeme_Bekliyor'"),
                ("urun", "kargo_takip_no", "VARCHAR(100)"),
                ("urun", "kargo_firmasi", "VARCHAR(50) DEFAULT 'Yurtiçi Kargo'"),
                ("urun", "kargo_barkod_url", "TEXT"),
                ("urun", "silindi_mi", "BOOLEAN DEFAULT FALSE"),
                ("urun", "silinme_tarihi", "TIMESTAMP"),
                ("on_teklif", "dukkan_kodu", "VARCHAR(30) DEFAULT 'gumus'"),
                ("on_teklif", "musteri_id", "INTEGER"),
                ("teklif", "dukkan_kodu", "VARCHAR(30) DEFAULT 'gumus'"),
                ("teklif", "musteri_id", "INTEGER"),
                ("teklif", "ip_adresi", "VARCHAR(50)"),
                ("sikayet_oneri", "ip_adresi", "VARCHAR(50)"),
            ]
            print("[BOOT] kolon ve ayar kontrolleri", flush=True)
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
                        text(
                            f"ALTER TABLE {tablo} ADD COLUMN IF NOT EXISTS {kolon} {tip}"
                        )
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    try:
                        db.session.execute(
                            text(f"ALTER TABLE {tablo} ADD COLUMN {kolon} {tip}")
                        )
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
            try:
                db.session.execute(text(
                    "UPDATE urun SET kaynak_tipi = 'manuel' "
                    "WHERE kaynak_tipi IS NULL OR kaynak_tipi = ''"
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()
            print("[BOOT] kolon ve ayar kontrolleri bitti", flush=True)
            try:
                print("[BOOT] hemen al bosluk kontrolu", flush=True)
                xml_hemen_al_bosluklarini_doldur()
            except Exception:
                db.session.rollback()
            for genislet in (
                "ALTER TABLE urun ALTER COLUMN video TYPE TEXT",
                "ALTER TABLE urun ALTER COLUMN ses_dosyasi TYPE TEXT",
            ):
                try:
                    db.session.execute(text(genislet))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        except Exception as hata:
            db.session.rollback()
            print(f"[BOOT] veritabani onarim hatasi: {hata}", flush=True)
        finally:
            db.session.remove()
        try:
            print("[BOOT] icerik maddeleri kontrolu", flush=True)
            icerik_maddelerini_hazirla()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
        try:
            print("[BOOT] urun kategori indeksleri", flush=True)
            urun_vitrin_indekslerini_hazirla()
            print("[BOOT] sicak sorgu indeksleri", flush=True)
            sicak_sorgu_indekslerini_hazirla()
            print("[BOOT] veritabani onarimi bitti", flush=True)
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
        finally:
            try:
                db.session.remove()
            except Exception:
                pass


@app.route("/static/uploads/<path:filename>")
def serve_uploads(filename):
    mime = _YUKLEME_MIME.get(_dosya_uzantisi(filename))
    ekstra = {"conditional": True}
    if mime:
        ekstra["mimetype"] = mime
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, **ekstra)


# ==========================================
# İZLEYİCİ, VİTRİN VE DÜKKAN ROTALARI
# ==========================================
def _urunleri_tek_sorguda(idler):
    """Aktif sahne ürünlerini döngü yerine tek IN sorgusuyla çeker."""
    benzersiz = []
    gorulen = set()
    for ham in idler:
        if not ham:
            continue
        try:
            uid = int(ham)
        except (TypeError, ValueError):
            continue
        if uid in gorulen:
            continue
        gorulen.add(uid)
        benzersiz.append(uid)
    if not benzersiz:
        return {}
    kayitlar = (
        Urun.query.filter(
            Urun.id.in_(benzersiz),
            or_(Urun.silindi_mi.is_(False), Urun.silindi_mi.is_(None)),
        ).all()
    )
    return {kayit.id: kayit for kayit in kayitlar}


def _ana_sayfa_html(dukkan):
    oda = odalar.get(dukkan) or {}
    durum = oda.get("durum") or {}
    ilk_sahne = {
        "durum": durum.get("durum") or "Bekliyor",
        "pey": durum.get("pey") or 0,
        "kazanan": durum.get("kazanan") or "Yok",
        "aktif_urun": None,
    }
    try:
        urun_id = oda.get("aktif_urun_id")
        bulunan = _urunleri_tek_sorguda([urun_id] if urun_id else [])
        urun = None
        if urun_id:
            try:
                urun = bulunan.get(int(urun_id))
            except (TypeError, ValueError):
                urun = None
        if urun is not None:
            ilk_sahne["aktif_urun"] = urun.to_dict()
    except Exception:
        db.session.rollback()
    finally:
        db.session.remove()
    return render_template(
        "index.html",
        secili_dukkan=dukkan,
        dukkanlar=DUKKANLAR,
        ilk_sahne=ilk_sahne,
    )


@app.route("/")
def izleyici_index():
    dukkan = dukkan_dogrula(request.args.get("dukkan", "gumus"))
    if _vitrin_oturumu_ozel_mi():
        return _ana_sayfa_html(dukkan)
    paket = vitrin_mikro_onbellek(
        "anasayfa:" + dukkan,
        lambda: _bayt_ve_gzip(_ana_sayfa_html(dukkan)),
    )
    return _paketten_yanit(paket, "text/html; charset=utf-8")


def _api_durum_govdesi():
    idler = []
    for kod in DUKKANLAR:
        oda = odalar.get(kod) or {}
        if oda.get("aktif_urun_id"):
            idler.append(oda.get("aktif_urun_id"))
    try:
        urunler = _urunleri_tek_sorguda(idler)
        cikti = {}
        for kod, bilgi in DUKKANLAR.items():
            oda = odalar.get(kod) or {}
            durum = oda.get("durum") or {}
            sahne = durum.get("durum") or "Bekliyor"
            uid = oda.get("aktif_urun_id")
            urun = None
            if uid:
                try:
                    urun = urunler.get(int(uid))
                except (TypeError, ValueError):
                    urun = None
            cikti[kod] = {
                "ad": bilgi.get("ad"),
                "sure": bilgi.get("sure"),
                "min_artis": bilgi.get("min_artis"),
                "canli": bool(uid) and sahne != "Satıldı",
                "durum": sahne,
                "pey": durum.get("pey") or 0,
                "kazanan": durum.get("kazanan") or "Yok",
                "kazanan_id": durum.get("kazanan_id"),
                "kalan_sayac": oda.get("sayac_kalan") or 0,
                "aktif_urun": urun.to_dict() if urun is not None else None,
            }
        return {"success": True, "dukkanlar": cikti}
    finally:
        db.session.remove()


@app.route("/api/durum", methods=["GET"])
def api_durum():
    try:
        paket = vitrin_mikro_onbellek(
            "api_durum",
            lambda: _bayt_ve_gzip(jsonify(_api_durum_govdesi()).get_data()),
        )
        return _paketten_yanit(paket, "application/json")
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "dukkanlar": {}}), 500
    finally:
        db.session.remove()


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/sw.js")
def service_worker():
    yanit = send_from_directory(
        os.path.join(basedir, "static"),
        "sw.js",
        mimetype="application/javascript",
    )
    yanit.headers["Service-Worker-Allowed"] = "/"
    yanit.headers["Cache-Control"] = "no-cache"
    return yanit


@app.route("/manifest.json")
def pwa_manifest():
    return send_from_directory(
        os.path.join(basedir, "static"),
        "manifest.json",
        mimetype="application/manifest+json",
    )


@app.route("/robots.txt")
def robots_txt():
    icerik = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Sitemap: https://halicantikcanli.com/sitemap.xml\n"
    )
    return Response(icerik, mimetype="text/plain")


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


@app.route("/icerik-maddeleri", methods=["GET"])
def icerik_maddeleri():
    tur = (request.args.get("tur") or "kural").strip()
    if tur not in ("rehber", "kural"):
        tur = "kural"
    try:
        return jsonify({"success": True, "tur": tur, "maddeler": icerik_maddelerini_oku(tur)})
    finally:
        db.session.remove()


@app.route("/urun-incele/<int:urun_id>", methods=["GET"])
def urun_incele(urun_id):
    try:
        urun = db.session.get(Urun, urun_id)
        if not urun or urun.silindi_mi:
            return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
        en_yuksek = (
            OnTeklif.query.filter_by(urun_id=urun.id)
            .order_by(OnTeklif.teklif.desc())
            .first()
        )
        paket = urun.to_dict()
        if en_yuksek:
            paket["on_teklif"] = {
                "tutar": float(en_yuksek.teklif or 0),
                "musteri_adi": en_yuksek.musteri_adi or "",
            }
        else:
            paket["on_teklif"] = None
        return jsonify({"success": True, "urun": paket})
    finally:
        db.session.remove()


def _uye_satiri(kullanici):
    return {
        "id": kullanici.id,
        "ad_soyad": kullanici.ad_soyad or "",
        "telefon": kullanici.telefon or "",
        "email": kullanici.email or "",
        "adres": kullanici.adres or "",
        "durum": kullanici.durum or "",
    }


@app.route("/admin/uyeler", methods=["GET"])
@admin_required
def admin_uyeler():
    try:
        kayitlar = (
            Kullanici.query.filter(Kullanici.silindi_mi.is_(False))
            .order_by(Kullanici.id.desc())
            .limit(400)
            .all()
        )
        return jsonify({"success": True, "uyeler": [_uye_satiri(k) for k in kayitlar]})
    finally:
        db.session.remove()


@app.route("/admin/uye-kaydet", methods=["POST"])
@admin_required
def admin_uye_kaydet():
    veri = request.json or {}
    ad = (veri.get("ad_soyad") or veri.get("ad") or "").strip()
    tel = (veri.get("telefon") or veri.get("tel") or "").strip()
    mail = (veri.get("email") or veri.get("mail") or "").strip()
    adres = (veri.get("adres") or "").strip()
    if not ad or not tel:
        return jsonify({"success": False, "mesaj": "Ad soyad ve telefon zorunludur."})
    try:
        kayit_id = int(veri.get("id") or 0)
    except (TypeError, ValueError):
        kayit_id = 0
    try:
        cakisan = Kullanici.query.filter(
            Kullanici.silindi_mi.is_(False),
            Kullanici.id != kayit_id,
            Kullanici.telefon == tel,
        ).first()
        if cakisan:
            return jsonify({"success": False, "mesaj": "Bu telefon başka bir üyede kayıtlı."})
        if mail:
            posta = Kullanici.query.filter(
                Kullanici.silindi_mi.is_(False),
                Kullanici.id != kayit_id,
                func.lower(Kullanici.email) == mail.lower(),
            ).first()
            if posta:
                return jsonify({"success": False, "mesaj": "Bu e-posta başka bir üyede kayıtlı."})
        if kayit_id:
            kullanici = db.session.get(Kullanici, kayit_id)
            if not kullanici or kullanici.silindi_mi:
                return jsonify({"success": False, "mesaj": "Üye bulunamadı."})
        else:
            kullanici = Kullanici(
                ad_soyad=ad,
                telefon=tel,
                email=mail,
                adres=adres,
                onayli_mi=True,
                is_verified=True,
                durum="onayli",
                sozlesme_onay=True,
                puan=100.0,
            )
            db.session.add(kullanici)
        kullanici.ad_soyad = ad
        kullanici.telefon = tel
        kullanici.email = mail
        kullanici.adres = adres
        db.session.commit()
        return jsonify({
            "success": True,
            "mesaj": "Üye kaydedildi.",
            "uye": _uye_satiri(kullanici),
        })
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(hata)}), 500
    finally:
        db.session.remove()


@app.route("/admin/icerik-maddeleri", methods=["GET"])
@admin_required
def admin_icerik_maddeleri():
    try:
        return jsonify({
            "success": True,
            "rehber": icerik_maddelerini_oku("rehber"),
            "kural": icerik_maddelerini_oku("kural"),
        })
    finally:
        db.session.remove()


@app.route("/admin/icerik-madde-kaydet", methods=["POST"])
@admin_required
def admin_icerik_madde_kaydet():
    veri = request.json or {}
    tur = (veri.get("tur") or "").strip()
    baslik = (veri.get("baslik") or "").strip()
    metin = (veri.get("metin") or "").strip()
    if tur not in ("rehber", "kural"):
        return jsonify({"success": False, "mesaj": "Geçersiz içerik türü."})
    if not baslik or not metin:
        return jsonify({"success": False, "mesaj": "Başlık ve metin zorunludur."})
    try:
        kayit_id = int(veri.get("id") or 0)
    except (TypeError, ValueError):
        kayit_id = 0
    try:
        icerik_maddelerini_hazirla()
        if kayit_id:
            satir = db.session.get(IcerikMaddesi, kayit_id)
            if not satir or satir.tur != tur:
                return jsonify({"success": False, "mesaj": "Madde bulunamadı."})
        else:
            son = (
                IcerikMaddesi.query.filter_by(tur=tur)
                .order_by(IcerikMaddesi.sira.desc())
                .first()
            )
            satir = IcerikMaddesi(tur=tur, sira=(son.sira + 1) if son else 1)
            db.session.add(satir)
        satir.baslik = baslik[:200]
        satir.metin = metin
        db.session.commit()
        return jsonify({"success": True, "madde": icerik_maddesi_sozlugu(satir)})
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(hata)}), 500
    finally:
        db.session.remove()


@app.route("/admin/icerik-madde-sil", methods=["POST"])
@admin_required
def admin_icerik_madde_sil():
    veri = request.json or {}
    try:
        kayit_id = int(veri.get("id") or 0)
    except (TypeError, ValueError):
        kayit_id = 0
    try:
        satir = db.session.get(IcerikMaddesi, kayit_id) if kayit_id else None
        if not satir:
            return jsonify({"success": False, "mesaj": "Madde bulunamadı."})
        tur = satir.tur
        db.session.delete(satir)
        db.session.commit()
        kalan = (
            IcerikMaddesi.query.filter_by(tur=tur)
            .order_by(IcerikMaddesi.sira.asc(), IcerikMaddesi.id.asc())
            .all()
        )
        for sira, kayit in enumerate(kalan, start=1):
            kayit.sira = sira
        db.session.commit()
        return jsonify({"success": True})
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(hata)}), 500
    finally:
        db.session.remove()


@app.route("/admin/icerik-madde-sirala", methods=["POST"])
@admin_required
def admin_icerik_madde_sirala():
    veri = request.json or {}
    tur = (veri.get("tur") or "").strip()
    try:
        kayit_id = int(veri.get("id") or 0)
        yon = int(veri.get("yon") or 0)
    except (TypeError, ValueError):
        return jsonify({"success": False, "mesaj": "Sıra değiştirilemedi."})
    if tur not in ("rehber", "kural") or yon not in (-1, 1):
        return jsonify({"success": False, "mesaj": "Sıra değiştirilemedi."})
    try:
        satirlar = (
            IcerikMaddesi.query.filter_by(tur=tur)
            .order_by(IcerikMaddesi.sira.asc(), IcerikMaddesi.id.asc())
            .all()
        )
        yer = next((i for i, satir in enumerate(satirlar) if satir.id == kayit_id), -1)
        hedef = yer + yon
        if yer < 0 or hedef < 0 or hedef >= len(satirlar):
            return jsonify({"success": True, "maddeler": [icerik_maddesi_sozlugu(s) for s in satirlar]})
        satirlar[yer], satirlar[hedef] = satirlar[hedef], satirlar[yer]
        for sira, satir in enumerate(satirlar, start=1):
            satir.sira = sira
        db.session.commit()
        return jsonify({"success": True, "maddeler": [icerik_maddesi_sozlugu(s) for s in satirlar]})
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(hata)}), 500
    finally:
        db.session.remove()


@app.route("/ahmet-tanitim-geri", methods=["POST"])
def ahmet_tanitim_geri():
    return jsonify({"success": True, "url": AHMET_CIKIS_URL})


@app.route("/ahmet-metin-oku", methods=["POST"])
@admin_required
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
            or_(Urun.stok_adedi.is_(None), Urun.stok_adedi > 0),
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


_KURUMSAL_ALANLAR = (
    "sirketUnvani",
    "vergiDairesi",
    "mersisNo",
    "bankaAdi",
    "aliciAdi",
    "iban",
    "acikAdres",
    "haritaUrl",
    "telefon",
    "eposta",
)
_KURUMSAL_VARSAYILAN = {
    "sirketUnvani": "HALİÇ ANTİK MEZAT (Şirket Resmi Ünvanı Girilecek)",
    "vergiDairesi": "Vergi Dairesi ve VKN Girilecek",
    "mersisNo": "MERSİS No Girilecek",
    "bankaAdi": "Banka Adı Girilecek",
    "aliciAdi": "Resmi Hesap Sahibi Ünvanı Girilecek",
    "iban": "TR00 0000 0000 0000 0000 0000 00",
    "acikAdres": "Açık Şirket / Galeri Adresi Girilecek, İstanbul",
    "haritaUrl": "https://maps.google.com/?q=Istanbul",
    "telefon": "+90 (5XX) XXX XX XX",
    "eposta": "info@halicantikcanli.com",
}
_KURUMSAL_KILIT = threading.Lock()


_KURUMSAL_ANAHTAR = "kurumsal_bilgiler"


def _kurumsal_bilgi_dosyasi():
    return os.path.join(basedir, "instance", "kurumsal_bilgiler.json")


def _kurumsal_kaydi_normallestir(gelen):
    kayit = dict(_KURUMSAL_VARSAYILAN)
    if not isinstance(gelen, dict):
        return kayit
    for alan in _KURUMSAL_ALANLAR:
        if gelen.get(alan) is None:
            continue
        metin = str(gelen.get(alan)).strip()
        if metin:
            kayit[alan] = metin
    return kayit


def _kurumsal_json_oku():
    try:
        with open(_kurumsal_bilgi_dosyasi(), "r", encoding="utf-8") as f:
            gelen = json.load(f)
    except (OSError, ValueError):
        gelen = {}
    return _kurumsal_kaydi_normallestir(gelen)


def kurumsal_bilgileri_oku():
    with _KURUMSAL_KILIT:
        try:
            Ayar.__table__.create(bind=db.engine, checkfirst=True)
            satir = Ayar.query.filter_by(anahtar=_KURUMSAL_ANAHTAR).first()
            if satir and str(satir.deger or "").strip():
                try:
                    return _kurumsal_kaydi_normallestir(json.loads(satir.deger))
                except ValueError:
                    pass
            tasinan = _kurumsal_json_oku()
            metin = json.dumps(tasinan, ensure_ascii=False)
            if satir is None:
                db.session.add(Ayar(anahtar=_KURUMSAL_ANAHTAR, deger=metin))
            else:
                satir.deger = metin
            db.session.commit()
            return tasinan
        except Exception as hata:
            db.session.rollback()
            print(f"[KURUMSAL] Veritabanı okunamadı, yedek dosyaya düşüldü: {hata}")
            return _kurumsal_json_oku()


def _iban_denetim_yaz(onceki, sonraki):
    izlenen = ("iban", "bankaAdi", "aliciAdi")
    if all((onceki.get(alan) or "") == (sonraki.get(alan) or "") for alan in izlenen):
        return
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    yol = os.path.join(basedir, "instance", "iban_audit.log")
    satir = {
        "zaman": datetime.now().isoformat(timespec="seconds"),
        "ip": get_client_ip(),
        "once": {
            "unvan": onceki.get("sirketUnvani") or "",
            "banka": onceki.get("bankaAdi") or "",
            "alici": onceki.get("aliciAdi") or "",
            "iban": onceki.get("iban") or "",
        },
        "sonra": {
            "unvan": sonraki.get("sirketUnvani") or "",
            "banka": sonraki.get("bankaAdi") or "",
            "alici": sonraki.get("aliciAdi") or "",
            "iban": sonraki.get("iban") or "",
        },
    }
    with open(yol, "a", encoding="utf-8") as dosya:
        dosya.write(json.dumps(satir, ensure_ascii=False) + "\n")


def kurumsal_bilgileri_yaz(kayit):
    temiz = _kurumsal_kaydi_normallestir(kayit)
    metin = json.dumps(temiz, ensure_ascii=False)
    with _KURUMSAL_KILIT:
        Ayar.__table__.create(bind=db.engine, checkfirst=True)
        satir = Ayar.query.filter_by(anahtar=_KURUMSAL_ANAHTAR).first()
        if satir is None:
            db.session.add(Ayar(anahtar=_KURUMSAL_ANAHTAR, deger=metin))
        else:
            satir.deger = metin
        db.session.commit()


@app.route("/kurumsal-bilgiler", methods=["GET"])
def kurumsal_bilgiler_oku_api():
    return jsonify(kurumsal_bilgileri_oku())


@app.route("/admin/kurumsal-bilgiler", methods=["POST"])
@admin_required
def kurumsal_bilgiler_kaydet_api():
    gelen = request.get_json(silent=True) or {}
    if not isinstance(gelen, dict):
        gelen = {}
    onceki = kurumsal_bilgileri_oku()
    kayit = dict(onceki)
    for alan in _KURUMSAL_ALANLAR:
        if alan not in gelen:
            continue
        kayit[alan] = str(gelen.get(alan) or "").strip() or _KURUMSAL_VARSAYILAN[alan]
    try:
        kurumsal_bilgileri_yaz(kayit)
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": "Kurumsal bilgiler kaydedilemedi."}), 500
    try:
        _iban_denetim_yaz(onceki, kayit)
    except Exception:
        print("[IBAN] Denetim kaydı yazılamadı.")
    cevap = {"success": True, "mesaj": "✓ Kurumsal bilgiler kaydedildi"}
    cevap.update(kayit)
    return jsonify(cevap)


@app.route("/admin/urun-ara", methods=["GET"])
@admin_required
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


def _canli_asama_alanlari(oda):
    """Sayfa yenilenince iki ekranın okuduğu ortak sayaç alanları."""
    asama = str(oda.get("mevat_asama") or "").strip()
    sayac_aktif = bool(oda.get("sayac_aktif"))
    try:
        ham = int(oda.get("sayac_kalan") or 0)
    except (TypeError, ValueError):
        ham = 0
    if not asama:
        durum = str((oda.get("durum") or {}).get("durum") or "")
        if sayac_aktif:
            asama = "karar"
        elif durum in ("Bekliyor", "bosta", ""):
            asama = "bekliyor"
        else:
            asama = durum
    return {
        "sayac_aktif": sayac_aktif,
        "asama": asama,
        "otonom_aktif": bool(oda.get("otonom_aktif")),
        "kalan": gorunen_kalan(ham, asama) if asama in ("pazarlik", "karar") else ham,
        "ham_kalan": ham,
    }


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
        asama = _canli_asama_alanlari(oda)
        return jsonify({
            "dukkan": dukkan,
            "durum": oda["durum"]["durum"],
            "sure_bitis": oda["durum"]["sure_bitis"],
            "pey": oda["durum"]["pey"],
            "kazanan": oda["durum"]["kazanan"],
            "kazanan_id": oda["durum"].get("kazanan_id"),
            "kalan_sayac": asama["kalan"],
            "aktif_urun": (
                aktif_urun_obj.to_dict()
                if (aktif_urun_obj and not aktif_urun_obj.silindi_mi)
                else None
            ),
            "son_satis": None if oda.get("aktif_urun_id") else oda.get("son_satis"),
            **asama,
        })
    finally:
        db.session.remove()


@app.route("/durum-getir", methods=["GET"])
@admin_required
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
                "kargo_firmasi": u.kargo_firmasi or "",
                "kargo_takip_no": u.kargo_takip_no or "",
                "kargo_barkod_url": getattr(u, "kargo_barkod_url", None) or "",
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

        asama = _canli_asama_alanlari(oda)
        return jsonify({
            "dukkan": dukkan,
            "durum": oda["durum"]["durum"],
            "sure_bitis": oda["durum"]["sure_bitis"],
            "pey": oda["durum"]["pey"],
            "kazanan": oda["durum"]["kazanan"],
            "kazanan_id": oda["durum"].get("kazanan_id"),
            "kalan_sayac": asama["kalan"],
            "aktif_urun": aktif_urun,
            **asama,
            "urunler": urunler,
            "musteriler": musteriler,
            "satilan_urunler": [u.to_dict(hafif=True) for u in satilan_urunler],
            "gruplanmis_dosyalar": list(gruplanmis_dosyalar.values()),
            "on_teklifler": on_teklifler,
            "muzik_listesi": muzikler,
        })
    except Exception as e:
        db.session.rollback()
        print("DURUM_GETIR_HATA:", e)
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
# CANLI SOHBET, GİZLİ REJİ VE PATRON YAYINI
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


GECICI_POSTA_PARCALARI = (
    "tempmail", "10minutemail", "guerrillamail", "mailinator", "yopmail",
    "trashmail", "getnada", "temp-mail", "fakemail", "throwaway",
    "sharklasers", "maildrop", "dispostable", "guerrillamailblock",
)
ON_ODEME_MESAJI = (
    "Yeni katılımcı olduğunuz için 5. üründe ön ödeme yapmanız gerekmektedir; "
    "ön ödeme tamamlanana kadar yeni alım hakkınız durdurulmuştur"
)


def gecici_posta_mi(mail):
    adres = (mail or "").strip().lower()
    if "@" not in adres:
        return False
    alan = adres.split("@", 1)[1]
    return any(parca in alan for parca in GECICI_POSTA_PARCALARI)


def istek_cihaz_kodu(veri=None):
    veri = veri or {}
    gelen = (veri.get("cihaz_kodu") or request.cookies.get("halic_cihaz") or "").strip()
    if gelen:
        return gelen[:64]
    return get_device_fingerprint()


def kara_listeye_yaz(kullanici):
    if not kullanici:
        return
    telefon = (kullanici.telefon or "").strip() or None
    email = (kullanici.email or "").strip().lower() or None
    cihaz = (kullanici.cihaz_kodu or "").strip() or None
    ip = (kullanici.ip_adresi or "").strip() or None
    if not any((telefon, email, cihaz, ip)):
        return
    var = KaraListe.query.filter_by(
        telefon=telefon, email=email, cihaz_kodu=cihaz, ip_adresi=ip
    ).first()
    if var:
        return
    db.session.add(KaraListe(
        telefon=telefon,
        email=email,
        cihaz_kodu=cihaz,
        ip_adresi=ip,
        kaynak_id=kullanici.id,
    ))


def kara_liste_cakisma(telefon="", email="", cihaz="", ip=""):
    kosullar = []
    tel = (telefon or "").strip()
    posta = (email or "").strip().lower()
    cihaz_kodu = (cihaz or "").strip()
    adres = (ip or "").strip()
    if tel:
        kosullar.append(KaraListe.telefon == tel)
    if posta:
        kosullar.append(func.lower(KaraListe.email) == posta)
    if cihaz_kodu:
        kosullar.append(KaraListe.cihaz_kodu == cihaz_kodu)
    if adres:
        kosullar.append(KaraListe.ip_adresi == adres)
    if not kosullar:
        return None
    return KaraListe.query.filter(or_(*kosullar)).first()


def kimlik_engeli(kullanici=None, telefon="", email="", cihaz="", ip=""):
    if kullanici and kullanici_banli_mi(kullanici):
        kara_listeye_yaz(kullanici)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return True
    tel = telefon or (getattr(kullanici, "telefon", "") if kullanici else "")
    posta = email or (getattr(kullanici, "email", "") if kullanici else "")
    cihaz_kodu = cihaz or (getattr(kullanici, "cihaz_kodu", "") if kullanici else "")
    adres = ip or (getattr(kullanici, "ip_adresi", "") if kullanici else "")
    if not kara_liste_cakisma(tel, posta, cihaz_kodu, adres):
        return False
    if kullanici:
        kullanici.is_banned = True
        kullanici.durum = "engellendi"
        kullanici.onayli_mi = False
        kara_listeye_yaz(kullanici)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        kullanici_soketlerini_kes(kullanici.id)
    return True


def on_odeme_kilitli_mi(kullanici):
    if not kullanici:
        return False
    odenmis = Urun.query.filter(
        Urun.kazanan_id == kullanici.id,
        Urun.silindi_mi.is_(False),
        Urun.siparis_durumu == "Odendi",
    ).count()
    if odenmis > 0:
        return False
    bekleyen = Urun.query.filter(
        Urun.kazanan_id == kullanici.id,
        Urun.silindi_mi.is_(False),
        or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
        Urun.siparis_durumu == "Odeme_Bekliyor",
    ).count()
    return bekleyen >= 4


def on_odeme_yaniti():
    return jsonify({
        "success": False,
        "on_odeme_gerekli": True,
        "mesaj": ON_ODEME_MESAJI,
        "yonlendir": "/pos",
    }), 403


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


def _kullanici_sid_anahtar(kullanici_id):
    return "mezat:kullanici_sid:" + str(int(kullanici_id))


def sokete_kullanici_bagla(sid, kullanici_id):
    try:
        kid = int(kullanici_id)
    except (TypeError, ValueError):
        return
    if not REDIS_URL:
        kullanici_soketleri.setdefault(kid, set()).add(sid)
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        anahtar = _kullanici_sid_anahtar(kid)
        istemci.sadd(anahtar, sid)
        istemci.expire(anahtar, 6 * 3600)
        istemci.hset("mezat:kullanici:sid", sid, str(kid))
    except Exception as hata:
        _redis_dusur(hata)


def soketten_kullanici_kopar(sid):
    if not REDIS_URL:
        for sids in kullanici_soketleri.values():
            sids.discard(sid)
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        kid = istemci.hget("mezat:kullanici:sid", sid)
        if kid:
            istemci.srem(_kullanici_sid_anahtar(kid), sid)
        istemci.hdel("mezat:kullanici:sid", sid)
    except Exception as hata:
        _redis_dusur(hata)


def _kullanici_sidleri(kullanici_id):
    if not REDIS_URL:
        return list(kullanici_soketleri.get(int(kullanici_id), ()))
    istemci = _redis_al()
    if istemci is None:
        return []
    try:
        return list(istemci.smembers(_kullanici_sid_anahtar(kullanici_id)) or [])
    except Exception as hata:
        _redis_dusur(hata)
        return []


def kullanici_soketlerini_kes(kullanici_id):
    paket = {"kullanici_id": int(kullanici_id), "ban": True, "mesaj": BAN_MESAJI}
    for sid in _kullanici_sidleri(kullanici_id):
        socketio.emit("kullanici_engellendi", paket, room=sid)
        try:
            socketio.server.disconnect(sid)
        except Exception:
            pass
    if not REDIS_URL:
        kullanici_soketleri.pop(int(kullanici_id), None)
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        istemci.delete(_kullanici_sid_anahtar(kullanici_id))
    except Exception as hata:
        _redis_dusur(hata)


def kullaniciyi_otomatik_banla(kullanici):
    if not kullanici:
        return
    kullanici.is_banned = True
    kullanici.durum = "engellendi"
    kullanici.onayli_mi = False
    kara_listeye_yaz(kullanici)
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
        print("[OTP] SMTP_HOST tanımlı değil; kod yalnızca test çıktısına yazıldı.")
        return False
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
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if os.environ.get("SMTP_TLS", "1") != "0":
                smtp.starttls()
            kullanici = os.environ.get("SMTP_USER", "")
            sifre = os.environ.get("SMTP_PASSWORD", "")
            if kullanici:
                smtp.login(kullanici, sifre)
            smtp.send_message(ileti)
        return True
    except Exception as hata:
        print(f"[OTP] E-posta gönderilemedi: {hata}")
        return False


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
    kullanici_id_raw = veri.get("kullanici_id") or veri.get("musteri_id") or session.get("kullanici_id") or session.get("musteri_id")
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
    _nabiz_dokun(dukkan)

    if teklif_tutari and teklif_tutari > 0 and aktif_urun_id:
        try:
            mevcut_fiyat = float(oda["durum"].get("pey", 0) or 0)

            if teklif_tutari > mevcut_fiyat:
                pey_yazildi = False
                urun = None
                urun_adi = ""
                with oda_kilitleri[dukkan]:
                    if kullanici_id:
                        kilitli_kullanici = _yazma_icin_kilitle(Kullanici, kullanici_id)
                        if kilitli_kullanici and on_odeme_kilitli_mi(kilitli_kullanici):
                            db.session.rollback()
                            return on_odeme_yaniti()
                    urun = _yazma_icin_kilitle(Urun, aktif_urun_id)
                    urun_adi = urun.urun_adi if urun else ""
                    taban = _kilitli_fiyat(oda, urun) if urun is not None else mevcut_fiyat
                    if (
                        not urun
                        or urun.silindi_mi
                        or urun.durum == "Satıldı"
                        or teklif_tutari <= taban
                    ):
                        db.session.rollback()
                    else:
                        etiket_ad = f"[{platform}] {kullanici}"
                        oda["durum"]["pey"] = teklif_tutari
                        oda["durum"]["kazanan"] = etiket_ad
                        oda["durum"]["kazanan_id"] = kullanici_id
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
                        pey_yazildi = True

                    if pey_yazildi and oda.get("mevat_asama") == "karar":
                        oda["karar_pey_geldi"] = True
                    elif pey_yazildi and _gizli_esige_vardi(oda, teklif_tutari):
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

                    if pey_yazildi:
                        guncelleme = {
                            "dukkan": dukkan,
                            "urun_id": aktif_urun_id,
                            "pey": teklif_tutari,
                            "kazanan": etiket_ad,
                            "kazanan_id": kullanici_id,
                        }
                        _pey_duyur(dukkan, guncelleme)
                        if (
                            oda.get("mevat_asama") == "karar"
                            and oda["sayac_aktif"]
                            and oda["sayac_kalan"] <= 10
                        ):
                            oda["sayac_kalan"] = 15
                            oda["durum"]["sure_bitis"] = time.time() + 15
                            _iki_odayi(
                                dukkan,
                                "sayac_uzatildi",
                                {
                                    "dukkan": dukkan,
                                    "kalan": 15,
                                    "mesaj": "Son saniye teklifi nedeniyle süre 15 sn uzatıldı!",
                                },
                            )
                            _iki_odayi(
                                dukkan,
                                "sayac_guncelle",
                                {"dukkan": dukkan, "kalan": 15, "sayac_gorunsun": True, "asama": "karar"},
                            )

                if pey_yazildi:
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
        return jsonify({"success": True, "mesaj": "Ses mezat sunucusuna iletildi."})
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
                "platform": "Mezat Sunucusu",
                "kullanici": "Canlı Sunucu",
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

    try:
        musteri_no = int(musteri_id)
        urun_no = int(urun_id)
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "mesaj": "Geçersiz müşteri veya ürün bilgisi.",
        }), 400

    kullanici = db.session.get(Kullanici, musteri_no)
    urun = db.session.get(Urun, urun_no)

    if not kullanici or not urun:
        return jsonify({
            "success": False, "mesaj": "Kullanıcı veya ürün bulunamadı.",
        })

    if kimlik_engeli(kullanici, cihaz=istek_cihaz_kodu(veri), ip=get_client_ip()):
        return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI}), 403
    if on_odeme_kilitli_mi(kullanici):
        return on_odeme_yaniti()

    uygun, stok_mesaj = stok_satisa_uygun_mu(urun)
    if not uygun:
        return jsonify({"success": False, "mesaj": stok_mesaj})

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
    try:
        if sepet_id:
            item = db.session.get(SepetItem, int(sepet_id))
        elif musteri_id and urun_id:
            item = SepetItem.query.filter_by(
                musteri_id=int(musteri_id), urun_id=int(urun_id)
            ).first()
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "mesaj": "Geçersiz sepet bilgisi.",
        }), 400

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
        try:
            kullanici = db.session.get(Kullanici, int(musteri_id))
        except (ValueError, TypeError):
            return jsonify({
                "success": False,
                "mesaj": "Geçersiz müşteri bilgisi.",
            }), 400
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

    if kullanici:
        Urun.query.filter(
            Urun.kazanan_id == kullanici.id,
            Urun.silindi_mi.is_(False),
            Urun.siparis_durumu == "Odeme_Bekliyor",
        ).update({"siparis_durumu": "Odendi"}, synchronize_session=False)
        db.session.commit()

    return jsonify({
        "success": True,
        "musteri_id": kullanici.id if kullanici else None,
        "mesaj": "Ödeme ekranına yönlendiriliyorsunuz (3D Secure).",
        "yonlendir": "/pos",
    })


_POS_KILIT = threading.Lock()


def _pos_siparis_dosyasi():
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    return os.path.join(basedir, "instance", "pos_siparisler.json")


def _pos_siparisleri_oku():
    try:
        with open(_pos_siparis_dosyasi(), "r", encoding="utf-8") as f:
            gelen = json.load(f)
    except (OSError, ValueError):
        return []
    return gelen if isinstance(gelen, list) else []


@app.route("/pos")
def pos_ekrani():
    return render_template("pos.html")


@app.route("/pos/siparis-onayla", methods=["POST"])
def pos_siparis_onayla():
    veri = request.get_json(silent=True) or {}
    ad = str(veri.get("ad") or veri.get("ad_soyad") or "").strip()
    telefon = str(veri.get("telefon") or veri.get("tel") or "").strip()
    adres = str(veri.get("adres") or "").strip()
    urunler = veri.get("urunler") if isinstance(veri.get("urunler"), list) else []
    try:
        genel_toplam = round(float(veri.get("genel_toplam") or veri.get("toplam") or 0), 2)
    except (TypeError, ValueError):
        genel_toplam = 0.0
    try:
        ara_toplam = round(float(veri.get("ara_toplam") or 0), 2)
    except (TypeError, ValueError):
        ara_toplam = 0.0
    try:
        kdv_tutari = round(float(veri.get("kdv_tutari") or 0), 2)
    except (TypeError, ValueError):
        kdv_tutari = 0.0
    try:
        kargo_ucreti = round(float(veri.get("kargo_ucreti") or 0), 2)
    except (TypeError, ValueError):
        kargo_ucreti = 0.0

    if not ad or not telefon or not adres:
        return jsonify({
            "success": False,
            "mesaj": "Ad soyad, telefon ve teslimat adresi zorunludur.",
        }), 400
    if not urunler:
        return jsonify({"success": False, "mesaj": "Sepetiniz boş. Sipariş kaydedilemedi."}), 400

    siparis_no = "SIP-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
    kayit = {
        "siparis_no": siparis_no,
        "tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "musteri_id": veri.get("musteri_id"),
        "ad": ad,
        "telefon": telefon,
        "adres": adres,
        "urunler": urunler,
        "ara_toplam": ara_toplam,
        "kdv_tutari": kdv_tutari,
        "kargo_ucreti": kargo_ucreti,
        "genel_toplam": genel_toplam,
        "odeme": "Havale/EFT",
        "durum": "Havale Bekleniyor",
    }
    try:
        with _POS_KILIT:
            liste = _pos_siparisleri_oku()
            liste.append(kayit)
            with open(_pos_siparis_dosyasi(), "w", encoding="utf-8") as f:
                json.dump(liste, f, ensure_ascii=False, indent=2)
    except OSError:
        return jsonify({"success": False, "mesaj": "Sipariş kaydedilemedi."}), 500

    return jsonify({
        "success": True,
        "siparis_no": siparis_no,
        "mesaj": "Siparişiniz alındı. Havale açıklamasına sipariş numaranızı yazınız.",
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

    musteri_id = veri.get("musteri_id") or veri.get("kullanici_id") or session.get("kullanici_id") or session.get("musteri_id")
    if not _rate_limit_kontrol("pey:" + str(musteri_id or "yok") + ":" + client_ip, 3, 2):
        return jsonify({
            "success": False,
            "mesaj": "Çok hızlı pey denemesi. Birkaç saniye sonra tekrar deneyin.",
        }), 429
    musteri_adi = (veri.get("musteri_adi") or veri.get("isim") or "").strip()
    telefon = (veri.get("telefon") or "").strip()

    kullanici = None
    try:
        if musteri_id:
            try:
                kullanici = db.session.get(Kullanici, int(musteri_id))
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "mesaj": "Geçersiz müşteri bilgisi.",
                }), 400
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

        if kimlik_engeli(
            kullanici,
            telefon=telefon,
            cihaz=istek_cihaz_kodu(veri),
            ip=client_ip,
        ):
            return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI}), 403
        if on_odeme_kilitli_mi(kullanici):
            return on_odeme_yaniti()

        kullanici_id = int(kullanici.id)
        kullanici_ad = str(kullanici.ad_soyad or "")
        etiket_ad = f"[Web] {kullanici_ad}"
        raw_uid = veri.get("urun_id") or oda["aktif_urun_id"]
        try:
            urun_id = int(raw_uid) if raw_uid else oda["aktif_urun_id"]
        except SATimeoutError:
            raise
        except Exception:
            urun_id = oda["aktif_urun_id"]

        if not urun_id:
            return jsonify({
                "success": False, "mesaj": "Bu dükkanda şu an aktif bir mezat yok!",
            })
    except SATimeoutError:
        db.session.rollback()
        return _havuz_yogun_yaniti()

    db.session.remove()

    with oda_kilitleri[dukkan]:
        try:
            kullanici = _yazma_icin_kilitle(Kullanici, kullanici_id)
            if not kullanici or kullanici.silindi_mi:
                db.session.rollback()
                return jsonify({
                    "success": False,
                    "kayit_gerekli": True,
                    "mesaj": KAYIT_PEY_MESAJI,
                })
            if on_odeme_kilitli_mi(kullanici):
                db.session.rollback()
                return on_odeme_yaniti()
            urun = _yazma_icin_kilitle(Urun, urun_id)

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
                uygun, stok_mesaj = stok_satisa_uygun_mu(
                    urun, haric_musteri_id=kullanici.id
                )
                if not uygun:
                    return jsonify({"success": False, "mesaj": stok_mesaj})

                statik_hemen_al = float(urun.hemen_al_fiyati or 0.0)
                mevcut_pey = _kilitli_fiyat(oda, urun)

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
                _satis_kapanisini_isaretle(dukkan, oda)
                biten_id = oda["aktif_urun_id"]
                oda["aktif_urun_id"] = None
                _son_satisi_yaz(oda, etiket_ad, hemen_al_fiyat, biten_id)

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
                stok_satis_sonrasi_dus(urun)
                hedef_sepet = urunu_kazanana_sepete_ekle(
                    urun.id, etiket_ad, kullanici.id, commit=False
                )
                db.session.commit()
                invalidate_vitrin_cache()

                if hedef_sepet:
                    _kazanan_sepetini_disari_aktar(urun.id, hedef_sepet)

                _pey_duyur(
                    dukkan,
                    {
                        "urun_id": urun.id,
                        "pey": hemen_al_fiyat,
                        "kazanan": etiket_ad,
                        "kazanan_id": kullanici.id,
                    },
                )
                _iki_odayi(
                    dukkan,
                    "sayac_bitti",
                    {
                        "dukkan": dukkan,
                        "urun_id": biten_id,
                        "satildi_mi": True,
                        "kazanan": etiket_ad,
                        "kazanan_id": kullanici.id,
                        "fiyat": hemen_al_fiyat,
                    },
                )
                bota_haber_ver(
                    "satis_bitti",
                    {
                        "dukkan": dukkan,
                        "kazanan": etiket_ad,
                        "fiyat": hemen_al_fiyat,
                    },
                )
                ahmet_usta_anons_yap(
                    dukkan,
                    ahmet_satis_cumlesi(urun.lot_no, urun.urun_adi, kullanici.ad_soyad),
                    tur="satis",
                )
                _otonom_disi_satisi_bosalt(dukkan, oda)
                bota_haber_ver("ahmet_sifirla", {"dukkan": dukkan})

                _admin_satisi_duyur(dukkan, biten_id, etiket_ad, hemen_al_fiyat, "hemen_al")
                return jsonify({
                    "success": True,
                    "guncel_fiyat": hemen_al_fiyat,
                    "kazanan": etiket_ad,
                })

            if islem == "pey":
                mevcut_fiyat = _kilitli_fiyat(oda, urun)

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
                            "dukkan": dukkan,
                            "urun_id": urun.id,
                            "yeni_fiyat": miktar,
                            "yeni_kisi": etiket_ad,
                            "mesaj": f"Peyiniz geçildi! Güncel teklif: {int(miktar)} TL",
                        },
                        room=dukkan,
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
                invalidate_vitrin_cache()

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
                    _iki_odayi(
                        dukkan,
                        "sayac_uzatildi",
                        {
                            "dukkan": dukkan,
                            "kalan": 15,
                            "mesaj": "Son saniye teklifi nedeniyle süre 15 sn uzatıldı!",
                        },
                    )
                    _iki_odayi(
                        dukkan,
                        "sayac_guncelle",
                        {"dukkan": dukkan, "kalan": 15, "sayac_gorunsun": True, "asama": "karar"},
                    )

                platform = (veri.get("platform") or "Web").strip()
                if platform not in ("Web", "TikTok", "Instagram"):
                    platform = "Web"
                _pey_duyur(
                    dukkan,
                    {
                        "urun_id": urun.id,
                        "pey": miktar,
                        "kazanan": etiket_ad,
                        "kazanan_id": kullanici.id,
                        "platform": platform,
                        "ad": kullanici.ad_soyad,
                    },
                )
                _nabiz_dokun(dukkan)
                return jsonify({
                    "success": True,
                    "guncel_fiyat": miktar,
                    "kazanan": etiket_ad,
                })
        except Exception as e:
            db.session.rollback()
            if isinstance(e, SATimeoutError):
                return _havuz_yogun_yaniti()
            if _satir_kilit_cakismasi(e):
                return jsonify({
                    "success": False,
                    "mesaj": "Şu anda başka bir pey işleniyor, lütfen tekrar deneyin.",
                })
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
@app.route("/kayit", methods=["POST"])
@app.route("/kayit-ol", methods=["POST"])
def kayit_ol():
    if not _rate_limit_kontrol("kayit:" + get_client_ip(), 6, 60):
        return jsonify({
            "status": "error",
            "success": False,
            "mesaj": "Çok fazla kayıt denemesi. Bir dakika sonra tekrar deneyin.",
            "message": "Çok fazla kayıt denemesi. Bir dakika sonra tekrar deneyin.",
        }), 429
    veri = request.get_json(silent=True) or request.form or {}
    ad = (veri.get("ad") or veri.get("ad_soyad") or veri.get("isim") or "").strip()
    tel = (veri.get("tel") or veri.get("telefon") or veri.get("phone") or "").strip()
    mail = (veri.get("mail") or veri.get("email") or veri.get("eposta") or "").strip()
    adres = (veri.get("adres") or veri.get("address") or "").strip()
    sozlesme = veri.get("sozlesme") in (True, "true", "1", 1, "on")
    cihaz = istek_cihaz_kodu(veri)
    ip = get_client_ip()

    if not ad or not tel or not mail:
        return jsonify({
            "status": "error",
            "success": False,
            "message": "Ad, telefon ve e-posta zorunludur",
            "mesaj": "Ad, telefon ve e-posta zorunludur",
        })
    if not sozlesme:
        return jsonify({
            "status": "error",
            "success": False,
            "message": "Mezat şartnamesini kabul etmelisiniz",
            "mesaj": "Mezat şartnamesini kabul etmelisiniz",
        })
    if gecici_posta_mi(mail):
        return jsonify({
            "status": "error",
            "success": False,
            "message": "Geçici e-posta adresleri kabul edilmez",
            "mesaj": "Geçici e-posta adresleri kabul edilmez",
        })

    def yanit(kayit):
        session.permanent = True
        session["kullanici_id"] = kayit.id
        session["kullanici_adi"] = kayit.ad_soyad or ad
        session["musteri_id"] = kayit.id
        session["musteri_ad"] = kayit.ad_soyad or ad
        govde = {
            "id": kayit.id,
            "ad_soyad": kayit.ad_soyad or ad,
            "ad": kayit.ad_soyad or ad,
            "telefon": kayit.telefon or tel,
            "tel": kayit.telefon or tel,
            "email": kayit.email or mail,
            "mail": kayit.email or mail,
            "adres": kayit.adres or adres or "",
            "onayli_mi": 1,
            "durum": "onayli",
            "is_verified": 1,
            "is_banned": 0,
        }
        return jsonify({
            "status": "success",
            "message": "Giriş başarılı",
            "success": True,
            "musteri_id": kayit.id,
            "durum": "onayli",
            "onayli_mi": True,
            "is_verified": True,
            "dogrulama_gerekli": False,
            "mesaj": "Giriş başarılı",
            "user": govde,
        })

    def bul():
        return (
            Kullanici.query.filter(
                or_(
                    Kullanici.telefon == tel,
                    func.lower(Kullanici.email) == mail.lower(),
                )
            )
            .order_by(Kullanici.id.asc())
            .first()
        )

    def kimligi_yaz(kayit):
        kayit.ad_soyad = ad
        kayit.telefon = tel
        kayit.email = mail
        kayit.adres = adres or None
        kayit.sozlesme_onay = True
        kayit.ip_adresi = ip
        kayit.cihaz_kodu = cihaz
        kayit.silindi_mi = False
        kayit.silinme_tarihi = None
        if not kayit.sifre_hash:
            kayit.sifre_hash = generate_password_hash(uuid.uuid4().hex)

    def kod_yaniti(kayit, kod, gitti):
        mesaj = "E-postanıza 6 haneli doğrulama kodu gönderildi."
        if not gitti:
            mesaj = "Doğrulama kodu şu an e-posta ile gönderilemedi."
            if app.debug:
                mesaj = "SMTP tanımlı değil. Test doğrulama kodu: " + kod
        govde = {
            "status": "pending",
            "success": True,
            "dogrulama_gerekli": True,
            "musteri_id": kayit.id,
            "mesaj": mesaj,
            "message": mesaj,
        }
        if not gitti and app.debug:
            govde["test_kod"] = kod
        return jsonify(govde)

    try:
        kullanici_ve_kara_liste_onar()
    except Exception as hata:
        db.session.rollback()
        print(f"Kayıt şema onarımı: {hata}")
        return jsonify({
            "status": "error",
            "success": False,
            "message": str(hata),
            "mesaj": str(hata),
        }), 500

    try:
        if kara_liste_cakisma(tel, mail, cihaz, ip):
            kullanici = bul()
            if kullanici:
                kullanici.is_banned = True
                kullanici.durum = "engellendi"
                kullanici.onayli_mi = False
                kullanici.ip_adresi = ip
                kullanici.cihaz_kodu = cihaz
                kara_listeye_yaz(kullanici)
                db.session.commit()
            return jsonify({
                "status": "error",
                "success": False,
                "ban": True,
                "mesaj": BAN_MESAJI,
                "message": BAN_MESAJI,
            }), 403

        kullanici = bul()
        if kullanici and kullanici_banli_mi(kullanici):
            kara_listeye_yaz(kullanici)
            db.session.commit()
            return jsonify({
                "status": "error",
                "success": False,
                "ban": True,
                "mesaj": BAN_MESAJI,
                "message": BAN_MESAJI,
            }), 403

        if (
            kullanici
            and bool(kullanici.is_verified)
            and (kullanici.durum or "") == "onayli"
            and not kullanici.silindi_mi
        ):
            kullanici.ip_adresi = ip
            kullanici.cihaz_kodu = cihaz or kullanici.cihaz_kodu
            db.session.commit()
            return yanit(kullanici)

        if kullanici:
            kimligi_yaz(kullanici)
        else:
            kullanici = Kullanici(
                ad_soyad=ad,
                telefon=tel,
                email=mail,
                adres=adres or None,
                sifre_hash=generate_password_hash(uuid.uuid4().hex),
                bonus=0.0,
                puan=100.0,
                onayli_mi=False,
                is_verified=False,
                is_banned=False,
                durum="bekliyor",
                sozlesme_onay=True,
                ip_adresi=ip,
                cihaz_kodu=cihaz,
                kayit_tarihi=suan_utc(),
                silindi_mi=False,
            )
            db.session.add(kullanici)
        kod = otp_kaydet(kullanici)
        db.session.commit()
        try:
            db.session.execute(
                text("UPDATE kullanici SET sifre = '' WHERE id = :i AND (sifre IS NULL OR sifre = '')"),
                {"i": kullanici.id},
            )
            db.session.commit()
        except Exception as sifre_hata:
            db.session.rollback()
            print(f"Kayıt şifre alanı: {sifre_hata}")
    except Exception as hata:
        db.session.rollback()
        print(f"Kayıt hatası: {hata}")
        return jsonify({
            "status": "error",
            "success": False,
            "message": str(hata),
            "mesaj": str(hata),
        }), 500

    try:
        gitti = otp_eposta_gonder(kullanici.email or mail, kod)
    except Exception as posta_hata:
        print(f"[OTP] E-posta gönderilemedi, kayıt korunuyor: {posta_hata}")
        gitti = False
    return kod_yaniti(kullanici, kod, gitti)


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
    if gecici_posta_mi(mail):
        return jsonify({"success": False, "mesaj": "Geçici e-posta adresleri kabul edilmez."})
    if kimlik_engeli(
        kullanici,
        telefon=tel,
        email=mail,
        cihaz=istek_cihaz_kodu(veri),
        ip=get_client_ip(),
    ):
        return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI})
    kullanici.ad_soyad = ad
    kullanici.telefon = tel
    kullanici.email = mail or None
    kullanici.adres = adres or None
    kullanici.ip_adresi = get_client_ip()
    gelen_cihaz = istek_cihaz_kodu(veri)
    if gelen_cihaz:
        kullanici.cihaz_kodu = gelen_cihaz
    try:
        db.session.commit()
    except Exception as hata:
        db.session.rollback()
        print(f"Profil güncelleme hatası: {hata}")
        return jsonify({"success": False, "mesaj": "Profil kaydedilemedi."})
    return jsonify({"success": True, "mesaj": "Profil başarıyla güncellendi."})


@app.route("/profil-sil", methods=["POST"])
@app.route("/profil_sil", methods=["POST"])
@app.route("/hesap_sifirla", methods=["POST"])
def profil_sil():
    veri = request.json or {}
    try:
        kullanici = db.session.get(Kullanici, int(veri.get("musteri_id") or 0))
    except (TypeError, ValueError):
        kullanici = None
    if kullanici:
        kullanici.silindi_mi = True
        kullanici.silinme_tarihi = suan_utc()
        kullanici.cihaz_kodu = None
        try:
            db.session.commit()
        except Exception as hata:
            db.session.rollback()
            print(f"Profil silme hatası: {hata}")
            return jsonify({"status": "error", "success": False, "mesaj": "Hesap sıfırlanamadı."})
    session.clear()
    return jsonify({
        "status": "success",
        "message": "Hesap sifirlandi",
        "success": True,
        "mesaj": "Hesap sifirlandi",
    })


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
    if not _rate_limit_kontrol("dogrulama:" + get_client_ip(), 6, 60):
        return jsonify({
            "success": False,
            "mesaj": "Çok fazla doğrulama denemesi. Bir dakika sonra tekrar deneyin.",
        }), 429
    veri = request.json or {}
    kod = "".join(ch for ch in str(veri.get("kod") or "") if ch.isdigit())
    try:
        kullanici = db.session.get(Kullanici, int(veri.get("musteri_id") or 0))
    except Exception:
        kullanici = None
    if not kullanici or kullanici.silindi_mi:
        return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})
    if kimlik_engeli(
        kullanici,
        cihaz=istek_cihaz_kodu(veri),
        ip=get_client_ip(),
    ):
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
    kullanici.onayli_mi = True
    kullanici.durum = "onayli"
    kullanici.otp_code = None
    kullanici.dogrulama_kodu = None
    kullanici.otp_bitis = None
    db.session.commit()
    session.permanent = True
    session["kullanici_id"] = kullanici.id
    session["kullanici_adi"] = kullanici.ad_soyad
    session["musteri_id"] = kullanici.id
    session["musteri_ad"] = kullanici.ad_soyad
    return jsonify({
        "success": True,
        "is_verified": True,
        "onayli_mi": True,
        "durum": "onayli",
        "musteri_id": kullanici.id,
        "mesaj": "Hesabınız onaylandı. Pey verebilirsiniz.",
        "user": {
            "id": kullanici.id,
            "ad_soyad": kullanici.ad_soyad,
            "ad": kullanici.ad_soyad,
            "telefon": kullanici.telefon,
            "tel": kullanici.telefon,
            "email": kullanici.email,
            "mail": kullanici.email,
            "adres": kullanici.adres or "",
        },
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
    if kimlik_engeli(kullanici, cihaz=istek_cihaz_kodu(veri), ip=get_client_ip()):
        return jsonify({"success": False, "ban": True, "mesaj": BAN_MESAJI}), 403
    if on_odeme_kilitli_mi(kullanici):
        return on_odeme_yaniti()

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
    giris_anahtari = "admin-giris:" + get_client_ip()
    if not _rate_limit_kontrol(giris_anahtari, 5, 900, kaydet=False):
        return jsonify({
            "success": False,
            "mesaj": "Çok fazla hatalı giriş. 15 dakika sonra tekrar deneyin.",
        }), 429
    if check_password_hash(ADMIN_PASSWORD_HASH, girilen_sifre):
        _rate_limit_sifirla(giris_anahtari)
        session["admin_sifre_ok"] = True
        return jsonify({
            "success": True, "mesaj": "Şifre doğrulandı, 2FA kodunu giriniz.",
        })
    _rate_limit_kontrol(giris_anahtari, 5, 900)
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

    if totp is not None and totp.verify(girilen_kod, valid_window=1):
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


_YEDEK_GIZLI_ALANLAR = {"sifre_hash", "sifre", "otp_code", "dogrulama_kodu"}


def _yedek_deger(deger):
    if isinstance(deger, datetime):
        return deger.isoformat(sep=" ")
    if isinstance(deger, (bytes, bytearray)):
        return None
    return deger


def _model_yedek_satirlari(model, sorgu=None):
    satirlar = []
    kayitlar = sorgu.all() if sorgu is not None else model.query.all()
    for kayit in kayitlar:
        satir = {}
        for kolon in model.__table__.columns:
            if kolon.name in _YEDEK_GIZLI_ALANLAR:
                continue
            satir[kolon.name] = _yedek_deger(getattr(kayit, kolon.name, None))
        satirlar.append(satir)
    return satirlar


@app.route("/admin/yedek-indir", methods=["GET"])
@admin_required
def admin_yedek_indir():
    try:
        urunler = _model_yedek_satirlari(Urun)
        satislar = [
            satir for satir in urunler
            if str(satir.get("durum") or "") in ("Satıldı", "satildi")
        ]
        paket = {
            "olusturma": datetime.now().isoformat(timespec="seconds"),
            "urunler": urunler,
            "musteriler": _model_yedek_satirlari(Kullanici),
            "teklifler": _model_yedek_satirlari(Teklif),
            "on_teklifler": _model_yedek_satirlari(OnTeklif),
            "satislar": satislar,
            "sepet": _model_yedek_satirlari(SepetItem),
            "sozlesmeler": _model_yedek_satirlari(UrunAlimSozlesmesi),
            "kurumsal_bilgiler": kurumsal_bilgileri_oku(),
        }
        try:
            paket["ayarlar"] = _model_yedek_satirlari(Ayar)
        except Exception:
            paket["ayarlar"] = []
        ham = json.dumps(paket, ensure_ascii=False, indent=2).encode("utf-8")
        ad = "halic_antik_yedek_" + datetime.now().strftime("%Y%m%d_%H%M") + ".json"
        return send_file(
            io.BytesIO(ham),
            as_attachment=True,
            download_name=ad,
            mimetype="application/json",
        )
    except Exception as hata:
        return jsonify({"success": False, "mesaj": "Yedek oluşturulamadı: " + str(hata)}), 500


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
        oda["lot_kapanis"] = ""
        oda["satildi"] = False
        oda["satis_tebrik_bekleniyor"] = False
        oda["karar_pey_geldi"] = False
        oda["sayac_aktif"] = False
        oda["sayac_kalan"] = 180
        oda["durum"]["durum"] = "Bekliyor"
        oda["durum"]["sure_bitis"] = 0
        oda["son_hareket"] = time.time()
        oda["son_sessizlik"] = 0

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

    _iki_odayi(
        dukkan,
        "sayac_guncelle",
        {
            "dukkan": dukkan,
            "kalan": 180,
            "kalan_sure": 180,
            "toplam": 180,
            "sayac_gorunsun": True,
            "sayac_aktif": False,
            "asama": "bekliyor",
        },
    )
    socketio.emit("mezat_basladi_muzik", room=dukkan)
    ahmet_kisa_acilis_seslendir(
        dukkan,
        urun_paket["ad"],
        urun_paket.get("fiyat") or 0,
        urun_paket.get("lot") or "",
    )

    invalidate_vitrin_cache()
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
            _iki_odayi(
                dukkan,
                "sayac_uzatildi",
                {
                    "dukkan": dukkan,
                    "kalan": kalan,
                    "mesaj": "Süre 15 sn uzatıldı!",
                },
            )
        _iki_odayi(dukkan, "sayac_guncelle", paket)
        invalidate_vitrin_cache()
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
            _fiyati_acilisa_cek(dukkan, oda, konus=False)
            oda["sayac_aktif"] = True
            oda["sayac_kalan"] = 60
            oda["sayac_nabiz"] = time.time()
            akis = "karar"
        token = _sayac_jetonu(gorev)

    if _sayac_kilidi_al(dukkan, gorev, token) == "dolu":
        oda["sayac_aktif"] = False
        return jsonify({
            "success": False,
            "mesaj": "Sayaç başka bir worker'da çalışıyor.",
        })
    if akis == "karar":
        _anonsu_arkada(dukkan, "Pazarlık bitti, son karar süresi başladı")
    socketio.start_background_task(
        dukkan_geri_sayim_gorevi, dukkan, gorev, akis, token
    )
    socketio.emit("mezat_basladi_muzik", room=dukkan)
    invalidate_vitrin_cache()
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
        _son_satisi_yaz(oda, oda["durum"].get("kazanan"), oda["durum"].get("pey"), biten_urun_id)

        satis_lot = ""
        satis_ad = "Eser"
        if biten_urun_id:
            if oda["durum"].get("kazanan_id"):
                _yazma_icin_kilitle(Kullanici, oda["durum"].get("kazanan_id"))
            urun = _yazma_icin_kilitle(Urun, biten_urun_id)
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
                stok_satis_sonrasi_dus(urun)
                hedef_sepet = urunu_kazanana_sepete_ekle(
                    biten_urun_id,
                    oda["durum"]["kazanan"],
                    oda["durum"].get("kazanan_id"),
                    commit=False,
                )
                db.session.commit()
                if hedef_sepet:
                    _kazanan_sepetini_disari_aktar(biten_urun_id, hedef_sepet)

        _iki_odayi(
            dukkan,
            "sayac_bitti",
            {
                "dukkan": dukkan,
                "urun_id": biten_urun_id,
                "satildi_mi": True,
                "kazanan": oda["durum"]["kazanan"],
                "kazanan_id": oda["durum"].get("kazanan_id"),
                "fiyat": oda["durum"]["pey"],
            },
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
        _admin_satisi_duyur(
            dukkan, biten_urun_id, oda["durum"]["kazanan"], oda["durum"]["pey"], "satis_bitir",
        )
    invalidate_vitrin_cache()
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

        invalidate_vitrin_cache()
        _pey_duyur(
            dukkan,
            {
                "urun_id": oda["aktif_urun_id"],
                "pey": oda["durum"]["pey"],
                "kazanan": oda["durum"]["kazanan"],
                "kazanan_id": oda["durum"].get("kazanan_id"),
            },
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
            invalidate_vitrin_cache()

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
                _pey_duyur(
                    dukkan,
                    {
                        "urun_id": urun.id,
                        "pey": oda["durum"]["pey"],
                        "kazanan": "Yok",
                        "kazanan_id": None,
                    },
                )

        return jsonify({
            "success": True,
            "mesaj": f"Lot #{urun.lot_no} ({urun.urun_adi}) satışı iptal edildi.",
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)}), 500


_GELIVER_FIRMA_KODLARI = {
    "yurtiçi kargo": "YURTICI_STANDART",
    "yurtici kargo": "YURTICI_STANDART",
    "yurtiçi": "YURTICI_STANDART",
    "aras kargo": "ARAS_STANDART",
    "aras": "ARAS_STANDART",
    "mng kargo": "MNG_STANDART",
    "mng": "MNG_STANDART",
    "kolay gelsin": "KOLAYGELSIN_STANDART",
    "hepsijet": "HEPSIJET_STANDART",
    "ptt kargo": "PTT_STANDART",
    "ptt": "PTT_STANDART",
}


def _geliver_metin(nesne, *adlar, derinlik=0):
    if derinlik > 4 or not isinstance(nesne, dict):
        return ""
    for ad in adlar:
        deger = nesne.get(ad)
        if isinstance(deger, str) and deger.strip():
            return deger.strip()
    for anahtar in ("data", "shipment", "result"):
        ic = nesne.get(anahtar)
        if isinstance(ic, dict):
            bulunan = _geliver_metin(ic, *adlar, derinlik=derinlik + 1)
            if bulunan:
                return bulunan
    return ""


def _geliver_teklif_id(yanit, firma_kodu):
    kutular = [yanit]
    if isinstance(yanit, dict):
        for anahtar in ("data", "shipment"):
            ic = yanit.get(anahtar)
            if isinstance(ic, dict):
                kutular.append(ic)
    teklifler = []
    en_ucuz = ""
    for kutu in kutular:
        if not isinstance(kutu, dict):
            continue
        offers = kutu.get("offers")
        if isinstance(offers, list):
            teklifler.extend(oge for oge in offers if isinstance(oge, dict))
        elif isinstance(offers, dict):
            liste = offers.get("list") or offers.get("offers") or []
            if isinstance(liste, list):
                teklifler.extend(oge for oge in liste if isinstance(oge, dict))
            ucuz = offers.get("cheapest")
            if isinstance(ucuz, dict) and ucuz.get("id"):
                en_ucuz = str(ucuz.get("id"))
    kok = (firma_kodu or "").split("_")[0]
    for teklif in teklifler:
        servis = str(
            teklif.get("providerServiceCode") or teklif.get("providerCode") or ""
        ).upper()
        if kok and kok in servis and teklif.get("id"):
            return str(teklif.get("id"))
    if en_ucuz:
        return en_ucuz
    if teklifler and teklifler[0].get("id"):
        return str(teklifler[0].get("id"))
    return ""


def _geliver_gonderi_olustur(urun, firma):
    token = (os.environ.get("GELIVER_API_TOKEN") or "").strip()
    if not token:
        return None, "Geliver anahtarı tanımlı değil."
    gonderen = (os.environ.get("GELIVER_SENDER_ADDRESS_ID") or "").strip()
    if not gonderen:
        return None, (
            "Geliver gönderen adresi eksik. GELIVER_SENDER_ADDRESS_ID tanımlayın "
            "veya takip numarasını elle girin."
        )
    alici = None
    if urun.kazanan_id:
        alici = db.session.get(Kullanici, urun.kazanan_id)
    ad = (alici.ad_soyad if alici and alici.ad_soyad else urun.kazanan_adi) or "Alıcı"
    telefon = "".join(ch for ch in str((alici.telefon if alici else "") or "") if ch.isdigit())
    if telefon.startswith("0") and len(telefon) == 11:
        telefon = "+90" + telefon[1:]
    elif telefon.startswith("90") and len(telefon) == 12:
        telefon = "+" + telefon
    elif len(telefon) == 10:
        telefon = "+90" + telefon
    if not telefon:
        return None, "Alıcının telefonu kayıtlı değil. Takip numarasını elle girin."
    adres = (alici.adres if alici and alici.adres else "") or "Adres belirtilmedi"
    email = (alici.email if alici and alici.email else "") or "alici@halicantik.com"
    firma_kodu = _GELIVER_FIRMA_KODLARI.get(
        (firma or "").strip().lower(), "YURTICI_STANDART"
    )
    basliklar = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }
    govde = {
        "senderAddressID": gonderen,
        "recipientAddress": {
            "name": ad[:80],
            "email": email[:120],
            "phone": telefon,
            "address1": adres[:240],
            "countryCode": "TR",
            "cityName": "İstanbul",
            "cityCode": "34",
            "districtName": "Merkez",
        },
        "length": "10",
        "width": "10",
        "height": "10",
        "distanceUnit": "cm",
        "weight": "1",
        "massUnit": "kg",
        "providerServiceCode": firma_kodu,
        "items": [{
            "title": (urun.urun_adi or "Mezat ürünü")[:120],
            "quantity": 1,
        }],
        "order": {
            "orderNumber": "LOT-%s" % (urun.lot_no or urun.id),
            "sourceIdentifier": "https://halicantik.com",
            "totalAmount": str(int(float(urun.guncel_fiyat or urun.acilis_fiyati or 0))),
            "totalAmountCurrency": "TRY",
        },
    }
    try:
        yanit = requests.post(
            "https://api.geliver.io/api/v1/shipments",
            json=govde,
            headers=basliklar,
            timeout=25,
        )
        try:
            paket = yanit.json()
        except ValueError:
            paket = {}
        if yanit.status_code >= 400:
            mesaj = _geliver_metin(paket, "message", "error", "mesaj") or (
                "Geliver gönderi oluşturamadı (HTTP %s)." % yanit.status_code
            )
            return None, mesaj
        takip = _geliver_metin(paket, "trackingNumber", "trackingNo")
        etiket = _geliver_metin(paket, "labelURL", "labelUrl", "responsiveLabelURL")
        if takip or etiket:
            return {"takip_no": takip, "barkod_url": etiket}, ""
        teklif_id = _geliver_teklif_id(paket, firma_kodu)
        if not teklif_id:
            return None, "Geliver teklif döndürmedi. Takip numarasını elle girebilirsiniz."
        kabul = requests.post(
            "https://api.geliver.io/api/v1/transactions",
            json={"offerID": teklif_id},
            headers=basliklar,
            timeout=25,
        )
        try:
            sonuc = kabul.json()
        except ValueError:
            sonuc = {}
        if kabul.status_code >= 400:
            mesaj = _geliver_metin(sonuc, "message", "error", "mesaj") or (
                "Geliver etiketi kesilemedi (HTTP %s)." % kabul.status_code
            )
            return None, mesaj
        takip = _geliver_metin(sonuc, "trackingNumber", "trackingNo")
        etiket = _geliver_metin(sonuc, "labelURL", "labelUrl", "responsiveLabelURL")
        if not takip and not etiket:
            return None, "Geliver takip numarası döndürmedi. Takip numarasını elle girebilirsiniz."
        return {"takip_no": takip, "barkod_url": etiket}, ""
    except requests.RequestException as hata:
        return None, "Geliver bağlantısı kurulamadı: %s" % hata


@app.route("/admin/kargo-olustur", methods=["POST"])
@admin_required
def admin_kargo_olustur():
    veri = request.get_json(silent=True) or {}
    try:
        urun_id = int(veri.get("urun_id") or 0)
    except (TypeError, ValueError):
        urun_id = 0
    if not urun_id:
        return jsonify({"success": False, "mesaj": "Ürün seçilmedi."}), 400
    urun = db.session.get(Urun, urun_id)
    if not urun or urun.silindi_mi:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    firma = (veri.get("kargo_firmasi") or "Yurtiçi Kargo").strip()[:50] or "Yurtiçi Kargo"
    takip = (veri.get("takip_no") or veri.get("kargo_takip_no") or "").strip()[:100]
    token = (os.environ.get("GELIVER_API_TOKEN") or "").strip()
    elle_girildi = bool(takip)
    barkod = (getattr(urun, "kargo_barkod_url", None) or "").strip()
    if token and not takip:
        gelen, hata = _geliver_gonderi_olustur(urun, firma)
        if hata or not gelen:
            return jsonify({
                "success": False,
                "mesaj": hata or "Geliver gönderisi oluşturulamadı.",
            }), 502
        takip = (gelen.get("takip_no") or "")[:100]
        if gelen.get("barkod_url"):
            barkod = gelen.get("barkod_url")
    elif not takip and not barkod:
        return jsonify({
            "success": False,
            "mesaj": "Takip numarası girin. Geliver anahtarı tanımlıysa alanı boş bırakabilirsiniz.",
        }), 400
    try:
        urun.kargo_firmasi = firma
        urun.kargo_takip_no = takip or urun.kargo_takip_no
        if hasattr(urun, "kargo_barkod_url"):
            urun.kargo_barkod_url = barkod
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": "Kargo bilgisi kaydedilemedi."}), 500
    return jsonify({
        "success": True,
        "mesaj": "Kargo kaydedildi." if elle_girildi or not token else "Kargo oluşturuldu.",
        "urun_id": urun.id,
        "kargo_firmasi": urun.kargo_firmasi or "",
        "takip_no": urun.kargo_takip_no or "",
        "kargo_barkod_url": getattr(urun, "kargo_barkod_url", None) or "",
    })


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


@app.route("/api/cloudinary-imza", methods=["POST"])
@admin_required
def cloudinary_imza():
    cloud_name = (os.environ.get("CLOUDINARY_CLOUD_NAME") or "").strip()
    api_key = (os.environ.get("CLOUDINARY_API_KEY") or "").strip()
    api_secret = (os.environ.get("CLOUDINARY_API_SECRET") or "").strip()
    if not cloud_name or not api_key or not api_secret:
        return jsonify({
            "success": False,
            "mesaj": (
                "Cloudinary anahtarları eksik. CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY ve CLOUDINARY_API_SECRET tanımlı olmalı."
            ),
        }), 400
    timestamp = int(time.time())
    signature = hashlib.sha1(
        f"folder=halic-antik&timestamp={timestamp}{api_secret}".encode("utf-8")
    ).hexdigest()
    return jsonify({
        "success": True,
        "cloud_name": cloud_name,
        "api_key": api_key,
        "timestamp": timestamp,
        "folder": "halic-antik",
        "signature": signature,
    })


@app.route("/urun-ekle", methods=["POST"])
@admin_required
def urun_ekle():
    try:
        dukkan = dukkan_dogrula(request.form.get("dukkan", "gumus"))
        ham_adresler = request.form.getlist("medya_urls")

        fotograflar = []
        video_url = ""
        ses_url = ""
        video_uzantilari = (
            ".mp4", ".mov", ".avi", ".webm", ".mkv", ".3gp", ".m4v"
        )
        ses_uzantilari = (".mp3", ".wav", ".ogg", ".m4a")

        for ham in ham_adresler:
            url = str(ham or "").strip()
            if not url.startswith("https://res.cloudinary.com/"):
                continue
            yol = url.split("?", 1)[0].lower()
            if "/video/upload/" in yol or yol.endswith(video_uzantilari):
                if not video_url:
                    video_url = url
                fotograflar.append(url)
            elif yol.endswith(ses_uzantilari):
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
        if hasattr(yeni_urun, "eklenme_tarihi") and getattr(yeni_urun, "eklenme_tarihi", None) is None:
            yeni_urun.eklenme_tarihi = datetime.now()
        db.session.add(yeni_urun)
        db.session.commit()
        invalidate_vitrin_cache()

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
        invalidate_vitrin_cache()

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
        invalidate_vitrin_cache()
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
            kara_listeye_yaz(kullanici)
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
            invalidate_vitrin_cache()
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


@app.route("/cop-kutusunu-kalici-bosalt", methods=["POST"])
@admin_required
def cop_kutusunu_kalici_bosalt():
    try:
        urunler = Urun.query.filter(Urun.silindi_mi.is_(True)).all()
        adet = 0
        for urun in urunler:
            dukkan = urun.dukkan_kodu or "gumus"
            Teklif.query.filter_by(urun_id=urun.id).delete()
            OnTeklif.query.filter_by(urun_id=urun.id).delete()
            SepetItem.query.filter_by(urun_id=urun.id).delete()
            UrunTakip.query.filter_by(urun_id=urun.id).delete()
            if dukkan in odalar and odalar[dukkan].get("aktif_urun_id") == urun.id:
                odalar[dukkan]["aktif_urun_id"] = None
                odalar[dukkan]["durum"]["durum"] = "Bekliyor"
                odalar[dukkan]["durum"]["sure_bitis"] = 0
                odalar[dukkan]["durum"]["pey"] = 0
                odalar[dukkan]["durum"]["kazanan"] = "Yok"
                odalar[dukkan]["durum"]["kazanan_id"] = None
            db.session.delete(urun)
            adet += 1
        db.session.commit()
        invalidate_vitrin_cache()
        return jsonify({
            "success": True,
            "silinen": adet,
            "mesaj": f"Çöp kutusundaki {adet} ürün kalıcı olarak silindi.",
        })
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": f"Çöp kutusu boşaltılamadı: {hata}"}), 500


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
    baslatilan = []
    lot_yok = []

    for d in hedefler:
        oda = odalar[d]
        if durum:
            oda["otonom_aktif"] = True
            lot_no = _otonom_baslangic_sahnesi(d)
            _otonom_donguyu_garantile(d)
            if lot_no is not None and not oda.get("sayac_aktif"):
                sahne_urun = (
                    db.session.get(Urun, oda.get("aktif_urun_id"))
                    if oda.get("aktif_urun_id")
                    else None
                )
                if sahne_urun is not None:
                    _otonom_sayaci_baslat(d, sahne_urun)
            if lot_no is not None:
                baslatilan.append({"dukkan": d, "lot": lot_no})
            else:
                lotlar = _otonom_satilmamis_lotlar(d)
                if lotlar:
                    baslatilan.append({"dukkan": d, "lot": lotlar[0].lot_no})
                else:
                    lot_yok.append(d)
            _iki_odayi(
                d,
                "otonom_durum_degisti",
                {
                    "dukkan": d,
                    "aktif": True,
                    "sahne": oda.get("durum").get("durum") if oda.get("durum") else "Sayim",
                    "aktif_urun_id": oda.get("aktif_urun_id"),
                },
            )
        else:
            oda["otonom_aktif"] = False
            _otonom_sahneyi_kapat(d)

    if durum:
        mesaj = f"{len(baslatilan)} dükkanda otonom tur tetiklendi."
        if lot_yok:
            mesaj += " Lot bulunamayan: " + ", ".join(lot_yok) + "."
    else:
        mesaj = f"Otonom mezat durduruldu: {hedef.upper()}"

    invalidate_vitrin_cache()
    return jsonify({
        "success": True,
        "mesaj": mesaj,
        "baslatilan_dukkanlar": baslatilan,
        "lot_bulunamayan": lot_yok,
        "durumlar": {d: odalar[d]["otonom_aktif"] for d in DUKKANLAR.keys()},
    })


MUZIK_VARSAYILAN_VIDEO = "j12l7mQANFU"
MUZIK_ISTASYON_SIRASI = (
    ("nostalji", "Nostalji / Taş Plak"),
    ("tsm", "Türk Sanat Müziği"),
    ("akustik", "Akustik / Enstrümantal"),
    ("fon", "Dinlendirici / Fon"),
)
_MUZIK_ISTASYON_KILIT = threading.Lock()
_YT_KIMLIK = re.compile(r"^[A-Za-z0-9_-]{6,128}$")


def _muzik_istasyon_dosyasi():
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    return os.path.join(basedir, "instance", "muzik_istasyonlari.json")


def _muzik_istasyon_varsayilan():
    return {
        kod: {"id": MUZIK_VARSAYILAN_VIDEO, "tur": "video", "ad": ad}
        for kod, ad in MUZIK_ISTASYON_SIRASI
    }


def _youtube_kimlik_ayikla(ham):
    metin = (ham or "").strip()
    if not metin:
        return "", "video"
    if "playlist?list=" in metin or "/playlist?" in metin:
        if "list=" in metin:
            return metin.split("list=", 1)[1].split("&", 1)[0], "playlist"
    if "youtu.be/" in metin:
        parca = metin.split("youtu.be/", 1)[1]
        return parca.split("?")[0].split("&")[0].split("/")[0], "video"
    if "v=" in metin:
        return metin.split("v=", 1)[1].split("&", 1)[0], "video"
    if "list=" in metin:
        return metin.split("list=", 1)[1].split("&", 1)[0], "playlist"
    temiz = metin.split("?")[0].split("&")[0].split("/")[-1]
    if temiz.startswith(("PL", "UU", "OL", "LL", "RD", "FL")) and len(temiz) > 12:
        return temiz, "playlist"
    return temiz, "video"


def muzik_istasyonlarini_oku():
    kayit = _muzik_istasyon_varsayilan()
    yol = _muzik_istasyon_dosyasi()
    try:
        with open(yol, "r", encoding="utf-8") as f:
            gelen = json.load(f)
    except (OSError, ValueError):
        gelen = {}
    if not isinstance(gelen, dict):
        gelen = {}
    for kod, ad in MUZIK_ISTASYON_SIRASI:
        ham = gelen.get(kod) or {}
        if isinstance(ham, str):
            ham = {"id": ham, "tur": "video"}
        if not isinstance(ham, dict):
            continue
        kimlik = str(ham.get("id") or "").strip()
        tur = ham.get("tur") if ham.get("tur") in ("video", "playlist") else "video"
        if _YT_KIMLIK.match(kimlik):
            kayit[kod] = {"id": kimlik, "tur": tur, "ad": ad}
        else:
            kayit[kod]["ad"] = ad
    return kayit


def muzik_istasyonlarini_yaz(istasyonlar):
    yol = _muzik_istasyon_dosyasi()
    with _MUZIK_ISTASYON_KILIT:
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(istasyonlar, f, ensure_ascii=False, indent=2)
    return istasyonlar


def _muzik_istasyon_yayinla(istasyonlar, eski_video_id=None):
    socketio.emit("canli_muzik_istasyonlari", {"istasyonlar": istasyonlar})
    if eski_video_id:
        socketio.emit("canli_muzik_degisti", {
            "video_id": eski_video_id,
            "tur": "video",
            "kategori": "nostalji",
        })


@app.route("/api/muzik-istasyonlari", methods=["GET"])
def muzik_istasyonlari():
    return jsonify({"success": True, "istasyonlar": muzik_istasyonlarini_oku()})


@app.route("/api/admin/muzik-link-guncelle", methods=["POST"])
@admin_required
def admin_muzik_link_guncelle():
    veri = request.json or {}
    mevcut = muzik_istasyonlarini_oku()

    if isinstance(veri.get("istasyonlar"), dict):
        for kod, _ad in MUZIK_ISTASYON_SIRASI:
            ham = veri["istasyonlar"].get(kod, "")
            if isinstance(ham, dict):
                ham = ham.get("id") or ham.get("link") or ""
            kimlik, tur = _youtube_kimlik_ayikla(str(ham or ""))
            if not _YT_KIMLIK.match(kimlik):
                return jsonify({
                    "success": False,
                    "mesaj": f"{mevcut[kod]['ad']} için geçerli bir YouTube video veya liste kimliği girin.",
                }), 400
            mevcut[kod]["id"] = kimlik
            mevcut[kod]["tur"] = tur
        muzik_istasyonlarini_yaz(mevcut)
        _muzik_istasyon_yayinla(mevcut)
        return jsonify({"success": True, "istasyonlar": mevcut})

    video_id, tur = _youtube_kimlik_ayikla(veri.get("video_id", MUZIK_VARSAYILAN_VIDEO))
    if not _YT_KIMLIK.match(video_id):
        return jsonify({"success": False, "mesaj": "Geçersiz video ID!"}), 400

    kategori = veri.get("kategori") or "nostalji"
    if kategori not in mevcut:
        kategori = "nostalji"
    mevcut[kategori]["id"] = video_id
    mevcut[kategori]["tur"] = tur
    muzik_istasyonlarini_yaz(mevcut)
    _muzik_istasyon_yayinla(mevcut, eski_video_id=video_id if kategori == "nostalji" else None)
    return jsonify({"success": True, "video_id": video_id, "istasyonlar": mevcut})


YENI_URUN_HAVUZ = 100
_YENI_URUN_HARIC_KILIT = threading.Lock()
_YENI_VIDEO_UZANTILARI = (".mp4", ".webm", ".ogg", ".mov", ".m4v", ".m3u8")


def _yeni_urun_haric_dosyasi():
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    return os.path.join(basedir, "instance", "yeni_urun_haric.json")


def yeni_urun_haric_oku():
    try:
        with open(_yeni_urun_haric_dosyasi(), "r", encoding="utf-8") as f:
            gelen = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(gelen, list):
        return set()
    kimlikler = set()
    for deger in gelen:
        try:
            kimlikler.add(int(deger))
        except (TypeError, ValueError):
            continue
    return kimlikler


def yeni_urun_haric_yaz(kimlikler):
    yol = _yeni_urun_haric_dosyasi()
    with _YENI_URUN_HARIC_KILIT:
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(sorted(kimlikler), f)
    return kimlikler


def _yeni_video_mu(yol):
    adres = str(yol or "").split("?", 1)[0].lower()
    return adres.endswith(_YENI_VIDEO_UZANTILARI)


def _yeni_medya_listesi(ham):
    try:
        if ham is None:
            return []
        if isinstance(ham, str):
            temiz = ham.strip()
            if not temiz:
                return []
            if temiz.startswith("["):
                ham = json.loads(temiz)
            else:
                return [temiz]
        if isinstance(ham, (list, tuple)):
            return [str(oge).strip() for oge in ham if str(oge).strip()]
    except Exception:
        return []
    return []


def _yeni_urun_temel_sorgu():
    silinmedi_kosullari = [
        Urun.silindi_mi.is_(False),
        Urun.silindi_mi.is_(None),
    ]
    if IS_SQLITE:
        silinmedi_kosullari.append(Urun.silindi_mi == 0)
    silinmedi = or_(*silinmedi_kosullari)
    satilmadi = or_(Urun.durum.is_(None), Urun.durum != "Satıldı")
    try:
        kolonlar = {kolon.name for kolon in Urun.__table__.columns}
    except Exception:
        kolonlar = set()
    if "satildi_mi" in kolonlar and hasattr(Urun, "satildi_mi"):
        satilmadi = or_(
            Urun.satildi_mi.is_(False),
            Urun.satildi_mi.is_(None),
            Urun.satildi_mi == 0,
        )
    return Urun.query.filter(silinmedi, satilmadi)


def _yeni_urun_paketle(urun):
    fotolar = _yeni_medya_listesi(getattr(urun, "fotograflar", None))
    video = str(getattr(urun, "video", None) or "").strip()
    if not video:
        for adres in fotolar:
            if _yeni_video_mu(adres):
                video = adres
                break
    gorsel = ""
    for adres in fotolar:
        if adres and not _yeni_video_mu(adres):
            gorsel = adres
            break
    if not gorsel and fotolar and not _yeni_video_mu(fotolar[0]) and not video:
        gorsel = fotolar[0]
    dukkan = getattr(urun, "dukkan_kodu", None) or "gumus"
    try:
        acilis = float(getattr(urun, "acilis_fiyati", None) or 0)
    except (TypeError, ValueError):
        acilis = 0.0
    baslik = getattr(urun, "urun_adi", None) or ""
    lot = getattr(urun, "lot_no", None)
    return {
        "id": urun.id,
        "baslik": baslik,
        "urun_adi": baslik,
        "ad": baslik,
        "gorsel": gorsel,
        "video_yolu": video,
        "video": video,
        "fotograflar": fotolar,
        "lot_no": lot,
        "lot": lot,
        "baslangic_fiyati": acilis,
        "fiyat": acilis,
        "dukkan_id": dukkan,
        "dukkan": dukkan,
        "dukkan_adi": DUKKANLAR.get(dukkan, {}).get("ad", dukkan),
        "yeni": True,
    }


def yeni_urunleri_getir():
    haric = yeni_urun_haric_oku()
    try:
        son_eklenenler = (
            _yeni_urun_temel_sorgu()
            .order_by(Urun.id.desc())
            .limit(YENI_URUN_HAVUZ)
            .all()
        )
    except Exception:
        db.session.rollback()
        son_eklenenler = (
            Urun.query.order_by(Urun.id.desc())
            .limit(YENI_URUN_HAVUZ)
            .all()
        )
    havuz = []
    for urun in son_eklenenler:
        if len(havuz) >= YENI_URUN_HAVUZ:
            break
        try:
            if urun is None or urun.id in haric:
                continue
            if getattr(urun, "silindi_mi", 0) in (True, 1):
                continue
            if (getattr(urun, "durum", None) or "") == "Satıldı":
                continue
            if "satildi_mi" in {kolon.name for kolon in Urun.__table__.columns}:
                if getattr(urun, "satildi_mi", False) in (True, 1):
                    continue
            havuz.append(_yeni_urun_paketle(urun))
        except Exception:
            continue
    return havuz


@app.route("/api/yeni-urunler", methods=["GET"])
def yeni_urunler():
    try:
        cevap = jsonify({"success": True, "urunler": yeni_urunleri_getir()})
    except Exception:
        db.session.rollback()
        cevap = jsonify({"success": True, "urunler": []})
    cevap.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    cevap.headers["Pragma"] = "no-cache"
    return cevap


@app.route("/api/admin/yeni-urun-cikar", methods=["POST"])
@admin_required
def yeni_urun_cikar():
    veri = request.json or {}
    try:
        urun_id = int(veri.get("urun_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "mesaj": "Geçersiz ürün."}), 400
    urun = Urun.query.get(urun_id)
    if not urun or urun.silindi_mi:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    haric = yeni_urun_haric_oku()
    haric.add(urun_id)
    yeni_urun_haric_yaz(haric)
    socketio.emit("yeni_urunler_degisti", {"urun_id": urun_id})
    try:
        liste = yeni_urunleri_getir()
    except Exception:
        db.session.rollback()
        liste = []
    return jsonify({"success": True, "urunler": liste})


_STUDYO_GORSEL = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_STUDYO_VIDEO = {".mp4", ".mov", ".webm", ".m4v", ".mkv", ".avi"}


def _studyo_uzanti(ad):
    return os.path.splitext(str(ad or "").lower())[1]


def _studyo_mime(ad):
    uzanti = _studyo_uzanti(ad)
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(uzanti, "image/jpeg")


def _studyo_json_ayikla(ham):
    metin = (ham or "").strip()
    if metin.startswith("```"):
        metin = re.sub(r"^```(?:json)?", "", metin, flags=re.I).strip()
        metin = re.sub(r"```$", "", metin).strip()
    try:
        veri = json.loads(metin)
    except (TypeError, ValueError):
        eslesen = re.search(r"\{.*\}", metin, re.S)
        if not eslesen:
            return {}
        try:
            veri = json.loads(eslesen.group(0))
        except (TypeError, ValueError):
            return {}
    return veri if isinstance(veri, dict) else {}


def _studyo_alanlari(veri):
    def yazi(anahtar):
        return str((veri or {}).get(anahtar) or "").strip()

    try:
        fiyat = float(veri.get("baslangic_fiyati") or 0)
    except (TypeError, ValueError):
        fiyat = 0.0
    if fiyat < 0:
        fiyat = 0.0
    try:
        hemen = float(veri.get("hemen_al_fiyati") or 0)
    except (TypeError, ValueError):
        hemen = 0.0
    if hemen < 0:
        hemen = 0.0
    return {
        "baslik": yazi("baslik")[:200],
        "malzeme": yazi("malzeme")[:100],
        "boyut": yazi("boyut")[:120],
        "secere": yazi("secere")[:300],
        "kondisyon": yazi("kondisyon")[:2000],
        "baslangic_fiyati": round(fiyat, 2),
        "hemen_al_fiyati": round(hemen, 2),
    }


def _studyo_gorsel_analiz(mime, ham_bayt):
    anahtar = (getattr(Config, "OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not anahtar:
        return {}, "OpenAI anahtarı tanımlı değil."
    b64 = base64.b64encode(ham_bayt).decode("ascii")
    mesajlar = [
        {
            "role": "system",
            "content": (
                "Sen Haliç Antik Mezat'ın kıdemli antikacısı ve eksperisin. "
                "Görseli mezat vitrini için incele. Emin olmadığın dönemi kesin tarih gibi yazma. "
                "Yalnızca JSON döndür. Anahtarlar: baslik, malzeme, boyut, secere, kondisyon, baslangic_fiyati, hemen_al_fiyati. "
                "baslik cezbedici ve kısa olsun. malzeme ürün cinsi ve malzemesidir. "
                "boyut görselden tahminse yaklaşık olduğunu belirt. secere menşei ve dönemdir. "
                "kondisyon satışa uygun Türkçe açıklamadır. baslangic_fiyati açılış peyidir. "
                "hemen_al_fiyati açılışın üstünde tahmini vitrin bedelidir. İkisi de TL cinsinden sayı olsun."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Bu ürün görselinden mezat kartını çıkar."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        },
    ]

    def _tamamla(response_format):
        import subprocess

        govde = {
            "model": "gpt-4o-mini",
            "temperature": 0.4,
            "max_tokens": 700,
            "messages": mesajlar,
        }
        if response_format:
            govde["response_format"] = response_format
        ham_govde = json.dumps(govde, ensure_ascii=False).encode("utf-8")
        if os.name == "nt":
            komut = [
                "curl.exe",
                "-sS",
                "-k",
                "--ssl-no-revoke",
                "-X", "POST",
                "https://api.openai.com/v1/chat/completions",
                "-H", "Authorization: Bearer " + anahtar,
                "-H", "Content-Type: application/json",
                "-H", "Accept: application/json",
                "--data-binary", "@-",
                "-w", "\n%{http_code}",
            ]
        else:
            komut = [
                "curl",
                "-sS",
                "-X", "POST",
                "https://api.openai.com/v1/chat/completions",
                "-H", "Authorization: Bearer " + anahtar,
                "-H", "Content-Type: application/json",
                "-H", "Accept: application/json",
                "--data-binary", "@-",
                "-w", "\n%{http_code}",
            ]
        proc = subprocess.run(
            komut,
            input=ham_govde,
            capture_output=True,
            timeout=90,
        )
        if proc.returncode != 0:
            hata = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(hata or ("curl çıkış kodu " + str(proc.returncode)))
        parca = (proc.stdout or b"").rsplit(b"\n", 1)
        if len(parca) != 2:
            raise RuntimeError("curl yanıtı okunamadı.")
        veri, kod_ham = parca
        try:
            kod = int((kod_ham or b"").strip() or b"0")
        except ValueError:
            kod = 0
        if kod != 200:
            ozet = veri.decode("utf-8", errors="replace").replace("\n", " ")[:500]
            raise RuntimeError("HTTP " + str(kod) + ": " + ozet)
        cozulen = json.loads(veri.decode("utf-8"))
        secim = (cozulen.get("choices") or [{}])[0]
        icerik = (secim.get("message") or {}).get("content") or ""
        return icerik

    try:
        try:
            ham = _tamamla({"type": "json_object"})
        except Exception:
            ham = _tamamla(None)
    except Exception as e:
        return {}, "Görsel analizi tamamlanamadı: " + str(e)
    return _studyo_alanlari(_studyo_json_ayikla(ham)), ""


@app.route("/api/studyo/analiz", methods=["POST"])
@admin_required
def studyo_analiz():
    try:
        dosya = request.files.get("gorsel")
        if dosya is None or not getattr(dosya, "filename", ""):
            for aday in request.files.getlist("dosyalar"):
                if _studyo_uzanti(aday.filename) in _STUDYO_GORSEL:
                    dosya = aday
                    break
        if dosya is None or _studyo_uzanti(getattr(dosya, "filename", "")) not in _STUDYO_GORSEL:
            return jsonify({
                "success": False,
                "mesaj": "Şecere için bir ürün fotoğrafı seçin. Video ayrıca kaydedilir.",
            }), 400
        ham = dosya.read()
        if not ham:
            return jsonify({"success": False, "mesaj": "Dosya boş."}), 400
        if len(ham) > 8 * 1024 * 1024:
            return jsonify({"success": False, "mesaj": "Fotoğraf 8 MB sınırını aşıyor."}), 400
        analiz, hata = _studyo_gorsel_analiz(_studyo_mime(dosya.filename), ham)
        if hata:
            return jsonify({"success": False, "mesaj": hata})
        return jsonify({"success": True, "analiz": analiz})
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)})


@app.route("/api/studyo/transfer", methods=["POST"])
@admin_required
def studyo_transfer():
    def _izleyici_url(adres, video_mu):
        adres = str(adres or "").strip()
        damga = "/upload/"
        yer = adres.find(damga)
        if "res.cloudinary.com" not in adres or yer < 0:
            return adres
        kuyruk = adres[yer + len(damga):]
        if not kuyruk:
            return adres
        ilk = kuyruk.split("/", 1)[0]
        surum = len(ilk) > 1 and ilk[0] == "v" and ilk[1:].isdigit()
        onekler = ("f_", "q_", "w_", "c_", "h_", "e_", "g_", "b_", "a_", "dpr_", "fl_", "ar_")
        if ("," in ilk) or (not surum and ilk.startswith(onekler)):
            return adres
        ek = (
            "f_auto,q_auto:good,w_1080,c_limit/"
            if video_mu
            else "f_auto,q_auto,w_1200,c_limit/"
        )
        return adres[: yer + len(damga)] + ek + kuyruk

    try:
        dukkan = dukkan_dogrula(request.form.get("dukkan", "gumus"))
        baslik = (request.form.get("baslik") or "İsimsiz Ürün").strip()[:200]
        malzeme = (request.form.get("malzeme") or "Genel").strip()[:100] or "Genel"
        boyut = (request.form.get("boyut") or "").strip()
        secere = (request.form.get("secere") or "").strip()
        kondisyon = (request.form.get("kondisyon") or "").strip()
        try:
            fiyat = float(request.form.get("baslangic_fiyati") or 0)
        except (TypeError, ValueError):
            fiyat = 0.0
        if fiyat < 0:
            fiyat = 0.0
        try:
            hemen_al = float(request.form.get("hemen_al_fiyati") or 0)
        except (TypeError, ValueError):
            hemen_al = 0.0
        if hemen_al < 0:
            hemen_al = 0.0

        fotograflar = []
        video_url = ""
        gelen_adresler = []
        for ham in request.form.getlist("medya_urlleri"):
            metin = str(ham or "").strip()
            if not metin:
                continue
            if metin.startswith("["):
                try:
                    cozulen = json.loads(metin)
                except (TypeError, ValueError):
                    cozulen = None
                if isinstance(cozulen, list):
                    for oge in cozulen:
                        adres = str(oge or "").strip()
                        if adres:
                            gelen_adresler.append(adres)
                    continue
            gelen_adresler.append(metin)

        if gelen_adresler:
            for url in gelen_adresler:
                if not url.startswith("https://res.cloudinary.com/"):
                    continue
                yol = url.split("?", 1)[0].lower()
                video_mu = ("/video/upload/" in yol) or (_studyo_uzanti(yol) in _STUDYO_VIDEO)
                url = _izleyici_url(url, video_mu)
                if video_mu and not video_url:
                    video_url = url
                fotograflar.append(url)
        else:
            for dosya in request.files.getlist("dosyalar"):
                url, temiz_ad = medyayi_kaydet(dosya)
                if not url or not temiz_ad:
                    continue
                uzanti = _studyo_uzanti(temiz_ad)
                video_mu = uzanti in _STUDYO_VIDEO
                url = _izleyici_url(url, video_mu)
                if video_mu and not video_url:
                    video_url = url
                fotograflar.append(url)
        if not fotograflar:
            return jsonify({"success": False, "mesaj": "En az bir fotoğraf veya video gerekli."}), 400

        en_yuksek_lot = (
            db.session.query(func.max(Urun.lot_no))
            .filter(Urun.dukkan_kodu == dukkan, Urun.silindi_mi.is_(False))
            .scalar()
        )
        lot_no = (en_yuksek_lot + 1) if en_yuksek_lot else 1
        tanitim = []
        if malzeme:
            tanitim.append("Malzeme: " + malzeme)
        if boyut:
            tanitim.append("Ebat: " + boyut)
        if secere:
            tanitim.append("Şecere: " + secere)
        if kondisyon:
            tanitim.append(kondisyon)

        ham_tedarikci = request.form.get("tedarikci_id", None)
        if ham_tedarikci is None:
            govde = request.get_json(silent=True) or {}
            if isinstance(govde, dict):
                ham_tedarikci = govde.get("tedarikci_id")
        secili_tedarikci_id = None
        if ham_tedarikci is not None:
            metin = str(ham_tedarikci).strip()
            if metin and metin.lower() not in ("null", "none"):
                try:
                    sayi = int(metin)
                except (TypeError, ValueError):
                    sayi = 0
                if sayi > 0:
                    secili_tedarikci_id = sayi

        yeni_urun = Urun(
            dukkan_kodu=dukkan,
            lot_no=lot_no,
            urun_adi=baslik,
            kategori=kategori_ad_temizle(malzeme),
            acilis_fiyati=fiyat,
            guncel_fiyat=fiyat,
            hemen_al_fiyati=hemen_al,
            tanitim_yazisi="\n".join(tanitim),
            fotograflar=fotograflar,
            video=video_url,
            ses_dosyasi="",
            durum="Aktif",
            silindi_mi=False,
            tedarikci_id=secili_tedarikci_id,
        )
        if hasattr(yeni_urun, "eklenme_tarihi") and getattr(yeni_urun, "eklenme_tarihi", None) is None:
            yeni_urun.eklenme_tarihi = datetime.now()
        db.session.add(yeni_urun)
        db.session.commit()
        return jsonify({
            "success": True,
            "lot_no": lot_no,
            "dukkan": dukkan,
            "id": yeni_urun.id,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)}), 500


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

_XML_ONIZLEME = 100
_XML_LAB_BAYT = 40 * 1024 * 1024
_XML_URUN_ETIKETLERI = {"item", "urun", "product", "entry", "offer"}
_XML_AD_ETIKETLERI = {
    "title", "name", "ad", "baslik", "urunadi", "urun_adi",
    "productname", "product_name",
}
_XML_ACIKLAMA_ETIKETLERI = {
    "description", "aciklama", "tanitim", "tanitim_yazisi",
    "detail", "summary", "long_description",
}
_XML_KATEGORI_ETIKETLERI = {
    "category", "kategori", "product_type", "google_product_category",
    "kategoriler",
}
_XML_FIYAT_ONCELIK = (
    "fiyat", "fiyat1", "fiyat2", "satisfiyati", "bayifiyati", "psf",
    "listefiyati", "price", "saleprice", "regularprice", "tutar",
    "birimfiyat", "kdvdahilfiyat", "kdvharicfiyat", "genelfiyat",
    "urunfiyati", "fiyattl", "pricewithvat",
)
_XML_STOK_ONCELIK = (
    "stok", "stock", "stokadedi", "stokadet", "quantity", "qty", "miktar", "adet",
)
_XML_GORSEL_ETIKETLERI = {
    "image", "image_link", "gorsel", "picture", "img", "foto", "fotograf",
    "image_url", "resim", "additional_image_link", "resim_url",
}
_XML_AD_BASLIKLARI = {
    "ad", "urun", "urun adi", "urunadi", "name", "title", "baslik",
    "product", "product name", "productname",
}
_XML_ACIKLAMA_BASLIKLARI = {
    "aciklama", "description", "tanitim", "tanitim yazisi", "detay", "detail",
}
_XML_KATEGORI_BASLIKLARI = {
    "kategori", "category", "product type", "producttype",
}
_XML_FIYAT_BASLIKLARI = _XML_FIYAT_ONCELIK
_XML_GORSEL_BASLIKLARI = {
    "gorsel", "resim", "image", "image link", "foto", "fotograf",
    "picture", "resim url", "image url",
}
_XML_MODELARABA_IFADELER = (
    "klasik araba", "model araba", "diecast", "maket araba", "koleksiyon araba",
    "metal araba", "vosvos", "1:18", "1:24", "1:32", "1:43", "1:64",
    "hot wheels", "matchbox",
)
_XML_VARSAYILAN_KURALLAR = (
    ("modelaraba", _XML_MODELARABA_IFADELER),
    ("plak", ("plak", "pikap", "kaset", "cd")),
    ("retro", (
        "retro", "nostalji", "hediyelik", "biblo", "küre", "kar küresi",
        "müzik kutusu", "nostaljik", "çerçeve", "kupa", "termos", "anahtarlık",
    )),
    ("evyasam", (
        "mobilya", "avize", "lamba", "mutfak", "salon", "dekor", "dekorasyon",
        "aydınlatma", "abajur", "masa", "sandalye", "organizer", "düzenleyici",
        "banyo", "havlu",
    )),
    ("tekstil", ("tekstil", "nevresim", "halı", "kilim", "giyim")),
    ("gumus", ("gumus", "925", "telkari")),
    ("bakir", ("bakir", "pirinc", "sahan", "ibrik", "maden", "bronz")),
    ("porselen", ("porselen", "seramik", "cini", "fincan", "tabak", "vazo")),
    ("elektronik", (
        "elektrik", "elektronik", "radyo", "pikap", "plak", "kamera", "saat",
        "şarj", "kablo", "kulaklık", "hoparlör", "bluetooth", "powerbank",
        "akıllı saat", "led", "adaptör", "usb",
    )),
    ("hirdavat", (
        "hirdavat", "nalbur", "alet", "matkap", "anahtar", "tornavida", "pense",
        "anahtar takımı", "ölçü", "metre", "tamir", "el aletleri", "yapıştırıcı", "bant",
    )),
    ("kozmetik", ("kozmetik", "parfum", "bakim", "krem")),
    ("bijuteri", ("bijuteri", "kolye", "kupe", "bileklik", "yuzuk")),
    ("antika", ("antika", "osmanli", "koleksiyon", "eski")),
    ("tasarim", ("tasarim", "dekoratif", "retro", "vintage", "lamba", "aydinlatma")),
    ("toptan", ("toptan", "koli", "seri")),
    ("karma", (
        "oyuncak", "peluş", "bebek", "lego", "puzzle", "yapboz",
        "figür", "kutu oyunu", "çocuk", "eğitici",
    )),
)
_XML_OTOMATIK_HARIC = frozenset({"diger"})
_XML_MODELARABA_YALIN = frozenset({"model", "modeller", "modelleri", "araba"})
_XML_MODELARABA_ENGEL = (
    "kerata", "çekecek", "elbise", "kıyafet", "tişört", "gömlek", "pantolon",
    "bebek arabası", "pazar arabası", "alışveriş arabası",
)


def _xml_kural_yolu():
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    return os.path.join(basedir, "instance", "xml_laboratuvar_kurallar.json")


def _xml_metin_sade(metin):
    s = html.unescape(str(metin or "")).casefold()
    s = s.replace("i\u0307", "i")
    s = s.translate(str.maketrans({
        "ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c",
        "â": "a", "î": "i", "û": "u",
    }))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _xml_etiket_temizle(metin):
    duz = re.sub(r"<[^>]+>", " ", html.unescape(str(metin or "")))
    return " ".join(duz.split())


def _xml_kural_listesi():
    yol = _xml_kural_yolu()
    if not os.path.isfile(yol):
        return []
    try:
        with open(yol, "r", encoding="utf-8") as dosya:
            ham = json.load(dosya)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(ham, list):
        return []
    temiz = []
    gorulen = set()
    for kayit in ham:
        if not isinstance(kayit, dict):
            continue
        kelime = _xml_metin_sade(kayit.get("kelime"))
        dukkan = str(kayit.get("dukkan") or "").strip().lower()
        if (
            not kelime
            or dukkan not in DUKKANLAR
            or dukkan in _XML_OTOMATIK_HARIC
            or (
                dukkan == "modelaraba"
                and not _xml_modelaraba_adayi_kabul("modelaraba", kelime, False)
            )
            or kelime in gorulen
        ):
            continue
        gorulen.add(kelime)
        temiz.append({"kelime": kelime, "dukkan": dukkan})
    return temiz


def _xml_kural_yaz(kurallar):
    yol = _xml_kural_yolu()
    with open(yol, "w", encoding="utf-8") as dosya:
        json.dump(kurallar, dosya, ensure_ascii=False, indent=2)


def _xml_kelime_var(metin, kelime):
    hedef = _xml_metin_sade(kelime)
    if not hedef or not metin:
        return False
    return f" {hedef} " in f" {metin} "


_XML_MODELARABA_IZIN = frozenset(
    _xml_metin_sade(kelime) for kelime in _XML_MODELARABA_IFADELER
)


def _xml_kategori_karma_mi(kategori):
    metin = _xml_metin_sade(kategori)
    if not metin:
        return False
    if "oyuncak" in metin:
        return True
    return "ahsap egitici" in metin


def _xml_modelaraba_baslik_engelli(ad):
    baslik = _xml_metin_sade(ad)
    if not baslik:
        return False
    for kelime in _XML_MODELARABA_ENGEL:
        if _xml_kelime_var(baslik, kelime):
            return True
    return False


def _xml_modelaraba_adayi_kabul(dukkan, kelime, baslik_engelli):
    if dukkan != "modelaraba":
        return True
    if baslik_engelli:
        return False
    sade = _xml_metin_sade(kelime)
    if not sade or sade in _XML_MODELARABA_YALIN:
        return False
    return sade in _XML_MODELARABA_IZIN


def _xml_dagitim_dukkani(kod):
    kod = str(kod or "").strip().lower()
    if kod in DUKKANLAR and kod not in _XML_OTOMATIK_HARIC:
        return kod
    return "karma"


def _xml_varsayilan_kural_listesi():
    liste = []
    for dukkan, kelimeler in _XML_VARSAYILAN_KURALLAR:
        if dukkan in _XML_OTOMATIK_HARIC:
            continue
        for kelime in kelimeler:
            liste.append({"kelime": kelime, "dukkan": dukkan, "yerlesik": True})
    return liste


def _xml_dukkan_siniflandir(ad, aciklama="", kategori="", kurallar=None):
    if _xml_kategori_karma_mi(kategori):
        return "karma"
    metin = _xml_metin_sade(" ".join([
        str(ad or ""), str(aciklama or ""), str(kategori or ""),
    ]))
    baslik_engelli = _xml_modelaraba_baslik_engelli(ad)
    if kurallar is None:
        kurallar = _xml_kural_listesi()
    for kural in kurallar:
        hedef = str(kural.get("dukkan") or "").strip().lower()
        if hedef in _XML_OTOMATIK_HARIC:
            continue
        if not _xml_modelaraba_adayi_kabul(hedef, kural.get("kelime"), baslik_engelli):
            continue
        if _xml_kelime_var(metin, kural.get("kelime")):
            return _xml_dagitim_dukkani(hedef)
    for dukkan, kelimeler in _XML_VARSAYILAN_KURALLAR:
        if dukkan in _XML_OTOMATIK_HARIC:
            continue
        for kelime in kelimeler:
            if not _xml_modelaraba_adayi_kabul(dukkan, kelime, baslik_engelli):
                continue
            if _xml_kelime_var(metin, kelime):
                return _xml_dagitim_dukkani(dukkan)
    return "karma"


def _xml_fiyat_oku(ham):
    if ham is None:
        return None
    try:
        if ham != ham:
            return None
    except Exception:
        pass
    if isinstance(ham, bool):
        return None
    if isinstance(ham, (int, float)):
        sayi = float(ham)
        return sayi if sayi > 0 else None
    metin = re.sub(r"[^\d,.\-]", "", str(ham).strip())
    if not metin or metin in {"-", ".", ",", "-."}:
        return None
    if "," in metin and "." in metin:
        if metin.rfind(",") > metin.rfind("."):
            metin = metin.replace(".", "").replace(",", ".")
        else:
            metin = metin.replace(",", "")
    elif "," in metin:
        parcalar = metin.split(",")
        if len(parcalar[-1]) == 3 and len(parcalar) > 1 and all(p.isdigit() for p in parcalar):
            metin = "".join(parcalar)
        else:
            metin = metin.replace(".", "").replace(",", ".")
    elif "." in metin:
        parcalar = metin.split(".")
        if len(parcalar[-1]) == 3 and len(parcalar) > 1 and all(p.isdigit() for p in parcalar):
            metin = "".join(parcalar)
    try:
        sayi = float(metin)
    except (TypeError, ValueError):
        return None
    return sayi if sayi > 0 else None


def _xml_gorsel_listesi(ham):
    if ham is None:
        return []
    if isinstance(ham, (list, tuple)):
        parcalar = list(ham)
    else:
        parcalar = re.split(r"[\s,;|]+", str(ham))
    adresler = []
    for parca in parcalar:
        adres = str(parca or "").strip()
        if adres.lower().startswith(("http://", "https://")) and adres not in adresler:
            adresler.append(adres[:1000])
        if len(adresler) >= 8:
            break
    return adresler


def _xml_kayit_paketle(ad, aciklama, kategori, fiyat, gorseller, kurallar=None, stok_sifir=False):
    ad = _xml_etiket_temizle(ad)[:200]
    if not ad:
        return None
    aciklama = _xml_etiket_temizle(aciklama)[:4000]
    kategori = _xml_etiket_temizle(kategori)[:100]
    try:
        fiyat = _xml_fiyat_oku(fiyat)
    except Exception:
        fiyat = None
    try:
        gorseller = _xml_gorsel_listesi(gorseller)
    except Exception:
        gorseller = []
    nedenler = []
    if stok_sifir:
        nedenler.append("stok 0")
    elif fiyat is None:
        nedenler.append("fiyat yok")
    return {
        "ad": ad,
        "aciklama": aciklama,
        "kategori": kategori or "Genel",
        "fiyat": fiyat,
        "gorseller": gorseller,
        "dukkan": _xml_dukkan_siniflandir(ad, aciklama, kategori, kurallar),
        "kayda_uygun": not nedenler,
        "neden": ", ".join(nedenler),
    }


def _xml_yerel_ad(etiket):
    return str(etiket or "").rsplit("}", 1)[-1].strip().lower()


def _xml_etiket_anahtari(etiket):
    s = _xml_yerel_ad(etiket).casefold().replace("i\u0307", "i")
    s = s.translate(str.maketrans({
        "ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c",
        "â": "a", "î": "i", "û": "u",
    }))
    return re.sub(r"[^a-z0-9]", "", s)


def _xml_stok_sayisi(ham):
    if ham is None:
        return None
    metin = str(ham).strip()
    if not metin:
        return None
    deger = _xml_fiyat_oku(metin)
    if deger is not None:
        return deger
    rakam = re.sub(r"[^\d]", "", re.sub(r"[^\d,.\-]", "", metin))
    if rakam and set(rakam) == {"0"}:
        return 0.0
    return None


def _xml_alan_metinleri(eleman, anahtarlar):
    bulunan = {}
    for cocuk in eleman.iter():
        if cocuk is eleman:
            continue
        anahtar = _xml_etiket_anahtari(cocuk.tag)
        if anahtar not in anahtarlar or anahtar in bulunan:
            continue
        if any(_xml_yerel_ad(alt.tag) in _XML_URUN_ETIKETLERI for alt in list(cocuk)):
            continue
        metin = " ".join(cocuk.itertext()).strip()
        if metin:
            bulunan[anahtar] = metin
    return bulunan


def _xml_fiyat_bul(eleman):
    bulunan = _xml_alan_metinleri(eleman, set(_XML_FIYAT_ONCELIK))
    for anahtar in _XML_FIYAT_ONCELIK:
        if anahtar not in bulunan:
            continue
        fiyat = _xml_fiyat_oku(bulunan[anahtar])
        if fiyat is not None:
            return fiyat
    return None


def _xml_stok_sifir_mi(eleman):
    bulunan = _xml_alan_metinleri(eleman, set(_XML_STOK_ONCELIK))
    for anahtar in _XML_STOK_ONCELIK:
        if anahtar not in bulunan:
            continue
        adet = _xml_stok_sayisi(bulunan[anahtar])
        if adet == 0:
            return True
    return False


def _xml_eleman_metinleri(eleman, etiketler):
    bulunan = []
    for cocuk in eleman.iter():
        if cocuk is eleman:
            continue
        if _xml_yerel_ad(cocuk.tag) not in etiketler:
            continue
        if any(_xml_yerel_ad(alt.tag) in _XML_URUN_ETIKETLERI for alt in list(cocuk)):
            continue
        metin = " ".join(cocuk.itertext()).strip()
        if metin:
            bulunan.append(metin)
    return bulunan


def _xml_bloktan_urun(eleman, kurallar):
    try:
        adlar = _xml_eleman_metinleri(eleman, _XML_AD_ETIKETLERI)
        if not adlar:
            return None
        aciklamalar = _xml_eleman_metinleri(eleman, _XML_ACIKLAMA_ETIKETLERI)
        kategoriler = _xml_eleman_metinleri(eleman, _XML_KATEGORI_ETIKETLERI)
        gorseller = []
        for metin in _xml_eleman_metinleri(eleman, _XML_GORSEL_ETIKETLERI):
            gorseller.extend(_xml_gorsel_listesi(metin))
        return _xml_kayit_paketle(
            adlar[0],
            " ".join(aciklamalar),
            " ".join(kategoriler),
            _xml_fiyat_bul(eleman),
            gorseller,
            kurallar,
            stok_sifir=_xml_stok_sifir_mi(eleman),
        )
    except Exception:
        return None


def _xml_metinden_urunler(metin, kurallar):
    import xml.etree.ElementTree as ET

    urunler = []
    try:
        kok = ET.fromstring(metin)
    except Exception:
        kok = None
    if kok is not None:
        for eleman in kok.iter():
            if _xml_yerel_ad(eleman.tag) not in _XML_URUN_ETIKETLERI:
                continue
            if any(_xml_yerel_ad(cocuk.tag) in _XML_URUN_ETIKETLERI for cocuk in list(eleman)):
                continue
            paket = _xml_bloktan_urun(eleman, kurallar)
            if paket:
                urunler.append(paket)
        if urunler:
            return urunler
    parcalar = re.findall(
        r"<(?:item|urun|product|entry|offer)\b[\s\S]*?</(?:item|urun|product|entry|offer)>",
        metin or "",
        flags=re.IGNORECASE,
    )
    for blok in parcalar:
        try:
            parca = ET.fromstring(blok)
        except Exception:
            continue
        paket = _xml_bloktan_urun(parca, kurallar)
        if paket:
            urunler.append(paket)
    return urunler


def _xml_tablo_alani(basliklar, kayit, hedefler):
    for baslik in basliklar:
        if _xml_metin_sade(baslik) in hedefler:
            return kayit.get(baslik)
    return None


def _xml_tablo_oncelikli(basliklar, kayit, oncelik):
    harita = {}
    for baslik in basliklar:
        anahtar = re.sub(r"[^a-z0-9]", "", _xml_metin_sade(baslik))
        if anahtar and anahtar not in harita:
            harita[anahtar] = baslik
    for anahtar in oncelik:
        if anahtar in harita:
            return kayit.get(harita[anahtar])
    return None


def _xml_tablodan_urunler(ham, uzanti, kurallar):
    if uzanti == ".csv":
        son_hata = None
        tablo = None
        for ayirac in (",", ";", "\t"):
            try:
                aday = pd.read_csv(io.BytesIO(ham), sep=ayirac, dtype=str)
            except Exception as hata:
                son_hata = hata
                continue
            tablo = aday
            if len(aday.columns) > 1:
                break
        if tablo is None:
            raise ValueError(f"CSV okunamadı: {son_hata}")
    else:
        tablo = pd.read_excel(io.BytesIO(ham), dtype=str)
    tablo = tablo.where(pd.notnull(tablo), None)
    basliklar = list(tablo.columns)
    urunler = []
    for _, satir in tablo.iterrows():
        kayit = satir.to_dict()
        try:
            stok_ham = _xml_tablo_oncelikli(basliklar, kayit, _XML_STOK_ONCELIK)
            paket = _xml_kayit_paketle(
                _xml_tablo_alani(basliklar, kayit, _XML_AD_BASLIKLARI),
                _xml_tablo_alani(basliklar, kayit, _XML_ACIKLAMA_BASLIKLARI),
                _xml_tablo_alani(basliklar, kayit, _XML_KATEGORI_BASLIKLARI),
                _xml_tablo_oncelikli(basliklar, kayit, _XML_FIYAT_ONCELIK),
                _xml_tablo_alani(basliklar, kayit, _XML_GORSEL_BASLIKLARI),
                kurallar,
                stok_sifir=_xml_stok_sayisi(stok_ham) == 0,
            )
        except Exception:
            paket = None
        if paket:
            urunler.append(paket)
    return urunler


def _xml_kaynagi_coz(ham, uzanti):
    kurallar = _xml_kural_listesi()
    if uzanti in {".xlsx", ".xls", ".csv"}:
        return _xml_tablodan_urunler(ham, uzanti, kurallar)
    metin = ham.decode("utf-8-sig", errors="replace") if isinstance(ham, (bytes, bytearray)) else str(ham or "")
    return _xml_metinden_urunler(metin, kurallar)


def _xml_dagilim_metni(urunler):
    sayilar = {kod: 0 for kod in DUKKANLAR}
    for urun in urunler:
        kod = _xml_dagitim_dukkani(urun.get("dukkan"))
        sayilar[kod] = sayilar.get(kod, 0) + 1
    parcalar = [f"{kod}: {adet} ürün" for kod, adet in sayilar.items() if adet]
    return ", ".join(parcalar), {kod: adet for kod, adet in sayilar.items() if adet}


def _xml_onizleme(urunler):
    satirlar = []
    for urun in urunler[:_XML_ONIZLEME]:
        satirlar.append({
            "ad": urun.get("ad") or "",
            "dukkan": _xml_dagitim_dukkani(urun.get("dukkan")),
            "dukkan_ad": (DUKKANLAR.get(_xml_dagitim_dukkani(urun.get("dukkan"))) or {}).get("ad") or "",
            "fiyat": urun.get("fiyat"),
            "gorsel": bool(urun.get("gorseller")),
            "kayda_uygun": bool(urun.get("kayda_uygun")),
            "neden": urun.get("neden") or "",
        })
    return satirlar


def _xml_tarama_yaniti(urunler, kaynak):
    dagilim_metni, dagilim = _xml_dagilim_metni(urunler)
    uygun = sum(1 for urun in urunler if urun.get("kayda_uygun"))
    atlanan = len(urunler) - uygun
    ornek = min(_XML_ONIZLEME, len(urunler))
    mesaj = (
        f"{len(urunler)} ürün tarandı, veritabanına yazılmadı. "
        f"{dagilim_metni or 'dağılım yok'}. "
        f"Kayda uygun: {uygun}, atlanan: {atlanan}. "
        f"Tabloda ilk {ornek} satır örnek; sayaç ve aktarım tüm listeyi kullanır."
    )
    return {
        "success": True,
        "mesaj": mesaj,
        "kaynak": kaynak,
        "toplam": len(urunler),
        "kayda_uygun": uygun,
        "atlanan": atlanan,
        "dagilim": dagilim,
        "onizleme": _xml_onizleme(urunler),
    }


def _xml_kaynagi_indir(url):
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("XML linki http veya https olmalı.")
    cevap = requests.get(url, timeout=90, stream=True)
    try:
        cevap.raise_for_status()
        parcalar = []
        boyut = 0
        for parca in cevap.iter_content(chunk_size=65536):
            if not parca:
                continue
            boyut += len(parca)
            if boyut > _XML_LAB_BAYT:
                raise ValueError("XML yanıtı 40 MB sınırını aşıyor.")
            parcalar.append(parca)
    finally:
        cevap.close()
    return b"".join(parcalar)



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
    return jsonify({"success": True, "mesaj": "XML linki ve laboratuvar kuyruğu temizlendi."})


@app.route("/api/xml-laboratuvar-kurallar", methods=["GET", "POST"])
@admin_required
def xml_laboratuvar_kurallar():
    try:
        if request.method == "GET":
            return jsonify({
                "success": True,
                "kurallar": _xml_kural_listesi(),
                "varsayilan": _xml_varsayilan_kural_listesi(),
            })
        veri = request.get_json(silent=True) or {}
        kurallar = _xml_kural_listesi()
        silinen = _xml_metin_sade(veri.get("sil"))
        if silinen:
            kurallar = [kural for kural in kurallar if kural["kelime"] != silinen]
            _xml_kural_yaz(kurallar)
            return jsonify({"success": True, "kurallar": kurallar, "mesaj": "Kural silindi."})
        kelime = _xml_metin_sade(veri.get("kelime"))
        dukkan = str(veri.get("dukkan") or "").strip().lower()
        if not kelime:
            return jsonify({"success": False, "mesaj": "Kelime gerekli."})
        if dukkan == "modelaraba" and not _xml_modelaraba_adayi_kabul("modelaraba", kelime, False):
            return jsonify({
                "success": False,
                "mesaj": "Klasik Arabalar yalnız net araba ifadesiyle eşleşir. model, modeller ve modelleri kullanılamaz.",
            })
        if dukkan in _XML_OTOMATIK_HARIC:
            return jsonify({
                "success": False,
                "mesaj": "Diğer - Her Şey otomatik dağıtımın dışındadır.",
            })
        if dukkan not in DUKKANLAR:
            return jsonify({"success": False, "mesaj": "Dükkan kodu geçersiz."})
        kurallar = [kural for kural in kurallar if kural["kelime"] != kelime]
        kurallar.append({"kelime": kelime, "dukkan": dukkan})
        _xml_kural_yaz(kurallar)
        return jsonify({
            "success": True,
            "kurallar": kurallar,
            "mesaj": f"{kelime}={dukkan} kuralı kaydedildi.",
        })
    except Exception as hata:
        return jsonify({"success": False, "mesaj": str(hata)}), 500


@app.route("/toplu-urun-yukle", methods=["POST"])
@admin_required
def toplu_urun_yukle():
    try:
        dosya = request.files.get("dosya")
        url = (request.form.get("xml_url") or "").strip()
        if dosya and getattr(dosya, "filename", ""):
            uzanti = os.path.splitext(dosya.filename)[1].lower()
            if uzanti not in {".xml", ".xlsx", ".xls", ".csv"}:
                return jsonify({"success": False, "mesaj": "Dosya .xml, .xlsx, .xls veya .csv olmalı."})
            ham = dosya.read(_XML_LAB_BAYT + 1)
            if not ham:
                return jsonify({"success": False, "mesaj": "Dosya boş."})
            if len(ham) > _XML_LAB_BAYT:
                return jsonify({"success": False, "mesaj": "Dosya 40 MB sınırını aşıyor."})
            urunler = _xml_kaynagi_coz(ham, uzanti)
            kaynak = dosya.filename
            _xml_besleme["link"] = ""
        elif url:
            ham = _xml_kaynagi_indir(url)
            urunler = _xml_kaynagi_coz(ham, ".xml")
            kaynak = url
            _xml_besleme["link"] = url
        else:
            return jsonify({"success": False, "mesaj": "XML linki veya dosya yok."})
        if not urunler:
            return jsonify({"success": False, "mesaj": "Taranan kaynakta ürün satırı bulunamadı."})
        _xml_besleme["kuyruk"] = urunler
        return jsonify(_xml_tarama_yaniti(urunler, kaynak))
    except Exception as hata:
        return jsonify({"success": False, "mesaj": f"Tarama tamamlanamadı: {hata}"})


@app.route("/api/xml-dagitimi-onayla", methods=["POST"])
@admin_required
def xml_dagitimi_onayla():
    kuyruk = list(_xml_besleme.get("kuyruk") or [])
    if not kuyruk:
        return jsonify({"success": False, "mesaj": "Onaylanacak tarama yok. Önce laboratuvarda analiz edin."})
    lot_sayaclari = {}
    eklenen = []
    guncellenen = []
    korunan = []
    atlanan = []

    def sonraki_lot(dukkan):
        if dukkan not in lot_sayaclari:
            en_yuksek = (
                db.session.query(func.max(Urun.lot_no))
                .filter(Urun.dukkan_kodu == dukkan, Urun.silindi_mi.is_(False))
                .scalar()
            )
            lot_sayaclari[dukkan] = int(en_yuksek or 0)
        lot_sayaclari[dukkan] += 1
        return lot_sayaclari[dukkan]

    try:
        dagitim_kari = _xml_kar_orani(_xml_besleme.get("link"))
        for sira, urun in enumerate(kuyruk, start=1):
            try:
                if not isinstance(urun, dict) or not urun.get("kayda_uygun"):
                    atlanan.append({
                        "sira": sira,
                        "ad": (urun or {}).get("ad") if isinstance(urun, dict) else "",
                        "neden": (urun or {}).get("neden") if isinstance(urun, dict) else "bozuk satır",
                    })
                    continue
                dukkan = _xml_dagitim_dukkani(urun.get("dukkan"))
                fiyat = _xml_fiyat_oku(urun.get("fiyat"))
                gorseller = _xml_gorsel_listesi(urun.get("gorseller"))
                ad = _xml_etiket_temizle(urun.get("ad"))[:200]
                if fiyat is None or not ad:
                    atlanan.append({"sira": sira, "ad": ad, "neden": "fiyat eksik"})
                    continue
                satis = _xml_satis_fiyati(fiyat, dagitim_kari)
                if satis is None:
                    atlanan.append({"sira": sira, "ad": ad, "neden": "fiyat eksik"})
                    continue
                adaylar = (
                    Urun.query.filter(
                        Urun.dukkan_kodu == dukkan,
                        Urun.urun_adi == ad,
                        Urun.durum == "Aktif",
                        Urun.silindi_mi.is_(False),
                    )
                    .order_by(Urun.id.asc())
                    .all()
                )
                kisisel = [
                    aday for aday in adaylar
                    if (getattr(aday, "kaynak_tipi", None) or "manuel") != "xml"
                ]
                if kisisel:
                    korunan.append({"sira": sira, "ad": ad, "dukkan": dukkan})
                    continue
                if adaylar:
                    hedef = adaylar[0]
                    eski_acilis = float(hedef.acilis_fiyati or 0)
                    eski_guncel = float(
                        hedef.guncel_fiyat if hedef.guncel_fiyat is not None else eski_acilis
                    )
                    kazanan = (hedef.kazanan_adi or "").strip()
                    acik_pey = kazanan not in ("", "Yok") or eski_guncel > eski_acilis
                    hedef.acilis_fiyati = satis
                    if not acik_pey:
                        hedef.guncel_fiyat = satis
                    hedef.fotograflar = gorseller
                    hedef.tanitim_yazisi = _xml_etiket_temizle(urun.get("aciklama"))[:4000]
                    hedef.kaynak_tipi = "xml"
                    hedef.hemen_al_fiyati = _xml_hemen_al_fiyati(satis)
                    if _xml_besleme.get("link"):
                        hedef.xml_kaynak_url = str(_xml_besleme.get("link"))[:500]
                    guncellenen.append({
                        "dukkan": dukkan,
                        "lot_no": hedef.lot_no,
                        "ad": ad,
                    })
                    continue
                lot_no = sonraki_lot(dukkan)
                yeni_urun = Urun(
                    dukkan_kodu=dukkan,
                    lot_no=lot_no,
                    urun_adi=ad,
                    kategori=kategori_ad_temizle(urun.get("kategori") or "Genel"),
                    acilis_fiyati=satis,
                    guncel_fiyat=satis,
                    hemen_al_fiyati=_xml_hemen_al_fiyati(satis),
                    tanitim_yazisi=_xml_etiket_temizle(urun.get("aciklama"))[:4000],
                    fotograflar=gorseller,
                    video="",
                    ses_dosyasi="",
                    durum="Aktif",
                    silindi_mi=False,
                    kaynak_tipi="xml",
                    xml_kaynak_url=(str(_xml_besleme.get("link") or "")[:500] or None),
                )
                if hasattr(yeni_urun, "eklenme_tarihi") and getattr(yeni_urun, "eklenme_tarihi", None) is None:
                    yeni_urun.eklenme_tarihi = datetime.now()
                db.session.add(yeni_urun)
                eklenen.append({"dukkan": dukkan, "lot_no": lot_no, "ad": ad})
            except Exception as hata:
                atlanan.append({
                    "sira": sira,
                    "ad": str((urun or {}).get("ad") if isinstance(urun, dict) else "")[:200],
                    "neden": str(hata),
                })
        if not eklenen and not guncellenen and not korunan:
            db.session.rollback()
            return jsonify({
                "success": False,
                "mesaj": "Kayda uygun ürün yok. Eksik fiyatlı veya görselsiz satırlar atlandı.",
                "eklendi": 0,
                "guncellendi": 0,
                "korundu": 0,
                "kaydedilen": 0,
                "atlanan": len(atlanan),
                "hatalar": atlanan[:20],
            })
        db.session.commit()
        _xml_besleme["kuyruk"] = []
        ozet = {}
        for kayit in eklenen:
            ozet[kayit["dukkan"]] = ozet.get(kayit["dukkan"], 0) + 1
        mesaj = (
            f"Yeni eklenen: {len(eklenen)}. "
            f"Güncellenen XML ürünü: {len(guncellenen)}. "
            f"Korunan kişisel ürün: {len(korunan)}. "
            f"Atlanan: {len(atlanan)}."
        )
        return jsonify({
            "success": True,
            "mesaj": mesaj,
            "eklendi": len(eklenen),
            "guncellendi": len(guncellenen),
            "korundu": len(korunan),
            "kaydedilen": len(eklenen),
            "atlanan": len(atlanan),
            "dagilim": ozet,
            "hatalar": atlanan[:20],
        })
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": f"Aktarım tamamlanamadı: {hata}"}), 500


_SATILDI_DURUMLARI = ("Satıldı", "satıldı", "SATILDI", "satildi", "Satildi")


def _vitrindeki_satilmamis(sorgu):
    return sorgu.filter(
        or_(Urun.silindi_mi.is_(False), Urun.silindi_mi.is_(None)),
        or_(
            Urun.durum.is_(None),
            Urun.durum == "",
            ~Urun.durum.in_(_SATILDI_DURUMLARI),
        ),
    )


def _xml_kaynakli_urun_kosulu():
    kosullar = [
        func.lower(func.coalesce(Urun.kaynak_tipi, "")) == "xml",
        func.length(func.coalesce(Urun.xml_kaynak_url, "")) > 0,
    ]
    kod_kolonu = getattr(Urun, "xml_kodu", None)
    if kod_kolonu is not None:
        kosullar.append(func.length(func.coalesce(kod_kolonu, "")) > 0)
    kosullar.append(
        (Urun.tedarikci_id.isnot(None))
        & (func.lower(func.coalesce(Urun.kaynak_tipi, "manuel")) != "manuel")
    )
    return or_(*kosullar)


@app.route("/api/xml-urunleri-temizle", methods=["POST"])
@admin_required
def xml_urunleri_temizle():
    try:
        simdi = suan_utc()
        temizlenen = _vitrindeki_satilmamis(
            Urun.query.filter(_xml_kaynakli_urun_kosulu())
        ).update(
            {
                Urun.silindi_mi: True,
                Urun.silinme_tarihi: simdi,
            },
            synchronize_session=False,
        )
        db.session.commit()
        adet = int(temizlenen or 0)
        return jsonify({
            "success": True,
            "temizlenen": adet,
            "mesaj": (
                f"{adet} eski XML ürünü çöp kutusuna alındı. "
                "Kendi manuel ürünleriniz ve satılmış kayıtlar duruyor."
            ),
        })
    except Exception as hata:
        db.session.rollback()
        return jsonify({
            "success": False,
            "temizlenen": 0,
            "mesaj": "Temizlik işlemi tamamlanamadı.",
            "error": str(hata),
        }), 500


@app.route("/api/dukkan-katalog-temizle", methods=["POST"])
@admin_required
def dukkan_katalog_temizle():
    try:
        veri = request.get_json(silent=True) or {}
        ham = veri.get("dukkan_id", None)
        if ham is None:
            ham = veri.get("dukkan", None)
        if ham is None:
            ham = request.form.get("dukkan_id") or request.form.get("dukkan")
        kod = str(ham or "").strip().lower()
        if kod not in DUKKANLAR:
            return jsonify({"success": False, "mesaj": "Seçili dükkan bulunamadı."}), 400
        kayitlar = _vitrindeki_satilmamis(
            Urun.query.filter(Urun.dukkan_kodu == kod)
        ).all()
        simdi = suan_utc()
        for urun in kayitlar:
            urun.silindi_mi = True
            urun.silinme_tarihi = simdi
        db.session.commit()
        dukkan_ad = (DUKKANLAR.get(kod) or {}).get("ad") or kod
        return jsonify({
            "success": True,
            "temizlenen": len(kayitlar),
            "dukkan": kod,
            "mesaj": (
                f"{dukkan_ad} dükkanında {len(kayitlar)} satılmamış aktif ürün çöp kutusuna alındı. "
                "Satılmış kayıtlar ve diğer dükkanlar duruyor."
            ),
        })
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": f"Dükkan kataloğu temizlenemedi: {hata}"}), 500


# ==========================================
# RAM ÖNBELLEKLİ OTOMATİK XML STOK SENKRONU
# ==========================================
XML_STOK_CACHE = {}
XML_STOK_CACHE_META = {"url": "", "zaman": "", "hazir": False}
_XML_STOK_CACHE_KILIT = BoundedSemaphore(1)
_XML_STOK_IS_KILIT = BoundedSemaphore(1)
_XML_STOK_ARALIK_SANIYE = 15 * 60
_XML_STOK_AYAR_URL = "xml_stok_senkron_url"
_XML_STOK_AYAR_ZAMAN = "xml_stok_son_guncelleme"
_XML_STOK_AYAR_LISTE = "xml_toptanci_listesi"
STOK_TUKENDI_MESAJI = (
    "Bu ürünün stoğu tükenmiştir. Satışı nazikçe durdurduk; "
    "stoğu yenilendiğinde vitrine dönecek."
)
_XML_STOK_ZAMANLAYICI_BASLADI = False


def _ayar_degeri(anahtar):
    satir = Ayar.query.filter_by(anahtar=anahtar).first()
    return satir.deger if satir is not None else ""


def _ayar_yaz(anahtar, deger):
    satir = Ayar.query.filter_by(anahtar=anahtar).first()
    metin = "" if deger is None else str(deger)
    if satir is None:
        db.session.add(Ayar(anahtar=anahtar, deger=metin))
    else:
        satir.deger = metin


def _stok_zaman_metni():
    an = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))
    return an.strftime("%d.%m.%Y %H:%M")


def _xml_hemen_al_fiyati(acilis):
    try:
        fiyat = float(acilis or 0)
    except (TypeError, ValueError):
        return 0.0
    if fiyat <= 0:
        return 0.0
    return float(int(round(fiyat * 1.5)))


def _xml_kar_sayisi(ham):
    try:
        oran = float(ham or 0)
    except (TypeError, ValueError):
        return 0.0
    if oran < 0:
        return 0.0
    if oran > 1000:
        return 1000.0
    return oran


def _xml_kar_orani(url):
    hedef = str(url or "").strip()[:500]
    if not hedef:
        return 0.0
    for kayit in _xml_toptanci_listesi_oku():
        if kayit.get("url") == hedef:
            return _xml_kar_sayisi(kayit.get("kar_orani"))
    return 0.0


def _xml_satis_fiyati(baz, kar_orani):
    """Her çalıştırmada tedarikçi baz fiyatı + sabit kâr. Önceki satış fiyatına binmez."""
    try:
        fiyat = float(baz or 0)
        oran = _xml_kar_sayisi(kar_orani)
    except (TypeError, ValueError):
        return None
    if fiyat <= 0:
        return None
    return float(int(round(fiyat * (1 + oran / 100.0))))


def xml_hemen_al_bosluklarini_doldur():
    kayitlar = Urun.query.filter(
        Urun.kaynak_tipi == "xml",
        or_(Urun.hemen_al_fiyati.is_(None), Urun.hemen_al_fiyati <= 0),
        Urun.acilis_fiyati > 0,
        or_(Urun.silindi_mi.is_(False), Urun.silindi_mi.is_(None)),
    ).all()
    doldu = False
    for urun in kayitlar:
        if (urun.durum or "") in ("Satıldı", "satildi", "Pasif"):
            continue
        urun.hemen_al_fiyati = _xml_hemen_al_fiyati(urun.acilis_fiyati)
        doldu = True
    if doldu:
        db.session.commit()


def _stok_onbellek_anahtari(ad, url=""):
    ad_k = _xml_metin_sade(ad)
    url_k = str(url or "").strip()
    if not ad_k:
        return ""
    return f"{url_k}\n{ad_k}" if url_k else ad_k


def _onbellek_stok_oku(ad, url=""):
    anahtar = _stok_onbellek_anahtari(ad, url)
    url_k = str(url or "").strip()
    onek = f"{url_k}\n" if url_k else ""
    _XML_STOK_CACHE_KILIT.acquire()
    try:
        if anahtar and anahtar in XML_STOK_CACHE:
            return XML_STOK_CACHE[anahtar].get("stok")
        if onek and any(str(kayit).startswith(onek) for kayit in XML_STOK_CACHE):
            return 0
        return None
    finally:
        _XML_STOK_CACHE_KILIT.release()


def _onbellek_stok_yaz(ad, adet, url=""):
    if not XML_STOK_CACHE_META.get("hazir"):
        return
    anahtar = _stok_onbellek_anahtari(ad, url)
    if not anahtar:
        return
    _XML_STOK_CACHE_KILIT.acquire()
    try:
        kayit = XML_STOK_CACHE.get(anahtar)
        if isinstance(kayit, dict):
            kayit["stok"] = adet
    finally:
        _XML_STOK_CACHE_KILIT.release()


def _onbellege_yukle(url, zaman, harita):
    global XML_STOK_CACHE
    url_k = str(url or "").strip()
    _XML_STOK_CACHE_KILIT.acquire()
    try:
        kalan = {
            anahtar: kayit
            for anahtar, kayit in XML_STOK_CACHE.items()
            if not str(anahtar).startswith(url_k + "\n")
        }
        for paket in harita.values():
            anahtar = _stok_onbellek_anahtari(paket.get("ad") or paket.get("anahtar"), url_k)
            if anahtar:
                kalan[anahtar] = paket
        XML_STOK_CACHE = kalan
        XML_STOK_CACHE_META["url"] = url_k
        XML_STOK_CACHE_META["zaman"] = zaman
        XML_STOK_CACHE_META["hazir"] = True
    finally:
        _XML_STOK_CACHE_KILIT.release()


def stok_satisa_uygun_mu(urun, haric_musteri_id=None):
    """Sepet ve hemen al: dış XML yok, veritabanı ve RAM önbelleği."""
    if urun is None or urun.silindi_mi:
        return False, STOK_TUKENDI_MESAJI
    if (urun.durum or "") in ("Satıldı", "satildi", "Pasif"):
        return False, STOK_TUKENDI_MESAJI
    try:
        adet = int(urun.stok_adedi if urun.stok_adedi is not None else 1)
    except (TypeError, ValueError):
        adet = 1
    if (getattr(urun, "kaynak_tipi", None) or "manuel") == "xml" and XML_STOK_CACHE_META.get("hazir"):
        onbellek = _onbellek_stok_oku(urun.urun_adi, getattr(urun, "xml_kaynak_url", "") or "")
        if onbellek is not None:
            adet = min(adet, int(onbellek))
    if adet <= 0:
        return False, STOK_TUKENDI_MESAJI
    return True, ""


def stok_satis_sonrasi_dus(urun):
    """Başarılı satışta stok 1 düşer. 0 olunca vitrin filtresi ürünü düşürür."""
    if urun is None:
        return
    try:
        adet = int(urun.stok_adedi if urun.stok_adedi is not None else 1)
    except (TypeError, ValueError):
        adet = 1
    adet = 0 if adet <= 0 else adet - 1
    urun.stok_adedi = adet
    if (getattr(urun, "kaynak_tipi", None) or "manuel") == "xml":
        _onbellek_stok_yaz(urun.urun_adi, adet, getattr(urun, "xml_kaynak_url", "") or "")
    if adet <= 0:
        return
    urun.durum = "Aktif"
    urun.silindi_mi = False
    urun.silinme_tarihi = None
    urun.kazanan_adi = "Yok"
    urun.kazanan_id = None
    try:
        urun.guncel_fiyat = float(urun.acilis_fiyati or 0)
    except (TypeError, ValueError):
        urun.guncel_fiyat = 0.0


def _xml_eleman_stok_adedi(eleman):
    try:
        bulunan = _xml_alan_metinleri(eleman, set(_XML_STOK_ONCELIK))
    except Exception:
        return None
    for anahtar in _XML_STOK_ONCELIK:
        if anahtar not in bulunan:
            continue
        adet = _xml_stok_sayisi(bulunan[anahtar])
        if adet is None:
            continue
        try:
            return max(0, int(float(adet)))
        except (TypeError, ValueError):
            continue
    return None


def _xml_stok_kayitlarini_oku(ham):
    import xml.etree.ElementTree as ET

    if isinstance(ham, (bytes, bytearray)):
        metin = ham.decode("utf-8-sig", errors="replace")
    else:
        metin = str(ham or "")
    kurallar = _xml_kural_listesi()
    elemanlar = []
    try:
        kok = ET.fromstring(metin)
    except Exception:
        kok = None
    if kok is not None:
        for eleman in kok.iter():
            if _xml_yerel_ad(eleman.tag) not in _XML_URUN_ETIKETLERI:
                continue
            if any(_xml_yerel_ad(cocuk.tag) in _XML_URUN_ETIKETLERI for cocuk in list(eleman)):
                continue
            elemanlar.append(eleman)
    if not elemanlar:
        parcalar = re.findall(
            r"<(?:item|urun|product|entry|offer)\b[\s\S]*?</(?:item|urun|product|entry|offer)>",
            metin or "",
            flags=re.IGNORECASE,
        )
        for blok in parcalar:
            try:
                elemanlar.append(ET.fromstring(blok))
            except Exception:
                continue
    kayitlar = []
    for eleman in elemanlar:
        paket = _xml_bloktan_urun(eleman, kurallar)
        if not paket:
            continue
        paket = dict(paket)
        paket["stok"] = _xml_eleman_stok_adedi(eleman)
        paket["anahtar"] = _xml_metin_sade(paket.get("ad"))
        if paket["anahtar"]:
            kayitlar.append(paket)
    return kayitlar


def _acik_teklif_var(urun, on_teklif_idler):
    if urun.id in on_teklif_idler:
        return True
    if (urun.durum or "") in ("Satıldı", "satildi", "Pasif"):
        return False
    kazanan = (urun.kazanan_adi or "").strip().casefold()
    if kazanan and kazanan not in {"", "yok", "kimse", "none", "-"}:
        return True
    try:
        acilis = float(urun.acilis_fiyati or 0)
        guncel = float(urun.guncel_fiyat if urun.guncel_fiyat is not None else acilis)
    except (TypeError, ValueError):
        return False
    return guncel > acilis + 0.009


def _stok_senkron_dokunulmaz(urun, on_teklif_idler):
    if _acik_teklif_var(urun, on_teklif_idler):
        return True
    oda = odalar.get(urun.dukkan_kodu or "")
    return bool(
        oda
        and oda.get("aktif_urun_id") == urun.id
        and oda.get("sayac_aktif")
    )


def _xml_cop_kutusuna(urun, simdi):
    urun.stok_adedi = 0
    urun.silindi_mi = True
    urun.silinme_tarihi = simdi
    if (urun.durum or "") not in ("Satıldı", "satildi"):
        urun.durum = "Pasif"
    dukkan = urun.dukkan_kodu or ""
    oda = odalar.get(dukkan)
    if (
        oda
        and oda.get("aktif_urun_id") == urun.id
        and not oda.get("sayac_aktif")
    ):
        _sahneyi_bosa_al(dukkan, "stok", sesi_kes=False)


def _xml_stok_veritabanina_uygula(url, harita):
    simdi = suan_utc()
    hedef_url = (url or "").strip()[:500]
    kar_orani = _xml_kar_orani(hedef_url)
    xml_urunler = Urun.query.filter(
        Urun.kaynak_tipi == "xml",
        Urun.xml_kaynak_url == hedef_url,
    ).all()
    indeks = {}
    for urun in xml_urunler:
        indeks.setdefault(_xml_metin_sade(urun.urun_adi), []).append(urun)
    on_teklif_idler = {
        satir[0]
        for satir in db.session.query(OnTeklif.urun_id).distinct()
        if satir[0]
    }
    manuel_adlar = set()
    for ad_metin, dukkan_kodu in db.session.query(Urun.urun_adi, Urun.dukkan_kodu).filter(
        or_(Urun.kaynak_tipi.is_(None), Urun.kaynak_tipi != "xml"),
        or_(Urun.silindi_mi.is_(False), Urun.silindi_mi.is_(None)),
    ):
        manuel_adlar.add((_xml_metin_sade(ad_metin), dukkan_kodu or "gumus"))
    lot_sayaclari = {}
    islem = 0

    def sonraki_lot(dukkan):
        if dukkan not in lot_sayaclari:
            en_yuksek = (
                db.session.query(func.max(Urun.lot_no))
                .filter(Urun.dukkan_kodu == dukkan, Urun.silindi_mi.is_(False))
                .scalar()
            )
            lot_sayaclari[dukkan] = int(en_yuksek or 0)
        lot_sayaclari[dukkan] += 1
        return lot_sayaclari[dukkan]

    def kaydet():
        nonlocal islem
        islem += 1
        if islem % 25 == 0:
            db.session.commit()
            socketio.sleep(0)

    for paket in harita.values():
        try:
            ad = _xml_etiket_temizle(paket.get("ad"))[:200]
            anahtar = paket.get("anahtar")
            if not ad or not anahtar:
                continue
            dukkan = _xml_dagitim_dukkani(paket.get("dukkan"))
            stok = paket.get("stok")
            fiyat = _xml_fiyat_oku(paket.get("fiyat"))
            adaylar = indeks.get(anahtar) or []
            if not adaylar:
                if stok == 0 or not paket.get("kayda_uygun") or fiyat is None:
                    continue
                if (anahtar, dukkan) in manuel_adlar:
                    continue
                satis = _xml_satis_fiyati(fiyat, kar_orani)
                if satis is None:
                    continue
                yeni_stok = stok if isinstance(stok, int) and stok > 0 else 1
                yeni_urun = Urun(
                    dukkan_kodu=dukkan,
                    lot_no=sonraki_lot(dukkan),
                    urun_adi=ad,
                    kategori=kategori_ad_temizle(paket.get("kategori") or "Genel"),
                    acilis_fiyati=satis,
                    guncel_fiyat=satis,
                    hemen_al_fiyati=_xml_hemen_al_fiyati(satis),
                    tanitim_yazisi=_xml_etiket_temizle(paket.get("aciklama"))[:4000],
                    fotograflar=_xml_gorsel_listesi(paket.get("gorseller")),
                    video="",
                    ses_dosyasi="",
                    durum="Aktif",
                    silindi_mi=False,
                    kaynak_tipi="xml",
                    stok_adedi=yeni_stok,
                    xml_kaynak_url=hedef_url,
                )
                db.session.add(yeni_urun)
                indeks.setdefault(anahtar, []).append(yeni_urun)
                kaydet()
                continue
            for aday in adaylar:
                if (aday.dukkan_kodu or "") in _XML_OTOMATIK_HARIC:
                    continue
                if (getattr(aday, "kaynak_tipi", None) or "manuel") != "xml":
                    continue
                if _stok_senkron_dokunulmaz(aday, on_teklif_idler):
                    continue
                if stok == 0:
                    if (aday.durum or "") in ("Satıldı", "satildi"):
                        continue
                    _xml_cop_kutusuna(aday, simdi)
                    kaydet()
                    continue
                yeni_stok = stok if isinstance(stok, int) and stok > 0 else max(int(aday.stok_adedi or 1), 1)
                eski_durum = aday.durum or ""
                if eski_durum in ("Satıldı", "satildi", "Pasif") or aday.silindi_mi:
                    aday.durum = "Aktif"
                    aday.kazanan_adi = "Yok"
                    aday.kazanan_id = None
                    aday.guncel_fiyat = float(aday.acilis_fiyati or 0)
                aday.silindi_mi = False
                aday.silinme_tarihi = None
                aday.stok_adedi = yeni_stok
                aday.xml_kaynak_url = hedef_url
                if fiyat is not None:
                    satis = _xml_satis_fiyati(fiyat, kar_orani)
                    if satis is None:
                        continue
                    aday.acilis_fiyati = satis
                    aday.guncel_fiyat = satis
                    aday.hemen_al_fiyati = _xml_hemen_al_fiyati(satis)
                elif not float(aday.hemen_al_fiyati or 0):
                    aday.hemen_al_fiyati = _xml_hemen_al_fiyati(aday.acilis_fiyati)
                kaydet()
        except Exception as hata:
            db.session.rollback()
            print(f"XML stok satırı atlandı: {hata}")

    for urun in xml_urunler:
        try:
            if (urun.dukkan_kodu or "") in _XML_OTOMATIK_HARIC:
                continue
            if (getattr(urun, "kaynak_tipi", None) or "manuel") != "xml":
                continue
            if urun.silindi_mi:
                continue
            if (urun.durum or "") in ("Satıldı", "satildi"):
                continue
            if _xml_metin_sade(urun.urun_adi) in harita:
                continue
            if _stok_senkron_dokunulmaz(urun, on_teklif_idler):
                continue
            _xml_cop_kutusuna(urun, simdi)
            kaydet()
        except Exception as hata:
            db.session.rollback()
            print(f"XML stok düşümü atlandı: {hata}")
    db.session.commit()


def _xml_toptanci_listesi_oku():
    ham = _ayar_degeri(_XML_STOK_AYAR_LISTE)
    if not ham:
        return []
    try:
        veri = json.loads(ham)
    except (TypeError, ValueError):
        return []
    if not isinstance(veri, list):
        return []
    temiz = []
    gorulen = set()
    for kayit in veri:
        if not isinstance(kayit, dict):
            continue
        url = str(kayit.get("url") or "").strip()[:500]
        if not url.lower().startswith(("http://", "https://")) or url in gorulen:
            continue
        gorulen.add(url)
        ad = str(kayit.get("ad") or "Toptancı").strip()[:80] or "Toptancı"
        temiz.append({
            "id": str(kayit.get("id") or uuid.uuid4().hex)[:40],
            "ad": ad,
            "url": url,
            "aktif": bool(kayit.get("aktif", True)),
            "kar_orani": _xml_kar_sayisi(kayit.get("kar_orani")),
            "son_senkron": str(kayit.get("son_senkron") or "").strip()[:40],
        })
    return temiz


def _xml_toptanci_listesi_yaz(liste):
    _ayar_yaz(_XML_STOK_AYAR_LISTE, json.dumps(liste, ensure_ascii=False))


def _xml_kaynaksiz_urunleri_bagla(url):
    hedef = str(url or "").strip()[:500]
    if not hedef.lower().startswith(("http://", "https://")):
        return
    Urun.query.filter(
        Urun.kaynak_tipi == "xml",
        or_(Urun.xml_kaynak_url.is_(None), Urun.xml_kaynak_url == ""),
    ).update({Urun.xml_kaynak_url: hedef}, synchronize_session=False)


def _xml_toptanci_listesini_hazirla():
    liste = _xml_toptanci_listesi_oku()
    if liste:
        return liste
    eski = (_ayar_degeri(_XML_STOK_AYAR_URL) or "").strip()[:500]
    if not eski.lower().startswith(("http://", "https://")):
        return []
    liste = [{
        "id": "keskin-hediyelik",
        "ad": "Keskin Hediyelik",
        "url": eski,
        "aktif": True,
        "kar_orani": 0.0,
        "son_senkron": (_ayar_degeri(_XML_STOK_AYAR_ZAMAN) or "").strip()[:40],
    }]
    _xml_toptanci_listesi_yaz(liste)
    _xml_kaynaksiz_urunleri_bagla(eski)
    db.session.commit()
    return liste


def _xml_toptanci_ekle(ad, url, kar_orani=None):
    url = str(url or "").strip()[:500]
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("XML linki http veya https olmalı.")
    liste = _xml_toptanci_listesini_hazirla()
    ad = str(ad or "").strip()[:80]
    for kayit in liste:
        if kayit["url"] == url:
            if ad:
                kayit["ad"] = ad
            kayit["aktif"] = True
            if kar_orani is not None:
                kayit["kar_orani"] = _xml_kar_sayisi(kar_orani)
            _xml_toptanci_listesi_yaz(liste)
            db.session.commit()
            return liste
    if not ad:
        ad = "Keskin Hediyelik" if not liste else "Toptancı"
    liste.append({
        "id": uuid.uuid4().hex,
        "ad": ad,
        "url": url,
        "aktif": True,
        "kar_orani": _xml_kar_sayisi(kar_orani),
        "son_senkron": "",
    })
    _xml_toptanci_listesi_yaz(liste)
    if len(liste) == 1:
        _ayar_yaz(_XML_STOK_AYAR_URL, url)
        _xml_kaynaksiz_urunleri_bagla(url)
    db.session.commit()
    return liste


def xml_stok_senkron_calistir(yalniz_id=None):
    if not _XML_STOK_IS_KILIT.acquire(blocking=False):
        return
    try:
        with app.app_context():
            try:
                liste = _xml_toptanci_listesini_hazirla()
                if yalniz_id:
                    secilen = [kaynak for kaynak in liste if kaynak.get("id") == yalniz_id]
                else:
                    secilen = [kaynak for kaynak in liste if kaynak.get("aktif")]
                if not secilen:
                    return
                for kaynak in secilen:
                    try:
                        url = kaynak["url"]
                        ham = _xml_kaynagi_indir(url)
                        kayitlar = _xml_stok_kayitlarini_oku(ham)
                        if not kayitlar:
                            print("XML stok senkronu: ürün satırı yok, bu kaynak duruyor.")
                            continue
                        harita = {}
                        for paket in kayitlar:
                            if paket.get("anahtar"):
                                harita[paket["anahtar"]] = paket
                        if not harita:
                            continue
                        zaman = _stok_zaman_metni()
                        _onbellege_yukle(url, zaman, harita)
                        _xml_stok_veritabanina_uygula(url, harita)
                        kaynak["son_senkron"] = zaman
                        print(f"XML stok önbelleği güncellendi: {len(harita)} ürün.")
                    except Exception as hata:
                        db.session.rollback()
                        print(f"XML stok senkronu atlandı: {hata}")
                _xml_toptanci_listesi_yaz(liste)
                if liste:
                    _ayar_yaz(_XML_STOK_AYAR_URL, liste[0]["url"])
                    if liste[0].get("son_senkron"):
                        _ayar_yaz(_XML_STOK_AYAR_ZAMAN, liste[0]["son_senkron"])
                db.session.commit()
            except Exception as hata:
                db.session.rollback()
                print(f"XML stok senkronu atlandı: {hata}")
            finally:
                db.session.remove()
    finally:
        _XML_STOK_IS_KILIT.release()


_LIDER_AL = """
local v = redis.call('GET', KEYS[1])
if not v then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
  return 1
end
if v == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

_xml_lider_token = None


def _xml_lider_tokenu():
    global _xml_lider_token
    if not _xml_lider_token:
        _xml_lider_token = uuid.uuid4().hex
    return _xml_lider_token


def _redis_lider_mi(anahtar, token, saniye=180):
    """Anahtar boşsa veya bu token'daysa lider odur. Diğer worker çalışmaz."""
    if not REDIS_URL:
        return True
    istemci = _redis_al()
    if istemci is None:
        return False
    try:
        return bool(int(istemci.eval(
            _LIDER_AL, 1, anahtar, token, str(int(saniye)),
        ) or 0))
    except Exception as hata:
        _redis_dusur(hata)
        return False


def _xml_stok_zaman_dongusu():
    socketio.sleep(30)
    while True:
        lider = False
        try:
            lider = _redis_lider_mi("mezat:lider:xml", _xml_lider_tokenu(), 180)
            if lider:
                xml_stok_senkron_calistir()
        except Exception as hata:
            print(f"XML stok senkronu atlandı: {hata}")
        socketio.sleep(_XML_STOK_ARALIK_SANIYE if lider else 20)


def xml_stok_zamanlayiciyi_baslat():
    """Her worker bir gözcü açar. Senkronu yalnız mezat:lider:xml kilidini tutan çalıştırır."""
    global _XML_STOK_ZAMANLAYICI_BASLADI
    if _XML_STOK_ZAMANLAYICI_BASLADI:
        return
    _XML_STOK_ZAMANLAYICI_BASLADI = True
    socketio.start_background_task(_xml_stok_zaman_dongusu)


def _xml_toptanci_yaniti(liste, mesaj=""):
    ilk = liste[0] if liste else {}
    cevap = {
        "success": True,
        "toptancilar": liste,
        "url": ilk.get("url") or "",
        "aktif": bool(ilk.get("aktif")),
        "son_guncelleme": ilk.get("son_senkron") or "",
    }
    if mesaj:
        cevap["mesaj"] = mesaj
    return jsonify(cevap)


def _xml_toptanci_senkronu_baslat(kaynak_id=None):
    if kaynak_id:
        socketio.start_background_task(xml_stok_senkron_calistir, kaynak_id)
    else:
        socketio.start_background_task(xml_stok_senkron_calistir)


@app.route("/api/xml-toptancilar", methods=["GET", "POST"])
@admin_required
def xml_toptancilar_api():
    try:
        if request.method == "GET":
            return _xml_toptanci_yaniti(_xml_toptanci_listesini_hazirla())
        veri = request.get_json(silent=True) or {}
        liste = _xml_toptanci_listesini_hazirla()
        kaynak_id = str(veri.get("id") or "").strip()
        if veri.get("sil") and kaynak_id:
            liste = [kaynak for kaynak in liste if kaynak.get("id") != kaynak_id]
            _xml_toptanci_listesi_yaz(liste)
            db.session.commit()
            return _xml_toptanci_yaniti(liste, "Toptancı XML kaydı silindi.")
        if kaynak_id and "kar_orani" in veri and not veri.get("url") and not veri.get("senkron") and not veri.get("sil"):
            oran = _xml_kar_sayisi(veri.get("kar_orani"))
            for kaynak in liste:
                if kaynak.get("id") == kaynak_id:
                    kaynak["kar_orani"] = oran
            _xml_toptanci_listesi_yaz(liste)
            db.session.commit()
            return _xml_toptanci_yaniti(
                liste,
                f"Kâr oranı %{oran:g} kaydedildi. Fiyat, sonraki senkronda tedarikçi baz fiyatına bu oran eklenerek yenilenir.",
            )
        if kaynak_id and "aktif" in veri and not veri.get("url") and not veri.get("senkron"):
            for kaynak in liste:
                if kaynak.get("id") == kaynak_id:
                    kaynak["aktif"] = bool(veri.get("aktif"))
            _xml_toptanci_listesi_yaz(liste)
            db.session.commit()
            return _xml_toptanci_yaniti(liste, "Toptancı durumu güncellendi.")
        if veri.get("senkron") and kaynak_id:
            _xml_toptanci_senkronu_baslat(kaynak_id)
            return _xml_toptanci_yaniti(liste, "Bu toptancı için senkron başlatıldı.")
        url = str(veri.get("url") or "").strip()
        if not url:
            return jsonify({"success": False, "mesaj": "XML linki gerekli."})
        liste = _xml_toptanci_ekle(veri.get("ad"), url, veri.get("kar_orani"))
        _xml_toptanci_senkronu_baslat()
        return _xml_toptanci_yaniti(liste, "Toptancı XML listesine kaydedildi. Arka planda senkron başlayacak.")
    except ValueError as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(hata)})
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": f"Toptancı listesi kaydedilemedi: {hata}"}), 500


@app.route("/api/xml-stok-senkron", methods=["GET", "POST"])
@admin_required
def xml_stok_senkron_api():
    try:
        if request.method == "GET":
            return _xml_toptanci_yaniti(_xml_toptanci_listesini_hazirla())
        veri = request.get_json(silent=True) or {}
        liste = _xml_toptanci_ekle(veri.get("ad"), veri.get("url"), veri.get("kar_orani"))
        _xml_toptanci_senkronu_baslat()
        return _xml_toptanci_yaniti(
            liste,
            "Otomatik stok senkronizasyonu kaydedildi. Arka planda çalışacak.",
        )
    except ValueError as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(hata)})
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": f"Stok senkronu kaydedilemedi: {hata}"}), 500


_MANUEL_FIYAT_YEDEK = "manuel_fiyat_yedek"


def _manuel_fiyata_oran(deger, carpan):
    try:
        sayi = float(deger or 0)
    except (TypeError, ValueError):
        return 0.0
    if sayi <= 0:
        return 0.0
    return float(int(round(sayi * carpan)))


@app.route("/api/manuel-kar", methods=["POST"])
@admin_required
def manuel_kar_api():
    veri = request.get_json(silent=True) or {}
    try:
        if veri.get("geri_al"):
            ham = _ayar_degeri(_MANUEL_FIYAT_YEDEK)
            try:
                yedek = json.loads(ham) if ham else {}
            except (TypeError, ValueError):
                yedek = {}
            satirlar = yedek.get("urunler") if isinstance(yedek, dict) else None
            if not satirlar:
                return jsonify({"success": False, "mesaj": "Geri alınacak fiyat kaydı yok."})
            donen = 0
            for kayit in satirlar:
                if not isinstance(kayit, dict):
                    continue
                try:
                    urun = db.session.get(Urun, int(kayit.get("id")))
                except (TypeError, ValueError):
                    urun = None
                if urun is None or (getattr(urun, "kaynak_tipi", None) or "manuel") != "manuel":
                    continue
                urun.acilis_fiyati = float(kayit.get("acilis") or 0)
                urun.guncel_fiyat = float(kayit.get("guncel") or 0)
                urun.hemen_al_fiyati = float(kayit.get("hemen_al") or 0)
                donen += 1
            _ayar_yaz(_MANUEL_FIYAT_YEDEK, "")
            db.session.commit()
            return jsonify({
                "success": True,
                "mesaj": f"Son fiyat artışı geri alındı. {donen} manuel ürün eski fiyatına döndü.",
            })

        oran = _xml_kar_sayisi(veri.get("oran"))
        if oran <= 0:
            return jsonify({"success": False, "mesaj": "Uygulanacak kâr oranı 0'dan büyük olmalı."})
        carpan = 1 + oran / 100.0
        on_teklif_idler = {
            satir[0]
            for satir in db.session.query(OnTeklif.urun_id).distinct()
            if satir[0]
        }
        urunler = Urun.query.filter(
            Urun.kaynak_tipi == "manuel",
            or_(Urun.silindi_mi.is_(False), Urun.silindi_mi.is_(None)),
        ).all()
        yedek = []
        uygulanan = 0
        atlanan = 0
        for urun in urunler:
            if (urun.durum or "") in ("Satıldı", "satildi", "Pasif"):
                continue
            if _stok_senkron_dokunulmaz(urun, on_teklif_idler):
                atlanan += 1
                continue
            acilis = float(urun.acilis_fiyati or 0)
            if acilis <= 0:
                continue
            guncel = float(urun.guncel_fiyat if urun.guncel_fiyat is not None else acilis)
            hemen = float(urun.hemen_al_fiyati or 0)
            yedek.append({
                "id": urun.id,
                "acilis": acilis,
                "guncel": guncel,
                "hemen_al": hemen,
            })
            urun.acilis_fiyati = _manuel_fiyata_oran(acilis, carpan)
            urun.guncel_fiyat = _manuel_fiyata_oran(guncel, carpan)
            urun.hemen_al_fiyati = (
                _manuel_fiyata_oran(hemen, carpan)
                if hemen > 0
                else _xml_hemen_al_fiyati(urun.acilis_fiyati)
            )
            uygulanan += 1
        if not yedek:
            return jsonify({
                "success": False,
                "mesaj": "Fiyat uygulanacak manuel ürün yok. XML ürünlerine dokunulmadı.",
            })
        _ayar_yaz(_MANUEL_FIYAT_YEDEK, json.dumps({
            "oran": oran,
            "zaman": _stok_zaman_metni(),
            "urunler": yedek,
        }, ensure_ascii=False))
        db.session.commit()
        mesaj = f"Kendi ürünlerinize %{oran:g} uygulandı. {uygulanan} manuel ürün güncellendi."
        if atlanan:
            mesaj += f" Açık teklifi olan {atlanan} ürün atlandı."
        mesaj += " XML ürünleri değişmedi. Yanlışsa Son Fiyat Artışını Geri Al kullanılabilir."
        return jsonify({"success": True, "mesaj": mesaj, "uygulanan": uygulanan, "atlanan": atlanan})
    except Exception as hata:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": f"Manuel kâr işlemi tamamlanamadı: {hata}"}), 500


# ==========================================
# BİZİMHESAP PERAKENDE SATIŞ CSV AKTARIM ROTASI
# ==========================================
@app.route("/admin/bizimhesap-aktarim-excel", methods=["GET"])
@admin_required
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
@admin_required
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
@admin_required
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
        _izleyici_baglandi(request.sid)
        izleyici_sayisini_yayinla()
        ozet = _izleyici_ozet()
        if ozet is not None:
            emit(
                "izleyici_sayisi_guncelle",
                {"sayi": ozet[0], "dukkan": "gumus", "isimler": ozet[1]},
            )
    finally:
        db.session.remove()


@socketio.on("disconnect")
def handle_disconnect():
    try:
        soketten_kullanici_kopar(request.sid)
        _sahne_izleyicisi_sil(request.sid)
        _izleyici_koptu(request.sid)
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


_PATRON_DEVRALMA = (
    "Patron sen hiç yorulma, mikrofon bende, salonu şimdi ayağa kaldırıyorum!",
    "Tamam patron, sen çayını yudumla, sahne bende, fiyatı ben ısıtıyorum!",
    "Duydum patron, sen kenara çekil, ben bu salonu bir dakikada ayaklandırırım!",
)
_patron_devir_sira = 0


def _patron_sunucuya_dondu(metin):
    s = _patron_yazi_sade(metin)
    return any(k in s for k in (
        "sunucu", "spiker", "mezatci", "kralice", "nazli", "sana soyluyorum",
        "bana bak", "sen anlat", "sen devam", "mikrofon sende", "mikrofonu al",
        "devral", "pas at", "pasliyorum", "seni dinle", "sus artik", "birak",
        "yanlis yaptin", "fiyati kacirdin", "duzelt sunucu",
    ))


def _patron_yayinina_cevap(dukkan, metin):
    global _patron_devir_sira
    temiz = ses_metnini_temizle(metin)
    if len(temiz) < 2:
        return
    socketio.emit(
        "yeni_canli_yorum",
        {
            "platform": "Patron",
            "kullanici": "Patron",
            "mesaj": temiz,
            "yorum": temiz,
            "zaman": time.strftime("%H:%M:%S"),
            "dukkan": dukkan,
        },
        room=dukkan,
    )
    _nabiz_dokun(dukkan)
    if not _patron_sunucuya_dondu(temiz):
        return
    yanit = _PATRON_DEVRALMA[_patron_devir_sira % len(_PATRON_DEVRALMA)]
    _patron_devir_sira += 1
    ahmet_usta_anons_yap(dukkan, yanit, tur="anons")


def _reji_admin_yaz(sid, mesaj):
    paket = {"mesaj": mesaj, "zaman": time.strftime("%H:%M:%S")}
    hedef = sid or "admin_room"
    socketio.emit("reji_geri_bildirim", paket, room=hedef)


def _reji_emrini_uygula(dukkan, metin, sid=None, bildir=True):
    """Gizli reji: GPT yanıtı sohbete yazılmaz, ElevenLabs ile seslenir."""
    with app.app_context():
        try:
            komut = _patron_papaganini_sil(metin).strip()
            komut = re.sub(r"^\[PATRON GİZLİ EMRİ\]\s*", "", komut, flags=re.IGNORECASE).strip()
            if len(komut) < 2:
                _reji_admin_yaz(
                    sid,
                    "[Gizli Reji] Kayıt sessiz veya anlaşılamadı. Mikrofonu basılı tutup net konuşun, sonra bırakın.",
                )
                return
            if _patron_emir_turu(komut) == "sayac":
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
                    _patron_sistemi(aktif_urun, "[PATRON GİZLİ EMRİ] " + komut),
                    "Gizli reji emrini uygula. İzleyiciye söyleyeceğin anonsu yaz. Emri, patronu ve kulaklığı ağzına alma.",
                    max_tokens=220,
                    zaman_asimi=8,
                )
            except Exception as hata:
                print(f"[GIZLI REJI] {hata}")
                yanit = ""
            yanit = _patron_papaganini_sil(yanit)
            if not yanit or yanit.casefold() == komut.casefold():
                if bildir:
                    _reji_admin_yaz(
                        sid,
                        "[Gizli Reji] Komut duyuldu ama sunucu yanıtı üretilemedi. Biraz daha net tekrar deneyin.",
                    )
                return
            ahmet_ses_akisi(dukkan, yanit, tur="anons", kesinti=False, gizli=True)
            if bildir:
                _reji_admin_yaz(sid, "[Gizli Reji Komutu İletildi] " + _dukkan_etiketi(dukkan) + " " + komut)
        finally:
            db.session.remove()


def _patron_sesini_isle(dukkan, ses_b64, uzanti, gizli=True, sid=None):
    import tempfile
    yol = ""
    try:
        try:
            ham = base64.b64decode(ses_b64)
        except Exception:
            print("[PATRON SESİ] Ses anlaşılmadı, kayıt bozuk.")
            if gizli:
                _reji_admin_yaz(sid, "[Gizli Reji] Ses kaydı bozuk. Tekrar basılı tutup konuşun.")
            return
        if len(ham) < 400:
            print("[PATRON SESİ] Ses anlaşılmadı, kayıt çok kısa.")
            if gizli:
                _reji_admin_yaz(
                    sid,
                    "[Gizli Reji] Kayıt çok kısa veya sessiz. Mikrofonu basılı tutup net konuşun, sonra bırakın.",
                )
            return
        if gizli and _openai_ses_istemcisi() is None:
            _reji_admin_yaz(sid, "[Gizli Reji] Ses motoru anahtarı yok, kayıt metne çevrilemedi.")
            return
        fd, yol = tempfile.mkstemp(prefix="temp_patron_voice_", suffix="." + uzanti)
        os.close(fd)
        with open(yol, "wb") as dosya:
            dosya.write(ham)
        try:
            metin = _patron_papaganini_sil(_patron_sesini_metne_cevir(yol))
        except Exception as hata:
            print(f"[PATRON SESİ] Transkripsiyon hatası: {hata}")
            if gizli:
                _reji_admin_yaz(sid, "[Gizli Reji] Ses metne çevrilemedi. Tekrar deneyin.")
            return
        if len(metin) < 2:
            print("[PATRON SESİ] Ses anlaşılmadı, metin boş.")
            if gizli:
                _reji_admin_yaz(
                    sid,
                    "[Gizli Reji] Kayıt sessiz veya anlaşılamadı. Biraz daha uzun ve net konuşup tekrar deneyin.",
                )
            return
        print(f"[PATRON SESİ METNE ÇEVRİLDİ]: '{metin}'")
        hedefler = dukkan if isinstance(dukkan, (list, tuple)) else [dukkan]
        if gizli:
            for kod in hedefler:
                socketio.start_background_task(
                    _reji_emrini_uygula, kod, metin, sid, len(hedefler) == 1,
                )
            if len(hedefler) > 1:
                _reji_admin_yaz(sid, "[Gizli Reji] Tüm dükkanlara iletildi: " + metin)
            return
        for kod in hedefler:
            socketio.start_background_task(_patron_emrini_uygula, kod, metin)
    except Exception as hata:
        print(f"[PATRON SESİ] Ses anlaşılmadı: {hata}")
        if gizli:
            _reji_admin_yaz(sid, "[Gizli Reji] Ses işlenemedi. Tekrar deneyin.")
    finally:
        if yol and os.path.exists(yol):
            try:
                os.remove(yol)
            except OSError:
                pass


def _reji_ses_komutunu_al(veri):
    if not session.get("is_admin"):
        print("[PATRON SESİ] Yönetici oturumu yok, ses işlenmedi.")
        _reji_admin_yaz(request.sid, "[Gizli Reji] Yönetici oturumu yok. Panele yeniden girin.")
        return
    veri = veri or {}
    ses = str(veri.get("audio") or "")
    if not ses or len(ses) > 8000000:
        print("[PATRON SESİ] Ses anlaşılmadı, paket boş.")
        _reji_admin_yaz(
            request.sid,
            "[Gizli Reji] Kayıt boş veya çok kısa. Mikrofonu basılı tutup net konuşun, sonra bırakın.",
        )
        return
    mime = str(veri.get("mime") or "audio/webm")
    uzanti = "wav" if "wav" in mime else "webm"
    hedefler = _reji_hedef_listesi(veri.get("room") or veri.get("dukkan") or "gumus")
    gizli = bool(veri.get("gizli", True))
    socketio.start_background_task(
        _patron_sesini_isle, hedefler, ses, uzanti, gizli, request.sid,
    )


@socketio.on("reji_ses_komutu")
def reji_ses_komutu(veri):
    """Gizli reji bas-konuş: Whisper, sonra gizli GPT anonsu."""
    try:
        _reji_ses_komutunu_al(veri)
    finally:
        db.session.remove()


@socketio.on("patron_ses_komut")
def patron_ses_komut(veri):
    """Bas-konuş kaydını Türkçe metne çevirir ve emri sahneye uygular."""
    try:
        _reji_ses_komutunu_al(veri)
    finally:
        db.session.remove()


@socketio.on("patron_yayin_metni")
def patron_yayin_metni(veri):
    """Patron yayındayken söz izleyiciye düşer. Sunucu yalnız hitap, eleştiri veya pasta cevap verir."""
    try:
        if not session.get("is_admin"):
            return
        veri = veri or {}
        metin = ses_metnini_temizle(veri.get("metin"))
        if len(metin) < 2:
            return
        for dukkan in _reji_hedef_listesi(veri.get("dukkan") or veri.get("room") or "gumus"):
            _patron_yayinina_cevap(dukkan, metin)
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
        mime = str(veri.get("mime") or "audio/webm;codecs=opus")[:80]
        for dukkan in _reji_hedef_listesi(veri.get("dukkan") or veri.get("room") or "gumus"):
            emit(
                "izleyici_moderator_sesi",
                {
                    "dukkan": dukkan,
                    "parca": parca,
                    "mime": mime,
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


def _katilana_sahneyi_ver(dukkan):
    """Yenileme ve kopma sonrası bu soket sahneyi, kalan saniyeyi ve aşamayı alır."""
    oda = odalar.get(dukkan)
    if oda is None:
        return
    asama = _canli_asama_alanlari(oda)
    urun_id = oda.get("aktif_urun_id")
    urun = None
    if urun_id:
        try:
            urun = db.session.get(Urun, int(urun_id))
        except (TypeError, ValueError):
            urun = None
    paket = urun.to_dict() if urun is not None and not urun.silindi_mi else None
    if paket:
        emit("yeni_sahne_urunu", paket)
    durum = oda.get("durum")
    try:
        pey = float(durum.get("pey") or 0) if durum is not None else 0
    except (TypeError, ValueError):
        pey = 0
    kazanan = (durum.get("kazanan") if durum is not None else None) or "Yok"
    emit(
        "pey_guncellendi",
        {
            "dukkan": dukkan,
            "urun_id": urun_id,
            "pey": pey,
            "kazanan": kazanan,
            "kazanan_id": durum.get("kazanan_id") if durum is not None else None,
        },
    )
    emit(
        "sayac_guncelle",
        {
            "dukkan": dukkan,
            "kalan": asama["kalan"],
            "kalan_sure": asama["kalan"],
            "ham_kalan": asama.get("ham_kalan", asama["kalan"]),
            "toplam": 120 if asama["asama"] == "pazarlik" else 60,
            "sayac_gorunsun": bool(asama["sayac_aktif"]) and asama["asama"] in ("pazarlik", "karar"),
            "sayac_aktif": asama["sayac_aktif"],
            "asama": asama["asama"] if asama["sayac_aktif"] else "bekliyor",
        },
    )
    emit(
        "sahne_durumu",
        {
            "dukkan": dukkan,
            "aktif_urun": paket,
            "pey": pey,
            "kazanan": kazanan,
            "durum": (durum.get("durum") if durum is not None else None) or "Bekliyor",
            **asama,
        },
    )


@socketio.on("join_dukkan")
def handle_join_dukkan(data):
    try:
        veri = data or {}
        dukkan = dukkan_dogrula(veri.get("dukkan", "gumus"))
        ad = str(veri.get("isim") or "").strip()[:40] or "Misafir"
        _izleyici_ad_yaz(request.sid, ad)
        sokete_kullanici_bagla(request.sid, veri.get("kullanici_id"))
        try:
            kayit_no = int(veri.get("kullanici_id")) if veri.get("kullanici_id") not in (None, "", "null") else None
        except (TypeError, ValueError):
            kayit_no = None
        _sahne_izleyicisi_yaz(request.sid, dukkan, ad, kayit_no)
        join_room(dukkan)
        _katilana_sahneyi_ver(dukkan)
        izleyici_sayisini_yayinla()
        ozet = _izleyici_ozet()
        if ozet is not None:
            emit(
                "izleyici_sayisi_guncelle",
                {"dukkan": dukkan, "sayi": ozet[0], "isimler": ozet[1]},
            )
    finally:
        db.session.remove()


@socketio.on("leave_dukkan")
def handle_leave_dukkan(data):
    dukkan = dukkan_dogrula((data or {}).get("dukkan", "gumus"))
    leave_room(dukkan)
    _sahne_izleyicisi_cikar(request.sid, dukkan)
    _ozel_odayi_kapat(request.sid)


_ozel_kilit = threading.Lock()
_dukkan_izleyici = {}
_sid_dukkan = {}
_ozel_davet = {}
_ozel_oda = {}


def _izleyici_dukkan_oku(sid):
    if not REDIS_URL:
        return _sid_dukkan.get(sid)
    istemci = _redis_al()
    if istemci is None:
        return None
    try:
        return istemci.hget("mezat:izleyici:sid", sid) or None
    except Exception as hata:
        _redis_dusur(hata)
        return None


def _izleyici_bilgi(dukkan, sid):
    if not REDIS_URL:
        return (_dukkan_izleyici.get(dukkan) or {}).get(sid) or {}
    istemci = _redis_al()
    if istemci is None:
        return {}
    try:
        ham = istemci.hget("mezat:izleyici:" + str(dukkan), sid)
    except Exception as hata:
        _redis_dusur(hata)
        return {}
    if not ham:
        return {}
    try:
        gelen = json.loads(ham)
    except (TypeError, ValueError):
        return {}
    return gelen if isinstance(gelen, dict) else {}


def _izleyici_dukkan_harita(dukkan):
    if not REDIS_URL:
        return dict(_dukkan_izleyici.get(dukkan) or {})
    istemci = _redis_al()
    if istemci is None:
        return {}
    try:
        ham = istemci.hgetall("mezat:izleyici:" + str(dukkan)) or {}
    except Exception as hata:
        _redis_dusur(hata)
        return {}
    cikti = {}
    for sid, govde in ham.items():
        try:
            gelen = json.loads(govde)
        except (TypeError, ValueError):
            continue
        if isinstance(gelen, dict):
            cikti[sid] = gelen
    return cikti


def _sahne_izleyicisi_yaz(sid, dukkan, ad, kullanici_id):
    kayit = {"ad": str(ad or "Misafir")[:40], "kullanici_id": kullanici_id}
    if not REDIS_URL:
        with _ozel_kilit:
            eski = _sid_dukkan.get(sid)
            if eski and eski != dukkan:
                (_dukkan_izleyici.get(eski) or {}).pop(sid, None)
            _sid_dukkan[sid] = dukkan
            _dukkan_izleyici.setdefault(dukkan, {})[sid] = kayit
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        eski = istemci.hget("mezat:izleyici:sid", sid)
        if eski and eski != dukkan:
            istemci.hdel("mezat:izleyici:" + eski, sid)
        istemci.hset("mezat:izleyici:sid", sid, dukkan)
        istemci.hset(
            "mezat:izleyici:" + dukkan,
            sid,
            json.dumps(kayit, ensure_ascii=False),
        )
        istemci.hset("mezat:izleyici:ad", sid, kayit["ad"])
        istemci.hset("mezat:izleyici:oturum", sid, str(int(time.time())))
    except Exception as hata:
        _redis_dusur(hata)


def _sahne_izleyicisi_cikar(sid, dukkan):
    if not REDIS_URL:
        with _ozel_kilit:
            if _sid_dukkan.get(sid) == dukkan:
                _sid_dukkan.pop(sid, None)
                (_dukkan_izleyici.get(dukkan) or {}).pop(sid, None)
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        if istemci.hget("mezat:izleyici:sid", sid) == dukkan:
            istemci.hdel("mezat:izleyici:sid", sid)
            istemci.hdel("mezat:izleyici:" + dukkan, sid)
    except Exception as hata:
        _redis_dusur(hata)


def _ozel_indeks_ekle(istemci, sid, kimlik):
    anahtar = "mezat:ozel:sid:" + sid
    istemci.sadd(anahtar, kimlik)
    istemci.expire(anahtar, 6 * 3600)


def _ozel_kayit_yaz(tur, kimlik, kayit, omur):
    if not REDIS_URL:
        return
    istemci = _redis_al()
    if istemci is None:
        return
    govde = dict(kayit)
    if "uyeler" in govde:
        govde["uyeler"] = list(govde["uyeler"])
    try:
        istemci.set("mezat:ozel:" + tur + ":" + kimlik, json.dumps(govde, ensure_ascii=False), ex=omur)
        for sid in govde.get("uyeler") or (govde.get("kimden"), govde.get("kime")):
            if sid:
                _ozel_indeks_ekle(istemci, sid, tur + ":" + kimlik)
    except Exception as hata:
        _redis_dusur(hata)


def _ozel_kayit_oku(tur, kimlik):
    if not REDIS_URL:
        with _ozel_kilit:
            kaynak = _ozel_davet if tur == "davet" else _ozel_oda
            gelen = kaynak.get(kimlik)
            return dict(gelen) if gelen else None
    istemci = _redis_al()
    if istemci is None:
        return None
    try:
        ham = istemci.get("mezat:ozel:" + tur + ":" + kimlik)
    except Exception as hata:
        _redis_dusur(hata)
        return None
    if not ham:
        return None
    try:
        gelen = json.loads(ham)
    except (TypeError, ValueError):
        return None
    if isinstance(gelen, dict) and "uyeler" in gelen:
        gelen["uyeler"] = set(gelen["uyeler"])
    return gelen


def _ozel_kayit_sil(tur, kimlik):
    if not REDIS_URL:
        with _ozel_kilit:
            kaynak = _ozel_davet if tur == "davet" else _ozel_oda
            return kaynak.pop(kimlik, None)
    istemci = _redis_al()
    if istemci is None:
        return None
    try:
        ham = istemci.get("mezat:ozel:" + tur + ":" + kimlik)
        istemci.delete("mezat:ozel:" + tur + ":" + kimlik)
    except Exception as hata:
        _redis_dusur(hata)
        return None
    if not ham:
        return None
    try:
        return json.loads(ham)
    except (TypeError, ValueError):
        return None


def _ozel_odayi_kapat(sid):
    kapanan = []
    if not REDIS_URL:
        with _ozel_kilit:
            for oda_id, oda in list(_ozel_oda.items()):
                if sid in oda.get("uyeler", ()):
                    kapanan.append((oda_id, set(oda["uyeler"])))
                    _ozel_oda.pop(oda_id, None)
            for davet_id, davet in list(_ozel_davet.items()):
                if sid in (davet.get("kimden"), davet.get("kime")):
                    _ozel_davet.pop(davet_id, None)
    else:
        istemci = _redis_al()
        if istemci is not None:
            try:
                kimlikler = list(istemci.smembers("mezat:ozel:sid:" + sid) or [])
            except Exception as hata:
                _redis_dusur(hata)
                kimlikler = []
            for kimlik in kimlikler:
                tur, _, oda_id = str(kimlik).partition(":")
                kayit = _ozel_kayit_sil(tur, oda_id)
                if tur == "oda" and isinstance(kayit, dict):
                    uyeler = set(kayit.get("uyeler") or [])
                    kapanan.append((oda_id, uyeler))
                istemci.srem("mezat:ozel:sid:" + sid, kimlik)
    for oda_id, uyeler in kapanan:
        for diger in uyeler:
            if diger == sid:
                continue
            socketio.emit(
                "ozel_sohbet_kapandi",
                {"oda_id": oda_id, "mesaj": "Karşı taraf sahneden ayrıldı."},
                room=diger,
            )


def _sahne_izleyicisi_sil(sid):
    if not REDIS_URL:
        with _ozel_kilit:
            dukkan = _sid_dukkan.pop(sid, None)
            if dukkan:
                (_dukkan_izleyici.get(dukkan) or {}).pop(sid, None)
    else:
        istemci = _redis_al()
        if istemci is not None:
            try:
                _izleyici_redis_sil(istemci, sid)
            except Exception as hata:
                _redis_dusur(hata)
    _ozel_odayi_kapat(sid)


@socketio.on("ozel_sohbet_liste")
def ozel_sohbet_liste(veri=None):
    dukkan = _izleyici_dukkan_oku(request.sid) or dukkan_dogrula((veri or {}).get("dukkan"))
    kayitlar = [
        {"sid": sid, "ad": (bilgi.get("ad") or "İzleyici")}
        for sid, bilgi in _izleyici_dukkan_harita(dukkan).items()
        if sid != request.sid
    ]
    emit("ozel_sohbet_liste", {"dukkan": dukkan, "izleyiciler": kayitlar})


@socketio.on("ozel_sohbet_davet")
def ozel_sohbet_davet(veri):
    hedef = str((veri or {}).get("hedef_sid") or "")
    ben = request.sid
    dukkan = _izleyici_dukkan_oku(ben)
    if not dukkan or _izleyici_dukkan_oku(hedef) != dukkan or hedef == ben:
        emit("ozel_sohbet_hata", {"mesaj": "Bu izleyici artık bu sahnede değil."})
        return
    davet_id = uuid.uuid4().hex[:12]
    kayit = {"kimden": ben, "kime": hedef, "dukkan": dukkan}
    if not REDIS_URL:
        with _ozel_kilit:
            _ozel_davet[davet_id] = kayit
    else:
        _ozel_kayit_yaz("davet", davet_id, kayit, 180)
    ad = (_izleyici_bilgi(dukkan, ben).get("ad") or "İzleyici")
    socketio.emit(
        "ozel_sohbet_davet",
        {"davet_id": davet_id, "ad": ad},
        room=hedef,
    )
    emit("ozel_sohbet_bilgi", {"mesaj": "Sohbet daveti gönderildi."})


@socketio.on("ozel_sohbet_yanit")
def ozel_sohbet_yanit(veri):
    davet_id = str((veri or {}).get("davet_id") or "")
    davet = _ozel_kayit_oku("davet", davet_id)
    if not davet or davet.get("kime") != request.sid:
        return
    _ozel_kayit_sil("davet", davet_id)
    if not (veri or {}).get("kabul"):
        socketio.emit("ozel_sohbet_bilgi", {"mesaj": "Sohbet daveti reddedildi."}, room=davet["kimden"])
        return
    if (
        _izleyici_dukkan_oku(davet["kimden"]) != davet["dukkan"]
        or _izleyici_dukkan_oku(davet["kime"]) != davet["dukkan"]
    ):
        emit("ozel_sohbet_hata", {"mesaj": "Davet artık geçerli değil."})
        return
    oda_id = uuid.uuid4().hex[:12]
    uyeler = {davet["kimden"], davet["kime"]}
    oda = {"uyeler": uyeler, "dukkan": davet["dukkan"]}
    if not REDIS_URL:
        with _ozel_kilit:
            _ozel_oda[oda_id] = oda
    else:
        _ozel_kayit_yaz("oda", oda_id, oda, 6 * 3600)
    dukkan = davet["dukkan"]
    ad_kimden = _izleyici_bilgi(dukkan, davet["kimden"]).get("ad") or "İzleyici"
    ad_kime = _izleyici_bilgi(dukkan, davet["kime"]).get("ad") or "İzleyici"
    socketio.emit("ozel_sohbet_acildi", {"oda_id": oda_id, "karsi_ad": ad_kime}, room=davet["kimden"])
    socketio.emit("ozel_sohbet_acildi", {"oda_id": oda_id, "karsi_ad": ad_kimden}, room=davet["kime"])


@socketio.on("ozel_sohbet_mesaj")
def ozel_sohbet_mesaj(veri):
    oda_id = str((veri or {}).get("oda_id") or "")
    metin = str((veri or {}).get("metin") or "").strip()[:500]
    if not metin:
        return
    if metin_uygunsuz_mu(metin):
        emit("ozel_sohbet_hata", {"mesaj": "Mesaj iletilemedi."})
        return
    oda = _ozel_kayit_oku("oda", oda_id)
    uyeler = set((oda or {}).get("uyeler") or [])
    if not oda or request.sid not in uyeler:
        return
    dukkan = oda.get("dukkan") or _izleyici_dukkan_oku(request.sid)
    ad = _izleyici_bilgi(dukkan, request.sid).get("ad") or "İzleyici"
    for sid in uyeler:
        socketio.emit(
            "ozel_sohbet_mesaj",
            {
                "oda_id": oda_id,
                "ad": ad,
                "metin": metin,
                "benim": sid == request.sid,
            },
            room=sid,
        )


@socketio.on("anons_bitti")
def anons_bitti(veri):
    """Satış ve zafer anonsu bittiğinde otonom geçiş beklemesini çözer."""
    try:
        veri = veri or {}
        if str(veri.get("tur") or "") not in ("satis", "zafer"):
            return
        dukkan = dukkan_dogrula(veri.get("dukkan") or "gumus")
        _satis_tebrik_bitti(dukkan)
    finally:
        db.session.remove()


REHBER_ADIMLARI = (
    "Birinci adım. Vitrinden veya sahneden bir lot seçin. Üyeliğiniz olmadan yayını ve vitrini izleyebilirsiniz.",
    "İkinci adım. İlk peyinizde ad, soyad, telefon ve e-posta ile kayıt olun. Şartname kutusunu işaretleyin. E-postanıza gelen altı haneli kodu girince hesabınız açılır.",
    "Üçüncü adım. Yazılı pey, bas konuş ile sesli pey veya Hemen Al ile teklif verin.",
    "Dördüncü adım. Kazandığınız eser sepete düşer. Ödemeyi sepetten tamamlayın. Yeni katılımcı beşinci üründe ön ödeme yapmadan yeni alım açamaz.",
)


@socketio.on("rehber_ses_iste")
def rehber_ses_iste(veri):
    """Nasıl kullanılır turu. Ses yalnızca isteyen sokete gider."""
    try:
        adim = int((veri or {}).get("adim") or 1)
    except (TypeError, ValueError):
        adim = 1
    try:
        maddeler = icerik_maddelerini_oku("rehber")
    finally:
        db.session.remove()
    if not maddeler:
        maddeler = [
            {"baslik": "", "metin": metin} for metin in REHBER_ADIMLARI
        ]
    if adim < 1:
        adim = 1
    if adim > len(maddeler):
        adim = len(maddeler)
    kayit = maddeler[adim - 1]
    metin = ses_metnini_temizle(
        ((kayit.get("baslik") or "") + ". " + (kayit.get("metin") or "")).strip(" .")
    )
    sid = request.sid

    def uret():
        mp3, motor = _tts_uret(metin)
        if not mp3:
            socketio.emit("rehber_ses", {
                "adim": adim,
                "mesaj": "Ses üretilemedi. Adım metni ekranda duruyor.",
            }, room=sid)
            return
        adres = _mp3_kaydet(mp3, adim)
        socketio.emit("rehber_ses", {
            "adim": adim,
            "audio_url": adres,
            "motor": motor,
        }, room=sid)

    socketio.start_background_task(uret)


@socketio.on("ahmet_ses_iste")
def ahmet_ses_iste(veri):
    """Yönetici serbest metin okutur. İzleyici yalnız sahnedeki ürünün tanıtımını dinler."""
    try:
        veri = veri or {}
        dukkan = dukkan_dogrula(veri.get("dukkan") or "gumus")
        oda = odalar.get(dukkan) or {}
        durum = (oda.get("durum") or {}).get("durum")
        if not oda.get("aktif_urun_id") or durum in ("bosta", "Satıldı", "Pas"):
            return
        if session.get("is_admin"):
            metin = str(veri.get("metin") or "").strip()
        else:
            urun = db.session.get(Urun, oda.get("aktif_urun_id"))
            metin = str(getattr(urun, "tanitim_yazisi", "") or "").strip()
        if metin:
            ahmet_ses_akisi(dukkan, metin, tur="tanitim", kesinti=False)
    finally:
        db.session.remove()


@socketio.on('admin_sesli_komut')
def handle_admin_sesli_komut(data):
    if not session.get("is_admin"):
        return
    dukkan = data.get('dukkan', 'gumus')
    metin = data.get('metin', '').strip()
    if metin:
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
_IZLEYICI_OMUR = 120


def _izleyici_canli_sidler(istemci):
    simdi = int(time.time())
    ham = istemci.hgetall("mezat:izleyici:oturum") or {}
    canli = []
    for sid, an in ham.items():
        try:
            taze = simdi - int(an) <= _IZLEYICI_OMUR
        except (TypeError, ValueError):
            taze = False
        if taze:
            canli.append(sid)
        else:
            _izleyici_redis_sil(istemci, sid)
    return canli


def _izleyici_redis_sil(istemci, sid):
    dukkan = istemci.hget("mezat:izleyici:sid", sid)
    if dukkan:
        istemci.hdel("mezat:izleyici:" + dukkan, sid)
    istemci.hdel("mezat:izleyici:oturum", sid)
    istemci.hdel("mezat:izleyici:ad", sid)
    istemci.hdel("mezat:izleyici:sid", sid)


def _izleyici_baglandi(sid):
    if not REDIS_URL:
        aktif_izleyiciler.add(sid)
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        istemci.hset("mezat:izleyici:oturum", sid, str(int(time.time())))
    except Exception as hata:
        _redis_dusur(hata)


def _izleyici_ad_yaz(sid, ad):
    temiz = str(ad or "Misafir")[:40]
    if not REDIS_URL:
        izleyici_adlari[sid] = temiz
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        istemci.hset("mezat:izleyici:ad", sid, temiz)
        istemci.hset("mezat:izleyici:oturum", sid, str(int(time.time())))
    except Exception as hata:
        _redis_dusur(hata)


def _izleyici_koptu(sid):
    if not REDIS_URL:
        aktif_izleyiciler.discard(sid)
        izleyici_adlari.pop(sid, None)
        return
    istemci = _redis_al()
    if istemci is None:
        return
    try:
        _izleyici_redis_sil(istemci, sid)
    except Exception as hata:
        _redis_dusur(hata)


def _izleyici_ozet():
    """Canlı soket sayısı, bütün worker'ların toplamıdır."""
    if not REDIS_URL:
        isimler = []
        for ad in izleyici_adlari.values():
            temiz = str(ad or "").strip()
            if temiz and temiz not in isimler:
                isimler.append(temiz)
        return len(aktif_izleyiciler), isimler
    istemci = _redis_al()
    if istemci is None:
        return None
    try:
        canli = _izleyici_canli_sidler(istemci)
        adlar = istemci.hgetall("mezat:izleyici:ad") or {}
        isimler = []
        for sid in canli:
            temiz = str(adlar.get(sid) or "").strip()
            if temiz and temiz not in isimler:
                isimler.append(temiz)
        return len(canli), isimler
    except Exception as hata:
        _redis_dusur(hata)
        return None


def sahneyi_herkese_yayinla(dukkan, urun_paket, pey_paket=None, yeni_urun=False):
    """Sahne değişimini ilgili dükkân odasına bir kez duyurur."""
    oda = odalar.get(dukkan)
    if oda is not None and urun_paket:
        oda.pop("son_satis", None)
    if yeni_urun:
        _sahne_sesini_kes(dukkan)
        _iki_odayi(
            dukkan,
            "yeni_urun_sahneye_alindi",
            {"dukkan": dukkan, "urun_id": (urun_paket or {}).get("id")},
        )
    if urun_paket:
        _iki_odayi(dukkan, "yeni_sahne_urunu", urun_paket)
    if pey_paket:
        _pey_duyur(dukkan, pey_paket)
    _dukkan_canli_yayinla()


def izleyici_sayisini_yayinla():
    ozet = _izleyici_ozet()
    if ozet is None:
        return
    sayi, isimler = ozet
    for kod in DUKKANLAR:
        socketio.emit(
            "izleyici_sayisi_guncelle",
            {"dukkan": kod, "sayi": sayi, "isimler": isimler},
            room=kod,
        )


_veritabani_onarim_kilit = threading.Lock()
_veritabani_onarim_basladi = False
_SEMA_ADVISORY_ANAHTAR = 814531453


def _sema_kilidi_dene():
    """PostgreSQL advisory lock. Alan worker semayi kurar, digerleri DDL'i atlar.

    Kilit alinamadan hata olursa True doner; kolon ekleme zaten try-except icindedir.
    """
    if IS_SQLITE:
        return True, None
    baglanti = None
    try:
        baglanti = db.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        alindi = baglanti.execute(
            text("SELECT pg_try_advisory_lock(:anahtar)"),
            {"anahtar": _SEMA_ADVISORY_ANAHTAR},
        ).scalar()
        if alindi:
            print("[BOOT] sema advisory lock alindi", flush=True)
            return True, baglanti
        baglanti.close()
        print("[BOOT] sema baska workerda, DDL atlandi", flush=True)
        return False, None
    except Exception as hata:
        print(f"[BOOT] advisory lock denemesi basarisiz, DDL try-except ile: {hata}", flush=True)
        if baglanti is not None:
            try:
                baglanti.close()
            except Exception:
                pass
        return True, None


def _sema_kilidi_birak(baglanti):
    if baglanti is None:
        return
    try:
        baglanti.execute(
            text("SELECT pg_advisory_unlock(:anahtar)"),
            {"anahtar": _SEMA_ADVISORY_ANAHTAR},
        )
    except Exception as hata:
        print(f"[BOOT] advisory lock birakilamadi: {hata}", flush=True)
    try:
        baglanti.close()
    except Exception:
        pass


def veritabani_onarimini_bir_kez_baslat():
    """Port açıldıktan sonra bir kez. Hata uygulamayı durdurmaz.

    Uc worker ayni anda acilsa yalnizca advisory lock alan worker DDL calistirir.
    """
    global _veritabani_onarim_basladi
    with _veritabani_onarim_kilit:
        if _veritabani_onarim_basladi:
            return
        _veritabani_onarim_basladi = True

    print("[BOOT] veritabani onarimi zamanlandi", flush=True)
    lider = False
    baglanti = None
    try:
        with app.app_context():
            lider, baglanti = _sema_kilidi_dene()
        if not lider:
            return
        veritabani_tablolari_onar()
        if _pazaryeri_semasi is not None:
            print("[BOOT] pazaryeri tablolari", flush=True)
            _pazaryeri_semasi(app, db)
    except Exception as hata:
        print(f"[BOOT] veritabani onarimi tamamlanamadi: {hata}", flush=True)
    finally:
        _sema_kilidi_birak(baglanti)


def _arkaplan_agir_baslangic():
    """Port açıldıktan sonra: onarım, pazaryeri şeması, XML senkronu ve ses hazırlığı."""
    print("[BOOT] gecikmeli baslangic calisiyor", flush=True)
    try:
        veritabani_onarimini_bir_kez_baslat()
        print("[BOOT] xml senkron zamanlayicisi", flush=True)
        xml_stok_zamanlayiciyi_baslat()
    except Exception as hata:
        print(f"[BOOT] gecikmeli baslangic atlandi: {hata}", flush=True)
    if getattr(Config, "ELEVENLABS_API_KEY", ""):
        print(f"Canlı ses akışı hazır: ElevenLabs {Config.ELEVENLABS_MODEL_ID}.")
    elif getattr(Config, "OPENAI_API_KEY", ""):
        print(f"Canlı ses akışı hazır: OpenAI {Config.OPENAI_TTS_VOICE}.")
    else:
        print("[SES] ElevenLabs ve OpenAI anahtarı yok; Edge TTS yedek.")


_gecikmeli_baslangic_kuruldu = False
_pazaryeri_semasi = None


def _gecikmeli_baslangici_kur():
    """Import greenlet'inde uyumaz. Hub döngüsü portu açtıktan 8 sn sonra çalışır."""
    global _gecikmeli_baslangic_kuruldu
    if _gecikmeli_baslangic_kuruldu:
        return
    _gecikmeli_baslangic_kuruldu = True
    import gevent
    gevent.spawn_later(8, _arkaplan_agir_baslangic)
    _otonom_devir_baslat()
    print("[BOOT] agir baslangic 8sn sonraya birakildi", flush=True)


# ==========================================
# PAZARYERİ VE TEDARİKÇİ MODÜLÜ BAĞLANTISI
# ==========================================
print("[BOOT] pazaryeri modulu", flush=True)
try:
    from pazaryeri import init_pazaryeri, pazaryeri_bp, pazaryeri_semasini_hazirla
    _pazaryeri_semasi = pazaryeri_semasini_hazirla
    init_pazaryeri(app, db, admin_required)
    app.register_blueprint(pazaryeri_bp, url_prefix="/admin/pazaryeri")
    print("[BOOT] pazaryeri rotalari hazir", flush=True)
except Exception as hata:
    print(f"[BOOT] pazaryeri atlandi: {hata}", flush=True)

_gecikmeli_baslangici_kur()
print("[BOOT] import bitti, gunicorn portu acabilir", flush=True)

# ==========================================
# SUNUCUYU BAŞLATMA
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Haliç Antik Çok Dükkanlı Mezat Portalı http://127.0.0.1:{port} adresinde başlatılıyor...")
    socketio.run(app, host="0.0.0.0", port=port, debug=True, use_reloader=False)
