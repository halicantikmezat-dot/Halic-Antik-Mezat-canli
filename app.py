# ==========================================
# GEVENT VE ASENKRON MOTOR (EN BASTA OLMALI)
# ==========================================
from gevent import monkey

monkey.patch_all()

try:
  import psycogreen.gevent

  psycogreen.gevent.patch_psycopg()
except Exception:
  pass

from datetime import datetime, timezone
import functools
from gevent.lock import BoundedSemaphore
import hashlib
import io
import logging
import os
import re
import time
import uuid
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import pyotp
from sqlalchemy import or_, text
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# Terminal log kirliligini engelle
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)

# ==========================================
# VERITABANI VE UYGULAMA YAPILANDIRMASI
# ==========================================
db_url = os.environ.get("DATABASE_URL", "sqlite:///halic_mezat.db")
if db_url.startswith("postgres://"):
  db_url = db_url.replace("postgres://", "postgresql://", 1)

IS_SQLITE = "sqlite" in db_url

ADMIN_PASSWORD_HASH = os.environ.get(
    "ADMIN_PASSWORD_HASH",
    generate_password_hash(os.environ.get("ADMIN_PASSWORD", "1453")),
)
ADMIN_2FA_SECRET = os.environ.get("ADMIN_2FA_SECRET", "JBSWY3DPEHPK3PXP")

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "halic_hamid_antik_mezat_gizli_anahtar_1453"
)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

if IS_SQLITE:
  app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"timeout": 30}}
else:
  app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
      "pool_size": 10,
      "max_overflow": 20,
      "pool_recycle": 300,
      "pool_pre_ping": True,
  }

app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024

if os.path.exists("/var/data"):
  UPLOAD_FOLDER = "/var/data/uploads"
else:
  UPLOAD_FOLDER = os.path.join(
      os.path.abspath(os.path.dirname(__file__)), "static", "uploads"
  )

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

db = SQLAlchemy(app)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="gevent",
    ping_timeout=60,
    ping_interval=25,
)

totp = pyotp.TOTP(ADMIN_2FA_SECRET)
mezat_kilidi = BoundedSemaphore(1)


def suan_utc():
  return datetime.now(timezone.utc)


@app.teardown_appcontext
def shutdown_session(exception=None):
  db.session.remove()


# ==========================================
# YARDIMCI VE GUVENLIK METOTLARI
# ==========================================
def admin_required(f):

  @functools.wraps(f)
  def decorated_function(*args, **kwargs):
    if session.get("is_admin") is True and session.get("admin_2fa_ok") is True:
      return f(*args, **kwargs)

    if request.is_json or request.path.startswith("/api/"):
      return (
          jsonify({
              "success": False,
              "mesaj": (
                  "Yetkisiz erişim! 2FA doğrulanmış yönetici girişi yapınız."
              ),
          }),
          403,
      )

    req_pass = request.args.get("sifre")
    if req_pass and check_password_hash(ADMIN_PASSWORD_HASH, req_pass):
      session["admin_sifre_ok"] = True
      return redirect(url_for("admin_2fa_ekrani"))

    return redirect(url_for("admin_login_ekrani"))

  return decorated_function


def get_client_ip():
  if request.headers.getlist("X-Forwarded-For"):
    return request.headers.getlist("X-Forwarded-For")[0].split(",")[0].strip()
  return request.remote_addr or "127.0.0.1"


def get_device_fingerprint():
  ip = get_client_ip()
  user_agent = request.headers.get("User-Agent", "")
  accept_lang = request.headers.get("Accept-Language", "")
  raw_str = f"{ip}-{user_agent}-{accept_lang}"
  return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


def kaydet_guvenli_dosya(dosya):
  if not dosya or not dosya.filename:
    return None, None
  temiz_ad = secure_filename(dosya.filename)
  benzersiz_ad = f"{uuid.uuid4().hex[:10]}_{temiz_ad}"
  dosya_yolu = os.path.join(app.config["UPLOAD_FOLDER"], benzersiz_ad)
  dosya.save(dosya_yolu)
  return f"/static/uploads/{benzersiz_ad}", temiz_ad


def saf_isim_temizle(isim):
  if not isim:
    return ""
  temiz = re.sub(r"\[.*?\]", "", str(isim)).strip()
  temiz = (
      temiz.replace("I", "ı")
      .replace("İ", "i")
      .replace("ş", "s")
      .replace("Ş", "s")
  )
  temiz = (
      temiz.replace("ğ", "g")
      .replace("Ğ", "g")
      .replace("ü", "u")
      .replace("Ü", "u")
  )
  temiz = (
      temiz.replace("ö", "o")
      .replace("Ö", "o")
      .replace("ç", "c")
      .replace("Ç", "c")
      .lower()
  )
  return " ".join(temiz.split())


# ==========================================
# VERITABANI MODELLERI
# ==========================================
class Kullanici(db.Model):
  __tablename__ = "kullanici"
  id = db.Column(db.Integer, primary_key=True)
  ad_soyad = db.Column(db.String(100), nullable=False)
  telefon = db.Column(db.String(20), nullable=True, index=True)
  email = db.Column(db.String(100), nullable=True)
  adres = db.Column(db.Text, nullable=True)
  sifre_hash = db.Column(db.String(255), nullable=True)
  bonus = db.Column(db.Float, default=0.0)
  puan = db.Column(db.Float, default=100.0)
  onayli_mi = db.Column(db.Boolean, default=False)
  durum = db.Column(db.String(20), default="bekliyor", index=True)
  sozlesme_onay = db.Column(db.Boolean, default=True)
  ip_adresi = db.Column(db.String(50), nullable=True)
  cihaz_kodu = db.Column(db.String(64), nullable=True)
  kayit_tarihi = db.Column(db.DateTime, default=suan_utc)
  silindi_mi = db.Column(db.Boolean, default=False, index=True)
  silinme_tarihi = db.Column(db.DateTime, nullable=True)


class Urun(db.Model):
  __tablename__ = "urun"
  id = db.Column(db.Integer, primary_key=True)
  lot_no = db.Column(db.Integer, nullable=False, index=True)
  urun_adi = db.Column(db.String(200), nullable=False)
  kategori = db.Column(db.String(100), default="Hediyelik eşya", index=True)
  acilis_fiyati = db.Column(db.Float, nullable=False, default=0.0)
  guncel_fiyat = db.Column(db.Float, nullable=False, default=0.0)
  hemen_al_fiyati = db.Column(db.Float, nullable=True, default=0.0)
  tanitim_yazisi = db.Column(db.Text, nullable=True)
  fotograflar = db.Column(db.JSON, default=list)
  video = db.Column(db.String(300), nullable=True, default="")
  ses_dosyasi = db.Column(db.String(300), nullable=True, default="")
  durum = db.Column(db.String(20), default="Aktif", index=True)
  kazanan_adi = db.Column(db.String(100), nullable=True, default="Yok")
  kazanan_id = db.Column(
      db.Integer, db.ForeignKey("kullanici.id"), nullable=True
  )
  silindi_mi = db.Column(db.Boolean, default=False, index=True)
  silinme_tarihi = db.Column(db.DateTime, nullable=True)

  def to_dict(self, hafif=False):
    fiyat_val = float(self.acilis_fiyati or 0.0)
    guncel_val = float(
        self.guncel_fiyat if self.guncel_fiyat is not None else fiyat_val
    )
    hemen_val = float(self.hemen_al_fiyati or 0.0)

    # Vitrin ve büyük listeler için hafif mod (Megabaytlarca JSON yükünü önler)
    if hafif:
      return {
          "id": self.id,
          "lot": self.lot_no,
          "ad": self.urun_adi,
          "kategori": self.kategori or "Genel",
          "fiyat": fiyat_val,
          "guncel_fiyat": guncel_val,
          "hemen_al_fiyat": hemen_val,
          "fotograflar": (self.fotograflar or [])[:1],
          "durum": self.durum or "Aktif",
      }

    return {
        "id": self.id,
        "lot": self.lot_no,
        "ad": self.urun_adi,
        "kategori": self.kategori or "Genel",
        "fiyat": fiyat_val,
        "guncel_fiyat": guncel_val,
        "hemen_al_fiyat": hemen_val,
        "hemen_al_fiyati": hemen_val,
        "tanitim_yazisi": self.tanitim_yazisi or "",
        "fotograflar": self.fotograflar or [],
        "video": self.video or "",
        "ses": self.ses_dosyasi or "",
        "durum": self.durum or "Aktif",
        "kazanan": self.kazanan_adi or "Yok",
        "kazanan_id": self.kazanan_id,
        "silindi_mi": bool(self.silindi_mi),
    }


class OnTeklif(db.Model):
  __tablename__ = "on_teklif"
  id = db.Column(db.Integer, primary_key=True)
  urun_id = db.Column(
      db.Integer, db.ForeignKey("urun.id"), nullable=False, index=True
  )
  musteri_id = db.Column(
      db.Integer, db.ForeignKey("kullanici.id"), nullable=True
  )
  musteri_adi = db.Column(db.String(100), nullable=False)
  teklif = db.Column(db.Float, nullable=False)
  zaman = db.Column(db.String(20), default=lambda: time.strftime("%H:%M:%S"))


class Teklif(db.Model):
  __tablename__ = "teklif"
  id = db.Column(db.Integer, primary_key=True)
  urun_id = db.Column(
      db.Integer, db.ForeignKey("urun.id"), nullable=False, index=True
  )
  musteri_id = db.Column(
      db.Integer, db.ForeignKey("kullanici.id"), nullable=True
  )
  musteri_adi = db.Column(db.String(100), nullable=False)
  tutar = db.Column(db.Float, nullable=False)
  ip_adresi = db.Column(db.String(50), nullable=True)
  tarih = db.Column(db.DateTime, default=suan_utc)


class SikayetOneri(db.Model):
  __tablename__ = "sikayet_oneri"
  id = db.Column(db.Integer, primary_key=True)
  musteri_adi = db.Column(db.String(100), nullable=True)
  tur = db.Column(db.String(50), default="Görüş / Tavsiye")
  konu = db.Column(db.String(200), nullable=False)
  mesaj = db.Column(db.Text, nullable=False)
  durum = db.Column(db.String(20), default="Yeni")
  ip_adresi = db.Column(db.String(50), nullable=True)
  tarih = db.Column(db.DateTime, default=suan_utc)


