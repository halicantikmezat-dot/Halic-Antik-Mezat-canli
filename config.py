import os

class Config:
    # Render'da "DATABASE_URL" değişkenini tanımladığınızda onu kullanır.
    # Yerelde ise kendi veritabanı bilginizi buraya yazabilirsiniz.
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'mysql+pymysql://kullanici:sifre@localhost/halic_antik_db'
    
    # Uyarıları kapatmak ve performansı artırmak için
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Güvenlik için gizli anahtar
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'cok-gizli-bir-anahtar-kelime'

    # Azure Speech (Metin Okuma - TTS) Ayarları
    AZURE_SPEECH_KEY = os.environ.get('AZURE_SPEECH_KEY') or '5Cnv0uA4wwqdklKnQTr4LE3iguQqDQWkokCNsustV4FvuH5XAfOmJQQJ99CJACi5YpzXJ3w3AAAYACOGjTY5'
    AZURE_SPEECH_REGION = os.environ.get('AZURE_SPEECH_REGION') or 'northeurope'
    AZURE_SPEECH_VOICE = 'tr-TR-AhmetNeural'

    # BizimHesap API Ayarları
    BIZIMHESAP_TOKEN = os.environ.get('BIZIMHESAP_TOKEN') or '6A35AAEBF1024183BA5DBE39BD4667A0'
    BIZIMHESAP_APP_ID = os.environ.get('BIZIMHESAP_APP_ID') or '358386'
    BIZIMHESAP_API_URL = os.environ.get('BIZIMHESAP_API_URL') or 'https://bizimhesap.com/api/b2b'