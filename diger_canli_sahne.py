"""Yalnızca Diğer - Her Şey dükkanı için geçici canlı sahne ürünü."""

DUKKAN = "diger"
AD = "Canlı Yayın Ürünü"
KAYNAK = "canli_yayin"


def kaynak_mi(urun):
    if urun is None:
        return False
    return (
        str(getattr(urun, "dukkan_kodu", "") or "") == DUKKAN
        and str(getattr(urun, "kaynak_tipi", "") or "") == KAYNAK
    )


def hazirla(db, Urun):
    """Boş sahnede yeniden kullanılabilir canlı ürünü açar. Diğer dükkanlara yazmaz."""
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
            acilis_fiyati=0.0,
            guncel_fiyat=0.0,
            hemen_al_fiyati=0.0,
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
        urun.acilis_fiyati = 0.0
        urun.guncel_fiyat = 0.0
        urun.hemen_al_fiyati = 0.0
        urun.kazanan_adi = "Yok"
        urun.kazanan_id = None
        urun.stok_adedi = 1
        urun.kaynak_tipi = KAYNAK
    db.session.add(urun)
    db.session.commit()
    return urun