class Muzik(db.Model):
  __tablename__ = "muzik"
  id = db.Column(db.Integer, primary_key=True)
  url = db.Column(db.String(300), nullable=False)


class SepetItem(db.Model):
  __tablename__ = "sepet_item"
  id = db.Column(db.Integer, primary_key=True)
  musteri_id = db.Column(
      db.Integer, db.ForeignKey("kullanici.id"), nullable=False, index=True
  )
  urun_id = db.Column(db.Integer, db.ForeignKey("urun.id"), nullable=False)
  eklenme_tarihi = db.Column(db.DateTime, default=suan_utc)


class UrunTakip(db.Model):
  __tablename__ = "urun_takip"
  id = db.Column(db.Integer, primary_key=True)
  musteri_id = db.Column(
      db.Integer, db.ForeignKey("kullanici.id"), nullable=False
  )
  urun_id = db.Column(
      db.Integer, db.ForeignKey("urun.id"), nullable=False, index=True
  )
  tarih = db.Column(db.DateTime, default=suan_utc)


def urunu_kazanana_sepete_ekle(urun_id, kazanan_adi, kazanan_id=None):
  if not kazanan_adi or kazanan_adi in ["Yok", "None", "-", ""]:
    return

  if (
      not kazanan_id
      and ("[" in kazanan_adi and "]" in kazanan_adi)
      and not kazanan_adi.startswith("[Web]")
  ):
    return

  hedef_kullanici = None
  if kazanan_id:
    hedef_kullanici = db.session.get(Kullanici, kazanan_id)

  if not hedef_kullanici:
    temiz_ad = saf_isim_temizle(kazanan_adi)
    if not temiz_ad:
      return
    hedef_kullanici = Kullanici.query.filter(
        Kullanici.silindi_mi == False, Kullanici.ad_soyad.ilike(temiz_ad)
    ).first()

  if hedef_kullanici:
    var_mi = SepetItem.query.filter_by(
        musteri_id=hedef_kullanici.id, urun_id=urun_id
    ).first()
    if not var_mi:
      yeni_item = SepetItem(musteri_id=hedef_kullanici.id, urun_id=urun_id)
      db.session.add(yeni_item)
      db.session.commit()


# ==========================================
# CANLI MEZAT DURUM VE SAYAC
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
    "kazanan": "Yok",
    "kazanan_id": None,
}

_son_durum_verisi = None
_son_durum_zamani = 0
_son_canli_verisi = None
_son_canli_zamani = 0


def onbellegi_temizle():
  global _son_canli_verisi, _son_canli_zamani, _son_durum_verisi, _son_durum_zamani
  _son_canli_verisi = None
  _son_canli_zamani = 0
  _son_durum_verisi = None
  _son_durum_zamani = 0


def geri_sayim_gorevi(saniye, gorev_id):
  global sayac_kalan, sayac_aktif, mezat_durumu, aktif_urun_id, sayac_gorev_id
  sayac_kalan = saniye
  sayac_aktif = True

  while sayac_aktif and sayac_gorev_id == gorev_id:
    if sayac_kalan <= 0:
      break
    socketio.emit("sayac_guncelle", {"kalan": sayac_kalan})
    socketio.sleep(1)
    if sayac_aktif and sayac_gorev_id == gorev_id:
      sayac_kalan -= 1

  with mezat_kilidi:
    if sayac_aktif and sayac_gorev_id == gorev_id:
      sayac_aktif = False
      mezat_durumu["durum"] = "Satıldı"
      mezat_durumu["sure_bitis"] = 0
      biten_urun_id = aktif_urun_id
      aktif_urun_id = None

      with app.app_context():
        if biten_urun_id:
          urun = db.session.get(Urun, biten_urun_id)
          if urun:
            urun.durum = "Satıldı"
            urun.kazanan_adi = mezat_durumu["kazanan"]
            urun.kazanan_id = mezat_durumu["kazanan_id"]
            urun.guncel_fiyat = (
                float(mezat_durumu["pey"])
                if float(mezat_durumu["pey"]) > 0
                else float(urun.acilis_fiyati or 0)
            )
            db.session.add(urun)
            OnTeklif.query.filter_by(urun_id=biten_urun_id).delete()
            db.session.commit()
            urunu_kazanana_sepete_ekle(
                biten_urun_id,
                mezat_durumu["kazanan"],
                mezat_durumu["kazanan_id"],
            )

      onbellegi_temizle()
      socketio.emit(
          "sayac_bitti",
          {
              "urun_id": biten_urun_id,
              "mesaj": "Süre doldu!",
              "kazanan": mezat_durumu["kazanan"],
              "kazanan_id": mezat_durumu["kazanan_id"],
              "fiyat": mezat_durumu["pey"],
          },
      )


def veritabani_tablolari_onar():
  with app.app_context():
    try:
      db.create_all()
      kolonlar = [
          ("kullanici", "ip_adresi", "VARCHAR(50)"),
          ("kullanici", "cihaz_kodu", "VARCHAR(64)"),
          ("kullanici", "sifre_hash", "VARCHAR(255)"),
          ("kullanici", "puan", "FLOAT DEFAULT 100.0"),
          ("kullanici", "bonus", "FLOAT DEFAULT 0.0"),
          ("kullanici", "durum", "VARCHAR(20) DEFAULT 'bekliyor'"),
          ("kullanici", "onayli_mi", "BOOLEAN DEFAULT FALSE"),
          ("kullanici", "sozlesme_onay", "BOOLEAN DEFAULT TRUE"),
          ("kullanici", "kayit_tarihi", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
          ("kullanici", "silindi_mi", "BOOLEAN DEFAULT FALSE"),
          ("kullanici", "silinme_tarihi", "TIMESTAMP"),
          ("urun", "hemen_al_fiyati", "FLOAT DEFAULT 0.0"),
          ("urun", "kazanan_adi", "VARCHAR(100) DEFAULT 'Yok'"),
          ("urun", "kazanan_id", "INTEGER"),
          ("urun", "guncel_fiyat", "FLOAT DEFAULT 0.0"),
          ("urun", "silindi_mi", "BOOLEAN DEFAULT FALSE"),
          ("urun", "silinme_tarihi", "TIMESTAMP"),
          ("on_teklif", "musteri_id", "INTEGER"),
          ("teklif", "musteri_id", "INTEGER"),
      ]
      for tablo, kolon, tip in kolonlar:
        try:
          db.session.execute(
              text(f"ALTER TABLE {tablo} ADD COLUMN {kolon} {tip};")
          )
          db.session.commit()
        except Exception:
          db.session.rollback()
    except Exception:
      pass


@app.route("/static/uploads/<path:filename>")
def serve_uploads(filename):
  return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ==========================================
# IZLEYICI VE VITRIN ROTALARI (OPTIMIZE EDILDI)
# ==========================================
@app.route("/")
def izleyici_index():
  return render_template("index.html")


@app.route("/vitrin")
def vitrin():
  return render_template("vitrin.html")


@app.route("/overlay")
def overlay_view():
  return render_template("overlay.html")


@app.route("/vitrin-urunler", methods=["GET"])
def vitrin_urunler():
  try:
    sayfa = max(1, int(request.args.get("sayfa", 1)))
    limit = min(60, max(10, int(request.args.get("limit", 40))))
    kategori = (request.args.get("kategori") or "").strip()
    arama = (request.args.get("arama") or "").strip()

    sorgu = Urun.query.filter(Urun.durum != "Satıldı", Urun.silindi_mi == False)

    if kategori and kategori not in ["Tüm Kategoriler", ""]:
      sorgu = sorgu.filter(Urun.kategori == kategori)

    if arama:
      if arama.isdigit():
        sorgu = sorgu.filter(
            or_(Urun.lot_no == int(arama), Urun.urun_adi.ilike(f"%{arama}%"))
        )
      else:
        sorgu = sorgu.filter(Urun.urun_adi.ilike(f"%{arama}%"))

    toplam_kayit = sorgu.count()
    toplam_sayfa = (toplam_kayit + limit - 1) // limit
    urunler = (
        sorgu.order_by(Urun.lot_no.asc())
        .offset((sayfa - 1) * limit)
        .limit(limit)
        .all()
    )

    return jsonify({
        "urunler": [u.to_dict(hafif=True) for u in urunler],
        "toplam_kayit": toplam_kayit,
        "toplam_sayfa": max(1, toplam_sayfa),
        "sayfa": sayfa,
    })
  finally:
    db.session.remove()


@app.route("/canli-durum", methods=["GET"])
def canli_durum():
  global mezat_durumu, aktif_urun_id, _son_canli_verisi, _son_canli_zamani
  suan = time.time()
  if _son_canli_verisi and (suan - _son_canli_zamani < 0.3):
    return jsonify(_son_canli_verisi)

  try:
    aktif_urun_obj = (
        db.session.get(Urun, aktif_urun_id) if aktif_urun_id else None
    )
    yanit = {
        "durum": mezat_durumu["durum"],
        "sure_bitis": mezat_durumu["sure_bitis"],
        "pey": mezat_durumu["pey"],
        "kazanan": mezat_durumu["kazanan"],
        "kazanan_id": mezat_durumu.get("kazanan_id"),
        "aktif_urun": (
            aktif_urun_obj.to_dict()
            if (aktif_urun_obj and not aktif_urun_obj.silindi_mi)
            else None
        ),
    }
    _son_canli_verisi = yanit
    _son_canli_zamani = suan
    return jsonify(yanit)
  finally:
    db.session.remove()


# ==========================================
# DURUM GETIR (10 BINLERCE URUN ICIN MAXIMUM HIZ)
# ==========================================
@app.route("/durum-getir", methods=["GET"])
def durum_getir():
  global mezat_durumu, aktif_urun_id, _son_durum_verisi, _son_durum_zamani
  suan = time.time()
  if _son_durum_verisi and (suan - _son_durum_zamani < 1.0):
    return jsonify(_son_durum_verisi)

  try:
    aktif_urun_obj = (
        db.session.get(Urun, aktif_urun_id) if aktif_urun_id else None
    )
    aktif_urun = (
        aktif_urun_obj.to_dict()
        if (aktif_urun_obj and not aktif_urun_obj.silindi_mi)
        else None
    )

    # 10 binlerce ürünü tek seferde çekip JSON'a boğmak yerine:
    # Aktif havuzdaki ilk 100 ürünü hızlıca çekiyoruz
    urunler_db = (
        Urun.query.filter_by(silindi_mi=False)
        .filter(Urun.durum != "Satıldı")
        .order_by(Urun.lot_no.asc())
        .limit(100)
        .all()
    )
    urunler = [u.to_dict(hafif=True) for u in urunler_db]

    # Müşterilerden sadece onaylı ve bekleyen son 100 kişiyi al
    tum_kullanicilar = (
        Kullanici.query.filter_by(silindi_mi=False)
        .order_by(Kullanici.id.desc())
        .limit(100)
        .all()
    )
    kullanici_tel_map = {k.id: (k.telefon or "-") for k in tum_kullanicilar}

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
        "ip": m.ip_adresi or "-",
    } for m in tum_kullanicilar]

    satilan_urunler = (
        Urun.query.filter(
            or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
            Urun.silindi_mi == False,
        )
        .order_by(Urun.id.desc())
        .limit(60)
        .all()
    )

    gruplanmis_dosyalar = {}
    for u in satilan_urunler:
      m_adi = (u.kazanan_adi or "").strip()
      if not m_adi or m_adi.lower() in ["yok", "none", "-", ""]:
        continue

      grup_anahtari = str(u.kazanan_id) if u.kazanan_id else m_adi
      if grup_anahtari not in gruplanmis_dosyalar:
        gruplanmis_dosyalar[grup_anahtari] = {
            "musteri_id": u.kazanan_id,
            "musteri_adi": m_adi,
            "telefon": kullanici_tel_map.get(u.kazanan_id, "-"),
            "urunler": [],
            "toplam_tutar": 0.0,
        }

      satiss_fiyati = float(
          u.guncel_fiyat
          if (u.guncel_fiyat and u.guncel_fiyat > 0)
          else (u.acilis_fiyati or 0)
      )
      gruplanmis_dosyalar[grup_anahtari]["urunler"].append({
          "urun_id": u.id,
          "lot": u.lot_no,
          "urun_adi": u.urun_adi,
          "musteri_adi": m_adi,
          "fiyat": satiss_fiyati,
      })
      gruplanmis_dosyalar[grup_anahtari]["toplam_tutar"] += satiss_fiyati

    on_teklif_listesi = (
        OnTeklif.query.order_by(OnTeklif.id.desc()).limit(60).all()
    )
    on_teklif_urun_idler = [ot.urun_id for ot in on_teklif_listesi]
    ilgili_urunler = (
        {
            u.id: u
            for u in Urun.query.filter(Urun.id.in_(on_teklif_urun_idler)).all()
        }
        if on_teklif_urun_idler
        else {}
    )

    on_teklifler = []
    for ot in on_teklif_listesi:
      u = ilgili_urunler.get(ot.urun_id)
      if u and (u.durum == "Satıldı" or u.silindi_mi):
        continue
      on_teklifler.append({
          "id": ot.id,
          "urun_id": ot.urun_id,
          "lot": u.lot_no if u else "-",
          "urun_adi": u.urun_adi if u else "Arşivlenmiş Ürün",
          "musteri_adi": ot.musteri_adi,
          "teklif": ot.teklif,
          "zaman": getattr(ot, "zaman", ""),
      })

    muzikler = [m.url for m in Muzik.query.limit(20).all()]

    yanit = {
        "durum": mezat_durumu["durum"],
        "sure_bitis": mezat_durumu["sure_bitis"],
        "pey": mezat_durumu["pey"],
        "kazanan": mezat_durumu["kazanan"],
        "kazanan_id": mezat_durumu.get("kazanan_id"),
        "aktif_urun": aktif_urun,
        "urunler": urunler,
        "musteriler": musteriler,
        "satilan_urunler": [u.to_dict(hafif=True) for u in satilan_urunler],
        "gruplanmis_dosyalar": list(gruplanmis_dosyalar.values()),
        "on_teklifler": on_teklifler,
        "muzik_listesi": muzikler,
    }

    _son_durum_verisi = yanit
    _son_durum_zamani = suan
    return jsonify(yanit)
  except Exception as e:
    db.session.rollback()
    return jsonify({"error": str(e), "urunler": []}), 200
  finally:
    db.session.remove()


