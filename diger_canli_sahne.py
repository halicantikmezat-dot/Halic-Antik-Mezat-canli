"""Yalnızca Diğer - Her Şey dükkanı için geçici canlı sahne ürünü."""

import math
import threading
import time

DUKKAN = "diger"
AD = "Canlı Yayın Ürünü"
KAYNAK = "canli_yayin"
VARSAYILAN_ACILIS = 10.0
VARSAYILAN_TANITIM = "Canlı yayın sahnesindeki eser."
_TARAMA = {"urun_adi": "", "secere": "", "ai": False}


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


def _js_yuvarla(sayi):
    """Tarayıcı Math.round ile aynı yarım-yukarı yuvarlama."""
    try:
        deger = float(sayi)
    except (TypeError, ValueError):
        return 0
    if deger >= 0:
        return int(math.floor(deger + 0.5))
    return int(math.ceil(deger - 0.5))


def hizli_adimlar(hemen_al, acilis):
    """2. buton hemen al (yoksa açılış) %10, 1. buton onun yarısı."""
    hemen = _tl(hemen_al) or 0
    baz = hemen if hemen > 0 else (_tl(acilis) or 0)
    adim2 = max(1, _js_yuvarla(baz * 0.10))
    adim1 = max(1, _js_yuvarla(adim2 / 2.0))
    return adim1, adim2


def asgari_pey(mevcut, urun=None):
    """Canlı üründe taban, hemen al fiyatının %5'idir. Eski çağrı %10 kalır."""
    taban = float(mevcut or 0)
    if urun is not None:
        adim1, _adim2 = hizli_adimlar(
            getattr(urun, "hemen_al_fiyati", 0),
            getattr(urun, "acilis_fiyati", 0),
        )
        if taban <= 0:
            return adim1
        return _js_yuvarla(taban + adim1)
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
    urun.tanitim_yazisi = VARSAYILAN_TANITIM
    if _TARAMA.get("ai") and (_TARAMA.get("secere") or "").strip():
        urun.urun_adi = (_TARAMA.get("urun_adi") or AD)[:200]
        urun.tanitim_yazisi = _TARAMA["secere"][:1500]
    db.session.add(urun)
    db.session.commit()
    return urun


def taramayi_yaz(urun_adi, secere, ai):
    _TARAMA["urun_adi"] = (urun_adi or AD).strip()[:200] or AD
    _TARAMA["secere"] = (secere or "").strip()[:1500]
    _TARAMA["ai"] = bool(ai and _TARAMA["secere"])


def taramayi_sil():
    _TARAMA["urun_adi"] = ""
    _TARAMA["secere"] = ""
    _TARAMA["ai"] = False


def tarama_ozeti():
    return {
        "urun_adi": _TARAMA.get("urun_adi") or "",
        "secere": _TARAMA.get("secere") or "",
        "ai": bool(_TARAMA.get("ai")),
        "tarandi": bool(_TARAMA.get("ai") and _TARAMA.get("secere")),
    }


def secere_gosterilir(metin):
    temiz = (metin or "").strip()
    return bool(temiz) and temiz != VARSAYILAN_TANITIM


def kare_bayti(ham):
    import base64
    import re

    metin = str(ham or "").strip()
    if not metin:
        return b""
    if "," in metin and metin.lower().startswith("data:"):
        metin = metin.split(",", 1)[1]
    metin = re.sub(r"\s+", "", metin)
    try:
        veri = base64.b64decode(metin, validate=False)
    except Exception:
        return b""
    if not veri or len(veri) > 4 * 1024 * 1024:
        return b""
    return veri


def _json_ayikla(ham):
    import json
    import re

    metin = (ham or "").strip()
    if metin.startswith("```"):
        metin = re.sub(r"^```(?:json)?", "", metin, flags=re.I).strip()
        metin = re.sub(r"```$", "", metin).strip()
    try:
        veri = json.loads(metin)
    except (TypeError, ValueError):
        eslesen = re.search(r"\{.*\}", metin, re.S)
        if not eslesen:
            return {}
        try:
            veri = json.loads(eslesen.group(0))
        except (TypeError, ValueError):
            return {}
    return veri if isinstance(veri, dict) else {}


def _openai_anahtari():
    import os

    anahtar = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if anahtar:
        return anahtar
    try:
        from config import Config
        return (getattr(Config, "OPENAI_API_KEY", "") or "").strip()
    except Exception:
        return ""


