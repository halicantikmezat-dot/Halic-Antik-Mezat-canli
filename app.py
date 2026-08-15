from gevent import monkey
monkey.patch_all()

import os
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
import pandas as pd

app = Flask(__name__)

db_url = os.environ.get('DATABASE_URL', 'sqlite:///halic_mezat.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'halic_hamid_antik_mezat_gizli_anahtar_1453')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins="*")

# ==========================================
# VERİTABANI MODELLERİ
# ==========================================
class Kullanici(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ad_soyad = db.Column(db.String(100), nullable=False)
    telefon = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(100), nullable=True)
    adres = db.Column(db.Text, nullable=True)
    sifre = db.Column(db.String(50), nullable=True)
    bonus = db.Column(db.Float, default=0.0)
    puan = db.Column(db.Float, default=100.0)
    onayli_mi = db.Column(db.Boolean, default=False)
    durum = db.Column(db.String(20), default='bekliyor')
    kayit_tarihi = db.Column(db.DateTime, default=datetime.utcnow)

class Urun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lot_no = db.Column(db.Integer, nullable=False)
    urun_adi = db.Column(db.String(200), nullable=False)
    kategori = db.Column(db.String(100), default="Mobilya ve ahşap ürünler")
    acilis_fiyati = db.Column(db.Float, nullable=False, default=0.0)
    guncel_fiyat = db.Column(db.Float, nullable=False, default=0.0)
    hemen_al_fiyati = db.Column(db.Float, nullable=True, default=0.0)
    tanitim_yazisi = db.Column(db.Text, nullable=True)
    fotograflar = db.Column(db.JSON, default=list)
    video = db.Column(db.String(300), nullable=True, default="")
    ses_dosyasi = db.Column(db.String(300), nullable=True, default="")
    durum = db.Column(db.String(20), default="Aktif")
    kazanan_adi = db.Column(db.String(100), nullable=True, default="Yok")

class OnTeklif(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey('urun.id'), nullable=False)
    musteri_adi = db.Column(db.String(100), nullable=False)
    teklif = db.Column(db.Float, nullable=False)
    zaman = db.Column(db.String(20), default=lambda: time.strftime('%H:%M:%S'))

class Teklif(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey('urun.id'), nullable=False)
    musteri_adi = db.Column(db.String(100), nullable=False)
    tutar = db.Column(db.Float, nullable=False)
    tarih = db.Column(db.DateTime, default=datetime.utcnow)

class Muzik(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(300), nullable=False)

def urun_to_dict(self):
    return {
        "id": self.id,
        "lot": self.lot_no,
        "ad": self.urun_adi,
        "kategori": self.kategori,
        "fiyat": self.acilis_fiyati,
        "guncel_fiyat": self.guncel_fiyat,
        "hemen_al_fiyat": self.hemen_al_fiyati,
        "tanitim_yazisi": self.tanitim_yazisi,
        "fotograflar": self.fotograflar or [],
        "video": self.video or "",
        "ses": self.ses_dosyasi or "",
        "durum": self.durum,
        "kazanan": self.kazanan_adi
    }
Urun.to_dict = urun_to_dict

# Global durum değişkenleri
aktif_izleyici_sayisi = 0
aktif_urun_id = None
sayac_kalan = 0
sayac_aktif = False
mevcut_sayac_id = 0

mezat_durumu = {
    "durum": "Bekliyor", 
    "sure_bitis": 0,
    "pey": 0,
    "kazanan": "Yok"
}

def geri_sayim_gorevi(saniye, gorev_id):
    global sayac_kalan, sayac_aktif, mezat_durumu, aktif_urun_id
    sayac_kalan = saniye
    
    while sayac_kalan > 0 and sayac_aktif and gorev_id == mevcut_sayac_id:
        socketio.emit('sayac_guncelle', {'kalan': sayac_kalan})
        socketio.sleep(1)
        sayac_kalan -= 1
        
    # Sadece sayaç iptal edilmediyse ve en son görev buysa bitir
    if sayac_aktif and gorev_id == mevcut_sayac_id:
        sayac_aktif = False
        mezat_durumu["durum"] = "Satıldı"
        with app.app_context():
            if aktif_urun_id:
                urun = Urun.query.get(aktif_urun_id)
                if urun:
                    urun.durum = "Satıldı"
                    urun.kazanan_adi = mezat_durumu["kazanan"]
                    db.session.commit()
        
        socketio.emit('sayac_bitti', {
            'mesaj': 'Süre doldu!',
            'kazanan': mezat_durumu["kazanan"],
            'fiyat': mezat_durumu["pey"]
        })
        socketio.emit('veri_guncellendi')

@app.route('/')
def izleyici_index():
    return render_template('index.html')

@app.route('/admin', methods=['GET'])
def admin_panel():
    sifre = request.args.get('sifre')
    if sifre != '1453':
        return "<h3 style='color:red; text-align:center;'>Yetkisiz erişim! Yönetici şifresi gerekli. (?sifre=1453)</h3>", 403
    return render_template('admin.html')

@app.route('/durum-getir', methods=['GET'])
def durum_getir():
    global mezat_durumu, aktif_urun_id
    try:
        if mezat_durumu["durum"] == "Sayim":
            kalan = mezat_durumu["sure_bitis"] - time.time()
            if kalan <= 0:
                mezat_durumu["durum"] = "Satıldı"
                if aktif_urun_id:
                    urun = Urun.query.get(aktif_urun_id)
                    if urun:
                        urun.durum = "Satıldı"
                        urun.kazanan_adi = mezat_durumu["kazanan"]
                        db.session.commit()

        aktif_urun_obj = Urun.query.get(aktif_urun_id) if aktif_urun_id else None
        aktif_urun = aktif_urun_obj.to_dict() if aktif_urun_obj else None
        urunler = [u.to_dict() for u in Urun.query.all()]
        
        musteriler = [{
            "id": m.id,
            "ad": m.ad_soyad or "İsimsiz", 
            "tel": m.telefon or "-", 
            "mail": m.email or "-", 
            "adres": m.adres or "-", 
            "bonus": m.bonus or 0.0,
            "puan": m.puan if m.puan is not None else 100.0,
            "onayli_mi": bool(m.onayli_mi),
            "durum": m.durum or "bekliyor"
        } for m in Kullanici.query.all()]
        
        satilan_urunler = Urun.query.filter_by(durum="Satıldı").all()
        gruplanmis_dosyalar = {}

        for u in satilan_urunler:
            m_adi = u.kazanan_adi or "Bilinmeyen"
            if m_adi == "Yok": continue
                
            if m_adi not in gruplanmis_dosyalar:
                gruplanmis_dosyalar[m_adi] = {
                    "musteri_adi": m_adi,
                    "urunler": [],
                    "toplam_tutar": 0.0
                }
            
            satiss_fiyati = float(u.guncel_fiyat or u.acilis_fiyati)
            gruplanmis_dosyalar[m_adi]["urunler"].append({
                "urun_id": u.id,
                "lot": u.lot_no,
                "urun_adi": u.urun_adi,
                "musteri_adi": m_adi,
                "fiyat": satiss_fiyati
            })
            gruplanmis_dosyalar[m_adi]["toplam_tutar"] += satiss_fiyati

        on_teklifler = []
        for ot in OnTeklif.query.order_by(OnTeklif.id.desc()).all():
            u = Urun.query.get(ot.urun_id)
            on_teklifler.append({
                "urun_id": ot.urun_id,
                "lot": u.lot_no if u else "-",
                "urun_adi": u.urun_adi if u else "Arşivlenmiş Ürün",
                "musteri_adi": ot.musteri_adi,
                "teklif": ot.teklif,
                "zaman": ot.zaman
            })

        muzikler = [m.url for m in Muzik.query.all()]

        return jsonify({
            "durum": mezat_durumu["durum"],
            "sure_bitis": mezat_durumu["sure_bitis"],
            "pey": mezat_durumu["pey"],
            "kazanan": mezat_durumu["kazanan"],
            "aktif_urun": aktif_urun,
            "urunler": urunler,
            "musteriler": musteriler,
            "satilan_urunler": [u.to_dict() for u in satilan_urunler],
            "gruplanmis_dosyalar": list(gruplanmis_dosyalar.values()),
            "on_teklifler": on_teklifler,
            "muzik_listesi": muzikler
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/kayit-ol', methods=['POST'])
def kayit_ol():
    veri = request.json or {}
    ad = veri.get('ad')
    tel = veri.get('tel')
    mail = veri.get('mail')
    adres = veri.get('adres')
    
    if not ad:
        return jsonify({"success": False, "mesaj": "Ad soyad zorunludur."})
      
    kullanici = Kullanici.query.filter_by(ad_soyad=ad).first()
    if kullanici:
        kullanici.telefon = tel
        kullanici.email = mail
        kullanici.adres = adres
    else:
        kullanici = Kullanici(ad_soyad=ad, telefon=tel, email=mail, adres=adres, onayli_mi=True, durum='onayli')
        db.session.add(kullanici)
    
    db.session.commit()
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True, "mesaj": "Kayıt talebiniz başarıyla alındı."})

@app.route('/musteri-durum-guncelle', methods=['POST'])
def musteri_durum_guncelle():
    veri = request.json or {}
    kullanici_id = veri.get('kullanici_id')
    yeni_durum = veri.get('durum')
    
    kullanici = Kullanici.query.get(kullanici_id)
    if kullanici:
        kullanici.durum = yeni_durum
        kullanici.onayli_mi = (yeni_durum == 'onayli')
        db.session.commit()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False, "mesaj": "Kullanıcı bulunamadı."})