# ==========================================
# SEPET, SIPARIS GECMISI VE TAKIP SISTEMI
# ==========================================
@app.route("/sepete-ekle", methods=["POST"])
def sepete_ekle():
  veri = request.json or {}
  musteri_id = veri.get("musteri_id")
  urun_id = veri.get("urun_id")

  if not musteri_id or not urun_id:
    return jsonify(
        {"success": False, "mesaj": "Müşteri ve ürün bilgisi zorunludur."}
    )

  kullanici = db.session.get(Kullanici, int(musteri_id))
  urun = db.session.get(Urun, int(urun_id))

  if not kullanici or not urun:
    return jsonify(
        {"success": False, "mesaj": "Kullanıcı veya ürün bulunamadı."}
    )

  var_mi = SepetItem.query.filter_by(
      musteri_id=kullanici.id, urun_id=urun.id
  ).first()
  if var_mi:
    return jsonify({"success": False, "mesaj": "Bu ürün zaten sepetinizde ekli."})

  yeni_item = SepetItem(musteri_id=kullanici.id, urun_id=urun.id)
  db.session.add(yeni_item)
  db.session.commit()
  return jsonify({"success": True, "mesaj": "Ürün sepetinize eklendi."})


@app.route("/sepetten-cikar", methods=["POST"])
def sepetten_cikar():
  veri = request.json or {}
  musteri_id = veri.get("musteri_id")
  urun_id = veri.get("urun_id")
  sepet_id = veri.get("sepet_id")

  item = None
  if sepet_id:
    item = db.session.get(SepetItem, int(sepet_id))
  elif musteri_id and urun_id:
    item = SepetItem.query.filter_by(
        musteri_id=int(musteri_id), urun_id=int(urun_id)
    ).first()

  if item:
    db.session.delete(item)
    db.session.commit()
    return jsonify({"success": True, "mesaj": "Ürün sepetten çıkarıldı."})

  return jsonify({"success": False, "mesaj": "Ürün sepetinizde bulunamadı."})


@app.route("/sepet-getir/<int:musteri_id>", methods=["GET"])
def sepet_getir(musteri_id):
  kullanici = db.session.get(Kullanici, musteri_id) if musteri_id > 0 else None
  url_ad = request.args.get("ad", "").strip()

  if not kullanici and url_ad:
    saf_ad = saf_isim_temizle(url_ad)
    kullanici = Kullanici.query.filter(
        Kullanici.silindi_mi == False, Kullanici.ad_soyad.ilike(saf_ad)
    ).first()

  items = []
  if kullanici:
    items = SepetItem.query.filter_by(musteri_id=kullanici.id).all()

  ekli_urun_idler = set()
  sepet_listesi = []
  ara_toplam = 0.0

  for it in items:
    u = db.session.get(Urun, it.urun_id)
    if u and not u.silindi_mi:
      ekli_urun_idler.add(u.id)
      fiyat = float(
          u.guncel_fiyat
          if (u.guncel_fiyat and u.guncel_fiyat > 0)
          else (u.acilis_fiyati or 0)
      )
      ara_toplam += fiyat
      sepet_listesi.append({
          "sepet_id": it.id,
          "urun_id": u.id,
          "lot": u.lot_no,
          "ad": u.urun_adi,
          "fiyat": fiyat,
          "gorsel": (
              u.fotograflar[0]
              if (u.fotograflar and len(u.fotograflar) > 0)
              else ""
          ),
      })

  if kullanici:
    kazanilan_satilanlar = Urun.query.filter(
        or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
        Urun.silindi_mi == False,
        Urun.kazanan_id == kullanici.id,
    ).all()

    for u in kazanilan_satilanlar:
      if u.id not in ekli_urun_idler:
        fiyat = float(
            u.guncel_fiyat
            if (u.guncel_fiyat and u.guncel_fiyat > 0)
            else (u.acilis_fiyati or 0)
        )
        ara_toplam += fiyat
        sepet_listesi.append({
            "sepet_id": 0,
            "urun_id": u.id,
            "lot": u.lot_no,
            "ad": u.urun_adi,
            "fiyat": fiyat,
            "gorsel": (
                u.fotograflar[0]
                if (u.fotograflar and len(u.fotograflar) > 0)
                else ""
            ),
        })

  kdv_tutari = round(ara_toplam * 0.20, 2)
  kargo_ucreti = 150.0 if ara_toplam > 0 else 0.0
  genel_toplam = round(ara_toplam + kdv_tutari + kargo_ucreti, 2)

  return jsonify({
      "success": True,
      "urunler": sepet_listesi,
      "ara_toplam": ara_toplam,
      "kdv_tutari": kdv_tutari,
      "kargo_ucreti": kargo_ucreti,
      "genel_toplam": genel_toplam,
  })


@app.route("/siparis-gecmisi/<int:musteri_id>", methods=["GET"])
def siparis_gecmisi(musteri_id):
  kullanici = db.session.get(Kullanici, musteri_id) if musteri_id > 0 else None
  url_ad = request.args.get("ad", "").strip()

  if not kullanici and url_ad:
    saf_ad = saf_isim_temizle(url_ad)
    kullanici = Kullanici.query.filter(
        Kullanici.silindi_mi == False, Kullanici.ad_soyad.ilike(saf_ad)
    ).first()

  gecmis = []
  toplam_harcama = 0.0

  if kullanici:
    satilan_urunler = (
        Urun.query.filter(
            or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
            Urun.silindi_mi == False,
            Urun.kazanan_id == kullanici.id,
        )
        .order_by(Urun.id.desc())
        .all()
    )

    for u in satilan_urunler:
      tutar = float(
          u.guncel_fiyat
          if (u.guncel_fiyat and u.guncel_fiyat > 0)
          else (u.acilis_fiyati or 0)
      )
      toplam_harcama += tutar
      gecmis.append({
          "id": u.id,
          "lot": u.lot_no,
          "ad": u.urun_adi,
          "kategori": u.kategori,
          "fiyat": tutar,
          "gorsel": (
              u.fotograflar[0]
              if (u.fotograflar and len(u.fotograflar) > 0)
              else ""
          ),
      })

  return jsonify({
      "success": True,
      "musteri_adi": (kullanici.ad_soyad if kullanici else (url_ad or "Misafir")),
      "gecmis": gecmis,
      "toplam_harcama": toplam_harcama,
  })