def urunu_analiz_et(ham_kare):
    """Iriun karesinden ad ve secere. Anahtar veya hata varsa yedek adla döner."""
    import base64
    import json
    import os
    import re
    import subprocess

    yedek = {"urun_adi": AD, "secere": "", "ai": False}
    bayt = kare_bayti(ham_kare)
    anahtar = _openai_anahtari()
    if not bayt or not anahtar:
        taramayi_yaz(AD, "", False)
        return yedek
    govde = {
        "model": "gpt-4o-mini",
        "temperature": 0.4,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Sen Haliç Antik Mezat'ın kıdemli antikacısısın. "
                    "Canlı kamera karesindeki tek ürünü mezat için tanı. "
                    "Yalnızca JSON döndür. Anahtarlar: urun_adi, secere. "
                    "urun_adi kısa ve çarpıcı bir antikacı/mezat ürün adıdır. "
                    "secere malzeme, tahmini dönem, işçilik ve koleksiyon değerini "
                    "anlatan 3 veya 4 cümlelik akıcı sunumdur. "
                    "Emin olmadığın dönemi kesin tarih gibi yazma."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Bu karedeki canlı yayın ürününü tanıt."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(bayt).decode("ascii")},
                    },
                ],
            },
        ],
    }
    ham_govde = json.dumps(govde, ensure_ascii=False).encode("utf-8")
    curl = "curl.exe" if os.name == "nt" else "curl"
    komut = [
        curl, "-sS", "-X", "POST",
        "https://api.openai.com/v1/chat/completions",
        "-H", "Authorization: Bearer " + anahtar,
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json",
        "--data-binary", "@-",
        "-w", "\n%{http_code}",
        "--max-time", "20",
    ]
    if os.name == "nt":
        komut[1:1] = ["-k", "--ssl-no-revoke"]
    try:
        proc = subprocess.run(komut, input=ham_govde, capture_output=True, timeout=25)
        if proc.returncode != 0:
            raise RuntimeError("vision")
        parca = (proc.stdout or b"").rsplit(b"\n", 1)
        if len(parca) != 2:
            raise RuntimeError("vision")
        veri, kod_ham = parca
        kod = int((kod_ham or b"").strip() or b"0")
        if kod != 200:
            raise RuntimeError("vision")
        cozulen = json.loads(veri.decode("utf-8"))
        secim = (cozulen.get("choices") or [{}])[0]
        icerik = (secim.get("message") or {}).get("content") or ""
        ayik = _json_ayikla(icerik)
    except Exception:
        taramayi_yaz(AD, "", False)
        return yedek
    ad = re.sub(r"\s+", " ", str(ayik.get("urun_adi") or "")).strip()[:200] or AD
    secere = re.sub(r"\s+", " ", str(ayik.get("secere") or "")).strip()[:1500]
    ai = bool(secere)
    taramayi_yaz(ad, secere, ai)
    return {"urun_adi": ad, "secere": secere, "ai": ai}


_ODUNC = {}


def _odunc_anahtar(urun_id):
    try:
        return int(urun_id)
    except (TypeError, ValueError):
        return 0


def odunc_yaz(kaynak_id, sahne_id):
    """Kaynak katalog kaydı sahnedeyken otonomun aynı ürünü seçmemesi için."""
    kaynak = _odunc_anahtar(kaynak_id)
    sahne = _odunc_anahtar(sahne_id)
    if not kaynak or not sahne:
        return
    _ODUNC.clear()
    _ODUNC[kaynak] = sahne


def odunc_mu(urun_id):
    return _odunc_anahtar(urun_id) in _ODUNC


def odunc_idleri():
    return list(_ODUNC.keys())


def odunc_sahne_mi(urun_id):
    sahne = _odunc_anahtar(urun_id)
    return bool(sahne) and sahne in _ODUNC.values()


def odunc_birak():
    """Satışta veya pas geçişte bağı koparır. Kaynak satırı buradan değiştirilmez."""
    if not _ODUNC:
        return 0
    kaynak = next(iter(_ODUNC))
    _ODUNC.pop(kaynak, None)
    return kaynak


