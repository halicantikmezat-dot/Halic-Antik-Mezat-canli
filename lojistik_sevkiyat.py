# -*- coding: utf-8 -*-
"""Faz 2 lojistik: raf sayacı, gizli koli etiketi ve kurye icmali.

Kumbara formülüne, XML kurallarına ve veritabanı modellerine dokunmaz.
Etiket metninde ürün bedeli veya hırsızlık riski doğuran ibare üretilmez.
"""
import hashlib
import html
import re
from datetime import datetime, timedelta, timezone

UCRETSIZ_SANIYE = 168 * 3600
YESIL_SANIYE = 120 * 3600
TR_SAAT = timezone(timedelta(hours=3))
GONDERICI = "Haliç Lojistik / Depo"
KIRILACAK_IBARE = "Alıcı Ödemeli - Kırılacak Eşya"

# Code 128. Çubuk ve boşluk genişlikleri. Son kalıp dur işaretidir.
_CODE128 = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213",
    "122312", "132212", "221213", "221312", "231212", "112232", "122132",
    "122231", "113222", "123122", "123221", "223211", "221132", "221231",
    "213212", "223112", "312131", "311222", "321122", "321221", "312212",
    "322112", "322211", "212123", "212321", "232121", "111323", "131123",
    "131321", "112313", "132113", "132311", "211313", "231113", "231311",
    "112133", "112331", "132131", "113123", "113321", "133121", "313121",
    "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114",
    "413111", "241112", "134111", "111242", "121142", "121241", "114212",
    "124112", "124211", "411212", "421112", "421211", "212141", "214121",
    "412121", "111143", "111341", "131141", "114113", "114311", "411113",
    "411311", "113141", "114131", "311141", "411131", "211412", "211214",
    "211232", "2331112",
)
_YASAK_IBARELER = ("antika", "değerli eşya", "degerli esya", "değerli", "degerli")


def _utc_an(an):
    if an is None or an == "":
        return None
    if isinstance(an, datetime):
        if an.tzinfo is None:
            return an.replace(tzinfo=timezone.utc)
        return an.astimezone(timezone.utc)
    metin = str(an).strip().replace("Z", "+00:00")
    if " " in metin and "T" not in metin:
        metin = metin.replace(" ", "T", 1)
    try:
        cozulen = datetime.fromisoformat(metin)
    except ValueError:
        return None
    if cozulen.tzinfo is None:
        cozulen = cozulen.replace(tzinfo=timezone.utc)
    return cozulen.astimezone(timezone.utc)


