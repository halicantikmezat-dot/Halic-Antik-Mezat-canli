"""Yalnızca Diğer - Her Şey (dukkan=diger) canlı yayını.

Diğer dükkanların izleyici sayacı, sesi ve reji akışı bu modüle girmez.
"""
import re
import threading
import time

DUKKAN = "diger"
NABIZ_ESIK_SANIYE = 18
ONAY_CUMLESI = "Tamam patron, sahne sende."
_KORUNAN_TURLER = {"satis", "zafer", "sahne", "yeni_urun"}

_kilit = threading.Lock()
_oturumlar = {}
_sid_oturum = {}
_spiker_sessiz = False
_mikrofon_kisik = False
_mic_kapali = False


def _sade(metin):
    s = str(metin or "").casefold()
    for eski, yeni in (
        ("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"), ("â", "a"),
    ):
        s = s.replace(eski, yeni)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_DEVAM = re.compile(r"(?:^|\s)(?:devam et|devral|sahne sende)(?:\s|$)")
_SUS_KELIME = re.compile(r"(?:^|\s)(?:dur|sus|bekle)(?:\s|$)")


def emir_karari(metin):
    """diger reji/patron emri. 'sus' susturur, 'devam' spikeri açar, boşta eski akış sürer."""
    s = _sade(metin)
    if not s:
        return ""
    if _DEVAM.search(s):
        return "devam"
    if "sahne bende" in s or _SUS_KELIME.search(s):
        return "sus"
    return ""


def sustur():
    global _spiker_sessiz
    _spiker_sessiz = True


def ac():
    global _spiker_sessiz
    _spiker_sessiz = False


def sessiz_mi():
    return _spiker_sessiz


def ses_kesilsin_mi(dukkan, tur):
    if str(dukkan or "").strip().lower() != DUKKAN or not _spiker_sessiz:
        return False
    return str(tur or "anons") not in _KORUNAN_TURLER


def mikrofon_kis(kisik):
    global _mikrofon_kisik
    _mikrofon_kisik = bool(kisik)


def mikrofon_kisik_mi():
    return _mikrofon_kisik


def mic_kapat(kapali):
    """Ses açık/kapalı anahtarı. Yalnız diger odasının canlı mikrofonu."""
    global _mic_kapali
    _mic_kapali = bool(kapali)


def mic_kapali_mi():
    return _mic_kapali


def canli_ses_kesik_mi():
    return _mikrofon_kisik or _mic_kapali


def hedeflerden_digeri_cikar(hedefler):
    """Bas-konuş veya ses kapalıyken yalnız diger odasına giden canlı sesi düşür."""
    if not canli_ses_kesik_mi():
        return list(hedefler or [])
    return [kod for kod in (hedefler or []) if kod != DUKKAN]


def _nesil_say(deger):
    try:
        return int(deger or 0)
    except (TypeError, ValueError):
        return 0


def dokun(sid, oturum, nesil, ad, kullanici_id=None):
    """Taze nabız. Yeni oturum veya isim değiştiyse True."""
    anahtar = str(oturum or sid or "").strip()[:80]
    if not anahtar or not sid:
        return False
    gelen = _nesil_say(nesil)
    simdi = time.time()
    ad_temiz = str(ad or "Misafir").strip()[:40] or "Misafir"
    with _kilit:
        eski_anahtar = _sid_oturum.get(sid)
        if eski_anahtar and eski_anahtar != anahtar:
            _oturumlar.pop(eski_anahtar, None)
        kayit = _oturumlar.get(anahtar)
        if kayit and gelen and kayit.get("nesil") and gelen < kayit.get("nesil"):
            return False
        taze_degil = (not kayit) or (simdi - float(kayit.get("nabiz") or 0) > NABIZ_ESIK_SANIYE)
        ad_degisti = (not kayit) or kayit.get("ad") != ad_temiz
        onceki_sid = (kayit or {}).get("sid")
        if onceki_sid and onceki_sid != sid:
            _sid_oturum.pop(onceki_sid, None)
        _oturumlar[anahtar] = {
            "sid": sid,
            "ad": ad_temiz,
            "nesil": gelen or (kayit or {}).get("nesil") or 0,
            "nabiz": simdi,
            "kullanici_id": kullanici_id,
        }
        _sid_oturum[sid] = anahtar
        return taze_degil or ad_degisti


def ayril(oturum, nesil=None):
    anahtar = str(oturum or "").strip()[:80]
    if not anahtar:
        return False
    with _kilit:
        kayit = _oturumlar.get(anahtar)
        if not kayit:
            return False
        if nesil is not None and nesil != "":
            gelen = _nesil_say(nesil)
            kayitli = _nesil_say(kayit.get("nesil"))
            if gelen and kayitli and gelen != kayitli:
                return False
        _oturumlar.pop(anahtar, None)
        if kayit.get("sid"):
            _sid_oturum.pop(kayit["sid"], None)
        return True


def sid_dusur(sid):
    if not sid:
        return False
    with _kilit:
        anahtar = _sid_oturum.pop(sid, None)
        if not anahtar:
            return False
        _oturumlar.pop(anahtar, None)
        return True


def supur(simdi=None):
    simdi = time.time() if simdi is None else simdi
    dustu = False
    with _kilit:
        for anahtar, kayit in list(_oturumlar.items()):
            if simdi - float(kayit.get("nabiz") or 0) > NABIZ_ESIK_SANIYE:
                _oturumlar.pop(anahtar, None)
                if _sid_oturum.get(kayit.get("sid")) == anahtar:
                    _sid_oturum.pop(kayit.get("sid"), None)
                dustu = True
    return dustu


def anlik(simdi=None):
    simdi = time.time() if simdi is None else simdi
    with _kilit:
        taze = [
            kayit for kayit in _oturumlar.values()
            if simdi - float(kayit.get("nabiz") or 0) <= NABIZ_ESIK_SANIYE
        ]
    isimler = []
    for kayit in taze:
        ad = str(kayit.get("ad") or "Misafir").strip() or "Misafir"
        isimler.append(ad)
    return len(taze), isimler


def temizlik_dongusu(uyut, duyur):
    while True:
        try:
            if supur():
                duyur()
        except Exception as hata:
            print(f"[DIGER IZLEYICI] temizlik atlandi: {hata}", flush=True)
        uyut(3)
