import os

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

    # BizimHesap API
    BIZIMHESAP_TOKEN = os.environ.get('BIZIMHESAP_TOKEN') or '6A35AAEBF1024183BA5DBE39BD4667A0'
    BIZIMHESAP_APP_ID = os.environ.get('BIZIMHESAP_APP_ID') or '358386'
    BIZIMHESAP_API_URL = os.environ.get('BIZIMHESAP_API_URL') or 'https://bizimhesap.com/api/b2b'

    # Port Ayarları
    MAIN_PORT = int(os.environ.get("PORT", 5000))
    BOT_PORT = 5005