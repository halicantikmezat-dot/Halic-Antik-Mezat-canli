from gevent import monkey
monkey.patch_all()

import os
import time
import uuid
import hashlib
import functools
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from flask_compress import Compress
from sqlalchemy import text, or_, func
import pandas as pd

app = Flask(__name__)
Compress(app)

# ==========================================
# VERİTABANI VE UYGULAMA YAPILANDIRMASI
# ==========================================
db_url = os.environ.get('DATABASE_URL', 'sqlite:///halic_mezat.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '1453')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'halic_hamid_antik_mezat_gizli_anahtar_1453')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 250 * 1024 * 1024

if os.path.exists('/var/data'):
    UPLOAD_FOLDER = '/var/data/uploads'
else:
    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static', 'uploads')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins="*")

# ==========================================
# GÜVENLİK VE YETKİLENDİRME YARDIMCILARI
# ==========================================
def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        req_pass = request.headers.get('X-Admin-Password') or request.args.get('sifre')
        if request.is_json and request.json:
            req_pass = req_pass or request.json.get('sifre')
        elif request.form:
            req_pass = req_pass or request.form.get('sifre')

        if req_pass != ADMIN_PASSWORD and session.get('is_admin') is not True:
            return jsonify({"success": False, "mesaj": "Yetkisiz erişim! Yönetici şifresi geçersiz."}), 403
        return f(*args, **kwargs)
    return decorated_function

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

def kaydet_guvenli_dosya(dosya):
    if not dosya or not dosya.filename:
        return None, None
    temiz_ad = secure_filename(dosya.filename)
    benzersiz_ad = f"{uuid.uuid4().hex[:10]}_{temiz_ad}"
    dosya_yolu = os.path.join(app.config['UPLOAD_FOLDER'], benzersiz_ad)
    dosya.save(dosya_yolu)
    return f"/static/uploads/{benzersiz_ad}", temiz_ad

# ==========================================
# VERİTABANI MODELLERİ
# ==========================================
class Kullanici(db.Model):
    __tablename__ = 'kullanici'
    id = db.Column(db.Integer, primary_key=True)
    ad_soyad = db.Column(db.String(100), nullable=False)
    telefon = db.Column(db.String(20), nullable=True, index=True)
    email = db.Column(db.String(100), nullable=True)
    adres = db.Column(db.Text, nullable=True)
    sifre = db.Column(db.String(50), nullable=True)
    bonus = db.Column(db.Float, default=0.0)
    puan = db.Column(db.Float, default=100.0)
    onayli_mi = db.Column(db.Boolean, default=False)
    durum = db.Column(db.String(20), default='bekliyor')
    sozlesme_onay = db.Column(db.Boolean, default=True)
    ip_adresi = db.Column(db.String(50), nullable=True)
    cihaz_kodu = db.Column(db.String(64), nullable=True)
    kayit_tarihi = db.Column(db.DateTime, default=datetime.utcnow)

class Urun(db.Model):
    __tablename__ = 'urun'
    id = db.Column(db.Integer, primary_key=True)
    lot_no = db.Column(db.Integer, nullable=False, index=True)
    urun_adi = db.Column(db.String(200), nullable=False)
    kategori = db.Column(db.String(100), default="Hediyelik eşya")
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
    musteri_id = db.Column(db.Integer, db.ForeignKey('kullanici.id'), nullable=True)
    musteri_adi = db.Column(db.String(100), nullable=False)
    teklif = db.Column(db.Float, nullable=False)
    zaman = db.Column(db.String(20), default=lambda: time.strftime('%H:%M:%S'))

class Teklif(db.Model):
    __tablename__ = 'teklif'
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey('urun.id'), nullable=False)
    musteri_id = db.Column(db.Integer, db.ForeignKey('kullanici.id'), nullable=True)
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
        "fiyat": self.acilis_fiyati or 0.0,
        "guncel_fiyat": self.guncel_fiyat or self.acilis_fiyati or 0.0,
        "hemen_al_fiyat": self.hemen_al_fiyati or 0.0,
        "tanitim_yazisi": self.tanitim_yazisi or "",
        "fotograflar": self.fotograflar or [],
        "video": self.video or "",
        "ses": self.ses_dosyasi or "",
        "durum": self.durum or "Aktif",
        "kazanan": self.kazanan_adi or "Yok"
    }
Urun.to_dict = urun_to_dict

# ==========================================
# CANLI MEZAT VE ÖNBELLEK DEĞİŞKENLERİ
# ==========================================
aktif_izleyici_sayisi = 0
aktif_urun_id = None
sayac_kalan = 0
sayac_aktif = False
sayac_gorev_id = 0

mezat_durumu = {
    "durum": "Bekliyor",
    "sure_bitis": 0,
    "pey": 0,
    "kazanan": "Yok"
}

_son_durum_verisi = None
_son_durum_zamani = 0
_son_canli_verisi = None
_son_canli_zamani = 0

