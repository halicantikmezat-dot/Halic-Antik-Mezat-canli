# Tedarikçi ayar, hesap ve ürün havuzu.
# Canlı yayın, mezat sayacı ve vitrin şablonlarından bağımsızdır.
# Mevcut mezat tablolarına kolon eklemez.

import base64
import hashlib
import json
import os
import secrets
import shutil
import threading
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request, send_file, session
from sqlalchemy import func, or_, text
from werkzeug.utils import secure_filename

tedarikci_bp = Blueprint("tedarikci_havuzu", __name__)

_KOK = os.path.abspath(os.path.dirname(__file__))
_MEDYA = os.path.join(_KOK, "instance", "tedarikci_medya")
_SEMA_KILIT = threading.Lock()
_SEMA_HAZIR = False
_PIN_KILIT = threading.Lock()
_PIN_DENEME = {}
_MUKERRER_KILIT = threading.Lock()
_MUKERRER = {}
_AI_KILIT = threading.Lock()
_AI_SON = {}
_AI_ON_NOTU = "AI ön analizi hazırlandı. Lütfen bilgileri kontrol edip gerekiyorsa düzeltin ve onaylayın."
_RESIM = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_KANIT = _RESIM | {".mp4", ".webm", ".mov", ".m4v"}
_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
}
_DUKKAN_ADLARI = (
    ("gumus", "Gümüş"),
    ("antika", "Antika"),
    ("toptan", "Toptan"),
    ("kozmetik", "Kozmetik"),
    ("bijuteri", "Bijuteri"),
    ("hirdavat", "Hırdavat & Yapı"),
    ("tasarim", "Tasarım"),
    ("modelaraba", "Klasik Arabalar"),
    ("karma", "Oyuncaklar"),
    ("diger", "Diğer - Her Şey"),
    ("porselen", "Porselen / Seramik"),
    ("bakir", "Bakır / Pirinç / Madenler"),
    ("elektronik", "Elektronik"),
    ("plak", "Plak / Kaset"),
    ("retro", "Hediyelik & Retro"),
    ("evyasam", "Ev & Yaşam"),
    ("tekstil", "Tekstil"),
)
_DUKKANLAR = {kod for kod, _ad in _DUKKAN_ADLARI}
_MENU_BASLIGI = "🤝 Ürün Sat / Tedarikçi"
_VARSAYILAN_SOZLESME = """TEDARİKÇİ VE SATIŞ ARACILIK SÖZLEŞMESİ
1. Mülkiyet, Hukuka Uygunluk ve Kültür Varlıkları Beyanı: Tedarikçi, sisteme yüklediği her ürünün mutlak yasal sahibi veya satışa tam yetkili temsilcisi olduğunu; ürünün çalıntı, gasp edilmiş, hacizli, rehinli, kaçak veya 2863 sayılı Kültür ve Tabiat Varlıklarını Koruma Kanunu kapsamında alım-satımı yasaklanmış tarihi eser/kültür varlığı olmadığını peşinen beyan ve taahhüt eder. TCK m. 165 ve ilgili mevzuat kapsamında doğabilecek tüm cezai, idari ve hukuki sorumluluk münhasıran Tedarikçi'ye aittir.
2. Platformun Yer Sağlayıcı / Aracı Konumu: Platform yalnızca alıcı ile tedarikçiyi buluşturan elektronik ortam ve aracılık hizmeti sunar; ürünlerin fiziksel zilyetliğini üstlenmez. Ürünün ayıplı, sahte, eksik veya üçüncü kişilerin haklarını ihlal eder nitelikte çıkması halinde Platform'un alıcıya veya üçüncü kişilere ödeyeceği her türlü tazminat, iade bedeli, yargılama gideri ve avukatlık ücretini Platform, Tedarikçi'nin mevcut veya gelecek hakedişlerinden mahsup etme ve doğrudan Tedarikçi'ye rücu etme hakkına sahiptir.
3. Münhasırlık ve Cayma Cezası: Ürün onaylanıp vitrine/mezata alındığı andan itibaren 72 saat (veya mezat sonuçlanana) kadar Tedarikçi ürünü başka bir platformda veya elden satamaz. Keyfi geri çekme veya çift satış halinde ürünün taban fiyatı üzerinden %15 cayma bedeli Tedarikçi'ye borç kaydedilir.
4. Paketleme, Kargo Garanti Kilidi ve Teslimat: Satış gerçekleştiğinde Tedarikçi, ürünü en geç 48 saat içinde kırılmaya karşı tam korumalı şekilde paketlemek, paketleme/kargo fişi görselini sisteme yüklemek ve doğru alıcı bilgilerine sevk etmekle yükümlüdür. Hatalı paketleme veya yanlış gönderimden doğan tüm hasar ve kayıplar Tedarikçi'ye aittir.
5. Komisyon, İsim-IBAN Eşleşmesi ve Hakediş: Satış bedeli üzerinden yürürlükteki komisyon oranı kesilir. Ödemeler yalnızca Tedarikçi'nin kendi T.C. kimlik numarasıyla eşleşen şahsi IBAN hesabına, kargo teslimatı ve alıcı kontrol süresi tamamlandıktan sonra yapılır. İşbu beyan Tedarikçi'nin IP adresi ve zaman damgasıyla dijital olarak kayıt altına alınmıştır."""
_KILITSIZ = {"bekliyor"}
_IPTAL_ACIK = {"onaylandi", "vitrin", "satildi_kargo_bekliyor", "kargolandi"}
_RESIM_BAYT = 12 * 1024 * 1024
_KANIT_BAYT = 40 * 1024 * 1024

VARSAYILAN_AYAR = {
    "panel_aktif": False,
    "menu_basligi": _MENU_BASLIGI,
    "duyuru_metni": "",
    "komisyon_orani": 20.0,
    "kargo_destek_orani": 10.0,
    "kargo_destek_alt_limit": 500.0,
    "kargo_suresi_saat": 48,
    "cayma_cezasi_orani": 15.0,
    "tc_zorunlu": True,
    "paketleme_kaniti_zorunlu": True,
    "min_fotograf": 1,
    "max_fotograf": 4,
    "yasal_sozlesme_metni": _VARSAYILAN_SOZLESME,
}

TedarikciAyarlari = None
TedarikciUrunleri = None
TedarikciHesaplari = None


def simdi_utc():
    return datetime.now(timezone.utc)


def _pozitif_sayi(ham, tam=False):
    try:
        deger = float(ham or 0)
    except (TypeError, ValueError):
        deger = 0.0
    if deger < 0:
        deger = 0.0
    if tam:
        return int(deger)
    return round(deger, 2)


def _oran(ham):
    deger = _pozitif_sayi(ham)
    if deger > 100:
        return 100.0
    return deger


def _metin(ham, sinir):
    return str(ham or "").strip()[:sinir]


def _evet(ham):
    if isinstance(ham, bool):
        return ham
    return str(ham or "").strip().lower() in ("1", "true", "on", "evet", "yes")


def _telefon_norm(ham):
    rakam = "".join(ch for ch in str(ham or "") if ch.isdigit())
    if rakam.startswith("90") and len(rakam) >= 12:
        rakam = rakam[2:]
    if rakam.startswith("0") and len(rakam) >= 11:
        rakam = rakam[1:]
    return rakam[:20]


def net_hakedis_hesapla(
    satis_fiyati,
    komisyon_orani,
    kargo_destek_orani=0.0,
    kargo_destek_alt_limit=0.0,
):
    """Satıştan komisyon düşülür.

    Satış, kargo destek alt limitinin altındaysa kargo destek oranı da düşülür.
    Limit ve üstünde bu oran kesilmez.
    """
    satis = _pozitif_sayi(satis_fiyati)
    komisyon = round(satis * _oran(komisyon_orani) / 100.0, 2)
    destek = 0.0
    if satis < _pozitif_sayi(kargo_destek_alt_limit):
        destek = round(satis * _oran(kargo_destek_orani) / 100.0, 2)
    net = round(satis - komisyon - destek, 2)
    if net < 0:
        return 0.0
    return net


def cayma_cezasi_hesapla(satis_fiyati, cayma_cezasi_orani):
    satis = _pozitif_sayi(satis_fiyati)
    return round(satis * _oran(cayma_cezasi_orani) / 100.0, 2)


def kargo_son_tarih_hesapla(satis_zamani, kargo_suresi_saat):
    if satis_zamani is None:
        return None
    return satis_zamani + timedelta(hours=_pozitif_sayi(kargo_suresi_saat, tam=True))


def tc_kimlik_gecerli(ham):
    tc = "".join(ch for ch in str(ham or "") if ch.isdigit())
    if len(tc) != 11 or tc[0] == "0":
        return False
    rakam = [int(c) for c in tc]
    onuncu = ((sum(rakam[0:9:2]) * 7) - sum(rakam[1:8:2])) % 10
    if onuncu != rakam[9]:
        return False
    return sum(rakam[:10]) % 10 == rakam[10]


def fotograflari_coz(ham):
    if ham is None:
        return []
    if isinstance(ham, (list, tuple)):
        return [str(oge).strip() for oge in ham if str(oge).strip()]
    metin = str(ham).strip()
    if not metin:
        return []
    try:
        veri = json.loads(metin)
    except (TypeError, ValueError):
        return [parca.strip() for parca in metin.split(",") if parca.strip()]
    if not isinstance(veri, list):
        return []
    return [str(oge).strip() for oge in veri if str(oge).strip()]


def fotograflari_metin(ham):
    return json.dumps(fotograflari_coz(ham), ensure_ascii=False)


def fotograf_sayisi_uygun(ham, min_fotograf, max_fotograf):
    adet = len(fotograflari_coz(ham))
    alt = _pozitif_sayi(min_fotograf, tam=True)
    ust = _pozitif_sayi(max_fotograf, tam=True)
    if ust < alt:
        ust = alt
    return alt <= adet <= ust


def _ayar_degeri(ayar, ad):
    if isinstance(ayar, dict):
        if ad in ayar:
            return ayar[ad]
        return VARSAYILAN_AYAR[ad]
    deger = getattr(ayar, ad, None)
    if deger is None and ad in VARSAYILAN_AYAR:
        return VARSAYILAN_AYAR[ad]
    return deger


