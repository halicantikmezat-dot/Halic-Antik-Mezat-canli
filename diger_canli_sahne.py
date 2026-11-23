"""Yalnızca Diğer - Her Şey dükkanı için geçici canlı sahne ürünü."""

import math

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