def satis_kimligi(urun, kazanan_adi, kazanan_id, fiyat, urun_adi=None, lot_no=None):
    """Satış kapanış yayını için kazanan özeti. Sahne, AI, havuz ve mini pencereye dokunmaz."""
    try:
        tutar = float(fiyat or 0)
    except (TypeError, ValueError):
        tutar = 0.0
    ad = str(kazanan_adi or "").strip() or "Yok"
    if kazanan_id in (None, "", "None"):
        kimlik = None
    else:
        try:
            kimlik = int(kazanan_id)
        except (TypeError, ValueError):
            kimlik = kazanan_id
    ad_urun = str(urun_adi or "").strip()
    if not ad_urun:
        ad_urun = str(getattr(urun, "urun_adi", "") or "").strip() or AD
    if lot_no in (None, ""):
        lot = getattr(urun, "lot_no", "")
    else:
        lot = lot_no
    if lot in (None,):
        lot = ""
    else:
        try:
            lot = int(lot)
        except (TypeError, ValueError):
            lot = str(lot)
    return {
        "kazanan_id": kimlik,
        "kazanan_adi": ad,
        "urun_adi": ad_urun[:200],
        "lot_no": lot,
        "son_fiyat": tutar,
    }


# Yalnızca Diğer canlı odasının "Az Sonra Sahnede" sırası. Diğer dükkanlara yazılmaz.
KUYRUK_TAVAN = 5
_KUYRUK = []
_KUYRUK_KILIT = threading.Lock()


def _kuyruk_karti(kart):
    if not isinstance(kart, dict):
        return None
    try:
        urun_id = int(kart.get("urun_id") or 0)
    except (TypeError, ValueError):
        return None
    if urun_id <= 0:
        return None
    try:
        lot_no = int(kart.get("lot_no") or 0)
    except (TypeError, ValueError):
        lot_no = 0
    try:
        baslangic = float(kart.get("baslangic_fiyati") or 0)
    except (TypeError, ValueError):
        baslangic = 0.0
    try:
        hemen_al = float(kart.get("hemen_al") or 0)
    except (TypeError, ValueError):
        hemen_al = 0.0
    return {
        "urun_id": urun_id,
        "lot_no": lot_no,
        "baslik": str(kart.get("baslik") or "")[:200],
        "gorsel": str(kart.get("gorsel") or "")[:2000],
        "kaynak_dukkan": str(kart.get("kaynak_dukkan") or "")[:30],
        "baslangic_fiyati": baslangic,
        "hemen_al": hemen_al,
    }


def kuyruk_listesi():
    with _KUYRUK_KILIT:
        return [dict(kart) for kart in _KUYRUK]


def kuyruk_ekle(kart):
    """Sıraya en fazla 5 ürün. Aynı ürün ikinci kez girmez."""
    temiz = _kuyruk_karti(kart)
    if temiz is None:
        return False, "Geçersiz ürün.", kuyruk_listesi()
    with _KUYRUK_KILIT:
        for kayit in _KUYRUK:
            if kayit.get("urun_id") == temiz["urun_id"]:
                return False, "Bu ürün zaten sırada.", [dict(x) for x in _KUYRUK]
        if len(_KUYRUK) >= KUYRUK_TAVAN:
            return False, "Sırada en fazla 5 ürün olabilir.", [dict(x) for x in _KUYRUK]
        _KUYRUK.append(temiz)
        return True, "Sıraya eklendi.", [dict(x) for x in _KUYRUK]


def kuyruktan_dus(urun_id):
    try:
        hedef = int(urun_id or 0)
    except (TypeError, ValueError):
        return False
    if hedef <= 0:
        return False
    with _KUYRUK_KILIT:
        once = len(_KUYRUK)
        _KUYRUK[:] = [kart for kart in _KUYRUK if kart.get("urun_id") != hedef]
        return len(_KUYRUK) != once


def kuyruk_ilkini_cikar():
    with _KUYRUK_KILIT:
        if not _KUYRUK:
            return None
        return dict(_KUYRUK.pop(0))


def kuyruk_basa_koy(kart):
    temiz = _kuyruk_karti(kart)
    if temiz is None:
        return kuyruk_listesi()
    with _KUYRUK_KILIT:
        _KUYRUK[:] = [kayit for kayit in _KUYRUK if kayit.get("urun_id") != temiz["urun_id"]]
        _KUYRUK.insert(0, temiz)
        return [dict(x) for x in _KUYRUK]


# Diğer canlı sahnesinin satış listesi. Kuyruğa, sahneye ve diğer dükkanlara yazılmaz.
_SATIS_KILIT = threading.Lock()
_SATISLAR = []
_SATIS_SIRA = 0
_SATIS_TAVAN = 80
_PLATFORMLAR = {
    "web": "Web",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
}