@app.route('/musteri-sil', methods=['POST'])
def musteri_sil():
    veri = request.json or {}
    kullanici_id = veri.get('kullanici_id')
    kullanici = Kullanici.query.get(kullanici_id)
    if kullanici:
        db.session.delete(kullanici)
        db.session.commit()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/urun-ekle', methods=['POST'])
def urun_ekle():
    dosyalar = request.files.getlist('dosyalar')
    fotograflar = []
    video_url = ""
    ses_url = ""
      
    for dosya in dosyalar:
        if dosya and dosya.filename:
            dosya_yolu = os.path.join(app.config['UPLOAD_FOLDER'], dosya.filename)
            dosya.save(dosya_yolu)
            url = f"/static/uploads/{dosya.filename}"
            ext = dosya.filename.lower()
            if ext.endswith(('.mp4', '.mov', '.avi', '.webm', '.mkv')):
                video_url = url
            elif ext.endswith(('.mp3', '.wav', '.ogg', '.m4a')):
                ses_url = url
            else:
                fotograflar.append(url)

    mevcut_urun_sayisi = Urun.query.count()
    lot_no = int(request.form.get('lot') or (mevcut_urun_sayisi + 1))
    
    yeni_urun = Urun(
        lot_no=lot_no,
        urun_adi=request.form.get('ad', 'İsimsiz Ürün'),
        kategori=request.form.get('kategori', 'Mobilya ve ahşap ürünler'),
        acilis_fiyati=float(request.form.get('fiyat') or 0),
        guncel_fiyat=float(request.form.get('fiyat') or 0),
        hemen_al_fiyati=float(request.form.get('hemen_al_fiyat') or 0),
        tanitim_yazisi=request.form.get('tanitim_yazisi', ''),
        fotograflar=fotograflar,
        video=video_url,
        ses_dosyasi=ses_url,
        durum="Aktif"
    )
    
    db.session.add(yeni_urun)
    db.session.commit()
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True})