def raf_bekleme_durumu(ilk_an, simdi=None):
    """İlk kazanmadan 168 saatlik ücretsiz tek koli penceresi.

    0-5. gün yeşil, 6-7. gün sarı, 8. gün ve sonrası kırmızı ardiye.
    """
    baslangic = _utc_an(ilk_an)
    if baslangic is None:
        return {
            "sinif": "yesil",
            "metin": "Açık Kutu - Kalan: —",
            "ilk_kazanma": "",
        }
    an = _utc_an(simdi) or datetime.now(timezone.utc)
    gecen = (an - baslangic).total_seconds()
    if gecen < 0:
        gecen = 0
    if gecen < YESIL_SANIYE:
        kalan = UCRETSIZ_SANIYE - gecen
        gun = int(kalan // 86400)
        saat = int((kalan % 86400) // 3600)
        metin = "Açık Kutu - Kalan: %s Gün %s Saat" % (gun, saat)
        sinif = "yesil"
    elif gecen < UCRETSIZ_SANIYE:
        metin = "Sevk Zamanı - Son Günler"
        sinif = "sari"
    else:
        ekstra = int((gecen - UCRETSIZ_SANIYE) // 86400) + 1
        metin = "Süre Doldu - Günlük Ardiye Başladı (+%s Gün)" % ekstra
        sinif = "kirmizi"
    return {
        "sinif": sinif,
        "metin": metin,
        "ilk_kazanma": baslangic.isoformat(),
    }


def _ilk_an(anlar):
    cozulen = [_utc_an(an) for an in (anlar or [])]
    dolu = [an for an in cozulen if an is not None]
    if not dolu:
        return None
    return min(dolu)


def koli_takip_kodu(musteri_anahtar, ilk_an):
    """Aynı açık kutu için sabit, ürün bedeli içermeyen takip kodu."""
    baslangic = _utc_an(ilk_an)
    if baslangic is None:
        gun = datetime.now(TR_SAAT).strftime("%y%m%d")
        an_metin = "yok"
    else:
        gun = baslangic.astimezone(TR_SAAT).strftime("%y%m%d")
        an_metin = baslangic.isoformat()
    ham = "%s|%s" % (musteri_anahtar, an_metin)
    ek = hashlib.sha256(ham.encode("utf-8")).hexdigest()[:6].upper()
    return "HL%s%s" % (gun, ek)


def ilce_il_ayikla(adres):
    metin = " ".join(str(adres or "").replace("\n", " ").split())
    if not metin:
        return ""
    parcalar = [parca.strip(" /") for parca in metin.split(",") if parca.strip(" /")]
    if len(parcalar) >= 2:
        return parcalar[-1][:80]
    if "/" in metin:
        sol, sag = metin.rsplit("/", 1)
        sol_parca = sol.strip().split()[-1] if sol.strip() else ""
        return ("%s/%s" % (sol_parca, sag.strip()))[:80]
    return metin[:80]


def lojistik_dosya_ozeti(musteri_id, musteri_adi, anlar, urun_idler, telefon, adres):
    """Müşteri kartına eklenecek sayaç ve koli kimliği. Fiyat taşımaz."""
    baslangic = _ilk_an(anlar)
    anahtar = str(musteri_id or musteri_adi or "misafir").strip() or "misafir"
    kod = koli_takip_kodu(anahtar, baslangic)
    durum = raf_bekleme_durumu(baslangic)
    try:
        adet = len(list(urun_idler or []))
    except TypeError:
        adet = 0
    tel = " ".join(str(telefon or "").split())
    if tel in ("-", "Yok", "yok"):
        tel = ""
    yer = " ".join(str(adres or "").replace("\n", " ").split())
    return {
        "koli_kodu": kod,
        "ilk_kazanma": durum["ilk_kazanma"],
        "adet": adet,
        "telefon": tel[:40],
        "adres": yer[:300],
        "ilce_il": ilce_il_ayikla(yer),
        "rozet": durum["metin"],
        "rozet_sinif": durum["sinif"],
    }


def _tek_satir(deger, sinir):
    metin = " ".join(str(deger or "").replace("\n", " ").split())
    for ibare in _YASAK_IBARELER:
        metin = re.sub(re.escape(ibare), "", metin, flags=re.IGNORECASE)
    return " ".join(metin.split())[:sinir]


def _etiket_girdileri(veri):
    ham = veri if isinstance(veri, dict) else {}
    kod = _tek_satir(ham.get("koli_kodu"), 20).upper()
    if not kod.startswith("HL") or not kod.isalnum() or not (10 <= len(kod) <= 20):
        kod = ""
    try:
        adet = int(ham.get("adet") or 0)
    except (TypeError, ValueError):
        adet = 0
    if adet < 0:
        adet = 0
    if adet > 999:
        adet = 999
    return {
        "koli_kodu": kod,
        "alici": _tek_satir(ham.get("alici"), 80) or "Alıcı",
        "telefon": _tek_satir(ham.get("telefon"), 40),
        "adres": _tek_satir(ham.get("adres"), 300),
        "adet": adet,
    }


def _icmal_satiri(ham, sira):
    veri = ham if isinstance(ham, dict) else {}
    kod = _tek_satir(veri.get("koli_kodu"), 20).upper()
    if not kod.startswith("HL") or not kod.isalnum():
        return None
    try:
        adet = int(veri.get("adet") or 0)
    except (TypeError, ValueError):
        adet = 0
    if adet < 0:
        adet = 0
    if adet > 999:
        adet = 999
    return {
        "sira": sira,
        "koli_kodu": kod,
        "alici": _tek_satir(veri.get("alici"), 80) or "Alıcı",
        "ilce_il": _tek_satir(veri.get("ilce_il") or veri.get("adres"), 80),
        "adet": adet,
        "desi_not": _tek_satir(veri.get("desi_not"), 40),
    }


def _code128_degerler(metin):
    degerler = [104]
    for karakter in str(metin or ""):
        sira = ord(karakter) - 32
        if sira < 0 or sira > 94:
            sira = 0
        degerler.append(sira)
    toplam = degerler[0]
    for konum, deger in enumerate(degerler[1:], start=1):
        toplam += konum * deger
    degerler.append(toplam % 103)
    degerler.append(106)
    return degerler


def code128_svg(metin):
    parcalar = []
    x = 10
    for deger in _code128_degerler(metin):
        kalip = _CODE128[deger]
        cubuk = True
        for genislik in kalip:
            adim = int(genislik)
            if cubuk:
                parcalar.append(
                    '<rect x="%s" y="0" width="%s" height="40" fill="#000"/>' % (x, adim)
                )
            x += adim
            cubuk = not cubuk
    x += 10
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s 40" '
        'preserveAspectRatio="none" shape-rendering="crispEdges">%s</svg>'
    ) % (x, "".join(parcalar))


def _gf_tablolari():
    exp = [0] * 512
    log = [0] * 256
    deger = 1
    for i in range(255):
        exp[i] = deger
        log[deger] = i
        deger <<= 1
        if deger & 0x100:
            deger ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_GF_EXP, _GF_LOG = _gf_tablolari()


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_kalan(veri, ecc_adet):
    uretec = [1]
    for i in range(ecc_adet):
        yeni = [0] * (len(uretec) + 1)
        for j, katsayi in enumerate(uretec):
            yeni[j] ^= katsayi
            yeni[j + 1] ^= _gf_mul(katsayi, _GF_EXP[i])
        uretec = yeni
    sonuc = list(veri) + [0] * ecc_adet
    for i in range(len(veri)):
        katsayi = sonuc[i]
        if katsayi == 0:
            continue
        for j, uretec_katsayi in enumerate(uretec):
            sonuc[i + j] ^= _gf_mul(uretec_katsayi, katsayi)
    return sonuc[-ecc_adet:]


_QR_SURUM = {
    1: {"boyut": 21, "veri": 16, "ecc": 10, "kalan": 0, "hizalama": []},
    2: {"boyut": 25, "veri": 28, "ecc": 16, "kalan": 7, "hizalama": [6, 18]},
    3: {"boyut": 29, "veri": 44, "ecc": 26, "kalan": 7, "hizalama": [6, 22]},
}


def _qr_surum_sec(bayt_sayisi):
    for surum in (1, 2, 3):
        kapasite = _QR_SURUM[surum]["veri"] - 2
        if bayt_sayisi <= kapasite:
            return surum
    return 3


def _qr_kodkelimeleri(metin):
    ham = str(metin or "").encode("utf-8")[:42]
    surum = _qr_surum_sec(len(ham))
    bilgi = _QR_SURUM[surum]
    bitler = [0, 1, 0, 0]
    uzunluk = len(ham)
    for kaydir in range(7, -1, -1):
        bitler.append((uzunluk >> kaydir) & 1)
    for bayt in ham:
        for kaydir in range(7, -1, -1):
            bitler.append((bayt >> kaydir) & 1)
    kapasite = bilgi["veri"] * 8
    for _ in range(min(4, kapasite - len(bitler))):
        bitler.append(0)
    while len(bitler) % 8:
        bitler.append(0)
    kelimeler = []
    for i in range(0, len(bitler), 8):
        deger = 0
        for bit in bitler[i:i + 8]:
            deger = (deger << 1) | bit
        kelimeler.append(deger)
    dolgu = 0
    while len(kelimeler) < bilgi["veri"]:
        kelimeler.append(0xEC if dolgu % 2 == 0 else 0x11)
        dolgu += 1
    ecc = _rs_kalan(kelimeler, bilgi["ecc"])
    return surum, kelimeler + ecc


def _qr_format_bitleri(maske):
    veri = maske & 7
    kalan = veri << 10
    for i in range(4, -1, -1):
        if kalan & (1 << (i + 10)):
            kalan ^= 0x537 << i
    return ((veri << 10) | (kalan & 0x3FF)) ^ 0x5412


def _qr_islev_ciz(surum):
    bilgi = _QR_SURUM[surum]
    n = bilgi["boyut"]
    modul = [[False] * n for _ in range(n)]
    rezerve = [[False] * n for _ in range(n)]

    def boya(x, y, dolu):
        if 0 <= x < n and 0 <= y < n:
            modul[y][x] = bool(dolu)
            rezerve[y][x] = True

    def bulucu(ox, oy):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                x = ox + dx
                y = oy + dy
                if not (0 <= x < n and 0 <= y < n):
                    continue
                if 0 <= dx <= 6 and 0 <= dy <= 6:
                    kenar = dx in (0, 6) or dy in (0, 6)
                    orta = 2 <= dx <= 4 and 2 <= dy <= 4
                    boya(x, y, kenar or orta)
                else:
                    boya(x, y, False)

    bulucu(0, 0)
    bulucu(n - 7, 0)
    bulucu(0, n - 7)
    for i in range(n):
        if not rezerve[6][i]:
            boya(i, 6, i % 2 == 0)
        if not rezerve[i][6]:
            boya(6, i, i % 2 == 0)
    for merkez in bilgi["hizalama"]:
        for cy in bilgi["hizalama"]:
            if rezerve[cy][merkez]:
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    kenar = max(abs(dx), abs(dy)) == 2
                    boya(merkez + dx, cy + dy, kenar or (dx == 0 and dy == 0))
    boya(8, 4 * surum + 9, True)
    for i in range(9):
        rezerve[8][i] = True
        rezerve[i][8] = True
    rezerve[8][8] = True
    for i in range(8):
        rezerve[8][n - 1 - i] = True
        rezerve[n - 1 - i][8] = True
    return modul, rezerve


def _qr_veri_yerlestir(modul, rezerve, kodkelimeleri, kalan_bit):
    bitler = []
    for kelime in kodkelimeleri:
        for kaydir in range(7, -1, -1):
            bitler.append((kelime >> kaydir) & 1)
    bitler.extend([0] * kalan_bit)
    n = len(modul)
    konum = 0
    yukari = True
    sag = n - 1
    while sag > 0:
        if sag == 6:
            sag -= 1
        for adim in range(n):
            y = (n - 1 - adim) if yukari else adim
            for k in range(2):
                x = sag - k
                if rezerve[y][x]:
                    continue
                modul[y][x] = bool(bitler[konum]) if konum < len(bitler) else False
                konum += 1
        yukari = not yukari
        sag -= 2
    return konum == len(bitler)


def _qr_maske_uygula(modul, rezerve, maske):
    n = len(modul)
    for y in range(n):
        for x in range(n):
            if rezerve[y][x]:
                continue
            if maske == 0 and (x + y) % 2 == 0:
                modul[y][x] = not modul[y][x]


def _qr_format_yaz(modul, maske):
    """Biçim bitleri. Sütun x, satır y. Bit 0 en düşük basamaktır."""
    bitler = _qr_format_bitleri(maske)
    n = len(modul)

    def bit(i):
        return bool((bitler >> i) & 1)

    for i in range(6):
        modul[i][8] = bit(i)
    modul[7][8] = bit(6)
    modul[8][8] = bit(7)
    modul[8][7] = bit(8)
    for i in range(9, 15):
        modul[8][14 - i] = bit(i)
    for i in range(8):
        modul[8][n - 1 - i] = bit(i)
    for i in range(8, 15):
        modul[n - 15 + i][8] = bit(i)


def qr_matris(metin):
    surum, kelimeler = _qr_kodkelimeleri(metin)
    modul, rezerve = _qr_islev_ciz(surum)
    if not _qr_veri_yerlestir(modul, rezerve, kelimeler, _QR_SURUM[surum]["kalan"]):
        raise RuntimeError("QR veri yerlesmedi")
    _qr_maske_uygula(modul, rezerve, 0)
    _qr_format_yaz(modul, 0)
    return modul


def qr_svg(metin):
    matris = qr_matris(metin)
    n = len(matris)
    tam = n + 8
    parcalar = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" shape-rendering="crispEdges">' % (tam, tam),
        '<rect width="%s" height="%s" fill="#fff"/>' % (tam, tam),
    ]
    for y, satir in enumerate(matris):
        for x, dolu in enumerate(satir):
            if dolu:
                parcalar.append(
                    '<rect x="%s" y="%s" width="1" height="1" fill="#000"/>' % (x + 4, y + 4)
                )
    parcalar.append("</svg>")
    return "".join(parcalar)


def _sayfa_kabugu(baslik, govde, sayfa_olcusu):
    return """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>%s</title>
<style>
  @page { size: %s; margin: 8mm; }
  html, body { margin: 0; padding: 0; background: #fff; color: #000; }
  body { font-family: Arial, Helvetica, sans-serif; }
  h1 { font-size: 16px; margin: 0 0 8px; }
  table { width: 100%%; border-collapse: collapse; }
  th, td { border: 1px solid #000; padding: 6px; font-size: 12px; text-align: left; vertical-align: top; }
  th { background: #f2f2f2; }
  .imza { margin-top: 18px; display: flex; gap: 16px; }
  .kutu { flex: 1; border: 1px solid #000; min-height: 120px; padding: 8px; }
  .kutu b { display: block; margin-bottom: 8px; }
  .cizgi { margin-top: 28px; border-bottom: 1px solid #000; height: 18px; }
</style>
</head>
<body>
%s
<script>window.addEventListener("load", function () { setTimeout(function () { window.print(); }, 250); });</script>
</body>
</html>""" % (html.escape(baslik), sayfa_olcusu, govde)


def etiket_html(veri):
    alan = _etiket_girdileri(veri)
    if not alan["koli_kodu"]:
        govde = "<p>Koli takip kodu oluşmadı. Kartı yenileyip tekrar deneyin.</p>"
        return _sayfa_kabugu("Koli Etiketi", govde, "100mm 100mm")
    kod = html.escape(alan["koli_kodu"])
    govde = """
<style>
  .etiket { width: 92mm; min-height: 92mm; border: 1.5px solid #000; padding: 3mm; box-sizing: border-box; }
  .gonderici { font-size: 12pt; font-weight: 700; }
  .orta { display: flex; gap: 3mm; align-items: center; margin-top: 2mm; }
  .barkod { flex: 1; }
  .barkod svg { width: 58mm; height: 16mm; }
  .qr svg { width: 28mm; height: 28mm; }
  .kod { font-family: Consolas, monospace; font-size: 12pt; font-weight: 700; letter-spacing: 0.5px; margin-top: 1mm; }
  .alici { font-size: 13pt; font-weight: 700; margin-top: 2mm; }
  .satir { font-size: 10pt; margin-top: 1mm; line-height: 1.3; }
  .uyari { margin-top: 2mm; border: 1px solid #000; text-align: center; font-weight: 700; font-size: 11pt; padding: 1.5mm; }
</style>
<div class="etiket">
  <div class="gonderici">%s</div>
  <div class="orta">
    <div class="barkod">%s<div class="kod">%s</div></div>
    <div class="qr">%s</div>
  </div>
  <div class="alici">%s</div>
  <div class="satir">Tel: %s</div>
  <div class="satir">%s</div>
  <div class="satir">Koli içerik adedi: %s</div>
  <div class="uyari">%s</div>
</div>""" % (
        html.escape(GONDERICI),
        code128_svg(alan["koli_kodu"]),
        kod,
        qr_svg(alan["koli_kodu"]),
        html.escape(alan["alici"]),
        html.escape(alan["telefon"] or "-"),
        html.escape(alan["adres"] or "-"),
        alan["adet"],
        html.escape(KIRILACAK_IBARE),
    )
    sayfa = _sayfa_kabugu("Koli Etiketi", govde, "100mm 100mm")
    _etiket_guvenlik_kontrol(sayfa)
    return sayfa


def _etiket_guvenlik_kontrol(sayfa):
    kucuk = sayfa.casefold()
    for ibare in _YASAK_IBARELER:
        if ibare in kucuk:
            raise RuntimeError("Etiket metni güvenli değil")
    if " tl" in kucuk or "fiyat" in kucuk or "lot #" in kucuk:
        raise RuntimeError("Etiket metni güvenli değil")


def icmal_html(koliler):
    satirlar = []
    for ham in koliler or []:
        satir = _icmal_satiri(ham, len(satirlar) + 1)
        if satir:
            satirlar.append(satir)
    bugun = datetime.now(TR_SAAT).strftime("%d.%m.%Y")
    govde_satir = []
    for satir in satirlar:
        govde_satir.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                satir["sira"],
                html.escape(satir["koli_kodu"]),
                html.escape(satir["alici"]),
                html.escape(satir["ilce_il"]),
                satir["adet"],
                html.escape(satir["desi_not"]),
            )
        )
    if not govde_satir:
        govde_satir.append(
            '<tr><td colspan="6">Bugün sevkiyata hazır işaretlenen koli yok.</td></tr>'
        )
    govde = """
<h1>Günlük Teslim İcmal Listesi — %s</h1>
<table>
  <thead>
    <tr>
      <th>Sıra No</th><th>Koli Barkod Kodu</th><th>Alıcı Adı</th>
      <th>İlçe/İl</th><th>Adet</th><th>Desi/Not</th>
    </tr>
  </thead>
  <tbody>%s</tbody>
</table>
<div class="imza">
  <div class="kutu">
    <b>Teslim Eden (Depo Sorumlusu)</b>
    <div>Ad Soyad:</div><div class="cizgi"></div>
    <div>İmza:</div><div class="cizgi"></div>
  </div>
  <div class="kutu">
    <b>Teslim Alan (Kargo Kuryesi - İsim, İmza, Kaşe ve Okutulan Koli Adedi)</b>
    <div>Ad Soyad:</div><div class="cizgi"></div>
    <div>İmza / Kaşe:</div><div class="cizgi"></div>
    <div>Okutulan koli adedi:</div><div class="cizgi"></div>
  </div>
</div>""" % (html.escape(bugun), "".join(govde_satir))
    return _sayfa_kabugu("Günlük Kurye İcmali", govde, "A4 portrait")