def _platform_adi(ham):
    return _PLATFORMLAR.get(str(ham or "").strip().casefold(), "")


def kazanan_ayir(kazanan_adi, platform=None):
    """[TikTok] Ayşe gibi etiketleri ad ve rozet olarak ayırır."""
    ham = str(kazanan_adi or "").strip()
    secilen = _platform_adi(platform)
    ad = ham
    if ham.startswith("[") and "]" in ham:
        etiket, kalan = ham[1:].split("]", 1)
        if not secilen:
            secilen = _platform_adi(etiket)
        ad = kalan.strip() or ham
    if not ad:
        ad = "Yok"
    return ad[:80], (secilen or "Web")


def _satis_herkese(kayit):
    return {
        "id": kayit["id"],
        "urun_id": kayit["urun_id"],
        "lot_no": kayit["lot_no"],
        "urun_adi": kayit["urun_adi"],
        "son_fiyat": kayit["son_fiyat"],
        "kazanan_adi": kayit["kazanan_adi"],
        "kazanan_platform": kayit["kazanan_platform"],
        "telefon": kayit["telefon"],
        "saat": kayit["saat"],
        "durum": kayit["durum"],
        "donem": kayit.get("donem") or "",
    }


def satis_kaydi_ekle(
    urun_id,
    lot_no,
    urun_adi,
    son_fiyat,
    kazanan_adi,
    platform=None,
    telefon="",
):
    """Aynı ürün kısa aralıkta ikinci kez düşerse yeni satır açmaz."""
    global _SATIS_SIRA
    try:
        kimlik = int(urun_id or 0)
    except (TypeError, ValueError):
        kimlik = 0
    if lot_no in (None, ""):
        lot = ""
    else:
        try:
            lot = int(lot_no)
        except (TypeError, ValueError):
            lot = str(lot_no)[:40]
    try:
        tutar = float(son_fiyat or 0)
    except (TypeError, ValueError):
        tutar = 0.0
    ad, rozet = kazanan_ayir(kazanan_adi, platform)
    simdi = time.time()
    with _SATIS_KILIT:
        for eski in reversed(_SATISLAR):
            if simdi - eski["an"] > 25:
                break
            ayni_urun = kimlik > 0 and eski["urun_id"] == kimlik
            ayni_lot = (
                kimlik <= 0
                and eski["lot_no"] == lot
                and eski["kazanan_adi"] == ad
                and eski["son_fiyat"] == tutar
            )
            if ayni_urun or ayni_lot:
                return _satis_herkese(eski)
        _SATIS_SIRA += 1
        kayit = {
            "id": _SATIS_SIRA,
            "urun_id": kimlik,
            "lot_no": lot,
            "urun_adi": str(urun_adi or AD).strip()[:200] or AD,
            "son_fiyat": tutar,
            "kazanan_adi": ad,
            "kazanan_platform": rozet,
            "telefon": str(telefon or "").strip()[:40],
            "saat": time.strftime("%H:%M:%S"),
            "durum": "Ödeme Bekliyor",
            "donem": _donem_tarihi(simdi),
            "an": simdi,
        }
        _SATISLAR.append(kayit)
        if len(_SATISLAR) > _SATIS_TAVAN:
            del _SATISLAR[: len(_SATISLAR) - _SATIS_TAVAN]
        return _satis_herkese(kayit)


def satis_kayitlari():
    with _SATIS_KILIT:
        return [_satis_herkese(kayit) for kayit in reversed(_SATISLAR)]


def satis_odendi_isaretle(kayit_id):
    try:
        hedef = int(kayit_id)
    except (TypeError, ValueError):
        return None
    with _SATIS_KILIT:
        for kayit in _SATISLAR:
            if kayit["id"] == hedef:
                kayit["durum"] = "Ödendi"
                return _satis_herkese(kayit)
    return None


# Ciroya oranlı kargo kumbarası. Yalnızca Diğer canlı satış listesini okur.
KARGO_BARAJ = 1000
KARGO_ORAN = 0.10


def _donem_tarihi(an=None):
    """Türkiye saatiyle 06:00'da yenilenen gece/dönem anahtarı."""
    if an is None:
        an = time.time()
    return time.strftime("%Y-%m-%d", time.gmtime(float(an) + 3 * 3600 - 6 * 3600))


def _ad_anahtar(ad):
    metin = str(ad or "").strip()
    metin = metin.translate(str.maketrans({
        "İ": "i", "I": "ı", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç",
    }))
    return metin.casefold()