def urun_kaydini_hazirla(girdi, ayar, yukleyen_ip="", paketleme_kontrol=True):
    """Ürün alanlarını ayar anındaki oranlarla hazırlar. Veritabanına yazmaz."""
    girdi = girdi or {}
    hatalar = []
    satici = _metin(girdi.get("satici_ad_soyad"), 120)
    telefon = _telefon_norm(girdi.get("telefon"))
    urun_adi = _metin(girdi.get("urun_adi"), 200)
    if not satici:
        hatalar.append("satici_ad_soyad")
    if len(telefon) < 10:
        hatalar.append("telefon")
    if not urun_adi:
        hatalar.append("urun_adi")

    tc = "".join(ch for ch in _metin(girdi.get("tc_kimlik_no"), 11) if ch.isdigit())
    if _ayar_degeri(ayar, "tc_zorunlu") and not tc_kimlik_gecerli(tc):
        hatalar.append("tc_kimlik_no")

    fotograflar = fotograflari_coz(girdi.get("fotograflar"))
    if not fotograf_sayisi_uygun(
        fotograflar,
        _ayar_degeri(ayar, "min_fotograf"),
        _ayar_degeri(ayar, "max_fotograf"),
    ):
        hatalar.append("fotograflar")

    paketleme = _metin(girdi.get("paketleme_kaniti"), 500)
    if paketleme_kontrol and _ayar_degeri(ayar, "paketleme_kaniti_zorunlu") and not paketleme:
        hatalar.append("paketleme_kaniti")

    if not _evet(girdi.get("yasal_beyan_onay")):
        hatalar.append("yasal_beyan_onay")

    taban = _pozitif_sayi(girdi.get("taban_fiyat"))
    satis = _pozitif_sayi(girdi.get("satis_fiyati"))
    komisyon = _oran(_ayar_degeri(ayar, "komisyon_orani"))
    kargo_oran = _oran(_ayar_degeri(ayar, "kargo_destek_orani"))
    kargo_limit = _pozitif_sayi(_ayar_degeri(ayar, "kargo_destek_alt_limit"))
    satis_zamani = girdi.get("satis_zamani")
    alanlar = {
        "satici_ad_soyad": satici,
        "tc_kimlik_no": tc,
        "telefon": telefon,
        "iban": _metin(girdi.get("iban"), 34).replace(" ", "").upper(),
        "sehir": _metin(girdi.get("sehir"), 60),
        "urun_adi": urun_adi,
        "aciklama": _metin(girdi.get("aciklama"), 4000),
        "taban_fiyat": taban,
        "satis_fiyati": satis,
        "komisyon_orani": komisyon,
        "kargo_destek_orani": kargo_oran,
        "net_hakedis": net_hakedis_hesapla(satis, komisyon, kargo_oran, kargo_limit),
        "fotograflar": json.dumps(fotograflar, ensure_ascii=False),
        "paketleme_kaniti": paketleme,
        "hedef_kategori": _metin(girdi.get("hedef_kategori"), 120),
        "yasal_beyan_onay": _evet(girdi.get("yasal_beyan_onay")),
        "onaylanan_beyan_metni": str(_ayar_degeri(ayar, "yasal_sozlesme_metni") or ""),
        "yukleyen_ip": _metin(yukleyen_ip or girdi.get("yukleyen_ip"), 64),
        "olusturma_zamani": girdi.get("olusturma_zamani") or simdi_utc(),
        "satis_zamani": satis_zamani,
        "kargo_son_tarih": kargo_son_tarih_hesapla(
            satis_zamani, _ayar_degeri(ayar, "kargo_suresi_saat")
        ),
        "durum": _metin(girdi.get("durum"), 30) or "bekliyor",
        "kargo_firmasi": _metin(girdi.get("kargo_firmasi"), 80),
        "kargo_takip_no": _metin(girdi.get("kargo_takip_no"), 100),
        "alici_ad_soyad": _metin(girdi.get("alici_ad_soyad"), 120),
        "alici_telefon": _metin(girdi.get("alici_telefon"), 20),
        "alici_adres": _metin(girdi.get("alici_adres"), 1000),
        "admin_notu": _metin(girdi.get("admin_notu"), 2000),
    }
    return {"hatalar": hatalar, "alanlar": alanlar}


def _tablolari_tanimla(db):
    global TedarikciAyarlari, TedarikciUrunleri, TedarikciHesaplari
    if TedarikciAyarlari is not None and TedarikciUrunleri is not None and TedarikciHesaplari is not None:
        return

    class TedarikciAyarlariModel(db.Model):
        __tablename__ = "tedarikci_ayarlari"
        id = db.Column(db.Integer, primary_key=True)
        panel_aktif = db.Column(db.Boolean, nullable=False, default=False)
        menu_basligi = db.Column(db.String(120), nullable=False, default=_MENU_BASLIGI)
        duyuru_metni = db.Column(db.Text, nullable=False, default="")
        komisyon_orani = db.Column(db.Float, nullable=False, default=20.0)
        kargo_destek_orani = db.Column(db.Float, nullable=False, default=10.0)
        kargo_destek_alt_limit = db.Column(db.Float, nullable=False, default=500.0)
        kargo_suresi_saat = db.Column(db.Integer, nullable=False, default=48)
        cayma_cezasi_orani = db.Column(db.Float, nullable=False, default=15.0)
        tc_zorunlu = db.Column(db.Boolean, nullable=False, default=True)
        paketleme_kaniti_zorunlu = db.Column(db.Boolean, nullable=False, default=True)
        min_fotograf = db.Column(db.Integer, nullable=False, default=1)
        max_fotograf = db.Column(db.Integer, nullable=False, default=4)
        yasal_sozlesme_metni = db.Column(db.Text, nullable=False, default="")

    class TedarikciUrunleriModel(db.Model):
        __tablename__ = "tedarikci_urunleri"
        id = db.Column(db.Integer, primary_key=True)
        satici_ad_soyad = db.Column(db.String(120), nullable=False, default="")
        tc_kimlik_no = db.Column(db.String(11), nullable=False, default="")
        telefon = db.Column(db.String(20), nullable=False, default="")
        iban = db.Column(db.String(34), nullable=False, default="")
        sehir = db.Column(db.String(60), nullable=False, default="")
        urun_adi = db.Column(db.String(200), nullable=False, default="")
        aciklama = db.Column(db.Text, nullable=False, default="")
        taban_fiyat = db.Column(db.Float, nullable=False, default=0.0)
        satis_fiyati = db.Column(db.Float, nullable=False, default=0.0)
        komisyon_orani = db.Column(db.Float, nullable=False, default=20.0)
        kargo_destek_orani = db.Column(db.Float, nullable=False, default=10.0)
        net_hakedis = db.Column(db.Float, nullable=False, default=0.0)
        fotograflar = db.Column(db.Text, nullable=False, default="[]")
        paketleme_kaniti = db.Column(db.Text, nullable=False, default="")
        hedef_kategori = db.Column(db.String(120), nullable=False, default="")
        yasal_beyan_onay = db.Column(db.Boolean, nullable=False, default=False)
        onaylanan_beyan_metni = db.Column(db.Text, nullable=False, default="")
        yukleyen_ip = db.Column(db.String(64), nullable=False, default="")
        olusturma_zamani = db.Column(db.DateTime, nullable=False, default=simdi_utc)
        satis_zamani = db.Column(db.DateTime, nullable=True)
        kargo_son_tarih = db.Column(db.DateTime, nullable=True)
        durum = db.Column(db.String(30), nullable=False, default="bekliyor")
        kargo_firmasi = db.Column(db.String(80), nullable=False, default="")
        kargo_takip_no = db.Column(db.String(100), nullable=False, default="")
        alici_ad_soyad = db.Column(db.String(120), nullable=False, default="")
        alici_telefon = db.Column(db.String(20), nullable=False, default="")
        alici_adres = db.Column(db.Text, nullable=False, default="")
        admin_notu = db.Column(db.Text, nullable=False, default="")
        hakedis_odendi = db.Column(db.Boolean, nullable=False, default=False)
        hakedis_odeme_zamani = db.Column(db.DateTime, nullable=True)
        katalog_oge_id = db.Column(db.String(64), nullable=False, default="")

    class TedarikciHesaplariModel(db.Model):
        __tablename__ = "tedarikci_hesaplari"
        id = db.Column(db.Integer, primary_key=True)
        telefon = db.Column(db.String(20), nullable=False, unique=True, index=True)
        pin_hash = db.Column(db.String(160), nullable=False, default="")
        ad_soyad = db.Column(db.String(120), nullable=False, default="")
        tc_kimlik_no = db.Column(db.String(11), nullable=False, default="")
        iban = db.Column(db.String(34), nullable=False, default="")
        sehir = db.Column(db.String(60), nullable=False, default="")
        askida = db.Column(db.Boolean, nullable=False, default=False)
        olusturma_zamani = db.Column(db.DateTime, nullable=False, default=simdi_utc)

    TedarikciAyarlari = TedarikciAyarlariModel
    TedarikciUrunleri = TedarikciUrunleriModel
    TedarikciHesaplari = TedarikciHesaplariModel


def ayar_satiri_olustur():
    """Varsayılan tekil ayar satırı. Oturuma eklemez."""
    if TedarikciAyarlari is None:
        raise RuntimeError("Tedarikci tablolari kayitli degil")
    return TedarikciAyarlari(id=1, **VARSAYILAN_AYAR)


def urun_satiri_olustur(girdi, ayar, yukleyen_ip=""):
    """Doğrulanmış ürün satırı. Hata varsa satır üretmez, veritabanına yazmaz."""
    if TedarikciUrunleri is None:
        raise RuntimeError("Tedarikci tablolari kayitli degil")
    hazir = urun_kaydini_hazirla(girdi, ayar, yukleyen_ip=yukleyen_ip)
    if hazir["hatalar"]:
        return hazir
    hazir["satir"] = TedarikciUrunleri(**hazir["alanlar"])
    return hazir


_HATA_METIN = {
    "satici_ad_soyad": "Ad soyad gerekli.",
    "telefon": "Geçerli bir telefon gerekli.",
    "urun_adi": "Ürün adı gerekli.",
    "tc_kimlik_no": "Geçerli T.C. kimlik numarası gerekli.",
    "fotograflar": "En az 1, en fazla 4 fotoğraf gerekli.",
    "paketleme_kaniti": "Paketleme kanıtı gerekli.",
    "yasal_beyan_onay": "Yasal sözleşme onayı gerekli.",
}


def _db():
    return current_app.extensions["sqlalchemy"]


def _iso(deger):
    if not deger:
        return ""
    if not isinstance(deger, datetime):
        return str(deger)
    if deger.tzinfo is None:
        return deger.isoformat(timespec="seconds") + "Z"
    return deger.isoformat(timespec="seconds")


def _hata_mesaji(hatalar):
    return " ".join(_HATA_METIN.get(kod, kod) for kod in hatalar)


def _istek_ip():
    iletilen = request.headers.get("X-Forwarded-For", "")
    if iletilen.strip():
        return iletilen.split(",")[0].strip()[:64]
    return _metin(request.remote_addr, 64)


def _pin_uret():
    return f"{secrets.randbelow(9000) + 1000:04d}"


def _pin_uret_aralik():
    """Yönetici yedeği: 4, 5 veya 6 haneli rastgele PIN."""
    uzunluk = 4 + secrets.randbelow(3)
    alt = 10 ** (uzunluk - 1)
    return f"{secrets.randbelow(9 * alt) + alt:0{uzunluk}d}"


