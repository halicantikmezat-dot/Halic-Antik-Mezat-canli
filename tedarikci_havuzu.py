# Tedarikçi ayar ve ürün havuzu.
# Canlı yayın, mezat sayacı ve vitrin akışından bağımsızdır.
# Mevcut tablolara kolon eklemez; kendi iki tablosunu tanımlar.

import json
from datetime import datetime, timedelta, timezone

from flask import Blueprint

tedarikci_bp = Blueprint("tedarikci_havuzu", __name__)

VARSAYILAN_AYAR = {
    "panel_aktif": False,
    "menu_basligi": "Tedarikçi Paneli",
    "duyuru_metni": "",
    "komisyon_orani": 20.0,
    "kargo_destek_orani": 10.0,
    "kargo_destek_alt_limit": 500.0,
    "kargo_suresi_saat": 48,
    "cayma_cezasi_orani": 15.0,
    "tc_zorunlu": True,
    "paketleme_kaniti_zorunlu": True,
    "min_fotograf": 2,
    "max_fotograf": 4,
    "yasal_sozlesme_metni": "",
}

TedarikciAyarlari = None
TedarikciUrunleri = None


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


def urun_kaydini_hazirla(girdi, ayar, yukleyen_ip=""):
    """Ürün alanlarını ayar anındaki oranlarla hazırlar. Veritabanına yazmaz."""
    girdi = girdi or {}
    hatalar = []
    satici = _metin(girdi.get("satici_ad_soyad"), 120)
    telefon = "".join(ch for ch in _metin(girdi.get("telefon"), 32) if ch.isdigit())[:20]
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
    if _ayar_degeri(ayar, "paketleme_kaniti_zorunlu") and not paketleme:
        hatalar.append("paketleme_kaniti")

    if not bool(girdi.get("yasal_beyan_onay")):
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
        "yasal_beyan_onay": bool(girdi.get("yasal_beyan_onay")),
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
    global TedarikciAyarlari, TedarikciUrunleri
    if TedarikciAyarlari is not None and TedarikciUrunleri is not None:
        return

    class TedarikciAyarlariModel(db.Model):
        __tablename__ = "tedarikci_ayarlari"
        id = db.Column(db.Integer, primary_key=True)
        panel_aktif = db.Column(db.Boolean, nullable=False, default=False)
        menu_basligi = db.Column(db.String(120), nullable=False, default="Tedarikçi Paneli")
        duyuru_metni = db.Column(db.Text, nullable=False, default="")
        komisyon_orani = db.Column(db.Float, nullable=False, default=20.0)
        kargo_destek_orani = db.Column(db.Float, nullable=False, default=10.0)
        kargo_destek_alt_limit = db.Column(db.Float, nullable=False, default=500.0)
        kargo_suresi_saat = db.Column(db.Integer, nullable=False, default=48)
        cayma_cezasi_orani = db.Column(db.Float, nullable=False, default=15.0)
        tc_zorunlu = db.Column(db.Boolean, nullable=False, default=True)
        paketleme_kaniti_zorunlu = db.Column(db.Boolean, nullable=False, default=True)
        min_fotograf = db.Column(db.Integer, nullable=False, default=2)
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

    TedarikciAyarlari = TedarikciAyarlariModel
    TedarikciUrunleri = TedarikciUrunleriModel


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


@tedarikci_bp.record_once
def _blueprint_kaydi(state):
    db = state.app.extensions.get("sqlalchemy")
    if db is None:
        raise RuntimeError("SQLAlchemy eklentisi yok")
    _tablolari_tanimla(db)