def onbellegi_temizle():
    global _son_durum_verisi, _son_durum_zamani, _son_canli_verisi, _son_canli_zamani
    _son_durum_verisi = None
    _son_durum_zamani = 0
    _son_canli_verisi = None
    _son_canli_zamani = 0

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
            ("kullanici", "sozlesme_onay", "BOOLEAN DEFAULT TRUE"),
            ("kullanici", "kayit_tarihi", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("urun", "hemen_al_fiyati", "FLOAT DEFAULT 0.0"),
            ("urun", "kazanan_adi", "VARCHAR(100) DEFAULT 'Yok'"),
            ("urun", "guncel_fiyat", "FLOAT DEFAULT 0.0"),
            ("on_teklif", "musteri_id", "INTEGER"),
            ("teklif", "musteri_id", "INTEGER")
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

# ==========================================
# GERİ SAYIM MOTORU
# ==========================================
def geri_sayim_gorevi(saniye, gorev_id):
    global sayac_kalan, sayac_aktif, mezat_durumu, aktif_urun_id, sayac_gorev_id
    sayac_kalan = saniye
    sayac_aktif = True

    while sayac_kalan > 0 and sayac_aktif and sayac_gorev_id == gorev_id:
        socketio.emit('sayac_guncelle', {'kalan': sayac_kalan})
        socketio.sleep(1)
        if sayac_aktif and sayac_gorev_id == gorev_id:
            sayac_kalan -= 1

    if sayac_aktif and sayac_gorev_id == gorev_id:
        sayac_aktif = False
        mezat_durumu["durum"] = "Satıldı"
        with app.app_context():
            if aktif_urun_id:
                urun = db.session.get(Urun, aktif_urun_id)
                if urun:
                    urun.durum = "Satıldı"
                    urun.kazanan_adi = mezat_durumu["kazanan"]
                    urun.guncel_fiyat = float(mezat_durumu["pey"]) if float(mezat_durumu["pey"]) > 0 else float(urun.acilis_fiyati or 0)
                    db.session.add(urun)
                    OnTeklif.query.filter_by(urun_id=aktif_urun_id).delete()
                    db.session.commit()

        onbellegi_temizle()
        socketio.emit('sayac_bitti', {
            'mesaj': 'Süre doldu!',
            'kazanan': mezat_durumu["kazanan"],
            'fiyat': mezat_durumu["pey"]
        })
        socketio.emit('veri_guncellendi')

# ==========================================
# TEMEL ROTALAR VE LİSTELEME
# ==========================================
@app.route('/')
def izleyici_index():
    return render_template('index.html')

@app.route('/vitrin')
def vitrin():
    return render_template('vitrin.html')

@app.route('/vitrin-urunler', methods=['GET'])
def vitrin_urunler():
    sayfa = int(request.args.get('sayfa', 1))
    limit = int(request.args.get('limit', 24))
    kategori = (request.args.get('kategori') or '').strip()
    arama = (request.args.get('arama') or '').strip()

    sorgu = Urun.query.filter(Urun.durum != "Satıldı")

    if kategori and kategori not in ['Tüm Kategoriler', '']:
        sorgu = sorgu.filter(Urun.kategori == kategori)

    if arama:
        if arama.isdigit():
            sorgu = sorgu.filter(or_(Urun.lot_no == int(arama), Urun.urun_adi.ilike(f"%{arama}%")))
        else:
            sorgu = sorgu.filter(or_(Urun.urun_adi.ilike(f"%{arama}%"), Urun.tanitim_yazisi.ilike(f"%{arama}%")))

    toplam_kayit = sorgu.count()
    toplam_sayfa = (toplam_kayit + limit - 1) // limit

    urunler = sorgu.order_by(Urun.lot_no.asc()).offset((sayfa - 1) * limit).limit(limit).all()

    return jsonify({
        "urunler": [u.to_dict() for u in urunler],
        "toplam_kayit": toplam_kayit,
        "toplam_sayfa": max(1, toplam_sayfa),
        "sayfa": sayfa
    })

@app.route('/admin', methods=['GET'])
def admin_panel():
    sifre = request.args.get('sifre')
    if sifre != ADMIN_PASSWORD:
        return "<h3 style='color:red; text-align:center;'>Yetkisiz erişim! Yönetici şifresi gerekli. (?sifre=1453)</h3>", 403
    session['is_admin'] = True
    return render_template('admin.html')

@app.route('/kategoriler-listesi', methods=['GET'])
def kategoriler_listesi():
    try:
        kategoriler = db.session.query(Urun.kategori).distinct().all()
        kat_listesi = sorted([k[0] for k in kategoriler if k[0] and k[0].strip()])
        return jsonify({"kategoriler": kat_listesi})
    except Exception:
        return jsonify({"kategoriler": []})

@app.route('/urun-ara', methods=['GET'])
def urun_ara():
    kelime = (request.args.get('q') or '').strip()
    kategori = (request.args.get('kategori') or '').strip()

    sorgu = Urun.query
    if kategori and kategori not in ['Tümü', 'Tüm Kategoriler', '']:
        sorgu = sorgu.filter(Urun.kategori == kategori)

    if kelime:
        if kelime.isdigit():
            sorgu = sorgu.filter(or_(Urun.lot_no == int(kelime), Urun.urun_adi.ilike(f"%{kelime}%")))
        else:
            sorgu = sorgu.filter(or_(Urun.urun_adi.ilike(f"%{kelime}%"), Urun.tanitim_yazisi.ilike(f"%{kelime}%")))

    sonuclar = sorgu.order_by(Urun.lot_no.asc()).limit(50).all()
    return jsonify({"urunler": [u.to_dict() for u in sonuclar]})

@app.route('/canli-durum', methods=['GET'])
def canli_durum():
    global mezat_durumu, aktif_urun_id, _son_canli_verisi, _son_canli_zamani
    suan = time.time()

    if _son_canli_verisi and (suan - _son_canli_zamani < 0.5):
        return jsonify(_son_canli_verisi)

    aktif_urun_obj = db.session.get(Urun, aktif_urun_id) if aktif_urun_id else None
    yanit = {
        "durum": mezat_durumu["durum"],
        "sure_bitis": mezat_durumu["sure_bitis"],
        "pey": mezat_durumu["pey"],
        "kazanan": mezat_durumu["kazanan"],
        "aktif_urun": aktif_urun_obj.to_dict() if aktif_urun_obj else None
    }
    _son_canli_verisi = yanit
    _son_canli_zamani = suan
    return jsonify(yanit)

@app.route('/durum-getir', methods=['GET'])
def durum_getir():
    global mezat_durumu, aktif_urun_id, _son_durum_verisi, _son_durum_zamani
    suan = time.time()

    if _son_durum_verisi and (suan - _son_durum_zamani < 0.3):
        return jsonify(_son_durum_verisi)

    try:
        aktif_urun_obj = db.session.get(Urun, aktif_urun_id) if aktif_urun_id else None
        aktif_urun = aktif_urun_obj.to_dict() if aktif_urun_obj else None

        tum_urun_listesi = Urun.query.order_by(Urun.lot_no.asc()).all()
        urun_map = {u.id: u for u in tum_urun_listesi}
        urunler = [u.to_dict() for u in tum_urun_listesi]

        tum_kullanicilar = Kullanici.query.order_by(Kullanici.id.desc()).all()
        kullanici_tel_map = {k.ad_soyad.strip().lower(): (k.telefon or "-") for k in tum_kullanicilar if k.ad_soyad}

        musteriler = [{
            "id": m.id,
            "ad": m.ad_soyad or "İsimsiz",
            "tel": m.telefon or "-",
            "mail": m.email or "-",
            "adres": m.adres or "-",
            "bonus": m.bonus or 0.0,
            "puan": m.puan if m.puan is not None else 100.0,
            "onayli_mi": bool(m.onayli_mi),
            "durum": m.durum or "bekliyor",
            "ip": m.ip_adresi or "-"
        } for m in tum_kullanicilar]

        satilan_urunler = Urun.query.filter(or_(Urun.durum == "Satıldı", Urun.durum == "satildi")).all()
        gruplanmis_dosyalar = {}

        for u in satilan_urunler:
            m_adi = (u.kazanan_adi or "").strip()
            if not m_adi or m_adi.lower() in ["yok", "none", "-", ""]:
                continue

            norm_key = m_adi.lower()
            if norm_key not in gruplanmis_dosyalar:
                gruplanmis_dosyalar[norm_key] = {
                    "musteri_adi": m_adi,
                    "telefon": kullanici_tel_map.get(norm_key, "-"),
                    "urunler": [],
                    "toplam_tutar": 0.0
                }

            satiss_fiyati = float(u.guncel_fiyat if (u.guncel_fiyat and u.guncel_fiyat > 0) else (u.acilis_fiyati or 0))
            gruplanmis_dosyalar[norm_key]["urunler"].append({
                "urun_id": u.id,
                "lot": u.lot_no,
                "urun_adi": u.urun_adi,
                "musteri_adi": m_adi,
                "fiyat": satiss_fiyati
            })
            gruplanmis_dosyalar[norm_key]["toplam_tutar"] += satiss_fiyati

        on_teklif_listesi = OnTeklif.query.order_by(OnTeklif.id.desc()).all()
        on_teklifler = []
        for ot in on_teklif_listesi:
            u = urun_map.get(ot.urun_id)
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

        muzikler = [m.url for m in Muzik.query.all()]

        yanit = {
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
        }

        _son_durum_verisi = yanit
        _son_durum_zamani = suan
        return jsonify(yanit)

    except Exception as e:
        return jsonify({"error": str(e), "urunler": []}), 200

# ==========================================
# PEY VERME VE HEMEN AL SATIŞI
# ==========================================
@app.route('/pey-ver', methods=['POST'])
def pey_ver():
    global mezat_durumu, aktif_urun_id, sayac_kalan, sayac_aktif
    veri = request.json or {}
    client_ip = get_client_ip()

    musteri_id = veri.get('musteri_id')
    musteri_adi = (veri.get('musteri_adi') or veri.get('isim') or '').strip()
    telefon = (veri.get('telefon') or '').strip()

    kullanici = None
    if musteri_id:
        kullanici = db.session.get(Kullanici, int(musteri_id))
    elif telefon:
        kullanici = Kullanici.query.filter_by(telefon=telefon).first()
    elif musteri_adi:
        kullanici = Kullanici.query.filter(func.lower(Kullanici.ad_soyad) == func.lower(musteri_adi)).first()

    if not kullanici or kullanici.durum != 'onayli':
        return jsonify({"success": False, "mesaj": "Teklif verebilmek için yönetici onaylı üye olmanız gerekmektedir."})

    if kullanici.durum == 'engellendi':
        return jsonify({"success": False, "mesaj": "Hesabınız engellendiği için teklif veremezsiniz."})

    musteri_adi = kullanici.ad_soyad

    raw_uid = veri.get('urun_id') or aktif_urun_id
    try:
        urun_id = int(raw_uid) if raw_uid else aktif_urun_id
    except:
        urun_id = aktif_urun_id

    if not urun_id:
        return jsonify({"success": False, "mesaj": "Aktif bir ürün seçili değil!"})

    urun = db.session.query(Urun).filter_by(id=urun_id).with_for_update().first()
    if not urun or urun.durum == "Satıldı" or mezat_durumu.get("durum") == "Satıldı":
        return jsonify({"success": False, "mesaj": "⚠️ Bu ürün satılmıştır veya aktif mezatta değildir!"})

    islem = veri.get('islem', 'pey')

    if islem == 'hemen_al':
        hemen_al_fiyat = float(urun.hemen_al_fiyati if (urun.hemen_al_fiyati and urun.hemen_al_fiyati > 0) else (urun.guncel_fiyat or urun.acilis_fiyati))
        sayac_aktif = False

        urun.durum = 'Satıldı'
        urun.guncel_fiyat = hemen_al_fiyat
        urun.kazanan_adi = musteri_adi

        mezat_durumu['durum'] = 'Satıldı'
        mezat_durumu['kazanan'] = musteri_adi
        mezat_durumu['pey'] = hemen_al_fiyat

        yeni_teklif = Teklif(urun_id=urun.id, musteri_id=kullanici.id, musteri_adi=musteri_adi, tutar=hemen_al_fiyat, ip_adresi=client_ip)
        db.session.add(yeni_teklif)
        db.session.add(urun)
        OnTeklif.query.filter_by(urun_id=urun.id).delete()
        db.session.commit()

        onbellegi_temizle()
        socketio.emit('pey_guncellendi', {'urun_id': urun.id, 'guncel_fiyat': hemen_al_fiyat, 'kazanan_ad': musteri_adi})
        socketio.emit('sayac_bitti', {'urun_id': urun.id, 'kazanan': musteri_adi, 'fiyat': hemen_al_fiyat})
        socketio.emit('veri_guncellendi')

        return jsonify({"success": True, "guncel_fiyat": hemen_al_fiyat, "kazanan": musteri_adi})

    if islem == 'pey':
        mevcut_fiyat = float(mezat_durumu['pey'] if mezat_durumu['pey'] > 0 else (urun.acilis_fiyati or 0))
        artis = float(veri.get('artis', 0))
        miktar = float(veri.get('miktar', 0))

        if artis > 0:
            miktar = round(mevcut_fiyat + artis, 2)
        elif miktar == 0:
            hesaplanan_artis = max(10.0, round(mevcut_fiyat * 0.10))
            miktar = round(mevcut_fiyat + hesaplanan_artis, 2)

        if miktar <= mevcut_fiyat:
            return jsonify({"success": False, "mesaj": f"Teklif mevcut fiyattan ({mevcut_fiyat} TL) yüksek olmalıdır!"})

        mezat_durumu['pey'] = miktar
        mezat_durumu['kazanan'] = musteri_adi
        urun.guncel_fiyat = miktar

        yeni_teklif = Teklif(urun_id=urun.id, musteri_id=kullanici.id, musteri_adi=musteri_adi, tutar=miktar, ip_adresi=client_ip)
        db.session.add(yeni_teklif)
        db.session.add(urun)
        db.session.commit()

        if sayac_aktif and sayac_kalan <= 10:
            sayac_kalan = 15
            socketio.emit('sayac_uzatildi', {'kalan': 15, 'mesaj': 'Son saniye teklifi nedeniyle süre 15 sn uzatıldı!'})
            socketio.emit('sayac_guncelle', {'kalan': 15})

        onbellegi_temizle()
        socketio.emit('pey_guncellendi', {'urun_id': urun.id, 'guncel_fiyat': miktar, 'kazanan_ad': musteri_adi})
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True, "guncel_fiyat": miktar, "kazanan": musteri_adi})

