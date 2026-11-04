import requests

url = "http://127.0.0.1:5000/api/otomatik-urun-ekle"
headers = {
    "Content-Type": "application/json",
    "X-Mezat-Key": "halic_antik_gizli_token_2026"
}
veri = {
    "dukkan": "gumus",
    "ad": "Osmanli Tugrali Gumus Tabak",
    "kategori": "Gumus",
    "fiyat": 2500,
    "hemen_al_fiyat": 4000,
    "tanitim_yazisi": "Kondisyonu cok yuksek nadir parca.",
    "fotograflar": ["https://picsum.photos/600/600"]
}

yanit = requests.post(url, json=veri, headers=headers)
print("Durum Kodu:", yanit.status_code)
print("Sunucu Yanıtı:", yanit.text)