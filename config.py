import os

class Config:
    # Render'da "DATABASE_URL" değişkenini tanımladığınızda onu kullanır.
    # Yerelde ise kendi veritabanı bilginizi buraya yazabilirsiniz.
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'mysql+pymysql://kullanici:sifre@localhost/halic_antik_db'
    
    # Uyarıları kapatmak ve performansı artırmak için
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Güvenlik için gizli anahtar
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'cok-gizli-bir-anahtar-kelime'