@app.route('/son-peyi-iptal-et', methods=['POST'])
@admin_required
def son_peyi_iptal_et():
    global mezat_durumu, aktif_urun_id
    if not aktif_urun_id:
        return jsonify({"success": False, "mesaj": "Sahnede aktif ürün yok!"})

    son_teklif = Teklif.query.filter_by(urun_id=aktif_urun_id).order_by(Teklif.id.desc()).first()
    if son_teklif:
        db.session.delete(son_teklif)
        db.session.commit()

    bir_onceki = Teklif.query.filter_by(urun_id=aktif_urun_id).order_by(Teklif.id.desc()).first()
    urun = db.session.get(Urun, aktif_urun_id)
    if bir_onceki:
        mezat_durumu['pey'] = float(bir_onceki.tutar)
        mezat_durumu['kazanan'] = bir_onceki.musteri_adi
        if urun: urun.guncel_fiyat = float(bir_onceki.tutar)
    else:
        mezat_durumu['pey'] = float(urun.acilis_fiyati or 0) if urun else 0
        mezat_durumu['kazanan'] = "Yok"
        if urun: urun.guncel_fiyat = float(urun.acilis_fiyati or 0)

    if urun:
        db.session.add(urun)
        db.session.commit()

    onbellegi_temizle()
    socketio.emit('pey_guncellendi', {'urun_id': aktif_urun_id, 'guncel_fiyat': mezat_durumu['pey'], 'kazanan_ad': mezat_durumu['kazanan']})
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True})

