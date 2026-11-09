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

class Config:
    # Bulutta (Render) DATABASE_URL gelir; yerelde sqlite veya varsayılan db kullanılır
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///halic_mezat.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'halic_hamid_antik_mezat_gizli_anahtar_1453'

    # Azure Speech (Ahmet Usta TTS). Ortam değişkeni varsa o, yoksa kayıtlı anahtar.
    AZURE_SPEECH_KEY = _env("AZURE_SPEECH_KEY", "AZURE_KEY", "SPEECH_KEY") or "5Cnv0uA4wwqdklKnQTr4LE3iguQqDQWkokCNsustV4FvuH5XAfOmJQQJ99CJACi5YpzXJ3w3AAAYACOGjTY5"
    AZURE_SPEECH_REGION = _env("AZURE_SPEECH_REGION", "AZURE_REGION", "SPEECH_REGION", "REGION") or "northeurope"
    AZURE_SPEECH_LANG = "tr-TR"
    AZURE_SPEECH_VOICE = "tr-TR-AhmetNeural"

    # Gemini. Anahtar ortamda GEMINI_API_KEY olarak duruyor.
    GEMINI_API_KEY = _env("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_KEY")

    # Canlı mezat sesi. Anahtar yalnızca ortamdan okunur, koda yazılmaz.
    OPENAI_API_KEY = _env("OPENAI_API_KEY")
    OPENAI_TTS_MODEL = _env("OPENAI_TTS_MODEL") or "tts-1-hd"
    OPENAI_TTS_VOICE = _env("OPENAI_TTS_VOICE") or "nova"

    # BizimHesap API
    BIZIMHESAP_TOKEN = os.environ.get('BIZIMHESAP_TOKEN') or '6A35AAEBF1024183BA5DBE39BD4667A0'
    BIZIMHESAP_APP_ID = os.environ.get('BIZIMHESAP_APP_ID') or '358386'
    BIZIMHESAP_API_URL = os.environ.get('BIZIMHESAP_API_URL') or 'https://bizimhesap.com/api/b2b'

    # Port Ayarları
    MAIN_PORT = int(os.environ.get("PORT", 5000))
    BOT_PORT = 5005