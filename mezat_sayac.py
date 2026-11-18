# -*- coding: utf-8 -*-
"""Otonom sayaç kuralı. 180 sn iç saat: 120 pazarlık, ardından 60 karar.

Ekrana giden `kalan` pazarlıkta 120..1, kararda 60..0'dır.
`ham_kalan` worker'ın tuttuğu iç sayaçtır.
"""


def gorunen_kalan(ham, asama):
    try:
        ham = int(ham or 0)
    except (TypeError, ValueError):
        ham = 0
    if ham < 0:
        ham = 0
    if asama == "pazarlik":
        return max(0, ham - 60)
    return ham


def sayac_paketi(dukkan, ham, asama):
    gorunen = gorunen_kalan(ham, asama)
    return {
        "dukkan": dukkan,
        "kalan": gorunen,
        "kalan_sure": gorunen,
        "ham_kalan": int(ham or 0),
        "toplam": 120 if asama == "pazarlik" else 60,
        "sayac_gorunsun": True,
        "asama": asama,
    }


def otonom_tur_simulasyonu(pey_kararda=True):
    """Uyku yok. 180 tik: 120 pazarlık, 60. sn geçiş, 15. sn uyarı, kapanış."""
    olaylar = []
    kalan = 180
    asama = "pazarlik"
    uyari = False
    while kalan > 0:
        if kalan <= 60 and asama == "pazarlik":
            asama = "karar"
            olaylar.append("pazarlik_kapandi")
            olaylar.append("fiyat_acilis")
        if asama == "karar" and kalan == 15 and not uyari:
            uyari = True
            olaylar.append("ses_15")
        paket = sayac_paketi("gumus", kalan, asama)
        olaylar.append(("sayac_guncelle", paket["kalan"], paket["asama"]))
        kalan -= 1
    olaylar.append("sayac_bitti")
    olaylar.append("admin_satis_guncelle" if pey_kararda else "pas")
    return olaylar