def _ilk_kayit_pini(ham):
    """İlk hesap PIN'i. Boşsa otomatik 4 hane; doluysa yalnızca 4-6 rakam."""
    temiz = _metin(ham, 8)
    if not temiz:
        return _pin_uret(), ""
    if temiz.isdigit() and 4 <= len(temiz) <= 6:
        return temiz, ""
    return "", "PIN 4-6 rakam olmalı."


def _pin_ozet(pin, tuz):
    return hashlib.pbkdf2_hmac(
        "sha256", str(pin).encode("utf-8"), tuz.encode("utf-8"), 120000
    ).hex()


def _pin_hashle(pin):
    tuz = secrets.token_hex(16)
    return tuz + "$" + _pin_ozet(pin, tuz)


def _pin_dogru(pin, saklanan):
    tuz, ayirac, ozet = str(saklanan or "").partition("$")
    if not ayirac or not tuz or not ozet:
        return False
    return secrets.compare_digest(_pin_ozet(pin, tuz), ozet)


def _pin_kilitli(telefon):
    with _PIN_KILIT:
        kayit = _PIN_DENEME.get(telefon) or {}
        acilis = kayit.get("acilis")
        simdi = simdi_utc()
        if acilis and acilis > simdi:
            return True
        if acilis and acilis <= simdi:
            _PIN_DENEME.pop(telefon, None)
    return False


def _pin_basarisiz(telefon):
    with _PIN_KILIT:
        kayit = _PIN_DENEME.setdefault(telefon, {"adet": 0, "acilis": None})
        kayit["adet"] = int(kayit.get("adet") or 0) + 1
        if kayit["adet"] >= 5:
            kayit["acilis"] = simdi_utc() + timedelta(minutes=10)
            kayit["adet"] = 0


def _pin_temizle(telefon):
    with _PIN_KILIT:
        _PIN_DENEME.pop(telefon, None)


def _sqlite_mi(db):
    return "sqlite" in str(db.engine.url)


def _kolon_kumesi(db, tablo):
    if _sqlite_mi(db):
        satirlar = db.session.execute(text("PRAGMA table_info(" + tablo + ")")).fetchall()
        return {str(satir[1]).lower() for satir in satirlar}
    satirlar = db.session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :tablo"
        ),
        {"tablo": tablo},
    ).fetchall()
    return {str(satir[0]).lower() for satir in satirlar}


def _kolon_ekle(db, tablo, kolon, tip):
    mevcut = _kolon_kumesi(db, tablo)
    if not mevcut or kolon.lower() in mevcut:
        return
    db.session.execute(text("ALTER TABLE " + tablo + " ADD COLUMN " + kolon + " " + tip))
    db.session.commit()


def _sema_emin_ol():
    global _SEMA_HAZIR
    if _SEMA_HAZIR:
        return
    with _SEMA_KILIT:
        if _SEMA_HAZIR:
            return
        db = _db()
        TedarikciAyarlari.__table__.create(db.engine, checkfirst=True)
        TedarikciUrunleri.__table__.create(db.engine, checkfirst=True)
        TedarikciHesaplari.__table__.create(db.engine, checkfirst=True)
        boolean = "BOOLEAN DEFAULT 0" if _sqlite_mi(db) else "BOOLEAN DEFAULT FALSE"
        _kolon_ekle(db, "tedarikci_urunleri", "hakedis_odendi", boolean)
        _kolon_ekle(db, "tedarikci_urunleri", "hakedis_odeme_zamani", "TIMESTAMP")
        _kolon_ekle(db, "tedarikci_urunleri", "katalog_oge_id", "VARCHAR(64) DEFAULT ''")
        _kolon_ekle(db, "tedarikci_hesaplari", "tc_kimlik_no", "VARCHAR(11) DEFAULT ''")
        _kolon_ekle(db, "tedarikci_hesaplari", "iban", "VARCHAR(34) DEFAULT ''")
        _kolon_ekle(db, "tedarikci_hesaplari", "sehir", "VARCHAR(60) DEFAULT ''")
        _SEMA_HAZIR = True


def _ayar_satiri():
    db = _db()
    satir = db.session.get(TedarikciAyarlari, 1)
    if satir is None:
        satir = ayar_satiri_olustur()
        db.session.add(satir)
        db.session.commit()
    degisti = False
    baslik = str(satir.menu_basligi or "").strip()
    if not baslik or baslik == "Tedarikçi Paneli":
        satir.menu_basligi = _MENU_BASLIGI
        degisti = True
    if not str(satir.yasal_sozlesme_metni or "").strip():
        satir.yasal_sozlesme_metni = _VARSAYILAN_SOZLESME
        degisti = True
    if int(satir.min_fotograf or 0) == 2 and int(satir.max_fotograf or 0) == 4:
        satir.min_fotograf = 1
        degisti = True
    if degisti:
        db.session.commit()
    return satir


def _panel_acik():
    return bool(_ayar_satiri().panel_aktif)


def _admin_mi():
    return session.get("is_admin") is True and session.get("admin_2fa_ok") is True


def _admin_reddi():
    if _admin_mi():
        return None
    return jsonify({"success": False, "mesaj": "Yönetici oturumu gerekli."}), 401


def _oturum_telefon():
    return _telefon_norm(session.get("tedarikci_telefon"))


def _hesap_bul(telefon):
    return TedarikciHesaplari.query.filter_by(telefon=telefon).first()


def _giris_yaz(hesap):
    session["tedarikci_telefon"] = hesap.telefon
    session["tedarikci_id"] = hesap.id


def _uzanti(ad):
    return os.path.splitext(str(ad or ""))[1].lower()


def _dosyalari_kaydet(dosyalar, izin, tavan):
    os.makedirs(_MEDYA, exist_ok=True)
    kayitlar = []
    for dosya in dosyalar or []:
        ad = secure_filename(getattr(dosya, "filename", "") or "")
        uzanti = _uzanti(ad)
        if uzanti not in izin:
            continue
        try:
            icerik = dosya.read()
        except Exception:
            continue
        if not icerik or len(icerik) > tavan:
            continue
        saklanan = uuid.uuid4().hex + uzanti
        with open(os.path.join(_MEDYA, saklanan), "wb") as hedef:
            hedef.write(icerik)
        kayitlar.append(saklanan)
    return kayitlar


def _medya_yolu(ad):
    temiz = os.path.basename(str(ad or ""))
    if not temiz or temiz != str(ad or "") or temiz.startswith("."):
        return ""
    yol = os.path.join(_MEDYA, temiz)
    if not os.path.isfile(yol):
        return ""
    return yol


def _medya_adresi(ad):
    temiz = os.path.basename(str(ad or ""))
    if not temiz:
        return ""
    return "/api/tedarikci/medya/" + temiz


def _beyan_sakla(mevcut, yeni):
    eski = str(mevcut or "").strip()
    metin = str(yeni or "").strip()
    if not metin:
        return eski
    if not eski:
        return metin
    if metin == eski or ("\n" + metin) in ("\n" + eski):
        return eski
    damga = simdi_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
    return eski + "\n\n---\n[" + damga + "]\n" + metin


def _urun_sozluk(satir, yonetici=False):
    fotolar = fotograflari_coz(satir.fotograflar)
    paket = {
        "id": satir.id,
        "urun_adi": satir.urun_adi or "",
        "aciklama": satir.aciklama or "",
        "durum": satir.durum or "",
        "taban_fiyat": float(satir.taban_fiyat or 0),
        "satis_fiyati": float(satir.satis_fiyati or 0),
        "komisyon_orani": float(satir.komisyon_orani or 0),
        "kargo_destek_orani": float(satir.kargo_destek_orani or 0),
        "net_hakedis": float(satir.net_hakedis or 0),
        "fotograflar": [_medya_adresi(ad) for ad in fotolar],
        "paketleme_kaniti": _medya_adresi(satir.paketleme_kaniti),
        "hedef_kategori": satir.hedef_kategori or "",
        "sehir": satir.sehir or "",
        "kargo_firmasi": satir.kargo_firmasi or "",
        "kargo_takip_no": satir.kargo_takip_no or "",
        "kargo_son_tarih": _iso(satir.kargo_son_tarih),
        "olusturma_zamani": _iso(satir.olusturma_zamani),
        "satis_zamani": _iso(satir.satis_zamani),
        "hakedis_odendi": bool(getattr(satir, "hakedis_odendi", False)),
        "hakedis_odeme_zamani": _iso(getattr(satir, "hakedis_odeme_zamani", None)),
        "alici_ad_soyad": satir.alici_ad_soyad or "",
        "alici_telefon": satir.alici_telefon or "",
        "alici_adres": satir.alici_adres or "",
    }
    if yonetici:
        paket.update({
            "satici_ad_soyad": satir.satici_ad_soyad or "",
            "tc_kimlik_no": satir.tc_kimlik_no or "",
            "telefon": satir.telefon or "",
            "iban": satir.iban or "",
            "yukleyen_ip": satir.yukleyen_ip or "",
            "onaylanan_beyan_metni": satir.onaylanan_beyan_metni or "",
            "yasal_beyan_onay": bool(satir.yasal_beyan_onay),
            "admin_notu": satir.admin_notu or "",
            "katalog_oge_id": getattr(satir, "katalog_oge_id", "") or "",
        })
    return paket


def _ayar_sozluk(satir, herkese=False):
    paket = {
        "panel_aktif": bool(satir.panel_aktif),
        "menu_basligi": str(satir.menu_basligi or "").strip() or _MENU_BASLIGI,
        "dukkanlar": [{"kod": kod, "ad": ad} for kod, ad in _DUKKAN_ADLARI],
        "duyuru_metni": satir.duyuru_metni or "",
        "komisyon_orani": float(satir.komisyon_orani or 0),
        "kargo_destek_orani": float(satir.kargo_destek_orani or 0),
        "kargo_destek_alt_limit": float(satir.kargo_destek_alt_limit or 0),
        "kargo_suresi_saat": int(satir.kargo_suresi_saat or 0),
        "cayma_cezasi_orani": float(satir.cayma_cezasi_orani or 0),
        "tc_zorunlu": bool(satir.tc_zorunlu),
        "paketleme_kaniti_zorunlu": bool(satir.paketleme_kaniti_zorunlu),
        "min_fotograf": int(satir.min_fotograf or 0),
        "max_fotograf": int(satir.max_fotograf or 0),
        "yasal_sozlesme_metni": satir.yasal_sozlesme_metni or "",
    }
    if herkese and not paket["panel_aktif"]:
        paket["duyuru_metni"] = ""
        paket["yasal_sozlesme_metni"] = ""
    elif not str(paket["yasal_sozlesme_metni"]).strip():
        paket["yasal_sozlesme_metni"] = _VARSAYILAN_SOZLESME
    return paket


