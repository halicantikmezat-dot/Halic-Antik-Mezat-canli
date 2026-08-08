# server.py - Hâmid Antik Mezat Yönetim Sistemi
from flask import Flask, render_template, request, jsonify
import time, os, urllib.parse

app = Flask(__name__)
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

urunler_db = []
musteriler_db = []
musteri_pey_listesi = []
on_teklifler_db = []  # İzleyicilerin gönderdiği ön teklifler havuzu
muzik_listesi = ["https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"]
aktif_urun = None
mezat_durumu = {
    "durum": "Bekliyor", 
    "sure_bitis": 0,
    "pey": 0,
    "kazanan": "Yok"
}

KATEGORILER = [
    "Mobilya ve ahşap ürünler",
    "Halı Kilim bez ve örtüler",
    "Obje ve Aksesuarlar",
    "Porselen ve Seramik",
    "Tablolar ve Resim",
    "Aydınlatma ve aksesuarları",
    "Kitap dergi resim ve efemera",
    "Takı ve bijuteri"
]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/durum-getir', methods=['GET'])
def durum_getir():
    global mezat_durumu, aktif_urun, musteri_pey_listesi
    if mezat_durumu["durum"] == "Sayim":
        kalan = mezat_durumu["sure_bitis"] - time.time()
        if kalan <= 0:
            mezat_durumu["durum"] = "Satıldı"
            if aktif_urun:
                aktif_urun["durum"] = "Satıldı"
     
    # Müşteri dosyalarını tekilleştirip gruplayalım (Aynı müşteri tek satır, aldığı ürünler ve toplam borç bir arada)
    gruplanmis_dosyalar = {}
    for p in musteri_pey_listesi:
        m_adi = p.get('musteri_adi', 'Bilinmeyen')
        if m_adi not in gruplanmis_dosyalar:
            gruplanmis_dosyalar[m_adi] = {
                "musteri_adi": m_adi,
                "urunler": [],
                "toplam_tutar": 0
            }
        gruplanmis_dosyalar[m_adi]["urunler"].append(p)
        gruplanmis_dosyalar[m_adi]["toplam_tutar"] += float(p.get('fiyat', 0))

    return jsonify({
        "durum": mezat_durumu["durum"],
        "sure_bitis": mezat_durumu["sure_bitis"],
        "pey": mezat_durumu["pey"],
        "kazanan": mezat_durumu["kazanan"],
        "aktif_urun": aktif_urun,
        "urunler": urunler_db,
        "musteriler": musteriler_db,
        "musteri_pey_listesi": musteri_pey_listesi,
        "gruplanmis_dosyalar": list(gruplanmis_dosyalar.values()),
        "on_teklifler": on_teklifler_db,
        "muzik_listesi": muzik_listesi
    })

@app.route('/kayit-ol', methods=['POST'])
def kayit_ol():
    veri = request.json
    ad, tel, mail, adres = veri.get('ad'), veri.get('tel'), veri.get('mail'), veri.get('adres')
    if not ad: return jsonify({"success": False})
     
    bulundu = False
    for m in musteriler_db:
        if m['ad'] == ad:
            m['tel'], m['mail'], m['adres'] = tel, mail, adres
            bulundu = True
            break
    if not bulundu:
        musteriler_db.append({"ad": ad, "tel": tel, "mail": mail, "adres": adres, "bonus": 0})
    return jsonify({"success": True})

@app.route('/on-teklif-ver', methods=['POST'])
def on_teklif_ver():
    global urunler_db, on_teklifler_db
    veri = request.json
    urun_id = str(veri.get('urun_id'))
    musteri_adi = veri.get('musteri_adi')
    teklif = float(veri.get('teklif', 0))
     
    hedef_urun = next((u for u in urunler_db if str(u['id']) == urun_id), None)
    if not hedef_urun:
        return jsonify({"success": False, "mesaj": "Ürün arşivde bulunamadı!"})
         
    on_teklifler_db = [t for t in on_teklifler_db if not (str(t['urun_id']) == str(urun_id) and t['musteri_adi'] == musteri_adi)]

    on_teklifler_db.insert(0, {
        "urun_id": hedef_urun['id'],
        "lot": hedef_urun.get('lot'),
        "urun_adi": hedef_urun.get('ad'),
        "musteri_adi": musteri_adi,
        "teklif": teklif,
        "zaman": time.strftime('%H:%M:%S')
    })
     
    return jsonify({"success": True, "mesaj": "Ön teklifiniz yöneticiye başarıyla iletildi."})

