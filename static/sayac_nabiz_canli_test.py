# -*- coding: utf-8 -*-
"""130 sn duvar saati. Oda belgesindeki nabız donuk kalır; tik ayrı anahtarı yeniler.

Eski kontrol 10. saniyede turu keserdi. Yeni kontrol aynı anahtarı okur ve kesmez.
120. saniyede pazarlık bir kez karara döner.
"""
import sys
import threading
import time
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK))

from mezat_sayac import (  # noqa: E402
    NABIZ_ESIK_SANIYE,
    SAYAC_KILIT_SANIYE,
    gorunen_kalan,
    kilit_taze_mi,
    nabiz_anahtari,
    nabiz_paketle,
    nabiz_taze_mi,
    sayac_kesilsin_mi,
    sayac_paketi,
    yeni_tur_acilsin_mi,
)


def _eski_keser_mi(oda_nabiz, simdi, beklenen, sayac_aktif):
    """Canlıdaki eski kural: oda belgesindeki sayac_nabiz 8 saniyeden yaşlıysa kes."""
    return bool(
        sayac_aktif
        and oda_nabiz > 0
        and beklenen > NABIZ_ESIK_SANIYE
        and simdi - oda_nabiz > NABIZ_ESIK_SANIYE
    )


def _hizli_kontroller():
    simdi = 1_000_000.0
    taze = nabiz_paketle(7, simdi)
    eski = nabiz_paketle(7, simdi - 9)
    kontroller = [
        ("anahtar", nabiz_anahtari("gumus") == "mezat:sayac:nabiz:gumus"),
        ("taze_nabiz", nabiz_taze_mi(taze, simdi)),
        ("eski_nabiz", not nabiz_taze_mi(eski, simdi)),
        ("kilit_yeni", kilit_taze_mi(SAYAC_KILIT_SANIYE)),
        ("kilit_8sn", not kilit_taze_mi(SAYAC_KILIT_SANIYE - NABIZ_ESIK_SANIYE)),
        (
            "saglikli_kesilmez",
            not sayac_kesilsin_mi(True, True, False, 10),
        ),
        (
            "kilit_tek_basina_korur",
            not sayac_kesilsin_mi(True, False, True, 10),
        ),
        (
            "olu_sayac_kesilir",
            sayac_kesilsin_mi(True, False, False, 9),
        ),
        (
            "erken_kesilmez",
            not sayac_kesilsin_mi(True, False, False, 8),
        ),
        ("aktif_tur_yeniden_acilmaz", not yeni_tur_acilsin_mi(True, False, False)),
        ("canli_kilit_ikinci_yayini_keser", not yeni_tur_acilsin_mi(False, False, True)),
        ("canli_nabiz_ikinci_yayini_keser", not yeni_tur_acilsin_mi(False, True, False)),
        ("biten_tur_acilir", yeni_tur_acilsin_mi(False, False, False)),
        ("gorunen_120", gorunen_kalan(180, "pazarlik") == 120),
        ("gorunen_karar_60", gorunen_kalan(60, "karar") == 60),
    ]
    return kontroller