@app.route('/sahneye-al', methods=['POST'])
@admin_required
def sahneye_al():
    global mezat_durumu, aktif_urun_id, sayac_aktif
    veri = request.json or {}
    raw_id = veri.get('urun_id')

    try:
        urun_id = int(raw_id)
    except:
        return jsonify({"success": False, "mesaj": "Geçersiz ürün ID!"})

    urun = db.session.get(Urun, urun_id)
    if not urun:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})

    sayac_aktif = False
    aktif_urun_id = urun.id
    mezat_durumu['durum'] = 'Bekliyor'
    mezat_durumu['sure_bitis'] = 0
    urun.durum = 'Aktif'

    en_yuksek_ot = OnTeklif.query.filter_by(urun_id=urun_id).order_by(OnTeklif.teklif.desc()).first()
    if en_yuksek_ot:
        mezat_durumu['pey'] = float(en_yuksek_ot.teklif)
        mezat_durumu['kazanan'] = en_yuksek_ot.musteri_adi
        urun.guncel_fiyat = float(en_yuksek_ot.teklif)
    else:
        mezat_durumu['pey'] = float(urun.acilis_fiyati or 0)
        mezat_durumu['kazanan'] = 'Yok'
        urun.guncel_fiyat = float(urun.acilis_fiyati or 0)

    db.session.add(urun)
    db.session.commit()
    onbellegi_temizle()

    socketio.emit('yeni_sahne_urunu', urun.to_dict())
    socketio.emit('veri_guncellendi')
    socketio.emit('pey_guncellendi', {'guncel_fiyat': mezat_durumu['pey'], 'kazanan_ad': mezat_durumu['kazanan']})

    return jsonify({"success": True, "urun": urun.to_dict()})