def _kendini_dene():
    assert len(_CODE128) == 107
    for kalip in _CODE128[:-1]:
        assert sum(int(parca) for parca in kalip) == 11
    assert sum(int(parca) for parca in _CODE128[-1]) == 13
    bas = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)
    yesil = raf_bekleme_durumu(bas, bas + timedelta(hours=10))
    assert yesil["sinif"] == "yesil" and yesil["metin"].startswith("Açık Kutu - Kalan:")
    sari = raf_bekleme_durumu(bas, bas + timedelta(hours=130))
    assert sari["sinif"] == "sari" and sari["metin"] == "Sevk Zamanı - Son Günler"
    kirmizi = raf_bekleme_durumu(bas, bas + timedelta(hours=168))
    assert kirmizi["sinif"] == "kirmizi" and "(+1 Gün)" in kirmizi["metin"]
    ikinci = raf_bekleme_durumu(bas, bas + timedelta(hours=192))
    assert "(+2 Gün)" in ikinci["metin"]
    kod = koli_takip_kodu("42", bas)
    assert kod.startswith("HL") and kod.isalnum()
    svg = code128_svg(kod)
    assert "<rect" in svg
    matris = qr_matris(kod)
    assert len(matris) in (21, 25, 29) and matris[0][0] is True
    sayfa = etiket_html({
        "koli_kodu": kod,
        "alici": "Ayşe Yılmaz",
        "telefon": "5551112233",
        "adres": "Moda Cad. No:1, Kadıköy/İstanbul",
        "adet": 3,
        "fiyat": "15000",
        "urun_adi": "gizli urun",
    })
    assert "15000" not in sayfa and "gizli urun" not in sayfa
    assert GONDERICI in sayfa and KIRILACAK_IBARE in sayfa and kod in sayfa
    icmal = icmal_html([{
        "koli_kodu": kod,
        "alici": "Ayşe Yılmaz",
        "ilce_il": "Kadıköy/İstanbul",
        "adet": 3,
        "desi_not": "2 desi",
    }])
    assert "Teslim Eden (Depo Sorumlusu)" in icmal
    assert "Okutulan Koli Adedi" in icmal
    assert "Ayşe Yılmaz" in icmal


if __name__ == "__main__":
    _kendini_dene()
    print("lojistik_sevkiyat tamam")