@app.route("/urun-takip-et", methods=["POST"])
def urun_takip_et():
  veri = request.json or {}
  musteri_id = veri.get("musteri_id")
  urun_id = veri.get("urun_id")

  if not musteri_id or not urun_id:
    return jsonify({"success": False, "mesaj": "Bilgiler eksik."})

  var_mi = (
      db.session.query(UrunTakip)
      .filter_by(musteri_id=int(musteri_id), urun_id=int(urun_id))
      .first()
  )
  if var_mi:
    db.session.delete(var_mi)
    db.session.commit()
    return jsonify({
        "success": True,
        "takipte": False,
        "mesaj": "Ürün takip listenizden çıkarıldı.",
    })

  takip = UrunTakip(musteri_id=int(musteri_id), urun_id=int(urun_id))
  db.session.add(takip)
  db.session.commit()
  return jsonify({
      "success": True,
      "takipte": True,
      "mesaj": (
          "Ürün takibe alındı. Sahneye çıktığında veya pey verildiğinde"
          " bildirim alacaksınız."
      ),
  })


@app.route("/takip-ettiklerim/<int:musteri_id>", methods=["GET"])
def takip_ettiklerim(musteri_id):
  takipler = UrunTakip.query.filter_by(musteri_id=musteri_id).all()
  urun_idler = [t.urun_id for t in takipler]
  urunler = (
      Urun.query.filter(Urun.id.in_(urun_idler), Urun.silindi_mi == False).all()
      if urun_idler
      else []
  )
  return jsonify({
      "success": True,
      "urunler": [u.to_dict(hafif=True) for u in urunler],
  })


# ==========================================
# PEY VE SATIN ALMA (KILITLI & ROLLBACK GUVENLI)
# ==========================================
@app.route("/pey-ver", methods=["POST"])
def pey_ver():
  global mezat_durumu, aktif_urun_id, sayac_kalan, sayac_aktif
  veri = request.json or {}
  client_ip = get_client_ip()

  musteri_id = veri.get("musteri_id")
  musteri_adi = (veri.get("musteri_adi") or veri.get("isim") or "").strip()
  telefon = (veri.get("telefon") or "").strip()

  kullanici = None
  if musteri_id:
    kullanici = db.session.get(Kullanici, int(musteri_id))
  elif telefon:
    kullanici = Kullanici.query.filter_by(telefon=telefon).first()
  elif musteri_adi:
    saf_istek = saf_isim_temizle(musteri_adi)
    kullanici = Kullanici.query.filter(
        Kullanici.silindi_mi == False, Kullanici.ad_soyad.ilike(saf_istek)
    ).first()

  if not kullanici or kullanici.silindi_mi:
    return jsonify({
        "success": False,
        "mesaj": "Teklif verebilmek için profilinizi oluşturunuz.",
    })

  if kullanici.durum == "engellendi":
    return jsonify({
        "success": False,
        "mesaj": "Hesabınız engellendiği için teklif veremezsiniz.",
    })

  etiket_ad = f"[Web] {kullanici.ad_soyad}"
  raw_uid = veri.get("urun_id") or aktif_urun_id
  try:
    urun_id = int(raw_uid) if raw_uid else aktif_urun_id
  except Exception:
    urun_id = aktif_urun_id

  if not urun_id:
    return jsonify({"success": False, "mesaj": "Aktif bir ürün seçili değil!"})

  with mezat_kilidi:
    try:
      sorgu = Urun.query
      if not IS_SQLITE:
        sorgu = sorgu.with_for_update()
      urun = sorgu.filter_by(id=urun_id).first()

      if (
          not urun
          or urun.silindi_mi
          or urun.durum == "Satıldı"
          or mezat_durumu.get("durum") == "Satıldı"
      ):
        return jsonify({
            "success": False,
            "mesaj": "⚠️ Bu ürün satılmıştır veya aktif mezatta değildir!",
        })

      islem = veri.get("islem", "pey")

      if islem == "hemen_al":
        hemen_al_fiyat = float(
            urun.hemen_al_fiyati
            if (urun.hemen_al_fiyati and urun.hemen_al_fiyati > 0)
            else (urun.guncel_fiyat or urun.acilis_fiyati)
        )
        sayac_aktif = False

        urun.durum = "Satıldı"
        urun.guncel_fiyat = hemen_al_fiyat
        urun.kazanan_adi = etiket_ad
        urun.kazanan_id = kullanici.id

        mezat_durumu["durum"] = "Satıldı"
        mezat_durumu["sure_bitis"] = 0
        mezat_durumu["kazanan"] = etiket_ad
        mezat_durumu["kazanan_id"] = kullanici.id
        mezat_durumu["pey"] = hemen_al_fiyat
        aktif_urun_id = None

        yeni_teklif = Teklif(
            urun_id=urun.id,
            musteri_id=kullanici.id,
            musteri_adi=etiket_ad,
            tutar=hemen_al_fiyat,
            ip_adresi=client_ip,
        )
        db.session.add(yeni_teklif)
        db.session.add(urun)
        OnTeklif.query.filter_by(urun_id=urun.id).delete()
        db.session.commit()

        urunu_kazanana_sepete_ekle(urun.id, etiket_ad, kullanici.id)
        onbellegi_temizle()
        socketio.emit(
            "pey_guncellendi",
            {
                "urun_id": urun.id,
                "pey": hemen_al_fiyat,
                "kazanan": etiket_ad,
                "kazanan_id": kullanici.id,
            },
        )
        socketio.emit(
            "sayac_bitti",
            {
                "urun_id": urun.id,
                "kazanan": etiket_ad,
                "kazanan_id": kullanici.id,
                "fiyat": hemen_al_fiyat,
            },
        )
        return jsonify({
            "success": True,
            "guncel_fiyat": hemen_al_fiyat,
            "kazanan": etiket_ad,
        })

      if islem == "pey":
        mevcut_fiyat = float(
            mezat_durumu["pey"]
            if mezat_durumu["pey"] > 0
            else (urun.acilis_fiyati or 0)
        )
        miktar = float(veri.get("miktar", 0))

        if miktar == 0:
          hesaplanan_artis = max(10.0, round(mevcut_fiyat * 0.10))
          miktar = round(mevcut_fiyat + hesaplanan_artis, 2)

        if miktar <= mevcut_fiyat:
          return jsonify({
              "success": False,
              "mesaj": (
                  f"Teklif mevcut fiyattan ({mevcut_fiyat} TL) yüksek olmalıdır!"
              ),
          })

        mezat_durumu["pey"] = miktar
        mezat_durumu["kazanan"] = etiket_ad
        mezat_durumu["kazanan_id"] = kullanici.id
        urun.guncel_fiyat = miktar
        urun.kazanan_adi = etiket_ad
        urun.kazanan_id = kullanici.id

        yeni_teklif = Teklif(
            urun_id=urun.id,
            musteri_id=kullanici.id,
            musteri_adi=etiket_ad,
            tutar=miktar,
            ip_adresi=client_ip,
        )
        db.session.add(yeni_teklif)
        db.session.add(urun)
        db.session.commit()

        takip_edenler = [
            t.musteri_id
            for t in UrunTakip.query.filter_by(urun_id=urun.id).all()
            if t.musteri_id != kullanici.id
        ]
        if takip_edenler:
          socketio.emit(
              "urun_takip_bildirimi",
              {
                  "urun_id": urun.id,
                  "lot_no": urun.lot_no,
                  "urun_adi": urun.urun_adi,
                  "yeni_pey": miktar,
                  "hedef_musteriler": takip_edenler,
                  "mesaj": (
                      f"Takip ettiğiniz '{urun.urun_adi}' (Lot #{urun.lot_no})"
                      f" ürününe {miktar} TL pey verildi!"
                  ),
              },
          )

        if sayac_aktif and sayac_kalan <= 10:
          sayac_kalan = 15
          mezat_durumu["sure_bitis"] = time.time() + 15
          socketio.emit(
              "sayac_uzatildi",
              {
                  "kalan": 15,
                  "mesaj": "Son saniye teklifi nedeniyle süre 15 sn uzatıldı!",
              },
          )
          socketio.emit("sayac_guncelle", {"kalan": 15})

        onbellegi_temizle()
        socketio.emit(
            "pey_guncellendi",
            {
                "urun_id": urun.id,
                "pey": miktar,
                "kazanan": etiket_ad,
                "kazanan_id": kullanici.id,
            },
        )
        return jsonify(
            {"success": True, "guncel_fiyat": miktar, "kazanan": etiket_ad}
        )
    except Exception as e:
      db.session.rollback()
      return (
          jsonify({"success": False, "mesaj": f"Veritabanı hatası: {str(e)}"}),
          500,
      )
    finally:
      db.session.remove()


# ==========================================
# KAYIT & BILDIRIMLER
# ==========================================
@app.route("/kayit-ol", methods=["POST"])
def kayit_ol():
  veri = request.json or {}
  ad = (veri.get("ad") or "").strip()
  tel = (veri.get("tel") or "").strip()
  mail = (veri.get("mail") or "").strip()
  adres = (veri.get("adres") or "").strip()
  sozlesme = bool(veri.get("sozlesme", True))

  if not ad or not tel:
    return jsonify(
        {"success": False, "mesaj": "Ad Soyad ve Telefon zorunludur."}
    )

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
    kullanici.silindi_mi = False
    kullanici.durum = "onayli"
    kullanici.onayli_mi = True
  else:
    kullanici = Kullanici(
        ad_soyad=ad,
        telefon=tel,
        email=mail,
        adres=adres,
        sozlesme_onay=sozlesme,
        onayli_mi=True,
        durum="onayli",
        ip_adresi=client_ip,
        cihaz_kodu=fingerprint,
    )
    db.session.add(kullanici)

  db.session.commit()
  return jsonify({
      "success": True,
      "musteri_id": kullanici.id,
      "mesaj": "Bilgileriniz kaydedildi.",
  })


