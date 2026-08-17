from gevent import monkey
monkey.patch_all()

import os
import time
import hashlib
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from sqlalchemy import text
import pandas as pd

app = Flask(__name__)

db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'halic_hamid_antik_mezat_gizli_anahtar_1453')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///halic_mezat.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

if os.path.exists('/var/data'):
    UPLOAD_FOLDER = '/var/data/uploads'
else:
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static', 'uploads')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins="*")

def get_client_ip():
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    return request.remote_addr or "127.0.0.1"

def get_device_fingerprint():
    ip = get_client_ip()
    user_agent = request.headers.get('User-Agent', '')
    accept_lang = request.headers.get('Accept-Language', '')
    raw_str = f"{ip}-{user_agent}-{accept_lang}"
    return hashlib.sha256(raw_str.encode('utf-8')).hexdigest()

# ==========================================
# VERİTABANI MODELLERİ
# ==========================================
class Kullanici(db.Model):
    __tablename__ = 'kullanici'
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
    ip_adresi = db.Column(db.String(50), nullable=True)
    cihaz_kodu = db.Column(db.String(64), nullable=True)
    kayit_tarihi = db.Column(db.DateTime, default=datetime.utcnow)

class Urun(db.Model):
    __tablename__ = 'urun'
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
    __tablename__ = 'on_teklif'
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey('urun.id'), nullable=False)
    musteri_adi = db.Column(db.String(100), nullable=False)
    teklif = db.Column(db.Float, nullable=False)
    zaman = db.Column(db.String(20), default=lambda: time.strftime('%H:%M:%S'))

class Teklif(db.Model):
    __tablename__ = 'teklif'
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey('urun.id'), nullable=False)
    musteri_adi = db.Column(db.String(100), nullable=False)
    tutar = db.Column(db.Float, nullable=False)
    ip_adresi = db.Column(db.String(50), nullable=True)
    tarih = db.Column(db.DateTime, default=datetime.utcnow)

class SikayetOneri(db.Model):
    __tablename__ = 'sikayet_oneri'
    id = db.Column(db.Integer, primary_key=True)
    musteri_adi = db.Column(db.String(100), nullable=True)
    tur = db.Column(db.String(50), default="Görüş / Tavsiye")
    konu = db.Column(db.String(200), nullable=False)
    mesaj = db.Column(db.Text, nullable=False)
    durum = db.Column(db.String(20), default="Yeni")
    ip_adresi = db.Column(db.String(50), nullable=True)
    tarih = db.Column(db.DateTime, default=datetime.utcnow)

class Muzik(db.Model):
    __tablename__ = 'muzik'
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(300), nullable=False)

def urun_to_dict(self):
    return {
        "id": self.id,
        "lot": self.lot_no,
        "ad": self.urun_adi,
        "kategori": self.kategori or "Genel",
        "fiyat": self.acilis_fiyati or 0,
        "guncel_fiyat": self.guncel_fiyat or self.acilis_fiyati or 0,
        "hemen_al_fiyat": self.hemen_al_fiyati or 0,
        "tanitim_yazisi": self.tanitim_yazisi or "",
        "fotograflar": self.fotograflar or [],
        "video": self.video or "",
        "ses": self.ses_dosyasi or "",
        "durum": self.durum or "Aktif",
        "kazanan": self.kazanan_adi or "Yok"
    }
Urun.to_dict = urun_to_dict

aktif_izleyici_sayisi = 0
aktif_urun_id = None
sayac_kalan = 0
sayac_aktif = False

mezat_durumu = {
    "durum": "Bekliyor",
    "sure_bitis": 0,
    "pey": 0,
    "kazanan": "Yok"
}