def _form_girdi():
    if request.is_json:
        veri = request.get_json(silent=True) or {}
        return veri if isinstance(veri, dict) else {}
    return {anahtar: request.form.get(anahtar) for anahtar in request.form}


def _ayar_uygula(satir, veri):
    satir.panel_aktif = _evet(veri.get("panel_aktif"))
    satir.menu_basligi = _metin(veri.get("menu_basligi"), 120) or _MENU_BASLIGI
    satir.duyuru_metni = _metin(veri.get("duyuru_metni"), 4000)
    satir.komisyon_orani = _oran(veri.get("komisyon_orani"))
    satir.kargo_destek_orani = _oran(veri.get("kargo_destek_orani"))
    satir.kargo_destek_alt_limit = _pozitif_sayi(veri.get("kargo_destek_alt_limit"))
    saat = _pozitif_sayi(veri.get("kargo_suresi_saat"), tam=True)
    satir.kargo_suresi_saat = min(240, max(1, saat or 48))
    satir.cayma_cezasi_orani = _oran(veri.get("cayma_cezasi_orani"))
    satir.tc_zorunlu = _evet(veri.get("tc_zorunlu"))
    satir.paketleme_kaniti_zorunlu = _evet(veri.get("paketleme_kaniti_zorunlu"))
    alt = min(4, max(1, _pozitif_sayi(veri.get("min_fotograf"), tam=True) or 1))
    ust = min(4, max(alt, _pozitif_sayi(veri.get("max_fotograf"), tam=True) or 4))
    satir.min_fotograf = alt
    satir.max_fotograf = ust
    if "yasal_sozlesme_metni" in veri:
        metin = str(veri.get("yasal_sozlesme_metni") or "").strip()
        satir.yasal_sozlesme_metni = metin or _VARSAYILAN_SOZLESME


def _dukkan_kodu(ham):
    kod = str(ham or "").strip().lower()
    return kod if kod in _DUKKANLAR else "karma"


def _dukkan_eslestir(ham):
    """AI önerisini dükkan koduna çevirir. Tanınmazsa boş döner."""
    kod = str(ham or "").strip().lower()
    if kod in _DUKKANLAR:
        return kod
    for aday, ad in _DUKKAN_ADLARI:
        if ad.casefold() == kod or aday in kod:
            return aday
    return ""


@contextmanager
def _bir_dort_foto(ayar):
    """Tedarikçi gönderiminde fotoğraf kuralı 1-4. Ayar satırına yazılmaz."""
    eski = (ayar.min_fotograf, ayar.max_fotograf)
    ayar.min_fotograf = 1
    ayar.max_fotograf = 4
    try:
        yield
    finally:
        ayar.min_fotograf, ayar.max_fotograf = eski


def _urun_modeli():
    """Canlı vitrinin okuduğu `urun` tablosu. Model satırına dokunulmaz."""
    for esleme in _db().Model.registry.mappers:
        model = esleme.class_
        if getattr(model, "__tablename__", "") == "urun":
            return model
    raise RuntimeError("Vitrin ürün tablosu bulunamadı")


def _yukleme_klasoru():
    klasor = str(current_app.config.get("UPLOAD_FOLDER") or "").strip()
    if not klasor:
        klasor = os.path.join(_KOK, "static", "uploads")
    os.makedirs(klasor, exist_ok=True)
    return klasor


def _vitrin_kaynak_kodu(satir):
    return "tedarikci:" + str(int(satir.id))


def _vitrin_fotolarini_kopyala(satir):
    klasor = _yukleme_klasoru()
    adresler = []
    for ad in fotograflari_coz(satir.fotograflar):
        kaynak = _medya_yolu(ad)
        if not kaynak or _uzanti(ad) not in _RESIM:
            continue
        hedef_ad = uuid.uuid4().hex[:10] + "_" + os.path.basename(ad)
        shutil.copyfile(kaynak, os.path.join(klasor, hedef_ad))
        adresler.append("/static/uploads/" + hedef_ad)
    return adresler


def _vitrine_ekle(satir):
    """Seçilen dükkanın vitrinine ve izleyici vitrin listesine aktif ürün yazar."""
    Urun = _urun_modeli()
    db = _db()
    kod = _vitrin_kaynak_kodu(satir)
    var = Urun.query.filter_by(kaynak_tipi=kod).first()
    if var is not None:
        var.silindi_mi = False
        var.durum = "Aktif"
        var.dukkan_kodu = _dukkan_kodu(satir.hedef_kategori)
        var.urun_adi = _metin(satir.urun_adi, 200) or var.urun_adi
        var.acilis_fiyati = float(satir.taban_fiyat or 0)
        var.guncel_fiyat = float(satir.taban_fiyat or 0)
        fotolar = _vitrin_fotolarini_kopyala(satir)
        if fotolar:
            var.fotograflar = fotolar
        db.session.flush()
        return var.id
    fotolar = _vitrin_fotolarini_kopyala(satir)
    if not fotolar:
        raise RuntimeError("Vitrine aktarılacak fotoğraf yok")
    dukkan = _dukkan_kodu(satir.hedef_kategori)
    en_yuksek = (
        db.session.query(func.max(Urun.lot_no))
        .filter(Urun.dukkan_kodu == dukkan, Urun.silindi_mi.is_(False))
        .scalar()
    )
    taban = float(satir.taban_fiyat or 0)
    adlar = dict(_DUKKAN_ADLARI)
    urun = Urun(
        dukkan_kodu=dukkan,
        lot_no=int(en_yuksek or 0) + 1,
        urun_adi=_metin(satir.urun_adi, 200) or "Tedarikçi ürünü",
        kategori=_metin(adlar.get(dukkan, "Genel"), 100),
        acilis_fiyati=taban,
        guncel_fiyat=taban,
        hemen_al_fiyati=float(satir.satis_fiyati or 0),
        tanitim_yazisi=_metin(satir.aciklama, 4000),
        fotograflar=fotolar,
        video="",
        ses_dosyasi="",
        durum="Aktif",
        kazanan_adi="Yok",
        kaynak_tipi=kod,
        stok_adedi=1,
        silindi_mi=False,
    )
    db.session.add(urun)
    db.session.flush()
    return urun.id


def _vitrinden_kaldir(satir):
    try:
        Urun = _urun_modeli()
    except RuntimeError:
        return
    var = Urun.query.filter_by(kaynak_tipi=_vitrin_kaynak_kodu(satir)).first()
    if var is None:
        return
    var.silindi_mi = True
    var.durum = "Pasif"


def _katalogdan_cikar(oge_id):
    if not oge_id:
        return
    import katalog_havuzu as katalog

    with katalog._KILIT:
        kayitlar = katalog._havuz()
        kalan = [kayit for kayit in kayitlar if str(kayit.get("id") or "") != str(oge_id)]
        if len(kalan) != len(kayitlar):
            katalog._yaz(katalog._HAVUZ_YOL, kalan)


def _kataloga_ekle(satir):
    kaynak = ""
    uzanti = ""
    for ad in fotograflari_coz(satir.fotograflar):
        yol = _medya_yolu(ad)
        if yol and _uzanti(ad) in _RESIM:
            kaynak = yol
            uzanti = _uzanti(ad)
            break
    if not kaynak:
        raise RuntimeError("Vitrine aktarılacak fotoğraf yok")
    import katalog_havuzu as katalog

    oge_id = uuid.uuid4().hex
    saklanan = oge_id + uzanti
    os.makedirs(katalog._MEDYA, exist_ok=True)
    shutil.copyfile(kaynak, os.path.join(katalog._MEDYA, saklanan))
    kayit = {
        "id": oge_id,
        "ad": _metin(satir.urun_adi, 180) or saklanan,
        "tur": "fotograf",
        "mime": _MIME.get(uzanti, "image/jpeg"),
        "dosya": saklanan,
        "durum": "Onaylandı",
        "baslik": _metin(satir.urun_adi, 200),
        "malzeme": "",
        "boyut": "",
        "secere": "Tedarikçi havuzu #" + str(satir.id),
        "kondisyon": _metin(satir.aciklama, 2000),
        "baslangic_fiyati": float(satir.taban_fiyat or 0),
        "hemen_al_fiyati": float(satir.satis_fiyati or 0),
        "dukkan": _dukkan_kodu(satir.hedef_kategori),
        "olusturma": datetime.now().isoformat(timespec="seconds"),
    }
    with katalog._KILIT:
        kayitlar = katalog._havuz()
        kayitlar.append(kayit)
        katalog._yaz(katalog._HAVUZ_YOL, kayitlar)
    return oge_id


def _net_yaz(satir, ayar):
    satir.net_hakedis = net_hakedis_hesapla(
        satir.satis_fiyati,
        satir.komisyon_orani,
        satir.kargo_destek_orani,
        _ayar_degeri(ayar, "kargo_destek_alt_limit"),
    )


def _urun_bul(urun_id):
    try:
        kimlik = int(urun_id)
    except (TypeError, ValueError):
        return None
    return _db().session.get(TedarikciUrunleri, kimlik)


def _satici_urun(urun_id):
    satir = _urun_bul(urun_id)
    telefon = _oturum_telefon()
    if satir is None or not telefon or satir.telefon != telefon:
        return None
    return satir


def _profil_oku(hesap):
    ad = str(getattr(hesap, "ad_soyad", "") or "")
    tc = str(getattr(hesap, "tc_kimlik_no", "") or "")
    iban = str(getattr(hesap, "iban", "") or "")
    sehir = str(getattr(hesap, "sehir", "") or "")
    if not ad or not tc or not iban or not sehir:
        son = (
            TedarikciUrunleri.query.filter_by(telefon=hesap.telefon)
            .order_by(TedarikciUrunleri.id.desc())
            .first()
        )
        if son is not None:
            ad = ad or son.satici_ad_soyad or ""
            tc = tc or son.tc_kimlik_no or ""
            iban = iban or son.iban or ""
            sehir = sehir or son.sehir or ""
    return {
        "satici_ad_soyad": ad,
        "telefon": hesap.telefon or "",
        "tc_kimlik_no": tc,
        "iban": iban,
        "sehir": sehir,
    }


def _profil_kaydet(hesap, veri):
    veri = veri or {}
    ad = _metin(veri.get("satici_ad_soyad"), 120)
    if ad:
        hesap.ad_soyad = ad
    if "tc_kimlik_no" in veri:
        hesap.tc_kimlik_no = "".join(
            ch for ch in _metin(veri.get("tc_kimlik_no"), 11) if ch.isdigit()
        )[:11]
    if "iban" in veri:
        hesap.iban = _metin(veri.get("iban"), 34).replace(" ", "").upper()
    if "sehir" in veri:
        hesap.sehir = _metin(veri.get("sehir"), 60)


