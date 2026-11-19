import os

def _dotenv_yukle():
    yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(yol):
        return
    with open(yol, encoding="utf-8") as dosya:
        for satir in dosya:
            satir = satir.strip()
            if not satir or satir.startswith("#") or "=" not in satir:
                continue
            ad, deger = satir.split("=", 1)
            ad = ad.strip()
            deger = deger.strip()
            if len(deger) >= 2 and deger[0] in "\"'" and deger.find(deger[0], 1) > 0:
                deger = deger[1:deger.find(deger[0], 1)]
            else:
                pay = deger.find(" #")
                if pay >= 0:
                    deger = deger[:pay]
                deger = deger.strip().strip('"').strip("'")
            if ad and not os.environ.get(ad):
                os.environ[ad] = deger

_dotenv_yukle()

def _env(*adlar):
    for ad in adlar:
        deger = os.environ.get(ad)
        if deger and str(deger).strip():
            return str(deger).strip()
    return ""

def _havuz_tam_sayi(ad, varsayilan):
    """Ortamdaki havuz sayısını okur. Boş, bozuk veya sıfırın altındaki değerde varsayılan kalır."""
    try:
        deger = int(str(os.environ.get(ad, varsayilan)).strip())
    except (TypeError, ValueError):
        return int(varsayilan)
    if deger < 1:
        return int(varsayilan)
    return deger


def _sqlalchemy_engine_options(db_uri):
    """PostgreSQL için küçük bir bağlantı havuzu. SQLite havuz ayarı istemez."""
    adres = str(db_uri or "")
    if adres.startswith("sqlite"):
        return {}
    return {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "pool_size": _havuz_tam_sayi("DB_POOL_SIZE", 5),
        "max_overflow": _havuz_tam_sayi("DB_MAX_OVERFLOW", 5),
        "connect_args": {"options": "-c lock_timeout=5000"},
    }


def _veritabani_adresi():
    """Render DATABASE_URL. postgres:// adresi SQLAlchemy için postgresql:// olur."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url and database_url.strip():
        database_url = database_url.strip()
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        return database_url
    basedir = os.path.abspath(os.path.dirname(__file__))
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    sqlite_yol = os.path.join(basedir, "instance", "halic_mezat.db").replace("\\", "/")
    return "sqlite:///" + sqlite_yol


class Config:
    # Bulutta (Render) DATABASE_URL gelir; yerelde yalnız SQLite kullanılır
    SQLALCHEMY_DATABASE_URI = _veritabani_adresi()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _sqlalchemy_engine_options(SQLALCHEMY_DATABASE_URI)

    SECRET_KEY = os.environ.get('SECRET_KEY') or 'halic_hamid_antik_mezat_gizli_anahtar_1453'
    MEZAT_API_KEY = os.environ.get("MEZAT_API_KEY", "")

    # Azure Speech. Anahtar yalnızca ortamdan okunur.
    AZURE_SPEECH_KEY = _env("AZURE_SPEECH_KEY", "AZURE_KEY", "SPEECH_KEY")
    AZURE_SPEECH_REGION = _env("AZURE_SPEECH_REGION", "AZURE_REGION", "SPEECH_REGION", "REGION") or "northeurope"
    AZURE_SPEECH_LANG = "tr-TR"
    AZURE_SPEECH_VOICE = "tr-TR-AhmetNeural"

    # Gemini. Anahtar ortamda GEMINI_API_KEY olarak duruyor.
    GEMINI_API_KEY = _env("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_KEY")

    # Canlı mezat sesi. Anahtar yalnızca ortamdan okunur, koda yazılmaz.
    OPENAI_API_KEY = _env("OPENAI_API_KEY")
    OPENAI_TTS_MODEL = _env("OPENAI_TTS_MODEL") or "tts-1-hd"
    OPENAI_TTS_VOICE = _env("OPENAI_TTS_VOICE") or "nova"

    # ElevenLabs öncelikli mezat sunucusu. Anahtar yalnızca ortamdan okunur.
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
    ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL").strip()
    ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5").strip()

    # BizimHesap API. Anahtarlar yalnızca ortamdan okunur.
    BIZIMHESAP_TOKEN = os.environ.get("BIZIMHESAP_TOKEN") or ""
    BIZIMHESAP_APP_ID = os.environ.get("BIZIMHESAP_APP_ID") or ""
    BIZIMHESAP_API_URL = os.environ.get('BIZIMHESAP_API_URL') or 'https://bizimhesap.com/api/b2b'

    # Port Ayarları
    MAIN_PORT = int(os.environ.get("PORT", 5000))
    BOT_PORT = 5005