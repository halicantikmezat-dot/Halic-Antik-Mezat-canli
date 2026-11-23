"""Yalnızca Diğer - Her Şey dükkanı için geçici canlı sahne ürünü."""

import math

DUKKAN = "diger"
AD = "Canlı Yayın Ürünü"
KAYNAK = "canli_yayin"
VARSAYILAN_ACILIS = 10.0


def kaynak_mi(urun):
    if urun is None:
        return False
    return (
        str(getattr(urun, "dukkan_kodu", "") or "") == DUKKAN
        and str(getattr(urun, "kaynak_tipi", "") or "") == KAYNAK
    )


def _tl(deger):
    if deger is None or deger == "":
        return None
    try:
        sayi = float(str(deger).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    if sayi != sayi or sayi <= 0:
        return None
    return float(int(math.floor(sayi + 0.5)))


def coz_fiyatlar(acilis, hemen_al):
    """Açılış boş veya 10 TL altındaysa 10 TL. Hemen al boşsa kapalı (0)."""
    acilis_sayi = _tl(acilis)
    if acilis_sayi is None or acilis_sayi < VARSAYILAN_ACILIS:
        acilis_sayi = VARSAYILAN_ACILIS
    hemen_sayi = _tl(hemen_al)
    if hemen_sayi is None or hemen_sayi < 1:
        hemen_sayi = 0.0
    return acilis_sayi, hemen_sayi


def asgari_pey(mevcut):
    """Mevcut fiyatın en az %10 fazlası, yukarı yuvarlanmış."""
    taban = float(mevcut or 0)
    if taban <= 0:
        return 1
    return int(math.ceil(round(taban * 1.10, 6)))


def hazirla(db, Urun, acilis_fiyati=None, hemen_al_fiyati=None):
    """Boş sahnede yeniden kullanılabilir canlı ürünü açar. Diğer dükkanlara yazmaz."""
    if acilis_fiyati is None and hemen_al_fiyati is None:
        acilis, hemen = 0.0, 0.0
    else:
        acilis, hemen = coz_fiyatlar(acilis_fiyati, hemen_al_fiyati)
    urun = (
        Urun.query.filter(Urun.dukkan_kodu == DUKKAN, Urun.kaynak_tipi == KAYNAK)
        .order_by(Urun.id.desc())
        .first()
    )
    if urun is None:
        urun = Urun(
            dukkan_kodu=DUKKAN,
            lot_no=1,
            urun_adi=AD,
            kategori="Canlı Yayın",
            acilis_fiyati=acilis,
            guncel_fiyat=acilis,
            hemen_al_fiyati=hemen,
            tanitim_yazisi="Canlı yayın sahnesindeki eser.",
            fotograflar=[],
            durum="Aktif",
            kazanan_adi="Yok",
            kazanan_id=None,
            kaynak_tipi=KAYNAK,
            stok_adedi=1,
            silindi_mi=False,
        )
    else:
        urun.lot_no = 1
        urun.urun_adi = AD
        urun.durum = "Aktif"
        urun.silindi_mi = False
        urun.acilis_fiyati = acilis
        urun.guncel_fiyat = acilis
        urun.hemen_al_fiyati = hemen
        urun.kazanan_adi = "Yok"
        urun.kazanan_id = None
        urun.stok_adedi = 1
        urun.kaynak_tipi = KAYNAK
    db.session.add(urun)
    db.session.commit()
    return urun