@app.route('/mezat-baslat', methods=['POST'])
@admin_required
def mezat_baslat():
    global mezat_durumu, aktif_urun_id, sayac_aktif, sayac_gorev_id
    veri = request.json or {}
    urun_id = veri.get('urun_id', aktif_urun_id)
    sure = int(veri.get('sure', 30))

    if urun_id:
        aktif_urun_id = urun_id
        mezat_durumu['durum'] = 'Sayim'
        mezat_durumu['sure_bitis'] = time.time() + sure

        sayac_gorev_id += 1
        sayac_aktif = True

        socketio.start_background_task(geri_sayim_gorevi, sure, sayac_gorev_id)
        onbellegi_temizle()
        socketio.emit('mezat_basladi_muzik')
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})

    return jsonify({"success": False, "mesaj": "Ürün seçilmedi!"})

@app.route('/satis-bitir', methods=['POST'])
@admin_required
def satis_bitir():
    global mezat_durumu, aktif_urun_id, sayac_aktif, sayac_gorev_id
    sayac_aktif = False
    sayac_gorev_id += 1
    mezat_durumu['durum'] = 'Satıldı'
    if aktif_urun_id:
        urun = db.session.get(Urun, aktif_urun_id)
        if urun:
            urun.durum = "Satıldı"
            urun.kazanan_adi = mezat_durumu['kazanan']
            urun.guncel_fiyat = float(mezat_durumu['pey']) if float(mezat_durumu['pey']) > 0 else float(urun.acilis_fiyati or 0)
            db.session.add(urun)
            OnTeklif.query.filter_by(urun_id=aktif_urun_id).delete()
            db.session.commit()

    onbellegi_temizle()
    socketio.emit('sayac_bitti', {
        'urun_id': aktif_urun_id,
        'kazanan': mezat_durumu['kazanan'],
        'fiyat': mezat_durumu['pey']
    })
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True})

@app.route('/kayit-ol', methods=['POST'])
def kayit_ol():
    veri = request.json or {}
    ad = (veri.get('ad') or '').strip()
    tel = (veri.get('tel') or '').strip()
    mail = (veri.get('mail') or '').strip()
    adres = (veri.get('adres') or '').strip()
    sozlesme = bool(veri.get('sozlesme', True))

    if not ad:
        return jsonify({"success": False, "mesaj": "Ad soyad alanı zorunludur."})
    if not tel:
        return jsonify({"success": False, "mesaj": "Telefon numarası zorunludur."})

    client_ip = get_client_ip()
    fingerprint = get_device_fingerprint()

    kullanici = Kullanici.query.filter_by(telefon=tel).first()
    if kullanici:
        kullanici.ad_soyad = ad
        kullanici.email = mail
        kullanici.adres = adres
        kullanici.sozlesme_onay = sozlesme
        kullanici.ip_adresi = client_ip
        kullanici.cihaz_kodu = fingerprint
    else:
        kullanici = Kullanici(
            ad_soyad=ad,
            telefon=tel,
            email=mail,
            adres=adres,
            sozlesme_onay=sozlesme,
            onayli_mi=False,
            durum='bekliyor',
            ip_adresi=client_ip,
            cihaz_kodu=fingerprint
        )
        db.session.add(kullanici)

    db.session.commit()
    onbellegi_temizle()
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True, "musteri_id": kullanici.id, "mesaj": "Kaydınız ve sözleşme onayınız alındı. Yönetici onayının ardından teklif verebilirsiniz."})