# ==========================================
# POSTGRESQL OTOMATİK SÜTUN ONARICI (MİGRATİON)
# ==========================================
def veritabani_tablolari_onar():
    with app.app_context():
        db.create_all()
        kolonlar = [
            ("kullanici", "ip_adresi", "VARCHAR(50)"),
            ("kullanici", "cihaz_kodu", "VARCHAR(64)"),
            ("kullanici", "puan", "FLOAT DEFAULT 100.0"),
            ("kullanici", "bonus", "FLOAT DEFAULT 0.0"),
            ("kullanici", "durum", "VARCHAR(20) DEFAULT 'bekliyor'"),
            ("kullanici", "onayli_mi", "BOOLEAN DEFAULT FALSE"),
            ("kullanici", "kayit_tarihi", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("urun", "hemen_al_fiyati", "FLOAT DEFAULT 0.0"),
            ("urun", "kazanan_adi", "VARCHAR(100) DEFAULT 'Yok'"),
            ("urun", "guncel_fiyat", "FLOAT DEFAULT 0.0")
        ]
        for tablo, kolon, tip in kolonlar:
            try:
                db.session.execute(text(f"ALTER TABLE {tablo} ADD COLUMN {kolon} {tip};"))
                db.session.commit()
            except Exception:
                db.session.rollback()

@app.route('/static/uploads/<path:filename>')
def serve_uploads(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

def geri_sayim_gorevi(saniye):
    global sayac_kalan, sayac_aktif, mezat_durumu, aktif_urun_id
    sayac_kalan = saniye
    sayac_aktif = True
    
    while sayac_kalan > 0 and sayac_aktif:
        socketio.emit('sayac_guncelle', {'kalan': sayac_kalan})
        socketio.sleep(1)
        sayac_kalan -= 1
        
    if sayac_aktif:
        sayac_aktif = False
        mezat_durumu["durum"] = "Satıldı"
        with app.app_context():
            if aktif_urun_id:
                urun = Urun.query.get(aktif_urun_id)
                if urun:
                    urun.durum = "Satıldı"
                    urun.kazanan_adi = mezat_durumu["kazanan"]
                    OnTeklif.query.filter_by(urun_id=aktif_urun_id).delete()
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
        kategori_filtre = (request.args.get('kategori') or '').strip()
        
        aktif_urun_obj = Urun.query.get(aktif_urun_id) if aktif_urun_id else None
        aktif_urun = aktif_urun_obj.to_dict() if aktif_urun_obj else None
        
        urun_sorgu = Urun.query
        if kategori_filtre and kategori_filtre not in ['Tümü', 'Tüm Kategoriler', '']:
            urun_sorgu = urun_sorgu.filter(Urun.kategori == kategori_filtre)
            
        tum_urun_listesi = urun_sorgu.order_by(Urun.lot_no.asc()).all()
        urunler = [u.to_dict() for u in tum_urun_listesi]
        
        musteriler = []
        try:
            for m in Kullanici.query.order_by(Kullanici.id.desc()).all():
                musteriler.append({
                    "id": m.id,
                    "ad": getattr(m, 'ad_soyad', 'İsimsiz') or "İsimsiz", 
                    "tel": getattr(m, 'telefon', '-') or "-", 
                    "mail": getattr(m, 'email', '-') or "-", 
                    "adres": getattr(m, 'adres', '-') or "-", 
                    "bonus": getattr(m, 'bonus', 0.0) or 0.0,
                    "puan": getattr(m, 'puan', 100.0) if getattr(m, 'puan', None) is not None else 100.0,
                    "onayli_mi": bool(getattr(m, 'onayli_mi', False)),
                    "durum": getattr(m, 'durum', 'bekliyor') or "bekliyor",
                    "ip": getattr(m, 'ip_adresi', '-') or "-"
                })
        except Exception:
            musteriler = []
        
        satilan_urunler = Urun.query.filter_by(durum="Satıldı").all()
        gruplanmis_dosyalar = {}

        for u in satilan_urunler:
            m_adi = u.kazanan_adi or "Bilinmeyen"
            if m_adi == "Yok": continue
                
            if m_adi not in gruplanmis_dosyalar:
                mus_obj = Kullanici.query.filter_by(ad_soyad=m_adi).first()
                tel = mus_obj.telefon if mus_obj else ""
                gruplanmis_dosyalar[m_adi] = {
                    "musteri_adi": m_adi,
                    "telefon": tel,
                    "urunler": [],
                    "toplam_tutar": 0.0
                }
            
            satiss_fiyati = float(u.guncel_fiyat or u.acilis_fiyati or 0)
            gruplanmis_dosyalar[m_adi]["urunler"].append({
                "urun_id": u.id,
                "lot": u.lot_no,
                "urun_adi": u.urun_adi,
                "musteri_adi": m_adi,
                "fiyat": satiss_fiyati
            })
            gruplanmis_dosyalar[m_adi]["toplam_tutar"] += satiss_fiyati

        on_teklifler = []
        try:
            for ot in OnTeklif.query.order_by(OnTeklif.id.desc()).all():
                u = Urun.query.get(ot.urun_id)
                if u and u.durum == "Satıldı":
                    continue
                on_teklifler.append({
                    "id": ot.id,
                    "urun_id": ot.urun_id,
                    "lot": u.lot_no if u else "-",
                    "urun_adi": u.urun_adi if u else "Arşivlenmiş Ürün",
                    "musteri_adi": ot.musteri_adi,
                    "teklif": ot.teklif,
                    "zaman": getattr(ot, 'zaman', '')
                })
        except Exception:
            on_teklifler = []

        muzikler = []
        try:
            muzikler = [m.url for m in Muzik.query.all()]
        except Exception:
            muzikler = []

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
        return jsonify({"error": str(e), "urunler": []}), 200

@app.route('/kayit-ol', methods=['POST'])
def kayit_ol():
    veri = request.json or {}
    ad = (veri.get('ad') or '').strip()
    tel = (veri.get('tel') or '').strip()
    mail = (veri.get('mail') or '').strip()
    adres = (veri.get('adres') or '').strip()
    
    if not ad:
        return jsonify({"success": False, "mesaj": "Ad soyad alanı zorunludur."})
        
    client_ip = get_client_ip()
    fingerprint = get_device_fingerprint()

    kullanici = Kullanici.query.filter_by(ad_soyad=ad).first()
    if kullanici:
        kullanici.telefon = tel
        kullanici.email = mail
        kullanici.adres = adres
    else:
        kullanici = Kullanici(
            ad_soyad=ad, 
            telefon=tel, 
            email=mail, 
            adres=adres, 
            onayli_mi=False, 
            durum='bekliyor',
            ip_adresi=client_ip,
            cihaz_kodu=fingerprint
        )
        db.session.add(kullanici)
    
    db.session.commit()
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True, "mesaj": "Kaydınız alındı. Yönetici onayının ardından teklif verebilirsiniz."})

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

