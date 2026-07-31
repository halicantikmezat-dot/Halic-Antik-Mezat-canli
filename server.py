from flask import Flask, render_template, request, jsonify
import time

app = Flask(__name__)

mezat_verisi = {
    "urunler": [
        {"id": 1, "lot": 1, "ad": "Osmanlı Gümüş Şamdan", "acilis": 100.0, "hemen_al": 500.0, "gorsel": ""}
    ],
    "satilan_urunler": [],
    "aktif_urun": None,
    "pey": 0.0,
    "kazanan": "Yok",
    "durum": "Bekliyor", 
    "sure_bitis": 0,
    "kayitli_musteriler": [],
    "musteri_pey_listesi": []
}

# Başlangıçta ilk ürünü sahneye ve aktife alalım
if mezat_verisi["urunler"]:
    mezat_verisi["aktif_urun"] = mezat_verisi["urunler"][0]
    mezat_verisi["pey"] = mezat_verisi["aktif_urun"]["acilis"]

@app.route('/')
def izleyici():
    return render_template('index.html')

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/durum-getir')
def durum_getir():
    try:
        if mezat_verisi["durum"] == "Sayim":
            kalan = mezat_verisi["sure_bitis"] - time.time()
            if kalan <= 0:
                mezat_verisi["durum"] = "Satıldı"
                if mezat_verisi["aktif_urun"] and mezat_verisi["kazanan"] != "Yok":
                    satilan_bilgi = {
                        "lot": mezat_verisi["aktif_urun"]["lot"],
                        "ad": mezat_verisi["aktif_urun"]["ad"],
                        "fiyat": mezat_verisi["pey"],
                        "alan": mezat_verisi["kazanan"],
                        "adres": mezat_verisi.get("kazanan_adres", "Adres belirtilmedi")
                    }
                    if satilan_bilgi not in mezat_verisi["satilan_urunler"]:
                        mezat_verisi["satilan_urunler"].append(satilan_bilgi)
                    
                    mezat_verisi["urunler"] = [u for u in mezat_verisi["urunler"] if u['id'] != mezat_verisi["aktif_urun"]["id"]]
                    mezat_verisi["aktif_urun"] = None
    except Exception as e:
        print("Durum getirme hatası:", e)
        
    return jsonify(mezat_verisi)

@app.route('/kayit-ol', methods=['POST'])
def kayit_ol():
    try:
        data = request.json or {}
        ad = data.get('ad')
        tel = data.get('tel')
        mail = data.get('mail')
        adres = data.get('adres')
        
        if mail and adres:
            musteri = {"ad": ad, "tel": tel, "mail": mail, "adres": adres}
            if musteri not in mezat_verisi["kayitli_musteriler"]:
                mezat_verisi["kayitli_musteriler"].append(musteri)
            return jsonify({"success": True})
        return jsonify({"success": False, "mesaj": "E-posta ve Açık Adres zorunludur!"})
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)})