@app.route("/on-teklif-ver", methods=["POST"])
def on_teklif_ver():
  veri = request.json or {}
  raw_uid = veri.get("urun_id")
  musteri_id = veri.get("musteri_id")
  musteri_adi = (veri.get("musteri_adi") or "").strip()
  teklif_tutari = float(veri.get("teklif", 0))

  try:
    urun_id = int(raw_uid)
  except Exception:
    return jsonify({"success": False, "mesaj": "Geçersiz ürün ID!"})

  if musteri_id and not musteri_adi:
    k = db.session.get(Kullanici, int(musteri_id))
    if k:
      musteri_adi = k.ad_soyad

  if not musteri_adi:
    return jsonify({"success": False, "mesaj": "Müşteri bilgisi belirtilmedi!"})

  urun = db.session.get(Urun, urun_id)
  if not urun or urun.silindi_mi or urun.durum == "Satıldı":
    return jsonify({"success": False, "mesaj": "⚠️ Bu ürün teklife kapalıdır!"})

  acilis = float(urun.acilis_fiyati or 0)
  if teklif_tutari < acilis:
    return jsonify({
        "success": False,
        "mesaj": (
            f"Ön teklif açılış fiyatından ({acilis} TL) düşük olamaz!"
        ),
    })

  en_yuksek = (
      OnTeklif.query.filter_by(urun_id=urun_id)
      .order_by(OnTeklif.teklif.desc())
      .first()
  )
  if en_yuksek and teklif_tutari <= en_yuksek.teklif:
    return jsonify({
        "success": False,
        "mesaj": (
            "Teklifiniz mevcut en yüksek ön tekliften"
            f" ({en_yuksek.teklif} TL) büyük olmalıdır!"
        ),
    })

  yeni_ot = OnTeklif(
      urun_id=urun_id,
      musteri_id=musteri_id,
      musteri_adi=musteri_adi,
      teklif=teklif_tutari,
  )
  db.session.add(yeni_ot)
  urun.guncel_fiyat = teklif_tutari
  db.session.add(urun)
  db.session.commit()

  onbellegi_temizle()
  return jsonify(
      {"success": True, "mesaj": "✅ Ön teklifiniz başarıyla kaydedildi."}
  )


@app.route("/sikayet-oneri-gonder", methods=["POST"])
def sikayet_oneri_gonder():
  veri = request.json or {}
  musteri_adi = veri.get("musteri_adi", "Misafir")
  tur = veri.get("tur", "Görüş / Tavsiye")
  konu = (veri.get("konu") or "").strip()
  mesaj = (veri.get("mesaj") or "").strip()

  if not konu or not mesaj:
    return jsonify({"success": False, "mesaj": "Konu ve mesaj zorunludur."})

  so = SikayetOneri(
      musteri_adi=musteri_adi,
      tur=tur,
      konu=konu,
      mesaj=mesaj,
      ip_adresi=get_client_ip(),
  )
  db.session.add(so)
  db.session.commit()
  return jsonify({"success": True, "mesaj": "Geri bildiriminiz iletildi."})