@app.route('/on-teklif-ver', methods=['POST'])
def on_teklif_ver():
    veri = request.json or {}
    raw_uid = veri.get('urun_id')
    musteri_id = veri.get('musteri_id')
    musteri_adi = (veri.get('musteri_adi') or '').strip()
    telefon = (veri.get('telefon') or '').strip()
    teklif_tutari = float(veri.get('teklif', 0))

    kullanici = None
    if musteri_id:
        kullanici = db.session.get(Kullanici, int(musteri_id))
    elif telefon:
        kullanici = Kullanici.query.filter_by(telefon=telefon).first()
    elif musteri_adi:
        kullanici = Kullanici.query.filter(func.lower(Kullanici.ad_soyad) == func.lower(musteri_adi)).first()

    if not kullanici:
        return jsonify({"success": False, "mesaj": "Lütfen önce sisteme kaydolun/giriş yapın!"})

    try:
        urun_id = int(raw_uid)
    except:
        return jsonify({"success": False, "mesaj": "Geçersiz ürün ID!"})

    urun = db.session.get(Urun, urun_id)
    if not urun or urun.durum == "Satıldı":
        return jsonify({"success": False, "mesaj": "⚠️ Bu ürün teklife kapalıdır!"})

    acilis = float(urun.acilis_fiyati or 0)
    if teklif_tutari < acilis:
        return jsonify({"success": False, "mesaj": f"Ön teklif açılış fiyatından ({acilis} TL) düşük olamaz!"})

    en_yuksek = OnTeklif.query.filter_by(urun_id=urun_id).order_by(OnTeklif.teklif.desc()).first()
    if en_yuksek and teklif_tutari <= en_yuksek.teklif:
        return jsonify({"success": False, "mesaj": f"Teklifiniz mevcut en yüksek ön tekliften ({en_yuksek.teklif} TL) büyük olmalıdır!"})

    eski_teklif = OnTeklif.query.filter_by(urun_id=urun_id, musteri_id=kullanici.id).first() if kullanici.id else None
    if not eski_teklif:
        eski_teklif = OnTeklif.query.filter_by(urun_id=urun_id, musteri_adi=kullanici.ad_soyad).first()

    if eski_teklif:
        eski_teklif.teklif = teklif_tutari
        eski_teklif.zaman = time.strftime('%H:%M:%S')
    else:
        yeni_ot = OnTeklif(urun_id=urun_id, musteri_id=kullanici.id, musteri_adi=kullanici.ad_soyad, teklif=teklif_tutari)
        db.session.add(yeni_ot)

    urun.guncel_fiyat = teklif_tutari
    db.session.add(urun)
    db.session.commit()

    onbellegi_temizle()
    socketio.emit('veri_guncellendi')
    return jsonify({"success": True, "mesaj": "✅ Ön teklifiniz başarıyla kaydedildi."})

@app.route('/on-teklif-guncelle', methods=['POST'])
@admin_required
def on_teklif_guncelle():
    veri = request.json or {}
    ot_id = veri.get('id')
    yeni_musteri = (veri.get('musteri_adi') or '').strip()
    yeni_teklif = float(veri.get('teklif', 0))

    ot = db.session.get(OnTeklif, ot_id)
    if ot:
        ot.musteri_adi = yeni_musteri
        ot.teklif = yeni_teklif
        db.session.commit()
        onbellegi_temizle()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False, "mesaj": "Ön teklif bulunamadı."})

@app.route('/on-teklif-sil', methods=['POST'])
@admin_required
def on_teklif_sil():
    veri = request.json or {}
    ot_id = veri.get('id')
    try:
        if ot_id:
            ot = db.session.get(OnTeklif, ot_id)
            if ot:
                u_id = ot.urun_id
                db.session.delete(ot)
                db.session.commit()

                urun = db.session.get(Urun, u_id)
                if urun:
                    kalan_en_yuksek = OnTeklif.query.filter_by(urun_id=u_id).order_by(OnTeklif.teklif.desc()).first()
                    if kalan_en_yuksek:
                        urun.guncel_fiyat = float(kalan_en_yuksek.teklif)
                    else:
                        urun.guncel_fiyat = float(urun.acilis_fiyati or 0)
                    db.session.add(urun)
                    db.session.commit()

                onbellegi_temizle()
                socketio.emit('veri_guncellendi')
                return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    return jsonify({'success': False})

@app.route('/musteri-durum-guncelle', methods=['POST'])
@admin_required
def musteri_durum_guncelle():
    veri = request.json or {}
    kullanici_id = veri.get('kullanici_id')
    yeni_durum = veri.get('durum')

    kullanici = db.session.get(Kullanici, kullanici_id)
    if kullanici:
        kullanici.durum = yeni_durum
        kullanici.onayli_mi = (yeni_durum == 'onayli')
        db.session.commit()
        onbellegi_temizle()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False, "mesaj": "Kullanıcı bulunamadı."})