@app.route('/on-teklif-ver', methods=['POST'])
def on_teklif_ver():
    global mezat_durumu, aktif_urun_id
    veri = request.json or {}
    raw_uid = veri.get('urun_id')
    musteri_adi = (veri.get('musteri_adi') or '').strip()
    teklif_tutari = float(veri.get('teklif', 0))

    if not musteri_adi:
        return jsonify({"success": False, "mesaj": "Lütfen önce sisteme kaydolun/giriş yapın!"})

    try:
        urun_id = int(raw_uid)
    except:
        return jsonify({"success": False, "mesaj": "Geçersiz ürün ID!"})

    urun = Urun.query.get(urun_id)
    if not urun:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})
        
    if urun.durum == "Satıldı":
        return jsonify({"success": False, "mesaj": "⚠️ Bu ürün satıldığı için ön teklif verilemez!"})

    taban_fiyat = float(urun.acilis_fiyati or 0)
    
    if urun.guncel_fiyat and float(urun.guncel_fiyat) > taban_fiyat:
        taban_fiyat = float(urun.guncel_fiyat)
        
    if aktif_urun_id == urun.id and mezat_durumu.get('pey', 0) > taban_fiyat:
        taban_fiyat = float(mezat_durumu['pey'])

    en_yuksek_on_teklif = OnTeklif.query.filter_by(urun_id=urun_id).order_by(OnTeklif.teklif.desc()).first()
    if en_yuksek_on_teklif and float(en_yuksek_on_teklif.teklif) > taban_fiyat:
        taban_fiyat = float(en_yuksek_on_teklif.teklif)

    yuzde_on_artis = round(taban_fiyat * 0.10)
    if yuzde_on_artis < 100:
        yuzde_on_artis = 100

    min_gecerli_tutar = taban_fiyat + yuzde_on_artis

    if taban_fiyat > float(urun.acilis_fiyati) or en_yuksek_on_teklif:
        if teklif_tutari < min_gecerli_tutar:
            return jsonify({
                "success": False, 
                "mesaj": f"⚠️ Bu ürünün canlıdaki güncel peyi / en yüksek teklifi {taban_fiyat} TL'dir.\nBir sonraki geçerli teklif en az %10 artışla {min_gecerli_tutar} TL olmalıdır!"
            })
    else:
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
    return jsonify({"success": True, "mesaj": "✅ Ön teklifiniz başarıyla alındı."})