# ==========================================
# YONETICI KONTROLLERI VE 2FA ROTALARI
# ==========================================
LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Haliç Antik - Yönetici Girişi</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .box { background: #1e293b; padding: 30px; border-radius: 12px; border: 1px solid #334155; width: 340px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; }
        h2 { color: #f59e0b; margin-top: 0; font-size: 20px; }
        input { width: 100%; padding: 12px; margin: 12px 0; border: 1px solid #475569; border-radius: 6px; background: #0f172a; color: #fff; box-sizing: border-box; text-align: center; font-size: 16px; }
        button { width: 100%; padding: 12px; background: #f59e0b; color: #000; font-weight: bold; border: none; border-radius: 6px; cursor: pointer; font-size: 15px; }
        button:hover { background: #d97706; }
        .hata { color: #ef4444; font-size: 14px; margin-top: 10px; display: none; }
    </style>
</head>
<body>
    <div class="box">
        <h2>🏛️ Yönetici Girişi</h2>
        <p style="font-size: 13px; color: #94a3b8;">Lütfen panel şifrenizi giriniz.</p>
        <form id="loginForm">
            <input type="password" id="sifre" placeholder="Şifrenizi Girin" required autofocus>
            <button type="submit">Devam Et</button>
        </form>
        <div id="hata" class="hata"></div>
    </div>
    <script>
        document.getElementById('loginForm').onsubmit = async (e) => {
            e.preventDefault();
            const res = await fetch('/admin-login-post', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ sifre: document.getElementById('sifre').value })
            });
            const data = await res.json();
            if (data.success) {
                window.location.href = '/admin-2fa';
            } else {
                const h = document.getElementById('hata');
                h.innerText = data.mesaj;
                h.style.display = 'block';
            }
        };
    </script>
</body>
</html>
"""

TWO_FA_PAGE_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Haliç Antik - 2FA Doğrulama</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .box { background: #1e293b; padding: 30px; border-radius: 12px; border: 1px solid #334155; width: 340px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; }
        h2 { color: #f59e0b; margin-top: 0; font-size: 20px; }
        input { width: 100%; padding: 12px; margin: 12px 0; border: 1px solid #475569; border-radius: 6px; background: #0f172a; color: #fff; box-sizing: border-box; text-align: center; font-size: 20px; letter-spacing: 4px; }
        button { width: 100%; padding: 12px; background: #f59e0b; color: #000; font-weight: bold; border: none; border-radius: 6px; cursor: pointer; font-size: 15px; }
        button:hover { background: #d97706; }
        .hata { color: #ef4444; font-size: 14px; margin-top: 10px; display: none; }
    </style>
</head>
<body>
    <div class="box">
        <h2>🔒 İki Aşamalı Doğrulama</h2>
        <p style="font-size: 13px; color: #94a3b8;">Authenticator uygulamanızdaki 6 haneli kodu giriniz.</p>
        <form id="tfaForm">
            <input type="text" id="kod" placeholder="000000" maxlength="6" pattern="[0-9]*" required autofocus>
            <button type="submit">Onayla ve Giriş Yap</button>
        </form>
        <div id="hata" class="hata"></div>
    </div>
    <script>
        document.getElementById('tfaForm').onsubmit = async (e) => {
            e.preventDefault();
            const res = await fetch('/admin-2fa-dogrula', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ kod: document.getElementById('kod').value })
            });
            const data = await res.json();
            if (data.success) {
                window.location.href = '/admin';
            } else {
                const h = document.getElementById('hata');
                h.innerText = data.mesaj;
                h.style.display = 'block';
            }
        };
    </script>
</body>
</html>
"""


@app.route("/admin-login", methods=["GET"])
def admin_login_ekrani():
  if session.get("is_admin") and session.get("admin_2fa_ok"):
    return redirect(url_for("admin_panel"))
  return LOGIN_PAGE_HTML


@app.route("/admin-login-post", methods=["POST"])
def admin_login_post():
  veri = request.json or {}
  girilen_sifre = str(veri.get("sifre", ""))
  if check_password_hash(ADMIN_PASSWORD_HASH, girilen_sifre):
    session["admin_sifre_ok"] = True
    return jsonify(
        {"success": True, "mesaj": "Şifre doğrulandı, 2FA kodunu giriniz."}
    )
  return jsonify({"success": False, "mesaj": "Hatalı şifre!"}), 401


@app.route("/admin-2fa", methods=["GET"])
def admin_2fa_ekrani():
  if not session.get("admin_sifre_ok"):
    return redirect(url_for("admin_login_ekrani"))
  return TWO_FA_PAGE_HTML


@app.route("/admin-2fa-dogrula", methods=["POST"])
def admin_2fa_dogrula():
  if not session.get("admin_sifre_ok"):
    return (
        jsonify({
            "success": False,
            "mesaj": "Yetkisiz oturum! Önce şifrenizi girin.",
        }),
        401,
    )

  veri = request.json or {}
  girilen_kod = str(veri.get("kod", "")).strip()

  if totp.verify(girilen_kod, valid_window=1):
    session["is_admin"] = True
    session["admin_2fa_ok"] = True
    session.pop("admin_sifre_ok", None)
    return jsonify({
        "success": True,
        "mesaj": "Doğrulama başarılı, panele yönlendiriliyorsunuz.",
    })

  return (
      jsonify(
          {"success": False, "mesaj": "Geçersiz veya süresi dolmuş 2FA kodu!"}
      ),
      400,
  )


@app.route("/admin", methods=["GET"])
@admin_required
def admin_panel():
  return render_template("admin.html")


@app.route("/admin-sifre-degistir", methods=["POST"])
@admin_required
def admin_sifre_degistir():
  global ADMIN_PASSWORD_HASH
  veri = request.json or {}
  eski_sifre = str(veri.get("eski_sifre", ""))
  yeni_sifre = str(veri.get("yeni_sifre", ""))

  if not check_password_hash(ADMIN_PASSWORD_HASH, eski_sifre):
    return jsonify({"success": False, "mesaj": "Eski şifre hatalı!"})

  if not yeni_sifre or len(yeni_sifre) < 6:
    return jsonify(
        {"success": False, "mesaj": "Yeni şifre en az 6 karakter olmalıdır!"}
    )

  ADMIN_PASSWORD_HASH = generate_password_hash(yeni_sifre)
  return jsonify(
      {"success": True, "mesaj": "Yönetici şifresi başarıyla güncellendi."}
  )


@app.route("/admin-cikis-yap", methods=["POST", "GET"])
def admin_cikis_yap():
  session.pop("is_admin", None)
  session.pop("admin_2fa_ok", None)
  session.pop("admin_sifre_ok", None)
  return redirect(url_for("izleyici_index"))


# ==========================================
# MEZAT SAHNE & OPERASYON ROTALARI
# ==========================================
@app.route("/sahneye-al", methods=["POST"])
@admin_required
def sahneye_al():
  global mezat_durumu, aktif_urun_id, sayac_aktif
  veri = request.json or {}
  raw_id = veri.get("urun_id")
  try:
    urun_id = int(raw_id)
  except Exception:
    return jsonify({"success": False, "mesaj": "Geçersiz ürün ID!"})

  urun = db.session.get(Urun, urun_id)
  if not urun or urun.silindi_mi:
    return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})

  with mezat_kilidi:
    sayac_aktif = False
    aktif_urun_id = urun.id
    mezat_durumu["durum"] = "Bekliyor"
    mezat_durumu["sure_bitis"] = 0
    urun.durum = "Aktif"

    en_yuksek_ot = (
        OnTeklif.query.filter_by(urun_id=urun_id)
        .order_by(OnTeklif.teklif.desc())
        .first()
    )
    if en_yuksek_ot:
      mezat_durumu["pey"] = float(en_yuksek_ot.teklif)
      mezat_durumu["kazanan"] = en_yuksek_ot.musteri_adi
      mezat_durumu["kazanan_id"] = en_yuksek_ot.musteri_id
      urun.guncel_fiyat = float(en_yuksek_ot.teklif)
      urun.kazanan_adi = en_yuksek_ot.musteri_adi
      urun.kazanan_id = en_yuksek_ot.musteri_id
    else:
      mezat_durumu["pey"] = float(urun.acilis_fiyati or 0)
      mezat_durumu["kazanan"] = "Yok"
      mezat_durumu["kazanan_id"] = None
      urun.guncel_fiyat = float(urun.acilis_fiyati or 0)
      urun.kazanan_adi = "Yok"
      urun.kazanan_id = None

    db.session.add(urun)
    db.session.commit()
    onbellegi_temizle()

  takip_edenler = [
      t.musteri_id for t in UrunTakip.query.filter_by(urun_id=urun.id).all()
  ]
  if takip_edenler:
    socketio.emit(
        "urun_takip_bildirimi",
        {
            "urun_id": urun.id,
            "lot_no": urun.lot_no,
            "urun_adi": urun.urun_adi,
            "hedef_musteriler": takip_edenler,
            "mesaj": (
                f"Takip ettiğiniz '{urun.urun_adi}' (Lot #{urun.lot_no}) canlı"
                " mezatta satışa çıktı!"
            ),
        },
    )

  socketio.emit("yeni_sahne_urunu", urun.to_dict())
  socketio.emit(
      "pey_guncellendi",
      {
          "pey": mezat_durumu["pey"],
          "kazanan": mezat_durumu["kazanan"],
          "kazanan_id": mezat_durumu["kazanan_id"],
      },
  )
  return jsonify({"success": True, "urun": urun.to_dict()})


@app.route("/mezat-baslat", methods=["POST"])
@admin_required
def mezat_baslat():
  global mezat_durumu, aktif_urun_id, sayac_aktif, sayac_gorev_id
  veri = request.json or {}
  urun_id = veri.get("urun_id", aktif_urun_id)
  sure = int(veri.get("sure", 30))

  if urun_id:
    with mezat_kilidi:
      aktif_urun_id = urun_id
      mezat_durumu["durum"] = "Sayim"
      mezat_durumu["sure_bitis"] = time.time() + sure

      sayac_gorev_id += 1
      sayac_aktif = True

      socketio.start_background_task(geri_sayim_gorevi, sure, sayac_gorev_id)
      onbellegi_temizle()
    socketio.emit("mezat_basladi_muzik")
    return jsonify({"success": True})

  return jsonify({"success": False, "mesaj": "Ürün seçilmedi!"})


@app.route("/satis-bitir", methods=["POST"])
@admin_required
def satis_bitir():
  global mezat_durumu, aktif_urun_id, sayac_aktif, sayac_gorev_id
  with mezat_kilidi:
    sayac_aktif = False
    sayac_gorev_id += 1
    mezat_durumu["durum"] = "Satıldı"
    mezat_durumu["sure_bitis"] = 0
    biten_urun_id = aktif_urun_id
    aktif_urun_id = None

    if biten_urun_id:
      urun = db.session.get(Urun, biten_urun_id)
      if urun:
        urun.durum = "Satıldı"
        urun.kazanan_adi = mezat_durumu["kazanan"]
        urun.kazanan_id = mezat_durumu.get("kazanan_id")
        urun.guncel_fiyat = (
            float(mezat_durumu["pey"])
            if float(mezat_durumu["pey"]) > 0
            else float(urun.acilis_fiyati or 0)
        )
        db.session.add(urun)
        OnTeklif.query.filter_by(urun_id=biten_urun_id).delete()
        db.session.commit()
        urunu_kazanana_sepete_ekle(
            biten_urun_id, mezat_durumu["kazanan"], mezat_durumu.get("kazanan_id")
        )

    onbellegi_temizle()
    socketio.emit(
        "sayac_bitti",
        {
            "urun_id": biten_urun_id,
            "kazanan": mezat_durumu["kazanan"],
            "kazanan_id": mezat_durumu.get("kazanan_id"),
            "fiyat": mezat_durumu["pey"],
        },
    )
  return jsonify({"success": True})


@app.route("/son-peyi-iptal-et", methods=["POST"])
@admin_required
def son_peyi_iptal_et():
  global mezat_durumu, aktif_urun_id
  if not aktif_urun_id:
    return jsonify({"success": False, "mesaj": "Sahnede aktif ürün yok!"})

  with mezat_kilidi:
    son_teklif = (
        Teklif.query.filter_by(urun_id=aktif_urun_id)
        .order_by(Teklif.id.desc())
        .first()
    )
    if son_teklif:
      db.session.delete(son_teklif)
      db.session.commit()

    bir_onceki = (
        Teklif.query.filter_by(urun_id=aktif_urun_id)
        .order_by(Teklif.id.desc())
        .first()
    )
    urun = db.session.get(Urun, aktif_urun_id)
    if bir_onceki:
      mezat_durumu["pey"] = float(bir_onceki.tutar)
      mezat_durumu["kazanan"] = bir_onceki.musteri_adi
      mezat_durumu["kazanan_id"] = bir_onceki.musteri_id
      if urun:
        urun.guncel_fiyat = float(bir_onceki.tutar)
        urun.kazanan_adi = bir_onceki.musteri_adi
        urun.kazanan_id = bir_onceki.musteri_id
    else:
      mezat_durumu["pey"] = float(urun.acilis_fiyati or 0) if urun else 0
      mezat_durumu["kazanan"] = "Yok"
      mezat_durumu["kazanan_id"] = None
      if urun:
        urun.guncel_fiyat = float(urun.acilis_fiyati or 0)
        urun.kazanan_adi = "Yok"
        urun.kazanan_id = None

    if urun:
      db.session.add(urun)
      db.session.commit()

    onbellegi_temizle()
    socketio.emit(
        "pey_guncellendi",
        {
            "urun_id": aktif_urun_id,
            "pey": mezat_durumu["pey"],
            "kazanan": mezat_durumu["kazanan"],
            "kazanan_id": mezat_durumu.get("kazanan_id"),
        },
    )
  return jsonify({"success": True})


@app.route("/tek-urun-iptal-et", methods=["POST"])
@admin_required
def tek_urun_iptal_et():
  global mezat_durumu, aktif_urun_id, sayac_aktif, sayac_gorev_id
  veri = request.json or {}
  urun_id = veri.get("urun_id")

  if not urun_id:
    return jsonify({"success": False, "mesaj": "Ürün ID belirtilmedi."})

  urun = db.session.get(Urun, int(urun_id))
  if not urun:
    return jsonify({"success": False, "mesaj": "Ürün bulunamadı."})

  try:
    with mezat_kilidi:
      Teklif.query.filter_by(urun_id=urun.id).delete()
      OnTeklif.query.filter_by(urun_id=urun.id).delete()
      SepetItem.query.filter_by(urun_id=urun.id).delete()

      urun.durum = "Aktif"
      urun.kazanan_adi = "Yok"
      urun.kazanan_id = None
      urun.guncel_fiyat = float(urun.acilis_fiyati or 0)

      db.session.add(urun)
      db.session.commit()

      if aktif_urun_id == urun.id:
        sayac_aktif = False
        sayac_gorev_id += 1
        aktif_urun_id = None
        mezat_durumu["durum"] = "Bekliyor"
        mezat_durumu["sure_bitis"] = 0
        mezat_durumu["pey"] = float(urun.acilis_fiyati or 0)
        mezat_durumu["kazanan"] = "Yok"
        mezat_durumu["kazanan_id"] = None
        socketio.emit(
            "pey_guncellendi",
            {
                "urun_id": urun.id,
                "pey": mezat_durumu["pey"],
                "kazanan": "Yok",
                "kazanan_id": None,
            },
        )

      onbellegi_temizle()

    return jsonify({
        "success": True,
        "mesaj": (
            f"Lot #{urun.lot_no} ({urun.urun_adi}) satışı iptal edildi, borçtan"
            " düşüldü ve ürün tekrar satış havuzuna eklendi."
        ),
    })
  except Exception as e:
    db.session.rollback()
    return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/musteri-dosya-sil", methods=["POST"])
@admin_required
def musteri_dosya_sil():
  global mezat_durumu, aktif_urun_id
  veri = request.json or {}
  ham_ad = (veri.get("musteri_adi") or "").strip()
  musteri_id = veri.get("musteri_id")

  if not ham_ad and not musteri_id:
    return jsonify({"success": False, "mesaj": "Müşteri bilgisi belirtilmedi."})

  try:
    hedef_kullanici = None
    if musteri_id:
      hedef_kullanici = db.session.get(Kullanici, int(musteri_id))

    if not hedef_kullanici and ham_ad:
      saf_ad = saf_isim_temizle(ham_ad)
      hedef_kullanici = Kullanici.query.filter(
          Kullanici.silindi_mi == False, Kullanici.ad_soyad.ilike(saf_ad)
      ).first()

    if hedef_kullanici:
      with mezat_kilidi:
        SepetItem.query.filter_by(musteri_id=hedef_kullanici.id).delete()
        satilanlar = Urun.query.filter(
            or_(Urun.durum == "Satıldı", Urun.durum == "satildi"),
            Urun.silindi_mi == False,
            Urun.kazanan_id == hedef_kullanici.id,
        ).all()

        for u in satilanlar:
          u.durum = "Aktif"
          u.kazanan_adi = "Yok"
          u.kazanan_id = None
          u.guncel_fiyat = float(u.acilis_fiyati or 0)
          Teklif.query.filter_by(urun_id=u.id).delete()
          OnTeklif.query.filter_by(urun_id=u.id).delete()
          SepetItem.query.filter_by(urun_id=u.id).delete()
          db.session.add(u)

          if aktif_urun_id == u.id:
            aktif_urun_id = None
            mezat_durumu["pey"] = float(u.acilis_fiyati or 0)
            mezat_durumu["kazanan"] = "Yok"
            mezat_durumu["kazanan_id"] = None

        db.session.commit()
        onbellegi_temizle()

    return jsonify({
        "success": True,
        "mesaj": (
            "Müşterinin tüm dosyası sıfırlandı ve ürünler havuza alındı."
        ),
    })
  except Exception as e:
    db.session.rollback()
    return jsonify({"success": False, "error": str(e)}), 500


@app.route("/urun-ekle", methods=["POST"])
@admin_required
def urun_ekle():
  try:
    dosyalar = request.files.getlist("dosyalar")
    fotograflar = []
    video_url = ""
    ses_url = ""

    for dosya in dosyalar:
      url, temiz_ad = kaydet_guvenli_dosya(dosya)
      if url and temiz_ad:
        ext = temiz_ad.lower()
        if ext.endswith((".mp4", ".mov", ".avi", ".webm", ".mkv")):
          video_url = url
        elif ext.endswith((".mp3", ".wav", ".ogg", ".m4a")):
          ses_url = url
        else:
          fotograflar.append(url)

    mevcut_urun_sayisi = Urun.query.filter_by(silindi_mi=False).count()
    lot_raw = request.form.get("lot")
    lot_no = (
        int(lot_raw)
        if lot_raw and lot_raw.isdigit()
        else (mevcut_urun_sayisi + 1)
    )

    yeni_urun = Urun(
        lot_no=lot_no,
        urun_adi=request.form.get("ad", "İsimsiz Ürün"),
        kategori=request.form.get("kategori", "Hediyelik eşya"),
        acilis_fiyati=float(request.form.get("fiyat") or 0),
        guncel_fiyat=float(request.form.get("fiyat") or 0),
        hemen_al_fiyati=float(request.form.get("hemen_al_fiyat") or 0),
        tanitim_yazisi=request.form.get("tanitim_yazisi", ""),
        fotograflar=fotograflar,
        video=video_url,
        ses_dosyasi=ses_url,
        durum="Aktif",
        silindi_mi=False,
    )
    db.session.add(yeni_urun)
    db.session.commit()
    onbellegi_temizle()
    return jsonify({"success": True})
  except Exception as e:
    db.session.rollback()
    return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/urun-guncelle", methods=["POST"])
@admin_required
def urun_guncelle():
  global mezat_durumu, aktif_urun_id
  veri = request.json or {}
  urun_id = veri.get("id")
  urun = db.session.get(Urun, urun_id)
  if not urun or urun.silindi_mi:
    return jsonify({"success": False, "mesaj": "Ürün bulunamadı!"})

  try:
    urun.lot_no = int(veri.get("lot", urun.lot_no))
    urun.urun_adi = veri.get("ad", urun.urun_adi)
    urun.kategori = veri.get("kategori", urun.kategori)
    urun.acilis_fiyati = float(veri.get("fiyat", urun.acilis_fiyati))
    urun.hemen_al_fiyati = float(veri.get("hemen_al_fiyat", urun.hemen_al_fiyati))
    urun.tanitim_yazisi = veri.get("tanitim_yazisi", urun.tanitim_yazisi)

    if aktif_urun_id == urun.id and mezat_durumu["pey"] == 0:
      mezat_durumu["pey"] = urun.acilis_fiyati
      urun.guncel_fiyat = urun.acilis_fiyati

    db.session.add(urun)
    db.session.commit()
    onbellegi_temizle()
    if aktif_urun_id == urun.id:
      socketio.emit("yeni_sahne_urunu", urun.to_dict())
    return jsonify({"success": True, "mesaj": "Ürün başarıyla güncellendi."})
  except Exception as e:
    db.session.rollback()
    return jsonify({"success": False, "mesaj": str(e)}), 500


@app.route("/urun-sil", methods=["POST"])
@admin_required
def urun_sil():
  global mezat_durumu, aktif_urun_id
  veri = request.json or {}
  urun_id = veri.get("id")
  urun = db.session.get(Urun, urun_id)
  if urun:
    urun.silindi_mi = True
    urun.silinme_tarihi = suan_utc()

    if aktif_urun_id == urun.id:
      aktif_urun_id = None
      mezat_durumu["durum"] = "Bekliyor"
      mezat_durumu["sure_bitis"] = 0
      mezat_durumu["pey"] = 0
      mezat_durumu["kazanan"] = "Yok"
      mezat_durumu["kazanan_id"] = None

    db.session.commit()
    onbellegi_temizle()
    return jsonify({"success": True, "mesaj": "Ürün çöp kutusuna taşındı."})
  return jsonify({"success": False, "mesaj": "Ürün bulunamadı."})


@app.route("/musteri-durum-guncelle", methods=["POST"])
@admin_required
def musteri_durum_guncelle():
  veri = request.json or {}
  kullanici_id = veri.get("kullanici_id")
  yeni_durum = veri.get("durum")

  kullanici = db.session.get(Kullanici, kullanici_id)
  if kullanici and not kullanici.silindi_mi:
    kullanici.durum = yeni_durum
    kullanici.onayli_mi = yeni_durum == "onayli"
    db.session.commit()
    onbellegi_temizle()
    return jsonify({"success": True})
  return jsonify({"success": False, "mesaj": "Kullanıcı bulunamadı."})


@app.route("/musteri-sil", methods=["POST"])
@admin_required
def musteri_sil():
  veri = request.json or {}
  kullanici_id = veri.get("kullanici_id")
  kullanici = db.session.get(Kullanici, kullanici_id)
  if kullanici:
    kullanici.silindi_mi = True
    kullanici.silinme_tarihi = suan_utc()
    db.session.commit()
    onbellegi_temizle()
    return jsonify({"success": True, "mesaj": "Müşteri çöp kutusuna taşındı."})
  return jsonify({"success": False, "mesaj": "Müşteri bulunamadı."})


@app.route("/on-teklif-guncelle", methods=["POST"])
@admin_required
def on_teklif_guncelle():
  veri = request.json or {}
  ot_id = veri.get("id")
  yeni_musteri = (veri.get("musteri_adi") or "").strip()
  yeni_teklif = float(veri.get("teklif", 0))

  ot = db.session.get(OnTeklif, ot_id)
  if ot:
    ot.musteri_adi = yeni_musteri
    ot.teklif = yeni_teklif
    db.session.commit()
    onbellegi_temizle()
    return jsonify({"success": True})
  return jsonify({"success": False, "mesaj": "Ön teklif bulunamadı."})


@app.route("/on-teklif-sil", methods=["POST"])
@admin_required
def on_teklif_sil():
  veri = request.json or {}
  ot_id = veri.get("id")
  try:
    if ot_id:
      ot = db.session.get(OnTeklif, ot_id)
      if ot:
        u_id = ot.urun_id
        db.session.delete(ot)
        db.session.commit()

        urun = db.session.get(Urun, u_id)
        if urun:
          kalan_en_yuksek = (
              OnTeklif.query.filter_by(urun_id=u_id)
              .order_by(OnTeklif.teklif.desc())
              .first()
          )
          urun.guncel_fiyat = (
              float(kalan_en_yuksek.teklif)
              if kalan_en_yuksek
              else float(urun.acilis_fiyati or 0)
          )
          db.session.add(urun)
          db.session.commit()

        onbellegi_temizle()
        return jsonify({"success": True})
  except Exception as e:
    db.session.rollback()
    return jsonify({"success": False, "error": str(e)}), 500
  return jsonify({"success": False})


@app.route("/cop-kutusu-listele", methods=["GET"])
@admin_required
def cop_kutusu_listele():
  try:
    silinen_urunler = (
        Urun.query.filter_by(silindi_mi=True)
        .order_by(Urun.silinme_tarihi.desc())
        .limit(100)
        .all()
    )
    silinen_musteriler = (
        Kullanici.query.filter_by(silindi_mi=True)
        .order_by(Kullanici.silinme_tarihi.desc())
        .limit(100)
        .all()
    )

    return jsonify({
        "success": True,
        "urunler": [{
            "id": u.id,
            "lot": u.lot_no,
            "ad": u.urun_adi,
            "fiyat": u.acilis_fiyati,
            "silinme_tarihi": (
                u.silinme_tarihi.strftime("%d.%m.%Y %H:%M")
                if u.silinme_tarihi
                else "-"
            ),
        } for u in silinen_urunler],
        "musteriler": [{
            "id": m.id,
            "ad": m.ad_soyad,
            "tel": m.telefon,
            "silinme_tarihi": (
                m.silinme_tarihi.strftime("%d.%m.%Y %H:%M")
                if m.silinme_tarihi
                else "-"
            ),
        } for m in silinen_musteriler],
    })
  except Exception as e:
    db.session.rollback()
    return jsonify({"success": False, "urunler": [], "musteriler": []})
  finally:
    db.session.remove()


@app.route("/cop-kutusundan-geri-yukle", methods=["POST"])
@admin_required
def cop_kutusundan_geri_yukle():
  veri = request.json or {}
  tur = veri.get("tur")
  kayit_id = veri.get("id")

  if tur == "urun":
    urun = db.session.get(Urun, kayit_id)
    if urun:
      urun.silindi_mi = False
      urun.silinme_tarihi = None
      db.session.commit()
      onbellegi_temizle()
      return jsonify({"success": True, "mesaj": "Ürün geri yüklendi."})
  elif tur == "musteri":
    kullanici = db.session.get(Kullanici, kayit_id)
    if kullanici:
      kullanici.silindi_mi = False
      kullanici.silinme_tarihi = None
      db.session.commit()
      onbellegi_temizle()
      return jsonify({"success": True, "mesaj": "Müşteri geri yüklendi."})

  return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})