@app.route('/urun-sil', methods=['POST'])
def urun_sil():
    veri = request.json or {}
    urun_id = veri.get('id')
    urun = Urun.query.get(urun_id)
    if urun:
        Teklif.query.filter_by(urun_id=urun_id).delete()
        OnTeklif.query.filter_by(urun_id=urun_id).delete()
        db.session.delete(urun)
        db.session.commit()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/sahneye-al', methods=['POST'])
def sahneye_al():
    global mezat_durumu, aktif_urun_id
    veri = request.json or {}
    raw_id = veri.get('urun_id')
    
    try:
        urun_id = int(raw_id)
    except:
        return jsonify({"success": False, "mesaj": "Geçersiz ürün ID!"})
    
    urun = Urun.query.get(urun_id)
    if not urun:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})
        
    aktif_urun_id = urun.id
    mezat_durumu['durum'] = 'Bekliyor'
    mezat_durumu['sure_bitis'] = 0
    
    en_yuksek_ot = OnTeklif.query.filter_by(urun_id=urun_id).order_by(OnTeklif.teklif.desc()).first()
    if en_yuksek_ot:
        mezat_durumu['pey'] = en_yuksek_ot.teklif
        mezat_durumu['kazanan'] = en_yuksek_ot.musteri_adi
        urun.guncel_fiyat = en_yuksek_ot.teklif
    else:
        mezat_durumu['pey'] = urun.acilis_fiyati
        mezat_durumu['kazanan'] = 'Yok'
        urun.guncel_fiyat = urun.acilis_fiyati
        
    db.session.commit()
    socketio.emit('veri_guncellendi')
    socketio.emit('pey_guncellendi', {'guncel_fiyat': mezat_durumu['pey'], 'kazanan_ad': mezat_durumu['kazanan']})
    return jsonify({"success": True})