@app.route('/sahneye-al', methods=['POST'])
def sahneye_al():
    global mezat_durumu, aktif_urun, on_teklifler_db
    veri = request.json
    urun_id = str(veri.get('urun_id'))
    
    hedef_urun = next((u for u in urunler_db if str(u['id']) == urun_id), None)
    if not hedef_urun:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})
        
    aktif_urun = hedef_urun
    mezat_durumu['durum'] = 'Bekliyor'
    mezat_durumu['sure_bitis'] = 0
    
    urun_teklifleri = [t for t in on_teklifler_db if str(t['urun_id']) == str(urun_id)]
    if urun_teklifleri:
        en_yuksek_teklif = max(t['teklif'] for t in urun_teklifleri)
        en_yuksek_kisi = next(t['musteri_adi'] for t in urun_teklifleri if t['teklif'] == en_yuksek_teklif)
        mezat_durumu['pey'] = en_yuksek_teklif
        mezat_durumu['kazanan'] = en_yuksek_kisi
    else:
        mezat_durumu['pey'] = hedef_urun.get('fiyat', 0)
        mezat_durumu['kazanan'] = 'Yok'
    
    return jsonify({"success": True})

@app.route('/pey-ver', methods=['POST'])
def pey_ver():
    global mezat_durumu, aktif_urun, musteri_pey_listesi, on_teklifler_db
    veri = request.json
    urun_id = str(veri.get('urun_id', ''))
    musteri_adi = veri.get('musteri_adi')
    miktar = float(veri.get('miktar', 0))
    islem = veri.get('islem', 'pey')
     
    if not urun_id and aktif_urun:
        urun_id = str(aktif_urun.get('id'))
         
    hedef_urun = next((u for u in urunler_db if str(u['id']) == urun_id), None)
     
    if islem == 'talep':
        if hedef_urun:
            aktif_urun = hedef_urun
            mezat_durumu['durum'] = 'Bekliyor'
            mezat_durumu['sure_bitis'] = 0
            
            urun_teklifleri = [t for t in on_teklifler_db if str(t['urun_id']) == str(urun_id)]
            if urun_teklifleri:
                en_yuksek_teklif = max(t['teklif'] for t in urun_teklifleri)
                en_yuksek_kisi = next(t['musteri_adi'] for t in urun_teklifleri if t['teklif'] == en_yuksek_teklif)
                mezat_durumu['pey'] = en_yuksek_teklif
                mezat_durumu['kazanan'] = en_yuksek_kisi
            else:
                mezat_durumu['pey'] = hedef_urun.get('fiyat', 0)
                mezat_durumu['kazanan'] = 'Yok'
        return jsonify({"success": True})
         
    if not hedef_urun: 
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı veya sahne aktif değil!"})
         
    if islem == 'hemen_al':
        hedef_urun['durum'] = 'Satıldı'
        mezat_durumu['durum'] = 'Satıldı'
        mezat_durumu['kazanan'] = musteri_adi
        hemen_al_fiyat = float(hedef_urun.get('hemen_al_fiyat', 0))
        mezat_durumu['pey'] = hemen_al_fiyat if hemen_al_fiyat > 0 else hedef_urun.get('fiyat', 0)
         
        for m in musteriler_db:
            if m['ad'] == musteri_adi:
                m['bonus'] += int(mezat_durumu['pey'] * 0.05)
                 
        musteri_pey_listesi = [p for p in musteri_pey_listesi if not (str(p.get('urun_id')) == str(urun_id) and p.get('musteri_adi') == musteri_adi)]

        musteri_pey_listesi.insert(0, {
            "urun_id": hedef_urun['id'],
            "lot": hedef_urun.get('lot'),
            "urun_adi": hedef_urun.get('ad'),
            "musteri_adi": musteri_adi,
            "fiyat": mezat_durumu['pey'],
            "metin": f"Lot #{hedef_urun['lot']} - {hedef_urun['ad']} -> {musteri_adi} ({mezat_durumu['pey']} TL)"
        })
        return jsonify({"success": True, "kazanan": musteri_adi})
         
    if islem == 'pey':
        mevcut_fiyat = mezat_durumu['pey'] if aktif_urun and str(aktif_urun['id']) == urun_id else hedef_urun.get('fiyat', 0)
        if miktar <= mevcut_fiyat:
            return jsonify({"success": False, "mesaj": f"Teklif mevcut fiyatın ({mevcut_fiyat} TL) altında olamaz!"})
             
        eski_kazanan = mezat_durumu['kazanan']
        aktif_urun = hedef_urun
        mezat_durumu['pey'] = miktar
        mezat_durumu['kazanan'] = musteri_adi
         
        musteri_pey_listesi = [p for p in musteri_pey_listesi if not (str(p.get('urun_id')) == str(urun_id) and p.get('musteri_adi') == musteri_adi)]

        uzgun_uyari = eski_kazanan if eski_kazanan and eski_kazanan != "Yok" and eski_kazanan != musteri_adi else None
        
        musteri_pey_listesi.insert(0, {
            "urun_id": hedef_urun['id'],
            "lot": hedef_urun.get('lot'),
            "urun_adi": hedef_urun.get('ad'),
            "musteri_adi": musteri_adi,
            "fiyat": miktar,
            "metin": f"Lot #{hedef_urun['lot']} - {hedef_urun['ad']} -> {musteri_adi} ({miktar} TL)"
        })
        return jsonify({"success": True, "uzgun_uyari": uzgun_uyari})