@app.route("/cop-kutusundan-kalici-sil", methods=["POST"])
@admin_required
def cop_kutusundan_kalici_sil():
  global aktif_urun_id, mezat_durumu
  veri = request.json or {}
  tur = veri.get("tur")
  kayit_id = veri.get("id")

  try:
    if tur == "urun":
      urun = db.session.get(Urun, kayit_id)
      if urun:
        Teklif.query.filter_by(urun_id=urun.id).delete()
        OnTeklif.query.filter_by(urun_id=urun.id).delete()
        SepetItem.query.filter_by(urun_id=urun.id).delete()
        UrunTakip.query.filter_by(urun_id=urun.id).delete()

        if aktif_urun_id == urun.id:
          aktif_urun_id = None
          mezat_durumu["durum"] = "Bekliyor"
          mezat_durumu["sure_bitis"] = 0
          mezat_durumu["pey"] = 0
          mezat_durumu["kazanan"] = "Yok"
          mezat_durumu["kazanan_id"] = None

        db.session.delete(urun)
        db.session.commit()
        onbellegi_temizle()
        return jsonify(
            {"success": True, "mesaj": "Ürün kalıcı olarak silindi."}
        )
    elif tur == "musteri":
      kullanici = db.session.get(Kullanici, kayit_id)
      if kullanici:
        Teklif.query.filter_by(musteri_id=kullanici.id).delete()
        OnTeklif.query.filter_by(musteri_id=kullanici.id).delete()
        SepetItem.query.filter_by(musteri_id=kullanici.id).delete()
        UrunTakip.query.filter_by(musteri_id=kullanici.id).delete()
        db.session.delete(kullanici)
        db.session.commit()
        onbellegi_temizle()
        return jsonify(
            {"success": True, "mesaj": "Müşteri kalıcı olarak silindi."}
        )
  except Exception as e:
    db.session.rollback()
    return jsonify({"success": False, "mesaj": str(e)}), 500

  return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})