@app.route('/on-teklif-ver', methods=['POST'])
def on_teklif_ver():
    veri = request.json or {}
    raw_uid = veri.get('urun_id')
    musteri_adi = veri.get('musteri_adi')
    teklif_tutari = float(veri.get('teklif', 0))

    try:
        urun_id = int(raw_uid)
    except:
        return jsonify({"success": False, "mesaj": "Geçersiz ürün ID!"})

    urun = Urun.query.get(urun_id)
    if not urun:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})
        
    if teklif_tutari < urun.acilis_fiyati:
        return jsonify({"success": False, "mesaj": f"Ön teklif açılış fiyatından ({urun.acilis_fiyati} TL) düşük olamaz!"})
          
    eski_teklif = OnTeklif.query.filter_by(urun_id=urun_id, musteri_adi=musteri_adi).first()
    if eski_teklif:
        eski_teklif.teklif = teklif_tutari
        eski_teklif.zaman = time.strftime('%H:%M:%S')
    else:
        yeni_ot = OnTeklif(urun_id=urun_id, musteri_adi=musteri_adi, teklif=teklif_tutari)
        db.session.add(yeni_ot)
        
    db.session.commit()
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True, "mesaj": "Ön teklifiniz alındı."})

@app.route('/on-teklif-sil', methods=['POST'])
def on_teklif_sil():
    veri = request.json or {}
    index = veri.get('index')
    try:
        ot_list = OnTeklif.query.order_by(OnTeklif.id.desc()).all()
        if index is not None and 0 <= index < len(ot_list):
            db.session.delete(ot_list[index])
            db.session.commit()
            socketio.emit('veri_guncellendi')
            return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    return jsonify({'success': False})