@app.route('/on-teklif-guncelle', methods=['POST'])
def on_teklif_guncelle():
    veri = request.json or {}
    ot_id = veri.get('id')
    yeni_teklif = float(veri.get('teklif', 0))
    yeni_musteri = (veri.get('musteri_adi') or '').strip()

    ot = OnTeklif.query.get(ot_id)
    if ot:
        if yeni_teklif > 0:
            ot.teklif = yeni_teklif
        if yeni_musteri:
            ot.musteri_adi = yeni_musteri
        db.session.commit()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False, "mesaj": "Ön teklif bulunamadı."})

@app.route('/on-teklif-sil', methods=['POST'])
def on_teklif_sil():
    veri = request.json or {}
    ot_id = veri.get('id')
    index = veri.get('index')
    try:
        if ot_id:
            ot = OnTeklif.query.get(ot_id)
            if ot:
                db.session.delete(ot)
                db.session.commit()
                socketio.emit('veri_guncellendi')
                return jsonify({'success': True})
        elif index is not None:
            ot_list = OnTeklif.query.order_by(OnTeklif.id.desc()).all()
            if 0 <= index < len(ot_list):
                db.session.delete(ot_list[index])
                db.session.commit()
                socketio.emit('veri_guncellendi')
                return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    return jsonify({'success': False})