@app.route("/kategoriler-listesi", methods=["GET"])
def kategoriler_listesi():
  try:
    sonuc = (
        db.session.execute(
            text(
                "SELECT DISTINCT kategori FROM urun WHERE silindi_mi = FALSE"
                " AND kategori IS NOT NULL;"
            )
        )
        .scalars()
        .all()
    )
    liste = [k for k in sonuc if k]
    return jsonify(liste)
  except Exception:
    db.session.rollback()
    return jsonify(
        ["Hediyelik eşya", "Antika", "Koleksiyon", "Tablo", "Gümüş"]
    )
  finally:
    db.session.remove()


@app.route("/sikayet-oneri-listele", methods=["GET"])
@admin_required
def sikayet_oneri_listele():
  try:
    veriler = (
        SikayetOneri.query.order_by(SikayetOneri.id.desc()).limit(100).all()
    )
    sonuc = [{
        "id": s.id,
        "musteri_adi": s.musteri_adi or "Misafir",
        "tur": s.tur,
        "konu": s.konu,
        "mesaj": s.mesaj,
        "durum": s.durum,
        "tarih": s.tarih.strftime("%d.%m.%Y %H:%M") if s.tarih else "-",
    } for s in veriler]
    return jsonify(sonuc)
  except Exception:
    db.session.rollback()
    return jsonify([])
  finally:
    db.session.remove()


@app.route("/itiraz-duzelt-bildir", methods=["POST"])
@admin_required
def itiraz_duzelt_bildir():
  veri = request.json or {}
  kayit_id = veri.get("id")
  kayit = db.session.get(SikayetOneri, kayit_id)
  if kayit:
    kayit.durum = "Düzeltildi"
    db.session.commit()
    socketio.emit(
        "urun_takip_bildirimi",
        {
            "mesaj": (
                f"Sayın {kayit.musteri_adi}, bildiriminiz ('{kayit.konu}')"
                " incelenip çözülmüştür."
            )
        },
    )
    return jsonify({"success": True, "mesaj": "Bildirim iletildi."})
  return jsonify({"success": False, "mesaj": "Kayıt bulunamadı."})


@app.route("/sikayet-oneri-sil", methods=["POST"])
@admin_required
def sikayet_oneri_sil():
  veri = request.json or {}
  kayit_id = veri.get("id")
  kayit = db.session.get(SikayetOneri, kayit_id)
  if kayit:
    db.session.delete(kayit)
    db.session.commit()
    return jsonify({"success": True})
  return jsonify({"success": False})


@app.route("/muzik-ekle", methods=["POST"])
@admin_required
def muzik_ekle():
  dosya = request.files.get("muzik_dosyasi")
  if dosya:
    url, ad = kaydet_guvenli_dosya(dosya)
    if url:
      yeni = Muzik(url=url)
      db.session.add(yeni)
      db.session.commit()
      onbellegi_temizle()
      return jsonify({"success": True})
  return jsonify({"success": False, "mesaj": "Dosya yüklenemedi."})


@app.route("/excel-indir", methods=["GET"])
@admin_required
def excel_indir():
  try:
    satilanlar = Urun.query.filter_by(durum="Satıldı", silindi_mi=False).all()
    musteriler = Kullanici.query.filter_by(silindi_mi=False).all()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
      if satilanlar:
        df_sat = [{
            "Lot No": s.lot_no,
            "Ürün Adı": s.urun_adi,
            "Satış Fiyatı (TL)": s.guncel_fiyat,
            "Alan Müşteri": s.kazanan_adi,
        } for s in satilanlar]
        pd.DataFrame(df_sat).to_excel(
            writer, sheet_name="Satilan_Urunler", index=False
        )
      if musteriler:
        df_mus = [{
            "Ad Soyad": getattr(m, "ad_soyad", "-"),
            "Telefon": getattr(m, "telefon", "-"),
            "E-posta": getattr(m, "email", "-"),
            "Adres": getattr(m, "adres", "-"),
            "Durum": getattr(m, "durum", "-"),
        } for m in musteriler]
        pd.DataFrame(df_mus).to_excel(
            writer, sheet_name="Musteriler", index=False
        )

    output.seek(0)
    dosya_adi = f"mezat_raporu_{suan_utc().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=dosya_adi,
        mimetype=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
  except Exception as e:
    return str(e), 500


@app.route("/api/social/tiktok/start", methods=["POST"])
@admin_required
def tiktok_start():
  data = request.get_json(silent=True) or {}
  username = data.get("username") or "halicantikmezat34"
  return jsonify(
      {"success": True, "mesaj": f"TikTok yayınına bağlanıldı: {username}"}
  )


@app.route("/api/social/tiktok/stop", methods=["POST"])
@admin_required
def tiktok_stop():
  return jsonify({"success": True, "mesaj": "TikTok bağlantısı kesildi."})


# ==========================================
# COKLU PLATFORM PEY / CHAT (GUCLENDIRILMIS REGEX & KILIT)
# ==========================================
@app.route("/api/tiktok-web-chat", methods=["POST"])
def tiktok_web_chat():
  global mezat_durumu, aktif_urun_id, sayac_kalan, sayac_aktif
  veri = request.json or {}
  platform = veri.get("platform", "Sosyal Medya")
  username = veri.get("username", "Misafir").strip()
  message = str(veri.get("message", "")).strip()

  if (
      not aktif_urun_id
      or not mezat_durumu
      or mezat_durumu.get("durum") == "Satıldı"
  ):
    return (
        jsonify({
            "success": False,
            "mesaj": "Sahnede aktif ürün yok veya mezat bitti.",
        }),
        200,
    )

  eslesme = re.search(
      r"(?:^|[^\d])(?:pey\s*|teklif\s*)?(\d{2,7})(?:\s*tl|\s*lira)?(?:$|[^\d])",
      message,
      re.IGNORECASE,
  )
  if not eslesme:
    return (
        jsonify({
            "success": True,
            "mesaj": "Sohbet mesajı geçerli bir pey formatı içermiyor.",
        }),
        200,
    )

  teklif_tutari = float(eslesme.group(1))

  with mezat_kilidi:
    try:
      sorgu = Urun.query
      if not IS_SQLITE:
        sorgu = sorgu.with_for_update()
      urun = sorgu.filter_by(id=aktif_urun_id).first()

      mevcut_fiyat = float(
          mezat_durumu["pey"]
          if mezat_durumu["pey"] > 0
          else (urun.acilis_fiyati if urun else 0)
      )

      if teklif_tutari > mevcut_fiyat:
        etiket_ad = f"[{platform}] {username}"
        mezat_durumu["pey"] = teklif_tutari
        mezat_durumu["kazanan"] = etiket_ad
        mezat_durumu["kazanan_id"] = None

        if urun:
          urun.guncel_fiyat = teklif_tutari
          urun.kazanan_adi = etiket_ad
          urun.kazanan_id = None
          db.session.add(urun)

        yeni_teklif = Teklif(
            urun_id=aktif_urun_id,
            musteri_adi=etiket_ad,
            tutar=teklif_tutari,
            ip_adresi=get_client_ip(),
        )
        db.session.add(yeni_teklif)
        db.session.commit()

        if sayac_aktif and sayac_kalan <= 10:
          sayac_kalan = 15
          mezat_durumu["sure_bitis"] = time.time() + 15
          socketio.emit(
              "sayac_uzatildi",
              {
                  "kalan": 15,
                  "mesaj": "Son saniye teklifi nedeniyle süre 15 sn uzatıldı!",
              },
          )
          socketio.emit("sayac_guncelle", {"kalan": 15})

        onbellegi_temizle()
        socketio.emit(
            "pey_guncellendi",
            {
                "urun_id": aktif_urun_id,
                "pey": teklif_tutari,
                "kazanan": etiket_ad,
                "kazanan_id": None,
            },
        )
        return (
            jsonify({
                "success": True,
                "guncel_fiyat": teklif_tutari,
                "kazanan": etiket_ad,
            }),
            200,
        )
    except Exception as e:
      db.session.rollback()
      return (
          jsonify({"success": False, "mesaj": f"Veritabanı hatası: {str(e)}"}),
          500,
      )
    finally:
      db.session.remove()

  return (
      jsonify({"success": False, "mesaj": "Teklif mevcut fiyattan düşük."}),
      200,
  )


# ==========================================
# SOKET BAGLANTILARI
# ==========================================
@socketio.on("connect")
def handle_connect():
  global aktif_izleyici_sayisi
  aktif_izleyici_sayisi += 1
  socketio.emit("izleyici_sayisi_guncelle", {"sayi": aktif_izleyici_sayisi})


@socketio.on("disconnect")
def handle_disconnect():
  global aktif_izleyici_sayisi
  if aktif_izleyici_sayisi > 0:
    aktif_izleyici_sayisi -= 1
  socketio.emit("izleyici_sayisi_guncelle", {"sayi": aktif_izleyici_sayisi})


# Tablolari onar
veritabani_tablolari_onar()

if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  socketio.run(app, host="0.0.0.0", port=port, debug=False)