@app.route('/pey-ver', methods=['POST'])
def pey_ver():
    global mezat_durumu, aktif_urun_id
    veri = request.json or {}
    raw_uid = veri.get('urun_id') or aktif_urun_id
    try:
        urun_id = int(raw_uid) if raw_uid else aktif_urun_id
    except:
        urun_id = aktif_urun_id

    if not urun_id:
        ilk = Urun.query.filter(Urun.durum != 'Satıldı').first()
        if ilk:
            urun_id = ilk.id
            aktif_urun_id = ilk.id

    musteri_adi = veri.get('musteri_adi') or veri.get('isim', 'Misafir')
    artis = float(veri.get('artis', 0))
    miktar = float(veri.get('miktar', 0))
    if artis > 0:
        mevcut = mezat_durumu['pey'] if mezat_durumu['pey'] > 0 else (urun.acilis_fiyati if urun else 0)
        miktar = mevcut + artis

    islem = veri.get('islem', 'pey')

    urun = Urun.query.get(urun_id) if urun_id else None
    if not urun:
        return jsonify({"success": False, "mesaj": "Sahnede aktif ürün bulunamadı!"})

    if islem == 'hemen_al':
        hemen_al_fiyat = urun.hemen_al_fiyati if (urun.hemen_al_fiyati and urun.hemen_al_fiyati > 0) else urun.acilis_fiyati
        urun.durum = 'Satıldı'
        urun.guncel_fiyat = hemen_al_fiyat
        urun.kazanan_adi = musteri_adi
        
        mezat_durumu['durum'] = 'Satıldı'
        mezat_durumu['kazanan'] = musteri_adi
        mezat_durumu['pey'] = hemen_al_fiyat
            
        yeni_teklif = Teklif(urun_id=urun.id, musteri_adi=musteri_adi, tutar=hemen_al_fiyat)
        db.session.add(yeni_teklif)
        db.session.commit()
        
        socketio.emit('pey_guncellendi', {'guncel_fiyat': hemen_al_fiyat, 'kazanan_ad': musteri_adi})
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True, "kazanan": musteri_adi})
          
    if islem == 'pey':
        mevcut_fiyat = mezat_durumu['pey'] if mezat_durumu['pey'] > 0 else urun.acilis_fiyati
        if miktar <= mevcut_fiyat:
            return jsonify({"success": False, "mesaj": f"Teklif mevcut fiyatın ({mevcut_fiyat} TL) üzerinde olmalıdır!"})
            
        mezat_durumu['pey'] = miktar
        mezat_durumu['kazanan'] = musteri_adi
        urun.guncel_fiyat = miktar
        
        yeni_teklif = Teklif(urun_id=urun.id, musteri_adi=musteri_adi, tutar=miktar)
        db.session.add(yeni_teklif)
        db.session.commit()
        
        socketio.emit('pey_guncellendi', {
            'urun_id': urun.id,
            'guncel_fiyat': miktar,
            'kazanan_ad': musteri_adi
        })
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})

@app.route('/mezat-baslat', methods=['POST'])
def mezat_baslat():
    global mezat_durumu, aktif_urun_id, sayac_aktif, mevcut_sayac_id
    veri = request.json or {}
    urun_id = veri.get('urun_id', aktif_urun_id)
    sure = int(veri.get('sure', 30))
      
    if urun_id:
        aktif_urun_id = urun_id
        mezat_durumu['durum'] = 'Sayim'
        mezat_durumu['sure_bitis'] = time.time() + sure
        
        # Önceki sayacı iptal et ve yeni sayaç kimliği ata
        sayac_aktif = False
        socketio.sleep(0.05)
        
        mevcut_sayac_id += 1
        sayac_aktif = True
        socketio.start_background_task(geri_sayim_gorevi, sure, mevcut_sayac_id)
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
        
    return jsonify({"success": False, "mesaj": "Ürün seçilmedi!"})

