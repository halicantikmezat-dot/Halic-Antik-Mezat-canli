import os
import sqlite3
from flask import Flask, render_template, send_from_directory, request, jsonify, session

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = "mezat_gizli_anahtar_hamdullah_abi"

SAYAC_VARSAYILAN_SURE = 15

def db_kur():
    conn = sqlite3.connect("mezat_veri.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS musteriler (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ad_soyad TEXT,
                        telefon TEXT UNIQUE
                    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS peyler (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        urun_adi TEXT,
                        musteri_ad TEXT,
                        fiyat REAL,
                        tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
    conn.commit()
    conn.close()

db_kur()

MEZAT_DURUM = {
    "lot": 1,
    "urun_adi": "Osmanlı Pirinç Şamdan",
    "fiyat": 500.0,
    "son_pey_veren": "Başlangıç",
    "kalan_sure": SAYAC_VARSAYILAN_SURE,
    "peyler": []
}

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/admin.html')
def admin():
    return send_from_directory('.', 'admin.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory('.', filename)

@app.route('/api/durum', methods=['GET'])
def get_durum():
    return jsonify(MEZAT_DURUM)

@app.route('/api/urun-yukle', methods=['POST'])
def urun_yukle():
    data = request.json or {}
    MEZAT_DURUM["lot"] = data.get('lot', 1)
    MEZAT_DURUM["urun_adi"] = data.get('urun_adi', 'Ürün')
    try:
        MEZAT_DURUM["fiyat"] = float(data.get('fiyat', 0))
    except:
        pass
    MEZAT_DURUM["son_pey_veren"] = "Başlangıç"
    MEZAT_DURUM["peyler"] = []
    return jsonify({"basari": True})

@app.route('/api/pey-ver', methods=['POST'])
def pey_ver():
    data = request.json or {}
    isim = data.get('isim', 'Misafir')
    artis = float(data.get('artis', 50))
    
    MEZAT_DURUM["fiyat"] += artis
    MEZAT_DURUM["son_pey_veren"] = isim
    
    pey_bilgi = {"isim": isim, "miktar": MEZAT_DURUM["fiyat"]}
    MEZAT_DURUM["peyler"].append(pey_bilgi)

    conn = sqlite3.connect("mezat_veri.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO peyler (urun_adi, musteri_ad, fiyat) VALUES (?, ?, ?)",
                   (MEZAT_DURUM["urun_adi"], isim, MEZAT_DURUM["fiyat"]))
    conn.commit()
    conn.close()

    return jsonify({"basari": True, "yeni_fiyat": MEZAT_DURUM["fiyat"]})

@app.route('/api/son-peyi-sil', methods=['POST'])
def son_peyi_sil():
    if MEZAT_DURUM["peyler"]:
        MEZAT_DURUM["peyler"].pop()
        if MEZAT_DURUM["peyler"]:
            son = MEZAT_DURUM["peyler"][-1]
            MEZAT_DURUM["fiyat"] = son["miktar"]
            MEZAT_DURUM["son_pey_veren"] = son["isim"]
        else:
            MEZAT_DURUM["fiyat"] = 0.0
            MEZAT_DURUM["son_pey_veren"] = "Başlangıç"
    return jsonify({"basari": True})

@app.route('/api/sayac-baslat', methods=['POST'])
def sayac_baslat():
    data = request.json or {}
    MEZAT_DURUM["sayac"] = int(data.get('saniye', 15))
    return jsonify({"basari": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)