def _mukerrer_veritabani(telefon, ad, an):
    try:
        kayitlar = (
            TedarikciUrunleri.query.filter(TedarikciUrunleri.telefon == telefon)
            .order_by(TedarikciUrunleri.id.desc())
            .limit(12)
            .all()
        )
    except Exception:
        return False
    for satir in kayitlar:
        if str(satir.urun_adi or "").strip().casefold() != ad:
            continue
        zaman = satir.olusturma_zamani
        if not isinstance(zaman, datetime):
            continue
        if zaman.tzinfo is None:
            zaman = zaman.replace(tzinfo=timezone.utc)
        if (an - zaman).total_seconds() < 60:
            return True
    return False


def _mukerrer_ayir(telefon, urun_adi):
    """Aynı telefon ve ürün adı için 60 saniyelik yer ayırır.

    Engel varsa None, kilit gerekmiyorsa boş metin, tutulduysa anahtar döner.
    """
    ad = _metin(urun_adi, 200).casefold()
    if len(telefon) < 10 or not ad:
        return ""
    anahtar = telefon + "\n" + ad
    an = simdi_utc()
    with _MUKERRER_KILIT:
        eski = [k for k, zaman in _MUKERRER.items() if (an - zaman).total_seconds() > 120]
        for k in eski:
            _MUKERRER.pop(k, None)
        once = _MUKERRER.get(anahtar)
        if once is not None and (an - once).total_seconds() < 60:
            return None
        _MUKERRER[anahtar] = an
    if _mukerrer_veritabani(telefon, ad, an):
        with _MUKERRER_KILIT:
            _MUKERRER.pop(anahtar, None)
        return None
    return anahtar


def _mukerrer_birak(anahtar):
    if not anahtar:
        return
    with _MUKERRER_KILIT:
        _MUKERRER.pop(anahtar, None)


def _ai_anahtarlari():
    gemini = ""
    openai_anahtar = ""
    try:
        from config import Config
        gemini = str(getattr(Config, "GEMINI_API_KEY", "") or "").strip()
        openai_anahtar = str(getattr(Config, "OPENAI_API_KEY", "") or "").strip()
    except Exception:
        pass
    if not gemini:
        gemini = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_KEY")
            or ""
        ).strip()
    if not openai_anahtar:
        openai_anahtar = (os.environ.get("OPENAI_API_KEY") or "").strip()
    return gemini, openai_anahtar


def _json_ayikla(ham):
    metin = str(ham or "").strip()
    if metin.startswith("```"):
        metin = metin.strip("`")
        if metin[:4].lower() == "json":
            metin = metin[4:]
        metin = metin.strip()
    try:
        veri = json.loads(metin)
        if isinstance(veri, dict):
            return veri
    except (TypeError, ValueError):
        pass
    bas = metin.find("{")
    son = metin.rfind("}")
    if bas < 0 or son <= bas:
        return None
    try:
        veri = json.loads(metin[bas:son + 1])
    except (TypeError, ValueError):
        return None
    return veri if isinstance(veri, dict) else None