@app.route('/son-peyi-iptal-et', methods=['POST'])
def son_peyi_iptal_et():
    global mezat_durumu, aktif_urun_id
    if aktif_urun_id:
        son_teklif = Teklif.query.filter_by(urun_id=aktif_urun_id).order_by(Teklif.id.desc()).first()
        if son_teklif:
            db.session.delete(son_teklif)
            db.session.commit()
            
            yeni_son = Teklif.query.filter_by(urun_id=aktif_urun_id).order_by(Teklif.id.desc()).first()
            if yeni_son:
                mezat_durumu['pey'] = yeni_son.tutar
                mezat_durumu['kazanan'] = yeni_son.musteri_adi
            else:
                urun = Urun.query.get(aktif_urun_id)
                mezat_durumu['pey'] = urun.acilis_fiyati if urun else 0
                mezat_durumu['kazanan'] = 'Yok'
                
            socketio.emit('pey_guncellendi', {'guncel_fiyat': mezat_durumu['pey'], 'kazanan_ad': mezat_durumu['kazanan']})
            socketio.emit('veri_guncellendi')
            return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/satis-bitir', methods=['POST'])
def satis_bitir():
    global mezat_durumu, aktif_urun_id, sayac_aktif
    sayac_aktif = False
    mezat_durumu['durum'] = 'Satıldı'
    if aktif_urun_id:
        urun = Urun.query.get(aktif_urun_id)
        if urun:
            urun.durum = "Satıldı"
            urun.kazanan_adi = mezat_durumu['kazanan']
            db.session.commit()
            
    socketio.emit('sayac_bitti', {
        'urun_id': aktif_urun_id,
        'kazanan': mezat_durumu['kazanan'],
        'fiyat': mezat_durumu['pey']
    })
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True})

@app.route('/muzik-ekle', methods=['POST'])
def muzik_ekle():
    dosya = request.files.get('muzik_dosyasi')
    if dosya and dosya.filename:
        dosya_yolu = os.path.join(app.config['UPLOAD_FOLDER'], dosya.filename)
        dosya.save(dosya_yolu)
        url = f"/static/uploads/{dosya.filename}"
        
        yeni_muzik = Muzik(url=url)
        db.session.add(yeni_muzik)
        db.session.commit()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/musteri-dosya-sil', methods=['POST'])
def musteri_dosya_sil():
    veri = request.json or {}
    musteri_adi = veri.get('musteri_adi')
    try:
        Teklif.query.filter_by(musteri_adi=musteri_adi).delete()
        satilanlar = Urun.query.filter_by(kazanan_adi=musteri_adi, durum="Satıldı").all()
        for u in satilanlar:
            u.kazanan_adi = "Yok"
            u.durum = "Arşiv"
        db.session.commit()
        socketio.emit('veri_guncellendi')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/excel-indir', methods=['GET'])
def excel_indir():
    try:
        dosya_yolu = "mezat_raporu.xlsx"
        satilanlar = Urun.query.filter_by(durum="Satıldı").all()
        musteriler = Kullanici.query.all()
        
        with pd.ExcelWriter(dosya_yolu, engine='openpyxl') as writer:
            if satilanlar:
                df_sat = [{"Lot No": s.lot_no, "Ürün Adı": s.urun_adi, "Satış Fiyatı (TL)": s.guncel_fiyat, "Alan Müşteri": s.kazanan_adi} for s in satilanlar]
                pd.DataFrame(df_sat).to_excel(writer, sheet_name='Satilan_Urunler', index=False)

            if musteriler:
                df_mus = [{"Ad Soyad": m.ad_soyad, "Telefon": m.telefon, "E-posta": m.email, "Adres": m.adres} for m in musteriler]
                pd.DataFrame(df_mus).to_excel(writer, sheet_name='Musteriler', index=False)
        
        return send_file(dosya_yolu, as_attachment=True)
    except Exception as e:
        return str(e), 500

@socketio.on('connect')
def handle_connect():
    global aktif_izleyici_sayisi
    aktif_izleyici_sayisi += 1
    socketio.emit('izleyici_sayisi_guncelle', {'sayi': aktif_izleyici_sayisi})

@socketio.on('disconnect')
def handle_disconnect():
    global aktif_izleyici_sayisi
    if aktif_izleyici_sayisi > 0:
        aktif_izleyici_sayisi -= 1
    socketio.emit('izleyici_sayisi_guncelle', {'sayi': aktif_izleyici_sayisi})

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)