@app.route('/musteri-sil', methods=['POST'])
@admin_required
def musteri_sil():
    veri = request.json or {}
    kullanici_id = veri.get('kullanici_id')
    kullanici = db.session.get(Kullanici, kullanici_id)
    if kullanici:
        Teklif.query.filter_by(musteri_id=kullanici.id).delete()
        OnTeklif.query.filter_by(musteri_id=kullanici.id).delete()
        db.session.delete(kullanici)
        db.session.commit()
        onbellegi_temizle()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/urun-ekle', methods=['POST'])
@admin_required
def urun_ekle():
    try:
        dosyalar = request.files.getlist('dosyalar')
        fotograflar = []
        video_url = ""
        ses_url = ""

        for dosya in dosyalar:
            url, temiz_ad = kaydet_guvenli_dosya(dosya)
            if url and temiz_ad:
                ext = temiz_ad.lower()
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
            kategori=request.form.get('kategori', 'Hediyelik eşya'),
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
        onbellegi_temizle()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)}), 500

@app.route('/toplu-urun-yukle', methods=['POST'])
@admin_required
def toplu_urun_yukle():
    try:
        dosya = request.files.get('dosya')
        xml_url = request.form.get('xml_url', '').strip()
        eklenen_sayisi = 0

        # Excel Yükleme
        if dosya and dosya.filename:
            ext = dosya.filename.lower()
            if ext.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(dosya)
                for _, row in df.iterrows():
                    mevcut_lot = Urun.query.count() + 1
                    u = Urun(
                        lot_no=int(row.get('lot', mevcut_lot)),
                        urun_adi=str(row.get('ad', 'İsimsiz Ürün')),
                        kategori=str(row.get('kategori', 'Hediyelik eşya')),
                        acilis_fiyati=float(row.get('fiyat', 0)),
                        guncel_fiyat=float(row.get('fiyat', 0)),
                        hemen_al_fiyati=float(row.get('hemen_al_fiyat', 0)),
                        tanitim_yazisi=str(row.get('tanitim_yazisi', '')),
                        durum="Aktif"
                    )
                    db.session.add(u)
                    eklenen_sayisi += 1
                db.session.commit()
                onbellegi_temizle()
                socketio.emit('veri_guncellendi')
                return jsonify({"success": True, "mesaj": f"✅ {eklenen_sayisi} adet ürün Excel'den yüklendi."})

        # XML URL ile Çekme
        if xml_url:
            resp = requests.get(xml_url, timeout=15)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                items = root.findall('.//urun') or root.findall('.//item') or root.findall('.//product')
                for item in items:
                    mevcut_lot = Urun.query.count() + 1
                    lot_text = item.findtext('lot') or item.findtext('lot_no')
                    lot = int(lot_text) if lot_text and lot_text.isdigit() else mevcut_lot
                    ad = item.findtext('ad') or item.findtext('name') or item.findtext('urun_adi') or 'İsimsiz Ürün'
                    kat = item.findtext('kategori') or item.findtext('category') or 'Genel'
                    fiyat_txt = item.findtext('fiyat') or item.findtext('price') or '0'
                    fiyat = float(fiyat_txt.replace(',', '.')) if fiyat_txt else 0.0
                    hemen_al_txt = item.findtext('hemen_al_fiyat') or '0'
                    hemen_al = float(hemen_al_txt.replace(',', '.')) if hemen_al_txt else 0.0
                    tanitim = item.findtext('tanitim') or item.findtext('tanitim_yazisi') or item.findtext('description') or ''

                    u = Urun(
                        lot_no=lot,
                        urun_adi=ad,
                        kategori=kat,
                        acilis_fiyati=fiyat,
                        guncel_fiyat=fiyat,
                        hemen_al_fiyati=hemen_al,
                        tanitim_yazisi=tanitim,
                        durum="Aktif"
                    )
                    db.session.add(u)
                    eklenen_sayisi += 1
                db.session.commit()
                onbellegi_temizle()
                socketio.emit('veri_guncellendi')
                return jsonify({"success": True, "mesaj": f"✅ {eklenen_sayisi} adet ürün XML kaynağından yüklendi."})

        return jsonify({"success": False, "mesaj": "Geçerli bir Excel dosyası veya XML URL girilmedi!"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mesaj": str(e)})

@app.route('/fiyatlari-yuzde-artir', methods=['POST'])
@admin_required
def fiyatlari_yuzde_artir():
    veri = request.json or {}
    oran = float(veri.get('oran', 30)) / 100.0
    try:
        urunler = Urun.query.filter(Urun.durum != "Satıldı").all()
        for u in urunler:
            u.acilis_fiyati = round(u.acilis_fiyati * (1.0 + oran), 2)
            u.guncel_fiyat = u.acilis_fiyati
            if u.hemen_al_fiyati:
                u.hemen_al_fiyati = round(u.hemen_al_fiyati * (1.0 + oran), 2)
            db.session.add(u)
        db.session.commit()
        onbellegi_temizle()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True, "mesaj": f"Tüm ürünlerin fiyatı %{int(oran*100)} artırıldı."})
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)})

