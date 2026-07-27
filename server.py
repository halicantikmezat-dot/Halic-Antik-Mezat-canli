import os
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

# Örnek bellek içi veri yapısı (mevcut sisteminize göre uyarlanmıştır)
mezat_durumu = {
    "lot": "1",
    "urun_adi": "Ürün Yok",
    "fiyat": 0,
    "sayac": 0,
    "peyler": []
}

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/admin.html')
def admin():
    return send_from_directory('.', 'admin.html')

@app.route('/api/durum', methods=['GET'])
def durum_getir():
    return jsonify(mezat_durumu)

@app.route('/api/urun-yukle', methods=['POST'])
def urun_yukle():
    data = request.json
    mezat_durumu['lot'] = data.get('lot', '1')
    mezat_durumu['urun_adi'] = data.get('urun_adi', 'Ürün Yok')
    mezat_durumu['fiyat'] = int(data.get('fiyat', 0))
    mezat_durumu['peyler'] = [] # Yeni ürünle birlikte eski peyler sıfırlanır
    return jsonify({"success": True})

@app.route('/api/pey-ver', methods=['POST'])
def pey_ver():
    data = request.json
    isim = data.get('isim', 'Sistem')
    artis = int(data.get('artis', 0))
    mezat_durumu['fiyat'] += artis
    mezat_durumu['peyler'].append({"isim": isim, "miktar": mezat_durumu['fiyat']})
    return jsonify({"success": True, "yeni_fiyat": mezat_durumu['fiyat']})

@app.route('/api/son-peyi-sil', methods=['POST'])
def son_peyi_sil():
    if mezat_durumu['peyler']:
        mezat_durumu['peyler'].pop()
        if mezat_durumu['peyler']:
            mezat_durumu['fiyat'] = mezat_durumu['peyler'][-1]['miktar']
        else:
            mezat_durumu['fiyat'] = 0
    return jsonify({"success": True})

@app.route('/api/sayac-baslat', methods=['POST'])
def sayac_baslat():
    data = request.json
    mezat_durumu['sayac'] = int(data.get('saniye', 10))
    return jsonify({"success": True})

if __name__ == '__main__':
    # Render ve yerel ortam için port ayarı
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)