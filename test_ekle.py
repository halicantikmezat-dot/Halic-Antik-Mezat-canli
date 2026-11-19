import os
import requests

try:
    from config import Config
except ImportError:
    Config = None

BASE_URL = (os.environ.get("MEZAT_URL") or "http://127.0.0.1:5000").rstrip("/")
MEZAT_KEY = (
    os.environ.get("MEZAT_API_KEY")
    or (getattr(Config, "MEZAT_API_KEY", None) if Config else None)
    or "halic_antik_gizli_token_2026"
)

url = f"{BASE_URL}/api/otomatik-urun-ekle"
headers = {
    "Content-Type": "application/json",
    "X-Mezat-Key": MEZAT_KEY,
}
veri = {
    "dukkan": "gumus",
    "ad": "Osmanli Tugrali Gumus Tabak",
    "kategori": "Gumus",
    "fiyat": 2500,
    "hemen_al_fiyat": 4000,
    "tanitim_yazisi": "Kondisyonu cok yuksek nadir parca.",
    "fotograflar": ["https://picsum.photos/600/600"],
}

try:
    yanit = requests.post(url, json=veri, headers=headers, timeout=10)
    print("Durum Kodu:", yanit.status_code)
    print("Sunucu Yanıtı:", yanit.text)
except requests.RequestException as hata:
    print("❌ Sunucuya bağlanılamadı:", hata)