@app.route('/urun-guncelle', methods=['POST'])
@admin_required
def urun_guncelle():
    global mezat_durumu, aktif_urun_id
    veri = request.json or {}
    urun_id = veri.get('id')
    urun = db.session.get(Urun, urun_id)
    if not urun: return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})

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

        db.session.add(urun)
        db.session.commit()
        onbellegi_temizle()
        if aktif_urun_id == urun.id: socketio.emit('yeni_sahne_urunu', urun.to_dict())
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True, "mesaj": "Ürün başarıyla güncellendi."})
    except Exception as e:
        return jsonify({"success": False, "mesaj": str(e)})

@app.route('/urun-sil', methods=['POST'])
@admin_required
def urun_sil():
    veri = request.json or {}
    urun_id = veri.get('id')
    urun = db.session.get(Urun, urun_id)
    if urun:
        Teklif.query.filter_by(urun_id=urun_id).delete()
        OnTeklif.query.filter_by(urun_id=urun_id).delete()
        db.session.delete(urun)
        db.session.commit()
        onbellegi_temizle()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/muzik-ekle', methods=['POST'])
@admin_required
def muzik_ekle():
    dosya = request.files.get('muzik_dosyasi')
    url, _ = kaydet_guvenli_dosya(dosya)
    if url:
        yeni_muzik = Muzik(url=url)
        db.session.add(yeni_muzik)
        db.session.commit()
        onbellegi_temizle()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False, "mesaj": "Müzik dosyası yüklenemedi."})

@app.route('/musteri-dosya-sil', methods=['POST'])
@admin_required
def musteri_dosya_sil():
    veri = request.json or {}
    musteri_adi = (veri.get('musteri_adi') or '').strip()
    try:
        Teklif.query.filter(func.lower(Teklif.musteri_adi) == func.lower(musteri_adi)).delete()
        satilanlar = Urun.query.filter(func.lower(Urun.kazanan_adi) == func.lower(musteri_adi), Urun.durum == "Satıldı").all()
        for u in satilanlar:
            u.kazanan_adi = "Yok"
            u.durum = "Arşiv"
            db.session.add(u)
        db.session.commit()
        onbellegi_temizle()
        socketio.emit('veri_guncellendi')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/excel-indir', methods=['GET'])
@admin_required
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

@app.route('/sikayet-oneri-gonder', methods=['POST'])
def sikayet_oneri_gonder():
    veri = request.json or {}
    musteri_adi = veri.get('musteri_adi', 'Misafir')
    tur = veri.get('tur', 'Görüş / Tavsiye')
    konu = veri.get('konu', '')
    mesaj = veri.get('mesaj', '')
    if not konu or not mesaj: return jsonify({"success": False, "mesaj": "Konu ve mesaj alanları boş bırakılamaz."})
    yeni_kayit = SikayetOneri(musteri_adi=musteri_adi, tur=tur, konu=konu, mesaj=mesaj, ip_adresi=get_client_ip())
    db.session.add(yeni_kayit)
    db.session.commit()
    return jsonify({"success": True, "mesaj": "Geri bildiriminiz yöneticiye iletildi."})

@app.route('/sikayet-oneri-listele', methods=['GET'])
@admin_required
def sikayet_oneri_listele():
    try:
        kayitlar = SikayetOneri.query.order_by(SikayetOneri.id.desc()).all()
        sonuc = [{"id": k.id, "musteri_adi": k.musteri_adi, "tur": k.tur, "konu": k.konu, "mesaj": k.mesaj, "durum": k.durum, "ip": getattr(k, 'ip_adresi', '-'), "tarih": k.tarih.strftime('%d.%m.%Y %H:%M') if k.tarih else ''} for k in kayitlar]
        return jsonify({"kayitlar": sonuc})
    except Exception:
        return jsonify({"kayitlar": []})

@app.route('/sikayet-oneri-durum', methods=['POST'])
@admin_required
def sikayet_oneri_durum():
    veri = request.json or {}
    kayit = db.session.get(SikayetOneri, veri.get('id'))
    if kayit:
        kayit.durum = veri.get('durum')
        db.session.commit()
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/sikayet-oneri-sil', methods=['POST'])
@admin_required
def sikayet_oneri_sil():
    veri = request.json or {}
    kayit = db.session.get(SikayetOneri, veri.get('id'))
    if kayit:
        db.session.delete(kayit)
        db.session.commit()
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/itiraz-duzelt-bildir', methods=['POST'])
@admin_required
def itiraz_duzelt_bildir():
    veri = request.json or {}
    kayit = db.session.get(SikayetOneri, veri.get('id'))
    if kayit:
        kayit.durum = "Düzeltildi"
        db.session.commit()
        socketio.emit('itiraz_sonuc_bildirimi', {'musteri_adi': kayit.musteri_adi, 'konu': kayit.konu, 'mesaj': "Talebiniz/itirazınız yönetici tarafından incelenmiş ve düzeltilmiştir."})
        socketio.emit('veri_guncellendi')
        return jsonify({"success": True})
    return jsonify({"success": False})

# ==========================================
# SOCKETIO VE TEST
# ==========================================
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

@app.route('/loaderio-1db86bebd8b8f9e3d2992b34cb9aec68/')
@app.route('/loaderio-1db86bebd8b8f9e3d2992b34cb9aec68.txt')
@app.route('/loaderio-1db86bebd8b8f9e3d2992b34cb9aec68')
def loaderio_verification():
    return 'loaderio-1db86bebd8b8f9e3d2992b34cb9aec68'

veritabani_tablolari_onar()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)