@app.route('/pey-ver', methods=['POST'])
def pey_ver_islem():
    try:
        data = request.json or {}
        urun_id = data.get('urun_id')
        musteri_adi = data.get('musteri_adi', 'Misafir Müşteri')
        eklenen_pey = float(data.get('miktar', 0))
        islem_tipi = data.get('islem', 'pey')

        # Hedef ürünü bul (Eğer urun_id belirtilmemişse aktif ürünü baz al)
        hedef_urun = None
        if not urun_id and mezat_verisi["aktif_urun"]:
            hedef_urun = mezat_verisi["aktif_urun"]
        elif urun_id:
            for u in mezat_verisi["urunler"]:
                if u['id'] == int(urun_id):
                    hedef_urun = u
                    break
            # Eğer ürün ana listede yoksa ama aktif ürünse
            if not hedef_urun and mezat_verisi["aktif_urun"] and mezat_verisi["aktif_urun"]["id"] == int(urun_id):
                hedef_urun = mezat_verisi["aktif_urun"]

        if not hedef_urun:
            return jsonify({"success": False, "mesaj": "Geçerli bir ürün bulunamadı!"})

        # Eğer izleyici farklı bir ürüne talep/pey attıysa, o ürünü otomatik olarak aktif (sahneye) taşıyalım
        if mezat_verisi["aktif_urun"] is None or mezat_verisi["aktif_urun"]["id"] != hedef_urun["id"]:
            mezat_verisi["aktif_urun"] = hedef_urun
            mezat_verisi["pey"] = hedef_urun['acilis']
            mezat_verisi["kazanan"] = "Yok"
            mezat_verisi["durum"] = "Bekliyor"

        # Pey miktarını güncelle
        if islem_tipi == 'hemen_al':
            mezat_verisi["pey"] = hedef_urun['hemen_al']
            mezat_verisi["kazanan"] = musteri_adi + " (HEMEN ALDI!)"
            mezat_verisi["durum"] = "Satıldı"
        elif eklenen_pey > 0:
            mezat_verisi["pey"] += eklenen_pey
            mezat_verisi["kazanan"] = musteri_adi
            mezat_verisi["durum"] = "Devam Ediyor"
        else:
            # Sadece sahneye alma / talep bırakma durumu
            mezat_verisi["kazanan"] = musteri_adi

        # Müşteri pey listesine ekle (Tekrarı önlemek veya anlık göstermek için)
        talep_kaydi = {
            "musteri": musteri_adi,
            "urun_id": hedef_urun['id'],
            "lot": hedef_urun['lot'],
            "urun_ad": hedef_urun['ad'],
            "fiyat": mezat_verisi["pey"]
        }
        mezat_verisi["musteri_pey_listesi"].append(talep_kaydi)

        return jsonify({"success": True, "yeni_pey": mezat_verisi["pey"]})
    except Exception as e:
        print("Pey verme hatası:", e)
        return jsonify({"success": False, "mesaj": "Sunucu işlem hatası!"})

@app.route('/admin-islem', methods=['POST'])
def admin_islem():
    try:
        data = request.json or {}
        islem = data.get('islem')
        
        if islem == 'urun_ekle':
            yeni_lot = len(mezat_verisi["urunler"]) + len(mezat_verisi["satilan_urunler"]) + 1 
            yeni_id = max([u['id'] for u in mezat_verisi["urunler"]], default=0) + 1
            mezat_verisi["urunler"].append({
                "id": yeni_id, 
                "lot": int(data.get('lot', yeni_lot)), 
                "ad": data.get('ad'),
                "acilis": float(data.get('acilis', 100)), 
                "hemen_al": float(data.get('hemen_al', 500)), 
                "gorsel": data.get('gorsel', '')
            })
        elif islem == 'urun_sil':
            sil_id = int(data.get('urun_id'))
            mezat_verisi["urunler"] = [u for u in mezat_verisi["urunler"] if u['id'] != sil_id]
            if mezat_verisi["aktif_urun"] and mezat_verisi["aktif_urun"]['id'] == sil_id:
                mezat_verisi["aktif_urun"] = None
                mezat_verisi["durum"] = "Bekliyor"
        elif islem == 'sahneye_al':
            s_id = int(data.get('urun_id'))
            for u in mezat_verisi["urunler"]:
                if u['id'] == s_id:
                    mezat_verisi["aktif_urun"] = u
                    mezat_verisi["pey"] = u['acilis']
                    mezat_verisi["kazanan"] = "Yok"
                    mezat_verisi["durum"] = "Bekliyor"
                    break
        elif islem == 'mezat_baslat':
            if mezat_verisi["aktif_urun"]:
                saniye = int(data.get('saniye', 20))
                mezat_verisi["durum"] = "Sayim"
                mezat_verisi["sure_bitis"] = time.time() + saniye
        elif islem == 'erken_kapat':
            mezat_verisi["durum"] = "Satıldı"
            if mezat_verisi["aktif_urun"]:
                satilan_bilgi = {
                    "lot": mezat_verisi["aktif_urun"]["lot"],
                    "ad": mezat_verisi["aktif_urun"]["ad"],
                    "fiyat": mezat_verisi["pey"],
                    "alan": mezat_verisi["kazanan"],
                    "adres": mezat_verisi.get("kazanan_adres", "Adres belirtilmedi")
                }
                mezat_verisi["satilan_urunler"].append(satilan_bilgi)
                mezat_verisi["urunler"] = [u for u in mezat_verisi["urunler"] if u['id'] != mezat_verisi["aktif_urun"]["id"]]
                mezat_verisi["aktif_urun"] = None

        return jsonify({"success": True})
    except Exception as e:
        print("Admin işlem hatası:", e)
        return jsonify({"success": False, "mesaj": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)