def _duvar_testi(saniye=132):
    kilit = threading.Lock()
    bas = time.time()
    durum = {
        "kanonik": nabiz_paketle(1, bas),
        "kilit_an": bas,
        "oda_nabiz": bas,
        "sayac_aktif": True,
        "kalan": 180,
        "asama": "pazarlik",
        "gecis": 0,
        "uyari": 0,
        "reset": 0,
        "eski_reset": 0,
        "ornekler": [],
        "bitti": False,
    }

    def tik():
        while True:
            with kilit:
                if durum["bitti"] or not durum["sayac_aktif"] or durum["kalan"] <= 0:
                    return
                simdi = time.time()
                if time.time() - bas >= saniye and durum["asama"] == "karar":
                    durum["ornekler"].append(
                        (round(simdi - bas, 1), durum["asama"], gorunen_kalan(durum["kalan"], durum["asama"]))
                    )
                    return
                durum["kanonik"] = nabiz_paketle(1, simdi)
                durum["kilit_an"] = simdi
                if durum["kalan"] <= 60 and durum["asama"] == "pazarlik":
                    durum["asama"] = "karar"
                    durum["gecis"] += 1
                if durum["asama"] == "karar" and durum["kalan"] == 15:
                    durum["uyari"] += 1
                paket = sayac_paketi("gumus", durum["kalan"], durum["asama"])
                gecen = round(simdi - bas, 1)
                durum["ornekler"].append((gecen, paket["asama"], paket["kalan"]))
                if int(gecen) % 10 == 0:
                    print(
                        f"[tik] {gecen:.0f}s {paket['asama']} gorunen={paket['kalan']}",
                        flush=True,
                    )
                durum["kalan"] -= 1
            time.sleep(1)

    def kontrol():
        beklenen = 0
        while True:
            time.sleep(0.5)
            with kilit:
                if durum["bitti"]:
                    return
                simdi = time.time()
                if not durum["sayac_aktif"]:
                    return
                ttl = SAYAC_KILIT_SANIYE - (simdi - durum["kilit_an"])
                nabiz_taze = nabiz_taze_mi(durum["kanonik"], simdi)
                kilit_taze = kilit_taze_mi(ttl)
                if sayac_kesilsin_mi(True, nabiz_taze, kilit_taze, beklenen):
                    durum["reset"] += 1
                    durum["sayac_aktif"] = False
                    print(f"[KONTROL] yeni kural turu kesti t={simdi - bas:.1f}", flush=True)
                    return
                if _eski_keser_mi(durum["oda_nabiz"], simdi, beklenen, True):
                    if durum["eski_reset"] == 0:
                        print(
                            f"[KONTROL] eski oda nabzi turu keserdi t={simdi - bas:.1f}",
                            flush=True,
                        )
                    durum["eski_reset"] += 1
                beklenen += 1
                if simdi - bas >= saniye and durum["asama"] == "karar":
                    return

    tik_is = threading.Thread(target=tik, name="sayac-tik")
    kontrol_is = threading.Thread(target=kontrol, name="sayac-kontrol")
    tik_is.start()
    kontrol_is.start()
    tik_is.join()
    with kilit:
        durum["bitti"] = True
    kontrol_is.join()
    return durum


def main():
    hatalar = []
    for ad, tamam in _hizli_kontroller():
        if not tamam:
            hatalar.append(ad)
            print(f"FAIL {ad}", flush=True)
        else:
            print(f"ok {ad}", flush=True)
    if hatalar:
        print("hizli kontroller kirildi, duvar testi baslamadi", flush=True)
        return 1

    print(f"duvar testi basliyor, en az {130} sn", flush=True)
    durum = _duvar_testi(132)
    ornekler = durum["ornekler"]
    pazarlik = [o for o in ornekler if o[1] == "pazarlik"]
    karar = [o for o in ornekler if o[1] == "karar"]
    ziplama = False
    onceki = None
    karar_goruldu = False
    for _t, asama, gorunen in ornekler:
        if asama == "karar":
            karar_goruldu = True
        if asama == "pazarlik" and karar_goruldu:
            ziplama = True
        if onceki is not None and asama == onceki[1] and gorunen > onceki[2]:
            ziplama = True
        onceki = (_t, asama, gorunen)

    onuncu = [o for o in ornekler if o[0] >= 9]
    kontroller = [
        ("reset_yok", durum["reset"] == 0),
        ("eski_kural_10sn_de_keserdi", durum["eski_reset"] >= 1),
        ("onuncu_saniye_pazarlik", bool(onuncu) and onuncu[0][1] == "pazarlik" and onuncu[0][2] < 120),
        ("pazarlik_120_ile_baslar", bool(pazarlik) and pazarlik[0][2] == 120),
        ("pazarlik_geri_sayar", len(pazarlik) >= 110),
        ("gecis_bir_kez", durum["gecis"] == 1),
        ("karar_basladi", bool(karar) and karar[0][2] == 60),
        ("karar_60_ustu_yok", all(o[2] <= 60 for o in karar)),
        ("basa_sarma_yok", not ziplama),
        ("sure_130", ornekler[-1][0] >= 130 if ornekler else False),
    ]
    for ad, tamam in kontroller:
        print(("ok" if tamam else "FAIL") + " " + ad, flush=True)
        if not tamam:
            hatalar.append(ad)
    if ornekler:
        print(
            f"son ornek t={ornekler[-1][0]} {ornekler[-1][1]} gorunen={ornekler[-1][2]} "
            f"gecis={durum['gecis']} reset={durum['reset']}",
            flush=True,
        )
    return 1 if hatalar else 0


if __name__ == "__main__":
    sys.exit(main())
