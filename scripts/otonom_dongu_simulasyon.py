# -*- coding: utf-8 -*-
"""180 sn otonom turun uykusuz provasi. Canli sahneye ve veritabanina dokunmaz."""
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK))

from mezat_sayac import otonom_tur_simulasyonu, sayac_paketi  # noqa: E402


def _sayaclar(olaylar):
    return [olay for olay in olaylar if isinstance(olay, tuple) and olay[0] == "sayac_guncelle"]


def main():
    olaylar = otonom_tur_simulasyonu(pey_kararda=True)
    sayac = _sayaclar(olaylar)
    pazarlik = [s for s in sayac if s[2] == "pazarlik"]
    karar = [s for s in sayac if s[2] == "karar"]
    kontroller = [
        ("pazarlik_120_tik", len(pazarlik) == 120),
        ("karar_60_tik", len(karar) == 60),
        ("pazarlik_2_00_ile_baslar", pazarlik[0][1] == 120),
        ("pazarlik_0_01_ile_biter", pazarlik[-1][1] == 1),
        ("karar_60_ile_baslar", karar[0][1] == 60),
        ("karar_1_ile_biter", karar[-1][1] == 1),
        ("gecis_fiyat_acilisa", olaylar.index("pazarlik_kapandi") < olaylar.index(karar[0])),
        ("gecis_bir_kez", olaylar.count("pazarlik_kapandi") == 1 and olaylar.count("fiyat_acilis") == 1),
        ("on_besinci_saniye_ses", olaylar.count("ses_15") == 1),
        ("ses_karar_15_ile", olaylar.index("ses_15") < olaylar.index(("sayac_guncelle", 15, "karar")) + 1),
        ("kapanis", olaylar[-2] == "sayac_bitti"),
        ("yonetici_satis", olaylar[-1] == "admin_satis_guncelle"),
        ("karar_60_ustu_yok", all(s[1] <= 60 for s in karar)),
    ]
    paket = sayac_paketi("gumus", 180, "pazarlik")
    kontroller.append(("paket_kalan_120", paket["kalan"] == 120 and paket["ham_kalan"] == 180))
    paket60 = sayac_paketi("gumus", 15, "karar")
    kontroller.append(("paket_karar_15", paket60["kalan"] == 15 and paket60["asama"] == "karar"))

    print("OTONOM 180 SN SIMULASYON")
    gecen = 0
    for ad, tamam in kontroller:
        print("  %s: %s" % (ad, "GECTI" if tamam else "KALDI"))
        gecen += int(bool(tamam))
    print("KARNE %s/%s" % (gecen, len(kontroller)))
    return 0 if gecen == len(kontroller) else 1


if __name__ == "__main__":
    raise SystemExit(main())