def _http_json(url, payload, headers, zaman_asimi):
    govde = json.dumps(payload).encode("utf-8")
    istek = urllib.request.Request(url, data=govde, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(istek, timeout=zaman_asimi) as yanit:
            return json.loads(yanit.read().decode("utf-8")), ""
    except TimeoutError:
        return None, "zaman"
    except urllib.error.URLError as hata:
        neden = getattr(hata, "reason", None)
        if isinstance(neden, TimeoutError):
            return None, "zaman"
        return None, "servis"
    except Exception:
        return None, "servis"


def _gemini_iste(anahtar, istem, gorseller):
    parcalar = [{"text": istem}]
    for mime, bayt in gorseller:
        parcalar.append({
            "inlineData": {
                "mimeType": mime or "image/jpeg",
                "data": base64.b64encode(bayt).decode("ascii"),
            }
        })
    paket, hata = _http_json(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + anahtar,
        {
            "contents": [{"role": "user", "parts": parcalar}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        },
        {"Content-Type": "application/json"},
        18,
    )
    if hata or not isinstance(paket, dict):
        return "", hata or "servis"
    adaylar = paket.get("candidates") or []
    if not adaylar:
        return "", "servis"
    parca_listesi = ((adaylar[0].get("content") or {}).get("parts") or [])
    metin = "\n".join(str(parca.get("text") or "") for parca in parca_listesi if isinstance(parca, dict))
    return metin, ""


def _openai_iste(anahtar, istem, gorseller):
    icerik = [{"type": "text", "text": istem}]
    for mime, bayt in gorseller:
        icerik.append({
            "type": "image_url",
            "image_url": {
                "url": "data:" + (mime or "image/jpeg") + ";base64," + base64.b64encode(bayt).decode("ascii")
            },
        })
    paket, hata = _http_json(
        "https://api.openai.com/v1/chat/completions",
        {
            "model": "gpt-4o-mini",
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": icerik}],
        },
        {"Content-Type": "application/json", "Authorization": "Bearer " + anahtar},
        18,
    )
    if hata or not isinstance(paket, dict):
        return "", hata or "servis"
    secimler = paket.get("choices") or []
    if not secimler:
        return "", "servis"
    return str(((secimler[0].get("message") or {}).get("content") or "")), ""


def _ai_metin(istem, gorseller):
    gemini, openai_anahtar = _ai_anahtarlari()
    if not gemini and not openai_anahtar:
        return "", "anahtar"
    hatalar = []
    if gemini:
        metin, hata = _gemini_iste(gemini, istem, gorseller)
        if metin.strip():
            return metin, ""
        if hata:
            hatalar.append(hata)
    if openai_anahtar:
        metin, hata = _openai_iste(openai_anahtar, istem, gorseller)
        if metin.strip():
            return metin, ""
        if hata:
            hatalar.append(hata)
    if "zaman" in hatalar:
        return "", "zaman"
    return "", "servis"


def _ai_nazik(kod):
    if kod == "anahtar":
        mesaj = "AI anahtarı tanımlı değil. Bilgileri kendiniz girip göndermeye devam edebilirsiniz."
    elif kod == "zaman":
        mesaj = "AI yanıtı zaman aşımına uğradı. Formu elle doldurmaya devam edebilirsiniz."
    else:
        mesaj = "AI ön analizi şu an tamamlanamadı. Ürünü kendiniz girip göndermeye devam edebilirsiniz."
    return jsonify({"success": False, "manuel": True, "mesaj": mesaj})


def _ai_sik_mi(anahtar, saniye):
    an = simdi_utc()
    with _AI_KILIT:
        eski = [k for k, zaman in _AI_SON.items() if (an - zaman).total_seconds() > 120]
        for k in eski:
            _AI_SON.pop(k, None)
        once = _AI_SON.get(anahtar)
        if once is not None and (an - once).total_seconds() < saniye:
            return True
        _AI_SON[anahtar] = an
    return False


def _gorselleri_oku(dosyalar, limit=4):
    gorseller = []
    for dosya in dosyalar or []:
        if len(gorseller) >= limit:
            break
        mime = str(getattr(dosya, "mimetype", "") or "").split(";")[0].strip().lower()
        uzanti = _uzanti(getattr(dosya, "filename", ""))
        if uzanti not in _RESIM and not mime.startswith("image/"):
            continue
        if not mime.startswith("image/"):
            mime = _MIME.get(uzanti, "image/jpeg")
        try:
            icerik = dosya.read()
        except Exception:
            continue
        if not icerik or len(icerik) > 6 * 1024 * 1024:
            continue
        gorseller.append((mime, icerik))
    return gorseller


def _disk_gorselleri(satir, limit=4):
    gorseller = []
    for ad in fotograflari_coz(satir.fotograflar):
        if len(gorseller) >= limit:
            break
        yol = _medya_yolu(ad)
        if not yol or _uzanti(ad) not in _RESIM:
            continue
        try:
            with open(yol, "rb") as dosya:
                icerik = dosya.read()
        except Exception:
            continue
        if not icerik or len(icerik) > 6 * 1024 * 1024:
            continue
        gorseller.append((_MIME.get(_uzanti(ad), "image/jpeg"), icerik))
    return gorseller


def _dukkan_listesi_metni():
    return ", ".join(kod + " (" + ad + ")" for kod, ad in _DUKKAN_ADLARI)


def _on_analiz_istemi():
    return (
        "Sen bir müzayede vitrini için ön ekspertiz asistanısın. Yalnızca fotoğraflarda görünen ürünü yorumla. "
        "Çıktın tek bir JSON nesnesi olsun. Başka metin yazma. Anahtarlar: "
        "onerilen_baslik (kısa kurumsal ürün adı), "
        "secere_ve_aciklama (muhtemel dönem, malzeme veya maden, işçilik tekniği, görünür kondisyon ve kısa seçere hikayesi), "
        "onerilen_dukkan (yalnızca verilen kodlardan biri). "
        "2863 sayılı Kültür ve Tabiat Varlıklarını Koruma Kanunu kapsamına giren kazı buluntusu veya satışı yasak kültür varlığı "
        "olabileceğini düşünüyorsan bunu secere_ve_aciklama içinde açıkça uyar. Satışı gizleyecek veya yasağı aşacak yönlendirme yazma. "
        "Dükkan kodları: " + _dukkan_listesi_metni()
    )


def _on_analiz_paketi(veri):
    dukkan = _dukkan_eslestir(veri.get("onerilen_dukkan"))
    return {
        "onerilen_baslik": _metin(veri.get("onerilen_baslik"), 200),
        "secere_ve_aciklama": _metin(veri.get("secere_ve_aciklama"), 4000),
        "onerilen_dukkan": dukkan,
        "ekspertiz_notu": _AI_ON_NOTU,
    }


def _risk_seviye(ham):
    metin = str(ham or "").casefold()
    if "yük" in metin or "yuksek" in metin or "high" in metin:
        return "Yüksek Risk"
    if "düş" in metin or "dusuk" in metin or "low" in metin:
        return "Düşük Risk"
    if "orta" in metin or "medium" in metin:
        return "Orta"
    return "Orta"


def _denetim_istemi(satir):
    return (
        "Sen yönetici denetim laboratuvarısın. Tedarikçi metnini ve fotoğrafları birbirinden bağımsız denetle. "
        "Yalnızca JSON döndür. Anahtarlar: "
        "risk_seviye (Düşük Risk, Orta veya Yüksek Risk), "
        "risk_gerekce (2863 sayılı kanun açısından kazı veya tarihi eser şüphesi varsa açık uyarı, yoksa kısa gerekçe), "
        "tutarlilik (satıcının yazdığı bilgi ile fotoğraftaki malzeme ve kusur uyuşuyor mu), "
        "vitrin_baslik (müzayede vitrinine uygun profesyonel başlık), "
        "vitrin_aciklama (profesyonel düzeltilmiş açıklama), "
        "piyasa_aralik (tahmini TL piyasa aralığı; kesin ekspertiz değildir). "
        "Yasak eser şüphesinde satışa teşvik veya gizleme önerisi yazma. "
        "Tedarikçi başlığı: " + _metin(satir.urun_adi, 200) + ". "
        "Tedarikçi açıklaması: " + _metin(satir.aciklama, 4000) + ". "
        "Hedef dükkan: " + _metin(satir.hedef_kategori, 120) + ". "
        "Taban fiyat: " + str(_pozitif_sayi(satir.taban_fiyat)) + " TL."
    )


def _denetim_paketi(veri):
    return {
        "risk_seviye": _risk_seviye(veri.get("risk_seviye")),
        "risk_gerekce": _metin(veri.get("risk_gerekce"), 1500),
        "tutarlilik": _metin(veri.get("tutarlilik"), 1500),
        "vitrin_baslik": _metin(veri.get("vitrin_baslik"), 200),
        "vitrin_aciklama": _metin(veri.get("vitrin_aciklama"), 4000),
        "piyasa_aralik": _metin(veri.get("piyasa_aralik"), 160),
    }


@tedarikci_bp.before_request
def _istek_oncesi():
    if TedarikciAyarlari is None:
        return jsonify({"success": False, "mesaj": "Tedarikçi modülü hazır değil."}), 503
    try:
        _sema_emin_ol()
    except Exception as hata:
        try:
            _db().session.rollback()
        except Exception:
            pass
        print(f"[TEDARIKCI] sema hazirlanamadi: {hata}", flush=True)
        return jsonify({"success": False, "mesaj": "Tedarikçi tabloları hazırlanamadı."}), 500
    return None


@tedarikci_bp.teardown_request
def _istek_sonu(hata):
    try:
        db = _db()
        if hata is not None:
            db.session.rollback()
        db.session.remove()
    except Exception:
        pass


@tedarikci_bp.route("/api/tedarikci/ayarlar", methods=["GET"])
def api_tedarikci_ayarlar():
    return jsonify({"success": True, "ayar": _ayar_sozluk(_ayar_satiri(), herkese=True)})


@tedarikci_bp.route("/api/tedarikci/giris", methods=["POST"])
def api_tedarikci_giris():
    if not _panel_acik():
        return jsonify({"success": False, "mesaj": "Tedarikçi paneli kapalı."}), 403
    veri = _form_girdi()
    telefon = _telefon_norm(veri.get("telefon"))
    pin = _metin(veri.get("pin"), 8)
    if len(telefon) < 10 or not pin.isdigit():
        return jsonify({"success": False, "mesaj": "Telefon ve PIN gerekli."}), 400
    if _pin_kilitli(telefon):
        return jsonify({"success": False, "mesaj": "Çok fazla deneme. Bir süre sonra yeniden deneyin."}), 429
    hesap = _hesap_bul(telefon)
    if hesap is None or not _pin_dogru(pin, hesap.pin_hash):
        _pin_basarisiz(telefon)
        return jsonify({"success": False, "mesaj": "Telefon veya PIN hatalı."}), 401
    if hesap.askida:
        return jsonify({"success": False, "mesaj": "Hesabınız askıya alındı."}), 403
    _pin_temizle(telefon)
    _giris_yaz(hesap)
    return jsonify({"success": True, "telefon": hesap.telefon, "ad_soyad": hesap.ad_soyad or ""})


@tedarikci_bp.route("/api/tedarikci/pin-guncelle", methods=["POST"])
def api_tedarikci_pin_guncelle():
    if not _panel_acik():
        return jsonify({"success": False, "mesaj": "Tedarikçi paneli kapalı."}), 403
    telefon = _oturum_telefon()
    if not telefon:
        return jsonify({"success": False, "mesaj": "Giriş gerekli."}), 401
    if _pin_kilitli(telefon):
        return jsonify({"success": False, "mesaj": "Çok fazla deneme. Bir süre sonra yeniden deneyin."}), 429
    hesap = _hesap_bul(telefon)
    if hesap is None or hesap.askida:
        session.pop("tedarikci_telefon", None)
        session.pop("tedarikci_id", None)
        return jsonify({"success": False, "mesaj": "Hesap bulunamadı."}), 403
    veri = _form_girdi()
    mevcut = _metin(veri.get("mevcut_pin"), 8)
    yeni = _metin(veri.get("yeni_pin"), 8)
    if not mevcut.isdigit() or not _pin_dogru(mevcut, hesap.pin_hash):
        _pin_basarisiz(telefon)
        return jsonify({"success": False, "mesaj": "Mevcut PIN hatalı."}), 401
    if not (yeni.isdigit() and 4 <= len(yeni) <= 6):
        return jsonify({"success": False, "mesaj": "Yeni PIN 4-6 rakam olmalı."}), 400
    hesap.pin_hash = _pin_hashle(yeni)
    _pin_temizle(telefon)
    _db().session.commit()
    return jsonify({"success": True, "mesaj": "PIN kodunuz başarıyla güncellendi"})


@tedarikci_bp.route("/api/tedarikci/cikis", methods=["POST"])
def api_tedarikci_cikis():
    session.pop("tedarikci_telefon", None)
    session.pop("tedarikci_id", None)
    return jsonify({"success": True})


@tedarikci_bp.route("/api/tedarikci/oturum", methods=["GET"])
def api_tedarikci_oturum():
    panel = _panel_acik()
    telefon = _oturum_telefon()
    hesap = _hesap_bul(telefon) if telefon else None
    if hesap is None or hesap.askida:
        session.pop("tedarikci_telefon", None)
        session.pop("tedarikci_id", None)
        return jsonify({"success": True, "giris": False, "panel_aktif": panel})
    profil = _profil_oku(hesap)
    return jsonify({
        "success": True,
        "giris": True,
        "panel_aktif": panel,
        "telefon": hesap.telefon,
        "ad_soyad": profil["satici_ad_soyad"] or hesap.ad_soyad or "",
        "profil": profil,
    })


@tedarikci_bp.route("/api/tedarikci/urunlerim", methods=["GET"])
def api_tedarikci_urunlerim():
    if not _panel_acik():
        return jsonify({"success": False, "mesaj": "Tedarikçi paneli kapalı."}), 403
    telefon = _oturum_telefon()
    if not telefon:
        return jsonify({"success": False, "mesaj": "Giriş gerekli."}), 401
    kayitlar = (
        TedarikciUrunleri.query.filter(
            TedarikciUrunleri.telefon == telefon,
            TedarikciUrunleri.durum != "silindi",
        )
        .order_by(TedarikciUrunleri.id.desc())
        .all()
    )
    ayar = _ayar_satiri()
    return jsonify({
        "success": True,
        "urunler": [_urun_sozluk(satir) for satir in kayitlar],
        "kargo_destek_alt_limit": float(ayar.kargo_destek_alt_limit or 0),
        "cayma_cezasi_orani": float(ayar.cayma_cezasi_orani or 0),
    })


@tedarikci_bp.route("/api/tedarikci/profil", methods=["POST"])
def api_tedarikci_profil():
    if not _panel_acik():
        return jsonify({"success": False, "mesaj": "Tedarikçi paneli kapalı."}), 403
    telefon = _oturum_telefon()
    if not telefon:
        return jsonify({"success": False, "mesaj": "Giriş gerekli."}), 401
    hesap = _hesap_bul(telefon)
    if hesap is None or hesap.askida:
        return jsonify({"success": False, "mesaj": "Hesap bulunamadı."}), 403
    _profil_kaydet(hesap, _form_girdi())
    _db().session.commit()
    return jsonify({"success": True, "profil": _profil_oku(hesap)})


@tedarikci_bp.route("/api/tedarikci/urun", methods=["POST"])
def api_tedarikci_urun_gonder():
    if not _panel_acik():
        return jsonify({"success": False, "mesaj": "Tedarikçi paneli kapalı."}), 403
    veri = _form_girdi()
    telefon = _telefon_norm(veri.get("telefon") or _oturum_telefon())
    if len(telefon) < 10:
        return jsonify({"success": False, "mesaj": "Telefon gerekli."}), 400
    kilit = _mukerrer_ayir(telefon, veri.get("urun_adi"))
    if kilit is None:
        return jsonify({
            "success": False,
            "mesaj": "Aynı telefon ve ürün adı 60 saniye içinde yeniden gönderilemez. Lütfen bekleyin.",
        }), 429
    try:
        return _urun_gonder_kaydet(veri, telefon, kilit)
    except Exception:
        _mukerrer_birak(kilit)
        raise


def _urun_gonder_kaydet(veri, telefon, kilit):
    ayar = _ayar_satiri()
    hesap = _hesap_bul(telefon)
    yeni_pin = ""
    if hesap is not None:
        if hesap.askida:
            _mukerrer_birak(kilit)
            return jsonify({"success": False, "mesaj": "Hesabınız askıya alındı."}), 403
        if _oturum_telefon() != telefon:
            _mukerrer_birak(kilit)
            return jsonify({"success": False, "mesaj": "Önce telefon ve PIN ile giriş yapın."}), 401
    else:
        if _oturum_telefon() and _oturum_telefon() != telefon:
            _mukerrer_birak(kilit)
            return jsonify({"success": False, "mesaj": "Oturumdaki telefon ile form telefonu farklı."}), 400
        yeni_pin, pin_hata = _ilk_kayit_pini(veri.get("kendi_pin"))
        if pin_hata:
            _mukerrer_birak(kilit)
            return jsonify({"success": False, "mesaj": pin_hata}), 400
        hesap = TedarikciHesaplari(
            telefon=telefon,
            pin_hash=_pin_hashle(yeni_pin),
            ad_soyad=_metin(veri.get("satici_ad_soyad"), 120),
            askida=False,
            olusturma_zamani=simdi_utc(),
        )
        _db().session.add(hesap)
    _profil_kaydet(hesap, veri)
    fotolar = _dosyalari_kaydet(request.files.getlist("fotograflar"), _RESIM, _RESIM_BAYT)
    girdi = dict(veri)
    girdi["telefon"] = telefon
    girdi["fotograflar"] = fotolar
    girdi["durum"] = "bekliyor"
    with _bir_dort_foto(ayar):
        hazir = urun_kaydini_hazirla(
            girdi, ayar, yukleyen_ip=_istek_ip(), paketleme_kontrol=False
        )
    if hazir["hatalar"]:
        _mukerrer_birak(kilit)
        if yeni_pin:
            _giris_yaz(hesap)
            _db().session.commit()
        else:
            _db().session.rollback()
        cevap = {
            "success": False,
            "hatalar": hazir["hatalar"],
            "mesaj": _hata_mesaji(hazir["hatalar"]),
        }
        if yeni_pin:
            cevap["yeni_pin"] = yeni_pin
        return jsonify(cevap), 400
    if _mukerrer_veritabani(telefon, _metin(veri.get("urun_adi"), 200).casefold(), simdi_utc()):
        _mukerrer_birak(kilit)
        _db().session.rollback()
        return jsonify({
            "success": False,
            "mesaj": "Aynı telefon ve ürün adı 60 saniye içinde yeniden gönderilemez. Lütfen bekleyin.",
        }), 429
    alanlar = hazir["alanlar"]
    alanlar["onaylanan_beyan_metni"] = str(ayar.yasal_sozlesme_metni or "")
    alanlar["yukleyen_ip"] = _istek_ip()
    alanlar["olusturma_zamani"] = simdi_utc()
    satir = TedarikciUrunleri(**alanlar)
    _profil_kaydet(hesap, {
        "satici_ad_soyad": satir.satici_ad_soyad,
        "tc_kimlik_no": satir.tc_kimlik_no,
        "iban": satir.iban,
        "sehir": satir.sehir,
    })
    _giris_yaz(hesap)
    _db().session.add(satir)
    _db().session.commit()
    cevap = {"success": True, "urun": _urun_sozluk(satir), "profil": _profil_oku(hesap)}
    if yeni_pin:
        cevap["yeni_pin"] = yeni_pin
        cevap["mesaj"] = "Hesabınız açıldı. PIN numaranızı saklayın: " + yeni_pin
    return jsonify(cevap)


def _satici_kilit_cevabi(satir):
    if satir is None:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    if satir.durum in _KILITSIZ:
        return None
    return jsonify({
        "success": False,
        "kilit": True,
        "mesaj": "Onaydan sonra düzenleme ve silme kapalı. Yalnızca iptal talebi gönderebilirsiniz.",
    }), 403


@tedarikci_bp.route("/api/tedarikci/urun/<int:urun_id>/guncelle", methods=["POST"])
def api_tedarikci_urun_guncelle(urun_id):
    if not _panel_acik():
        return jsonify({"success": False, "mesaj": "Tedarikçi paneli kapalı."}), 403
    satir = _satici_urun(urun_id)
    kilit = _satici_kilit_cevabi(satir)
    if kilit is not None:
        return kilit
    veri = _form_girdi()
    ayar = _ayar_satiri()
    eski_ip = satir.yukleyen_ip
    eski_zaman = satir.olusturma_zamani
    eski_beyan = satir.onaylanan_beyan_metni
    fotolar = _dosyalari_kaydet(request.files.getlist("fotograflar"), _RESIM, _RESIM_BAYT)
    girdi = dict(veri)
    girdi["telefon"] = satir.telefon
    for alan in (
        "satici_ad_soyad", "tc_kimlik_no", "iban", "sehir", "urun_adi",
        "aciklama", "hedef_kategori", "taban_fiyat", "satis_fiyati",
    ):
        if str(girdi.get(alan) or "").strip() == "":
            girdi[alan] = getattr(satir, alan)
    if not _evet(girdi.get("yasal_beyan_onay")):
        girdi["yasal_beyan_onay"] = bool(satir.yasal_beyan_onay)
    girdi["fotograflar"] = fotolar or fotograflari_coz(satir.fotograflar)
    girdi["durum"] = "bekliyor"
    with _bir_dort_foto(ayar):
        hazir = urun_kaydini_hazirla(
            girdi, ayar, yukleyen_ip=eski_ip, paketleme_kontrol=False
        )
    if hazir["hatalar"]:
        return jsonify({
            "success": False,
            "hatalar": hazir["hatalar"],
            "mesaj": _hata_mesaji(hazir["hatalar"]),
        }), 400
    for anahtar, deger in hazir["alanlar"].items():
        if anahtar in ("yukleyen_ip", "olusturma_zamani", "onaylanan_beyan_metni", "durum"):
            continue
        setattr(satir, anahtar, deger)
    satir.yukleyen_ip = eski_ip
    satir.olusturma_zamani = eski_zaman
    satir.onaylanan_beyan_metni = _beyan_sakla(eski_beyan, ayar.yasal_sozlesme_metni)
    satir.durum = "bekliyor"
    hesap = _hesap_bul(satir.telefon)
    if hesap is not None:
        _profil_kaydet(hesap, {
            "satici_ad_soyad": satir.satici_ad_soyad,
            "tc_kimlik_no": satir.tc_kimlik_no,
            "iban": satir.iban,
            "sehir": satir.sehir,
        })
    _db().session.commit()
    return jsonify({"success": True, "urun": _urun_sozluk(satir)})


@tedarikci_bp.route("/api/tedarikci/urun/<int:urun_id>/sil", methods=["POST"])
def api_tedarikci_urun_sil(urun_id):
    if not _panel_acik():
        return jsonify({"success": False, "mesaj": "Tedarikçi paneli kapalı."}), 403
    satir = _satici_urun(urun_id)
    kilit = _satici_kilit_cevabi(satir)
    if kilit is not None:
        return kilit
    satir.durum = "silindi"
    _db().session.commit()
    return jsonify({"success": True})


@tedarikci_bp.route("/api/tedarikci/urun/<int:urun_id>/iptal-talebi", methods=["POST"])
def api_tedarikci_iptal_talebi(urun_id):
    if not _panel_acik():
        return jsonify({"success": False, "mesaj": "Tedarikçi paneli kapalı."}), 403
    satir = _satici_urun(urun_id)
    if satir is None:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    if satir.durum == "iptal_talebi":
        return jsonify({"success": True, "mesaj": "İptal talebi zaten yöneticiye iletildi."})
    if satir.durum not in _IPTAL_ACIK:
        return jsonify({"success": False, "mesaj": "Bu durumda iptal talebi gönderilemez."}), 400
    satir.durum = "iptal_talebi"
    _db().session.commit()
    return jsonify({"success": True, "urun": _urun_sozluk(satir)})


@tedarikci_bp.route("/api/tedarikci/urun/<int:urun_id>/kargola", methods=["POST"])
def api_tedarikci_kargola(urun_id):
    if not _panel_acik():
        return jsonify({"success": False, "mesaj": "Tedarikçi paneli kapalı."}), 403
    satir = _satici_urun(urun_id)
    if satir is None:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    if satir.durum != "satildi_kargo_bekliyor":
        return jsonify({"success": False, "mesaj": "Kargo girişi yalnız satılmış ürünlerde açılır."}), 400
    veri = _form_girdi()
    firma = _metin(veri.get("kargo_firmasi"), 80)
    takip = _metin(veri.get("kargo_takip_no"), 100)
    kanitlar = _dosyalari_kaydet(
        request.files.getlist("paketleme_kaniti"), _KANIT, _KANIT_BAYT
    )
    kanit = kanitlar[0] if kanitlar else os.path.basename(str(satir.paketleme_kaniti or ""))
    if not kanit or not firma or not takip:
        return jsonify({
            "success": False,
            "mesaj": "Paketleme/fiş kanıtı, kargo firması ve takip numarası girilmeden kargolandı durumuna geçilemez.",
        }), 400
    satir.paketleme_kaniti = kanit
    satir.kargo_firmasi = firma
    satir.kargo_takip_no = takip
    satir.durum = "kargolandi"
    _db().session.commit()
    return jsonify({"success": True, "urun": _urun_sozluk(satir)})


@tedarikci_bp.route("/api/tedarikci/medya/<ad>", methods=["GET"])
def api_tedarikci_medya(ad):
    temiz = os.path.basename(ad or "")
    yol = _medya_yolu(temiz)
    if not yol:
        return jsonify({"success": False, "mesaj": "Dosya yok."}), 404
    if not _admin_mi():
        telefon = _oturum_telefon()
        if not telefon:
            return jsonify({"success": False, "mesaj": "Giriş gerekli."}), 401
        sahip = TedarikciUrunleri.query.filter(
            TedarikciUrunleri.telefon == telefon,
            or_(
                TedarikciUrunleri.fotograflar.contains(temiz),
                TedarikciUrunleri.paketleme_kaniti == temiz,
            ),
        ).first()
        if sahip is None:
            return jsonify({"success": False, "mesaj": "Bu dosyaya erişilemez."}), 403
    return send_file(yol, mimetype=_MIME.get(_uzanti(temiz), "application/octet-stream"), conditional=True)


@tedarikci_bp.route("/api/tedarikci/admin/ayarlar", methods=["GET", "POST"])
def api_tedarikci_admin_ayarlar():
    red = _admin_reddi()
    if red is not None:
        return red
    satir = _ayar_satiri()
    if request.method == "POST":
        _ayar_uygula(satir, _form_girdi())
        _db().session.commit()
    return jsonify({"success": True, "ayar": _ayar_sozluk(satir)})


def _sekme_sorgusu(sekme):
    sorgu = TedarikciUrunleri.query
    odendi = TedarikciUrunleri.hakedis_odendi.is_(True)
    odenmedi = or_(
        TedarikciUrunleri.hakedis_odendi.is_(False),
        TedarikciUrunleri.hakedis_odendi.is_(None),
    )
    if sekme == "kargo":
        return sorgu.filter(
            TedarikciUrunleri.durum.in_(("satildi_kargo_bekliyor", "kargolandi", "iptal_talebi")),
            odenmedi,
        )
    if sekme == "vitrin":
        return sorgu.filter(
            TedarikciUrunleri.durum.in_(("vitrin", "onaylandi")),
            odenmedi,
        )
    if sekme == "arsiv":
        return sorgu.filter(or_(
            TedarikciUrunleri.durum.in_(("reddedildi", "silindi")),
            odendi,
        ))
    return sorgu.filter(TedarikciUrunleri.durum == "bekliyor")


def _sekme_sayilari():
    return {
        "bekleyen": _sekme_sorgusu("bekleyen").count(),
        "kargo": _sekme_sorgusu("kargo").count(),
        "vitrin": _sekme_sorgusu("vitrin").count(),
        "arsiv": _sekme_sorgusu("arsiv").count(),
    }


@tedarikci_bp.route("/api/tedarikci/admin/urunler", methods=["GET"])
def api_tedarikci_admin_urunler():
    red = _admin_reddi()
    if red is not None:
        return red
    sekme = _metin(request.args.get("sekme"), 20) or "bekleyen"
    if sekme not in ("bekleyen", "kargo", "vitrin", "arsiv"):
        sekme = "bekleyen"
    hesaplar = {hesap.telefon: hesap for hesap in TedarikciHesaplari.query.all()}
    urunler = []
    kayitlar = (
        _sekme_sorgusu(sekme)
        .order_by(TedarikciUrunleri.id.desc())
        .limit(300)
        .all()
    )
    for satir in kayitlar:
        paket = _urun_sozluk(satir, yonetici=True)
        hesap = hesaplar.get(satir.telefon)
        paket["satici_askida"] = bool(hesap.askida) if hesap else False
        urunler.append(paket)
    return jsonify({
        "success": True,
        "sekme": sekme,
        "sayilar": _sekme_sayilari(),
        "urunler": urunler,
    })


@tedarikci_bp.route("/api/tedarikci/admin/urun/<int:urun_id>/guncelle", methods=["POST"])
def api_tedarikci_admin_urun_guncelle(urun_id):
    red = _admin_reddi()
    if red is not None:
        return red
    satir = _urun_bul(urun_id)
    if satir is None:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    if satir.durum != "bekliyor":
        return jsonify({"success": False, "mesaj": "Yalnız bekleyen ürün düzenlenir."}), 400
    veri = _form_girdi()
    ayar = _ayar_satiri()
    for alan, sinir in (
        ("satici_ad_soyad", 120),
        ("sehir", 60),
        ("urun_adi", 200),
        ("aciklama", 4000),
        ("hedef_kategori", 120),
        ("iban", 34),
        ("admin_notu", 2000),
    ):
        if alan in veri:
            deger = _metin(veri.get(alan), sinir)
            if alan == "iban":
                deger = deger.replace(" ", "").upper()
            setattr(satir, alan, deger)
    if "tc_kimlik_no" in veri:
        tc = "".join(ch for ch in _metin(veri.get("tc_kimlik_no"), 11) if ch.isdigit())
        if ayar.tc_zorunlu and not tc_kimlik_gecerli(tc):
            return jsonify({"success": False, "mesaj": "Geçerli T.C. kimlik numarası gerekli."}), 400
        satir.tc_kimlik_no = tc
    if "taban_fiyat" in veri:
        satir.taban_fiyat = _pozitif_sayi(veri.get("taban_fiyat"))
    if "satis_fiyati" in veri:
        satir.satis_fiyati = _pozitif_sayi(veri.get("satis_fiyati"))
    satir.komisyon_orani = _oran(ayar.komisyon_orani)
    satir.kargo_destek_orani = _oran(ayar.kargo_destek_orani)
    _net_yaz(satir, ayar)
    _db().session.commit()
    return jsonify({"success": True, "urun": _urun_sozluk(satir, yonetici=True)})


@tedarikci_bp.route("/api/tedarikci/admin/urun/<int:urun_id>/onayla", methods=["POST"])
def api_tedarikci_admin_onayla(urun_id):
    red = _admin_reddi()
    if red is not None:
        return red
    satir = _urun_bul(urun_id)
    if satir is None:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    if satir.durum != "bekliyor":
        return jsonify({"success": False, "mesaj": "Yalnız bekleyen ürün vitrine alınır."}), 400
    oge_id = ""
    try:
        oge_id = _kataloga_ekle(satir)
        vitrin_id = _vitrine_ekle(satir)
    except Exception as hata:
        _db().session.rollback()
        try:
            _katalogdan_cikar(oge_id)
        except Exception:
            pass
        return jsonify({"success": False, "mesaj": str(hata) or "Vitrine eklenemedi."}), 400
    satir.durum = "vitrin"
    satir.katalog_oge_id = oge_id
    _db().session.commit()
    paket = _urun_sozluk(satir, yonetici=True)
    paket["vitrin_urun_id"] = vitrin_id
    return jsonify({"success": True, "urun": paket})


@tedarikci_bp.route("/api/tedarikci/admin/urun/<int:urun_id>/kaydi-sil", methods=["POST"])
def api_tedarikci_admin_kaydi_sil(urun_id):
    red = _admin_reddi()
    if red is not None:
        return red
    satir = _urun_bul(urun_id)
    if satir is None:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    _vitrinden_kaldir(satir)
    try:
        _katalogdan_cikar(getattr(satir, "katalog_oge_id", "") or "")
    except Exception:
        pass
    satir.katalog_oge_id = ""
    satir.durum = "silindi"
    _db().session.commit()
    return jsonify({"success": True, "urun": _urun_sozluk(satir, yonetici=True)})


@tedarikci_bp.route("/api/tedarikci/admin/urun/<int:urun_id>/reddet", methods=["POST"])
def api_tedarikci_admin_reddet(urun_id):
    red = _admin_reddi()
    if red is not None:
        return red
    satir = _urun_bul(urun_id)
    if satir is None:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    if satir.durum not in ("bekliyor", "iptal_talebi"):
        return jsonify({"success": False, "mesaj": "Bu ürün reddedilemez."}), 400
    veri = _form_girdi()
    notu = _metin(veri.get("admin_notu"), 2000)
    if notu:
        satir.admin_notu = notu
    satir.durum = "reddedildi"
    _db().session.commit()
    return jsonify({"success": True, "urun": _urun_sozluk(satir, yonetici=True)})


@tedarikci_bp.route("/api/tedarikci/admin/urun/<int:urun_id>/alici", methods=["POST"])
def api_tedarikci_admin_alici(urun_id):
    red = _admin_reddi()
    if red is not None:
        return red
    satir = _urun_bul(urun_id)
    if satir is None:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    veri = _form_girdi()
    ayar = _ayar_satiri()
    satir.alici_ad_soyad = _metin(veri.get("alici_ad_soyad"), 120)
    satir.alici_telefon = _metin(veri.get("alici_telefon"), 20)
    satir.alici_adres = _metin(veri.get("alici_adres"), 1000)
    if str(veri.get("satis_fiyati") or "").strip():
        satir.satis_fiyati = _pozitif_sayi(veri.get("satis_fiyati"))
        _net_yaz(satir, ayar)
    if satir.durum in ("vitrin", "onaylandi") and satir.alici_ad_soyad and satir.alici_adres:
        satir.durum = "satildi_kargo_bekliyor"
        if satir.satis_zamani is None:
            satir.satis_zamani = simdi_utc()
        satir.kargo_son_tarih = kargo_son_tarih_hesapla(
            satir.satis_zamani, ayar.kargo_suresi_saat
        )
    _db().session.commit()
    return jsonify({"success": True, "urun": _urun_sozluk(satir, yonetici=True)})


@tedarikci_bp.route("/api/tedarikci/admin/urun/<int:urun_id>/odendi", methods=["POST"])
def api_tedarikci_admin_odendi(urun_id):
    red = _admin_reddi()
    if red is not None:
        return red
    satir = _urun_bul(urun_id)
    if satir is None:
        return jsonify({"success": False, "mesaj": "Ürün bulunamadı."}), 404
    satir.hakedis_odendi = True
    satir.hakedis_odeme_zamani = simdi_utc()
    _db().session.commit()
    return jsonify({"success": True, "urun": _urun_sozluk(satir, yonetici=True)})


@tedarikci_bp.route("/api/tedarikci/admin/satici/pin-uret", methods=["POST"])
def api_tedarikci_admin_pin_uret():
    red = _admin_reddi()
    if red is not None:
        return red
    veri = _form_girdi()
    telefon = _telefon_norm(veri.get("telefon"))
    hesap = _hesap_bul(telefon) if telefon else None
    if hesap is None:
        return jsonify({"success": False, "mesaj": "Satıcı hesabı bulunamadı."}), 404
    yeni_pin = _pin_uret_aralik()
    hesap.pin_hash = _pin_hashle(yeni_pin)
    _pin_temizle(telefon)
    _db().session.commit()
    return jsonify({
        "success": True,
        "telefon": hesap.telefon,
        "yeni_pin": yeni_pin,
        "mesaj": "Yeni PIN üretildi. Kopyalayıp satıcıya iletin.",
    })


@tedarikci_bp.route("/api/tedarikci/admin/satici/askiya", methods=["POST"])
def api_tedarikci_admin_askiya():
    red = _admin_reddi()
    if red is not None:
        return red
    veri = _form_girdi()
    telefon = _telefon_norm(veri.get("telefon"))
    hesap = _hesap_bul(telefon) if telefon else None
    if hesap is None:
        return jsonify({"success": False, "mesaj": "Satıcı hesabı bulunamadı."}), 404
    hesap.askida = _evet(veri.get("askida", True))
    _db().session.commit()
    return jsonify({"success": True, "telefon": hesap.telefon, "askida": bool(hesap.askida)})


@tedarikci_bp.route("/api/tedarikci/ai-on-analiz", methods=["POST"])
def api_tedarikci_ai_on_analiz():
    if not _panel_acik():
        return jsonify({
            "success": False,
            "manuel": True,
            "mesaj": "Tedarikçi paneli kapalı. Bilgileri panel açıldığında elle girebilirsiniz.",
        })
    gorseller = _gorselleri_oku(request.files.getlist("fotograflar"))
    if not gorseller:
        return jsonify({
            "success": False,
            "manuel": True,
            "mesaj": "Ön analiz için en az bir fotoğraf gerekli. Fotoğrafı ekleyip yeniden deneyebilir veya alanları elle doldurabilirsiniz.",
        })
    if _ai_sik_mi("on:" + (_istek_ip() or "yerel"), 8):
        return jsonify({
            "success": False,
            "manuel": True,
            "mesaj": "AI ön analizi az önce çalıştı. Birkaç saniye sonra yeniden deneyebilir veya formu elle doldurabilirsiniz.",
        })
    try:
        metin, hata = _ai_metin(_on_analiz_istemi(), gorseller)
        veri = _json_ayikla(metin) if metin else None
    except Exception:
        print("[TEDARIKCI] ai on analiz tamamlanamadi", flush=True)
        return _ai_nazik("servis")
    if not veri:
        return _ai_nazik(hata or "servis")
    paket = _on_analiz_paketi(veri)
    if not paket["onerilen_baslik"] and not paket["secere_ve_aciklama"]:
        return _ai_nazik("servis")
    return jsonify({"success": True, "analiz": paket, "mesaj": paket["ekspertiz_notu"]})


@tedarikci_bp.route("/api/tedarikci/admin-ai-denetim/<int:urun_id>", methods=["POST"])
def api_tedarikci_admin_ai_denetim(urun_id):
    red = _admin_reddi()
    if red is not None:
        return red
    satir = _urun_bul(urun_id)
    if satir is None or satir.durum == "silindi":
        return jsonify({"success": False, "manuel": True, "mesaj": "Ürün bulunamadı."}), 404
    if _ai_sik_mi("admin:" + str(urun_id), 5):
        return jsonify({
            "success": False,
            "manuel": True,
            "mesaj": "Bu ürün için laboratuvar az önce çalıştı. Kısa bir süre sonra yeniden deneyebilirsiniz.",
        })
    gorseller = _disk_gorselleri(satir)
    try:
        metin, hata = _ai_metin(_denetim_istemi(satir), gorseller)
        veri = _json_ayikla(metin) if metin else None
    except Exception:
        print("[TEDARIKCI] ai denetim tamamlanamadi", flush=True)
        return _ai_nazik("servis")
    if not veri:
        return _ai_nazik(hata or "servis")
    rapor = _denetim_paketi(veri)
    if not rapor["vitrin_baslik"] and not rapor["vitrin_aciklama"] and not rapor["risk_gerekce"]:
        return _ai_nazik("servis")
    return jsonify({"success": True, "rapor": rapor})


@tedarikci_bp.record_once
def _blueprint_kaydi(state):
    db = state.app.extensions.get("sqlalchemy")
    if db is None:
        raise RuntimeError("SQLAlchemy eklentisi yok")
    _tablolari_tanimla(db)