@app.route('/pey-ver', methods=['POST'])
def pey_ver():
    global mezat_durumu, aktif_urun_id, sayac_kalan
    veri = request.json or {}
    client_ip = get_client_ip()

    musteri_adi = veri.get('musteri_adi') or veri.get('isim', 'Misafir')
    kullanici = Kullanici.query.filter_by(ad_soyad=musteri_adi).first()

    if not kullanici or kullanici.durum != 'onayli':
        return jsonify({"success": False, "mesaj": "Teklif verebilmek için yönetici onaylı üye olmanız gerekmektedir."})

    if kullanici.durum == 'engellendi':
        return jsonify({"success": False, "mesaj": "Hesabınız engellendiği için teklif veremezsiniz."})

    raw_uid = veri.get('urun_id') or aktif_urun_id
    try:
        urun_id = int(raw_uid) if raw_uid else aktif_urun_id
    except:
        urun_id = aktif_urun_id

    urun = Urun.query.get(urun_id) if urun_id else None
    if not urun:
        return jsonify({"success": False, "mesaj": "Sahnede aktif ürün bulunamadı!"})

    if urun.durum == "Satıldı" or mezat_durumu.get("durum") == "Satıldı":
        return jsonify({"success": False, "mesaj": "⚠️ Bu ürün satılmıştır! Lütfen yöneticinin yeni ürünü sahneye almasını bekleyiniz."})

    islem = veri.get('islem', 'pey')

    if islem == 'hemen_al':
        hemen_al_fiyat = urun.hemen_al_fiyati if (urun.hemen_al_fiyati and urun.hemen_al_fiyati > 0) else urun.acilis_fiyati
        urun.durum = 'Satıldı'
        urun.guncel_fiyat = hemen_al_fiyat
        urun.kazanan_adi = musteri_adi
        
        mezat_durumu['durum'] = 'Satıldı'
        mezat_durumu['kazanan'] = musteri_adi
        mezat_durumu['pey'] = hemen_al_fiyat
            
        yeni_teklif = Teklif(urun_id=urun.id, musteri_adi=musteri_adi, tutar=hemen_al_fiyat, ip_adresi=client_ip)
        db.session.add(yeni_teklif)
        
        OnTeklif.query.filter_by(urun_id=urun.id).delete()
        db.session.commit()
        
        socketio.emit('pey_guncellendi', {'guncel_fiyat': hemen_al_fiyat, 'kazanan_ad': musteri_adi})
        socketio.emit('sayac_bitti', {
            'urun_id': urun.id,
            'kazanan': musteri_adi,
            'fiyat': hemen_al_fiyat
        })
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True, "kazanan": musteri_adi})

    if islem == 'pey':
        mevcut_fiyat = mezat_durumu['pey'] if mezat_durumu['pey'] > 0 else urun.acilis_fiyati
        artis = float(veri.get('artis', 0))
        miktar = float(veri.get('miktar', 0))

        if artis > 0:
            miktar = round(mevcut_fiyat + artis, 2)

        if miktar <= mevcut_fiyat:
            return jsonify({"success": False, "mesaj": f"Teklif mevcut fiyattan ({mevcut_fiyat} TL) yüksek olmalıdır!"})

        if (miktar - mevcut_fiyat) < 100.0:
            return jsonify({"success": False, "mesaj": f"En düşük teklif artış tutarı 100 TL'dir. Minimum verilebilecek pey: {mevcut_fiyat + 100} TL"})

        mezat_durumu['pey'] = miktar
        mezat_durumu['kazanan'] = musteri_adi
        urun.guncel_fiyat = miktar
        
        yeni_teklif = Teklif(urun_id=urun.id, musteri_adi=musteri_adi, tutar=miktar, ip_adresi=client_ip)
        db.session.add(yeni_teklif)
        db.session.commit()

        if sayac_aktif and sayac_kalan <= 10:
            sayac_kalan = 15
            socketio.emit('sayac_uzatildi', {'kalan': 15, 'mesaj': 'Son saniye teklifi nedeniyle süre uzatıldı!'})
        
        socketio.emit('pey_guncellendi', {
            'urun_id': urun.id,
            'guncel_fiyat': miktar,
            'kazanan_ad': musteri_adi
        })

        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})

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
    
    socketio.emit('yeni_sahne_urunu', urun.to_dict())
    socketio.emit('veri_guncellendi')
    socketio.emit('pey_guncellendi', {'guncel_fiyat': mezat_durumu['pey'], 'kazanan_ad': mezat_durumu['kazanan']})
    
    return jsonify({"success": True, "urun": urun.to_dict()})

@app.route('/mezat-baslat', methods=['POST'])
def mezat_baslat():
    global mezat_durumu, aktif_urun_id, sayac_aktif
    veri = request.json or {}
    urun_id = veri.get('urun_id', aktif_urun_id)
    sure = int(veri.get('sure', 30))
      
    if urun_id:
        aktif_urun_id = urun_id
        mezat_durumu['durum'] = 'Sayim'
        mezat_durumu['sure_bitis'] = time.time() + sure
        
        sayac_aktif = False
        socketio.sleep(0.1)
        socketio.start_background_task(geri_sayim_gorevi, sure)
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
            OnTeklif.query.filter_by(urun_id=aktif_urun_id).delete()
            db.session.commit()
            
    socketio.emit('sayac_bitti', {
        'urun_id': aktif_urun_id,
        'kazanan': mezat_durumu['kazanan'],
        'fiyat': mezat_durumu['pey']
    })
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True})

@app.route('/sikayet-oneri-gonder', methods=['POST'])
def sikayet_oneri_gonder():
    veri = request.json or {}
    musteri_adi = veri.get('musteri_adi', 'Misafir')
    tur = veri.get('tur', 'Görüş / Tavsiye')
    konu = veri.get('konu', '')
    mesaj = veri.get('mesaj', '')

    if not konu or not mesaj:
        return jsonify({"success": False, "mesaj": "Konu ve mesaj alanları boş bırakılamaz."})

    yeni_kayit = SikayetOneri(
        musteri_adi=musteri_adi,
        tur=tur,
        konu=konu,
        mesaj=mesaj,
        ip_adresi=get_client_ip()
    )
    db.session.add(yeni_kayit)
    db.session.commit()
    return jsonify({"success": True, "mesaj": "Geri bildiriminiz yöneticiye iletildi."})