@app.route('/urun-ekle', methods=['POST'])
def urun_ekle():
    dosyalar = request.files.getlist('dosyalar')
    fotograflar = []
    video = ""
     
    for dosya in dosyalar:
        if dosya and dosya.filename:
            yol = os.path.join(UPLOAD_FOLDER, dosya.filename)
            dosya.save(yol)
            dosya_url = f"/static/uploads/{dosya.filename}"
            if dosya.filename.lower().endswith(('.mp4', '.mov', '.avi', '.webm', '.mkv')):
                video = dosya_url
            else:
                fotograflar.append(dosya_url)

    yeni_id = str(len(urunler_db) + 1)
    yeni_urun = {
        "id": yeni_id,
        "lot": request.form.get('lot', len(urunler_db) + 1),
        "ad": request.form.get('ad'),
        "kategori": request.form.get('kategori', KATEGORILER[0]),
        "fiyat": float(request.form.get('fiyat', 0)),
        "hemen_al_fiyat": float(request.form.get('hemen_al_fiyat', 0)),
        "tanitim_yazisi": request.form.get('tanitim_yazisi', ''),
        "fotograflar": fotograflar,
        "video": video,
        "durum": "Aktif"
    }
    urunler_db.append(yeni_urun)
    return jsonify({"success": True})

@app.route('/muzik-ekle', methods=['POST'])
def muzik_ekle():
    dosya = request.files.get('muzik_dosyasi')
    if dosya and dosya.filename:
        yol = os.path.join(UPLOAD_FOLDER, dosya.filename)
        dosya.save(yol)
        muzik_listesi.append(f"/static/uploads/{dosya.filename}")
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/urun-sil', methods=['POST'])
def urun_sil():
    global urunler_db
    urun_id = str(request.json.get('id'))
    urunler_db = [u for u in urunler_db if str(u['id']) != urun_id]
    return jsonify({"success": True})

@app.route('/mezat-baslat', methods=['POST'])
def mezat_baslat():
    global mezat_durumu, aktif_urun, on_teklifler_db
    veri = request.json
    urun_id = str(veri.get('urun_id'))
    sure = int(veri.get('sure', 30)) 
     
    for u in urunler_db:
        if str(u['id']) == urun_id:
            aktif_urun = u
            mezat_durumu['durum'] = 'Sayim'
            mezat_durumu['sure_bitis'] = time.time() + sure
            
            urun_teklifleri = [t for t in on_teklifler_db if str(t['urun_id']) == str(urun_id)]
            if urun_teklifleri:
                en_yuksek_teklif = max(t['teklif'] for t in urun_teklifleri)
                en_yuksek_kisi = next(t['musteri_adi'] for t in urun_teklifleri if t['teklif'] == en_yuksek_teklif)
                mezat_durumu['pey'] = en_yuksek_teklif
                mezat_durumu['kazanan'] = en_yuksek_kisi
            else:
                mezat_durumu['pey'] = u.get('fiyat', 0)
                mezat_durumu['kazanan'] = 'Yok'
            break
    return jsonify({"success": True})

@app.route('/satis-bitir', methods=['POST'])
def satis_bitir():
    global mezat_durumu
    mezat_durumu['durum'] = 'Satıldı'
    return jsonify({"success": True})

@app.route('/on-teklif-sil', methods=['POST'])
def on_teklif_sil():
    veri = request.get_json()
    index = veri.get('index')
    try:
        if 0 <= index < len(on_teklifler_db):
            on_teklifler_db.pop(index)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/musteri-dosya-sil', methods=['POST'])
def musteri_dosya_sil():
    veri = request.get_json()
    musteri_adi = veri.get('musteri_adi')
    global musteri_pey_listesi
    try:
        # Belirli müşteriye ait tüm kazanılan ürünleri dosyadan temizle
        musteri_pey_listesi = [p for p in musteri_pey_listesi if p.get('musteri_adi') != musteri_adi]
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)