def kumbara_hesapla(toplam):
    """1.000 TL ve üzerinde toplamın %10'u. Altında destek 0 TL."""
    try:
        tutar = float(toplam or 0)
    except (TypeError, ValueError):
        tutar = 0.0
    if tutar < 0:
        tutar = 0.0
    tutar = float(_js_yuvarla(tutar))
    if tutar >= KARGO_BARAJ:
        destek = float(_js_yuvarla(tutar * KARGO_ORAN))
    else:
        destek = 0.0
    if tutar <= 0:
        kalan = float(KARGO_BARAJ)
    else:
        artik = int(tutar) % KARGO_BARAJ
        kalan = float(KARGO_BARAJ if artik == 0 else KARGO_BARAJ - artik)
    sonraki = tutar + kalan
    return {
        "toplam_tutar": tutar,
        "kargo_destegi": destek,
        "net_odenecek": tutar - destek,
        "sonraki_baraj_kalan": kalan,
        "sonraki_kargo_destegi": float(_js_yuvarla(sonraki * KARGO_ORAN)),
    }


def _aktif_donem(kayit, donem):
    kayit_donem = str(kayit.get("donem") or "")
    if not kayit_donem:
        return donem == _donem_tarihi()
    return kayit_donem == donem


def sepet_gruplari(kayitlar, donem=None):
    """Aynı gece, aynı ad ve platform satışlarını tek müşteri sepetinde birleştirir."""
    if donem is None:
        donem = _donem_tarihi()
    gruplar = {}
    sira = []
    for kayit in kayitlar or []:
        if not isinstance(kayit, dict) or not _aktif_donem(kayit, donem):
            continue
        ad = str(kayit.get("kazanan_adi") or "").strip()
        if not ad or _ad_anahtar(ad) in ("yok", "none", "-", "kimse"):
            continue
        platform = str(kayit.get("kazanan_platform") or "Web").strip() or "Web"
        anahtar = (_ad_anahtar(ad), platform.casefold())
        if anahtar not in gruplar:
            gruplar[anahtar] = {
                "kazanan_adi": ad,
                "kazanan_platform": platform,
                "urunler": [],
            }
            sira.append(anahtar)
        gruplar[anahtar]["urunler"].append(kayit)
    sonuc = []
    for anahtar in sira:
        grup = gruplar[anahtar]
        toplam = 0.0
        for urun in grup["urunler"]:
            try:
                toplam += float(urun.get("son_fiyat") or 0)
            except (TypeError, ValueError):
                continue
        ozet = kumbara_hesapla(toplam)
        ozet["kazanan_adi"] = grup["kazanan_adi"]
        ozet["kazanan_platform"] = grup["kazanan_platform"]
        ozet["urun_sayisi"] = len(grup["urunler"])
        ozet["kayit_idler"] = [urun.get("id") for urun in grup["urunler"]]
        ozet["donem"] = donem
        sonuc.append(ozet)
    return sonuc


def musteri_sepeti_bul(kayitlar, isim, platform=None, donem=None):
    """İsim (ve varsa platform) ile bu gecenin sepetini ve tekil satışlarını döndürür."""
    ham = str(isim or "").strip()
    onek_var = ham.startswith("[") and "]" in ham
    ad, rozet = kazanan_ayir(ham, platform)
    if platform or onek_var:
        hedef_platform = rozet
    else:
        hedef_platform = "Web"
    bos = kumbara_hesapla(0)
    bos.update({
        "kazanan_adi": ad if ad != "Yok" else "",
        "kazanan_platform": hedef_platform,
        "urun_sayisi": 0,
        "kayit_idler": [],
        "donem": donem or _donem_tarihi(),
    })
    if not ad or _ad_anahtar(ad) in ("yok", "none", "-", "kimse"):
        return [], bos, []
    sepetler = sepet_gruplari(kayitlar, donem)
    eslesen = [
        sepet for sepet in sepetler
        if _ad_anahtar(sepet.get("kazanan_adi")) == _ad_anahtar(ad)
        and str(sepet.get("kazanan_platform") or "").casefold() == hedef_platform.casefold()
    ]
    if not eslesen:
        return [], bos, []
    secilen = eslesen[0]
    idler = set(secilen.get("kayit_idler") or [])
    tekil = [kayit for kayit in (kayitlar or []) if kayit.get("id") in idler]
    return tekil, secilen, eslesen