@app.route('/sikayet-oneri-listele', methods=['GET'])
def sikayet_oneri_listele():
    sifre = request.args.get('sifre')
    if sifre != '1453':
        return jsonify({"error": "Yetkisiz erişim"}), 403

    try:
        kayitlar = SikayetOneri.query.order_by(SikayetOneri.id.desc()).all()
        sonuc = [{
            "id": k.id,
            "musteri_adi": k.musteri_adi,
            "tur": k.tur,
            "konu": k.konu,
            "mesaj": k.mesaj,
            "durum": k.durum,
            "ip": getattr(k, 'ip_adresi', '-'),
            "tarih": k.tarih.strftime('%d.%m.%Y %H:%M') if k.tarih else ''
        } for k in kayitlar]
        return jsonify({"kayitlar": sonuc})
    except Exception:
        return jsonify({"kayitlar": []})

@app.route('/sikayet-oneri-durum', methods=['POST'])
def sikayet_oneri_durum():
    veri = request.json or {}
    kayit_id = veri.get('id')
    yeni_durum = veri.get('durum')

    kayit = SikayetOneri.query.get(kayit_id)
    if kayit:
        kayit.durum = yeni_durum
        db.session.commit()
        return jsonify({"success": True})
    return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})

@app.route('/sikayet-oneri-sil', methods=['POST'])
def sikayet_oneri_sil():
    veri = request.json or {}
    kayit_id = veri.get('id')
    kayit = SikayetOneri.query.get(kayit_id)
    if kayit:
        db.session.delete(kayit)
        db.session.commit()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/itiraz-duzelt-bildir', methods=['POST'])
def itiraz_duzelt_bildir():
    veri = request.json or {}
    kayit_id = veri.get('id')
    kayit = SikayetOneri.query.get(kayit_id)
    if kayit:
        kayit.durum = "Düzeltildi"
        db.session.commit()
        
        socketio.emit('itiraz_sonuc_bildirimi', {
            'musteri_adi': kayit.musteri_adi,
            'konu': kayit.konu,
            'mesaj': "Talebiniz/itirazınız yönetici tarafından incelenmiş ve düzeltilmiştir."
        })
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/urun-ekle', methods=['POST'])
def urun_ekle():
    try:
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
        lot_raw = request.form.get('lot')
        lot_no = int(lot_raw) if lot_raw and lot_raw.isdigit() else (mevcut_urun_sayisi + 1)
        
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
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)}), 500

@app.route('/urun-guncelle', methods=['POST'])
def urun_guncelle():
    global mezat_durumu, aktif_urun_id
    veri = request.json or {}
    urun_id = veri.get('id')
    urun = Urun.query.get(urun_id)
    
    if not urun:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})
        
    try:
        urun.lot_no = int(veri.get('lot', urun.lot_no))
        urun.urun_adi = veri.get('ad', urun.urun_adi)
        urun.kategori = veri.get('kategori', urun.kategori)
        urun.acilis_fiyati = float(veri.get('fiyat', urun.acilis_fiyati))
        urun.hemen_al_fiyati = float(veri.get('hemen_al_fiyat', urun.hemen_al_fiyati))
        urun.tanitim_yazisi = veri.get('tanitim_yazisi', urun.tanitim_yazisi)
        
        if aktif_urun_id == urun.id and mezat_durumu['pey'] == 0:
            mezat_durumu['pey'] = urun.acilis_fiyati
            urun.guncel_fiyat = urun.acilis_fiyati
            
        db.session.commit()
        
        if aktif_urun_id == urun.id:
            socketio.emit('yeni_sahne_urunu', urun.to_dict())
            
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True, "mesaj": "Ürün başarıyla güncellendi."})
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)})

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
                df_mus = [{"Ad Soyad": getattr(m, 'ad_soyad', '-'), "Telefon": getattr(m, 'telefon', '-'), "E-posta": getattr(m, 'email', '-'), "Adres": getattr(m, 'adres', '-'), "Durum": getattr(m, 'durum', '-')} for m in musteriler]
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

# Tabloları ve eksik sütunları otomatik onar
veritabani_tablolari_onar()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)