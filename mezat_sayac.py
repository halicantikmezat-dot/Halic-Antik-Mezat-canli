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


def gecis_bir_kez(kalan, gecildi):
    """60'a inince geçiş yalnız bir kez. Redis yeniden pazarlik dese de tekrarlanmaz."""
    try:
        kalan = int(kalan or 0)
    except (TypeError, ValueError):
        kalan = 0
    return (not gecildi) and kalan <= 60


def uzatma_kabul(yerel, redis_kalan, asama):
    """Donuk Redis saati geri sarmaz. Kararda son 10 sn peyi süreyi 15'e uzatır."""
    try:
        yerel = int(yerel or 0)
    except (TypeError, ValueError):
        yerel = 0
    try:
        redis_kalan = int(redis_kalan or 0)
    except (TypeError, ValueError):
        return yerel
    if asama == "karar" and yerel <= 10 and yerel < redis_kalan <= 15:
        return redis_kalan
    return yerel


def karar_yayin_paketi(dukkan, kalan):
    """Manuel ve otomatik karar tiki. Ekran 60'tan 0'a bu paketi görür."""
    try:
        kalan = int(kalan or 0)
    except (TypeError, ValueError):
        kalan = 0
    if kalan < 0:
        kalan = 0
    paket = sayac_paketi(dukkan, kalan, "karar")
    paket["asama"] = "karar"
    paket["kalan"] = kalan
    paket["kalan_sure"] = kalan
    paket["sayac_gorunsun"] = True
    paket["sayac_aktif"] = True
    return paket


def takilma_simulasyonu():
    """Redis asaması pazarlik ve kalan 60'ta donuk kalsa bile geçiş bir, kapanış 0."""
    gorunen = []
    gecis = 0
    gecildi = False
    kalan = 180
    while kalan > 0:
        if gecis_bir_kez(kalan, gecildi):
            gecis += 1
            gecildi = True
        asama = "karar" if gecildi else "pazarlik"
        if asama == "karar":
            kalan = uzatma_kabul(kalan, 60, asama)
        paket = sayac_paketi("gumus", kalan, asama)
        gorunen.append((paket["asama"], paket["kalan"], paket["sayac_gorunsun"]))
        kalan -= 1
    kapanis = karar_yayin_paketi("gumus", 0)
    manuel = []
    saniye = 60
    while saniye >= 0:
        saniye = uzatma_kabul(saniye, 60, "karar")
        paket = karar_yayin_paketi("gumus", saniye)
        manuel.append(paket)
        if saniye == 0:
            break
        saniye -= 1
    return {"gecis": gecis, "gorunen": gorunen, "kapanis": kapanis, "manuel": manuel}


# Tik ile devralma aynı eşiği kullanır. 8 saniye, sağlıklı turu yaklaşık 10. saniyede
# başa sarıyordu. Nabız 15 saniyeden uzun kesilmedikçe tur devralınmaz.
NABIZ_ESIK_SANIYE = 15
SAYAC_KILIT_SANIYE = 40


def nabiz_anahtari(dukkan):
    return "mezat:sayac:nabiz:" + str(dukkan)


def tanitim_anahtari(dukkan, gorev_id):
    return "mezat:sayac:tanitim:" + str(dukkan) + ":" + str(int(gorev_id))


def nabiz_paketle(gorev_id, an):
    return str(int(gorev_id)) + ":" + format(float(an), ".3f")


def nabiz_coz(ham):
    """(gorev, zaman) döner. Bozuk kayıtta görev yok, zaman 0."""
    if ham is None:
        return None, 0.0
    if isinstance(ham, bytes):
        ham = ham.decode("utf-8", "ignore")
    metin = str(ham).strip()
    if not metin:
        return None, 0.0
    gorev_yazi, ayir, an_yazi = metin.rpartition(":")
    if not ayir:
        try:
            return None, float(metin)
        except (TypeError, ValueError):
            return None, 0.0
    try:
        return int(gorev_yazi), float(an_yazi)
    except (TypeError, ValueError):
        return None, 0.0


def nabiz_taze_mi(ham, simdi, esik=NABIZ_ESIK_SANIYE):
    _gorev, an = nabiz_coz(ham)
    if an <= 0:
        return False
    return (float(simdi) - an) <= float(esik)


def kilit_taze_mi(ttl, omur=SAYAC_KILIT_SANIYE, esik=NABIZ_ESIK_SANIYE):
    """Kilit, tikin her turda yenilediği mezat:sayac anahtarının kalan süresidir."""
    if ttl is None:
        return False
    try:
        kalan = float(ttl)
    except (TypeError, ValueError):
        return False
    if kalan < 0:
        return False
    return kalan > max(0.0, float(omur) - float(esik))


def sayac_kesilsin_mi(sayac_aktif, nabiz_taze, kilit_taze, beklenen, esik=NABIZ_ESIK_SANIYE):
    """Sağlıklı sayaç kesilmez. Nabız ya da kilit tazeyse tur başa sarmaz."""
    if not sayac_aktif:
        return False
    if nabiz_taze or kilit_taze:
        return False
    try:
        gecen = int(beklenen or 0)
    except (TypeError, ValueError):
        gecen = 0
    return gecen > int(esik)


def yeni_tur_acilsin_mi(sayac_aktif, nabiz_taze, kilit_taze):
    """Sayaç bayrağı düşünce bile nabız veya kilit duruyorsa ikinci yayın açılmaz."""
    if sayac_aktif:
        return False
    if nabiz_taze or kilit_taze:
        return False
    return True


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
