"""Çoklu sosyal medya RTMP havuzu.

Her işaretli hesap kendi FFmpeg sürecine yazılır. Bir hedefin kopması
ana Restream köprüsünü ve diğer hesapları durdurmaz.
Kalıcı kayıt instance/sosyal_yayin_havuzu.json dosyasındadır.
"""

import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading

_DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "sosyal_yayin_havuzu.json")
_KILIT = threading.RLock()
_YAYIN = False
_SLOTLAR = {}
_FFMPEG = ""
_BASLIK = b""
_MIME = ""
_UST_SINIR = 100

_URL = re.compile(
    r"^(rtmps?)://([A-Za-z0-9._-]{1,253})(?::\d{2,5})?(/[^\s\"'\\]{0,420})?$",
    re.IGNORECASE,
)


def yayin_kapisi(acik):
    """Diğer yayını açılınca havuzu beklemeye alır, kapanınca süreçleri sonlandırır."""
    global _YAYIN, _BASLIK, _MIME
    acik = bool(acik)
    with _KILIT:
        _YAYIN = acik
        if not acik:
            _BASLIK = b""
            _MIME = ""
            slotlar = list(_SLOTLAR.values())
            _SLOTLAR.clear()
        else:
            slotlar = []
    for slot in slotlar:
        _slot_sonlandir(slot)


def parca_yaz(paket, baslik, mime):
    """Aynı medya parçasını işaretli her hesaba ayrı süreçle dağıtır."""
    global _BASLIK, _MIME
    if not paket:
        return {"adet": 0, "yeniden": False}
    with _KILIT:
        if not _YAYIN:
            return {"adet": 0, "yeniden": False}
        if baslik:
            _BASLIK = bytes(paket)
            _MIME = str(mime or "")
        hesaplar = [dict(kayit) for kayit in _oku() if kayit.get("dahil")]
    adet = 0
    for hesap in hesaplar:
        hedef = _hedef_url(hesap)
        if not hedef:
            continue
        slot = _slot_hazirla(hesap["id"], hedef)
        with slot["kilit"]:
            if slot["durum"] == "hata" and not baslik:
                continue
            if slot["hedef"] != hedef:
                slot["hedef"] = hedef
                slot["nesil"] = slot.get("nesil", 0) + 1
                _proc_birak(slot)
                slot["durum"] = "bekliyor"
                slot["hata"] = ""
        try:
            slot["kuyruk"].put_nowait((bytes(paket), bool(baslik), str(mime or "")))
            adet += 1
        except queue.Full:
            with slot["kilit"]:
                slot["durum"] = "hata"
                slot["hata"] = "Hedef yanıt vermedi"
                _proc_birak(slot)
    return {"adet": adet, "yeniden": False}


def api(islem, veri):
    veri = veri if isinstance(veri, dict) else {}
    if islem == "sosyal_havuz_liste":
        return {"success": True, "hesaplar": liste()}
    if islem == "sosyal_havuz_ekle":
        return _ekle(veri.get("ad"), veri.get("rtmp_url"), veri.get("stream_key"))
    if islem == "sosyal_havuz_guncelle":
        return _guncelle(veri.get("id"), veri.get("stream_key"))
    if islem == "sosyal_havuz_sil":
        return _sil(veri.get("id"))
    if islem == "sosyal_havuz_dahil":
        return _dahil(veri.get("id"), veri.get("dahil"))
    return {"success": False, "mesaj": "Geçersiz havuz işlemi."}


def liste():
    with _KILIT:
        yayin = _YAYIN
        hesaplar = _oku()
        slotlar = {
            kim: {"durum": slot.get("durum") or "bekliyor", "hata": slot.get("hata") or ""}
            for kim, slot in _SLOTLAR.items()
        }
    cikti = []
    for kayit in hesaplar:
        durum = "pasif"
        hata = ""
        if kayit.get("dahil") and yayin:
            slot = slotlar.get(kayit["id"])
            if slot is None or slot["durum"] == "pasif":
                durum = "bekliyor"
            else:
                durum = slot["durum"] if slot["durum"] in ("bekliyor", "yayinda", "hata") else "bekliyor"
                hata = slot["hata"] if durum == "hata" else ""
        cikti.append(
            {
                "id": kayit["id"],
                "ad": kayit["ad"],
                "rtmp_url": kayit["rtmp_url"],
                "stream_key": kayit["stream_key"],
                "dahil": bool(kayit.get("dahil")),
                "durum": durum,
                "hata": hata,
            }
        )
    return cikti


def _ekle(ad, url, anahtar):
    temiz_ad = _ad_temizle(ad)
    temiz_url = _url_temizle(url)
    temiz_anahtar = _anahtar_temizle(anahtar)
    if not temiz_ad:
        return {"success": False, "mesaj": "Hesap adı gerekli."}
    if not temiz_url:
        return {"success": False, "mesaj": "Geçerli bir RTMP sunucu URL'si girin."}
    if not temiz_anahtar:
        return {"success": False, "mesaj": "Yayın anahtarı geçersiz."}
    with _KILIT:
        hesaplar = _oku()
        if len(hesaplar) >= _UST_SINIR:
            return {"success": False, "mesaj": "Havuz üst sınıra ulaştı."}
        hesaplar.append(
            {
                "id": _yeni_id(hesaplar),
                "ad": temiz_ad,
                "rtmp_url": temiz_url,
                "stream_key": temiz_anahtar,
                "dahil": False,
            }
        )
        _yaz(hesaplar)
    return {"success": True, "mesaj": "Hesap listeye eklendi.", "hesaplar": liste()}


def _guncelle(kim, anahtar):
    kim = _kimlik(kim)
    temiz = _anahtar_temizle(anahtar)
    if not kim:
        return {"success": False, "mesaj": "Hesap bulunamadı."}
    if not temiz:
        return {"success": False, "mesaj": "Yayın anahtarı geçersiz."}
    with _KILIT:
        hesaplar = _oku()
        bulunan = None
        for kayit in hesaplar:
            if kayit["id"] == kim:
                kayit["stream_key"] = temiz
                bulunan = kayit
                break
        if bulunan is None:
            return {"success": False, "mesaj": "Hesap bulunamadı."}
        _yaz(hesaplar)
        slot = _SLOTLAR.get(kim)
    if slot is not None:
        with slot["kilit"]:
            slot["hedef"] = _hedef_url(bulunan)
            slot["nesil"] = slot.get("nesil", 0) + 1
            slot["durum"] = "bekliyor" if _YAYIN and bulunan.get("dahil") else "pasif"
            slot["hata"] = ""
            _proc_birak(slot)
    return {"success": True, "mesaj": "Yayın anahtarı güncellendi.", "hesaplar": liste()}


def _sil(kim):
    kim = _kimlik(kim)
    if not kim:
        return {"success": False, "mesaj": "Hesap bulunamadı."}
    with _KILIT:
        hesaplar = _oku()
        kalan = [kayit for kayit in hesaplar if kayit["id"] != kim]
        if len(kalan) == len(hesaplar):
            return {"success": False, "mesaj": "Hesap bulunamadı."}
        _yaz(kalan)
        slot = _SLOTLAR.pop(kim, None)
    if slot is not None:
        _slot_sonlandir(slot)
    return {"success": True, "mesaj": "Hesap silindi.", "hesaplar": liste()}


def _dahil(kim, ham):
    kim = _kimlik(kim)
    if not kim:
        return {"success": False, "mesaj": "Hesap bulunamadı."}
    secili = _bool(ham)
    with _KILIT:
        hesaplar = _oku()
        bulunan = None
        for kayit in hesaplar:
            if kayit["id"] == kim:
                kayit["dahil"] = secili
                bulunan = kayit
                break
        if bulunan is None:
            return {"success": False, "mesaj": "Hesap bulunamadı."}
        _yaz(hesaplar)
        slot = _SLOTLAR.get(kim)
        if not secili and slot is not None:
            _SLOTLAR.pop(kim, None)
    if not secili and slot is not None:
        _slot_sonlandir(slot)
    elif secili and _YAYIN:
        _slot_hazirla(bulunan["id"], _hedef_url(bulunan))
    return {"success": True, "mesaj": "Yayın seçimi kaydedildi.", "hesaplar": liste()}


def _slot_hazirla(kim, hedef):
    with _KILIT:
        slot = _SLOTLAR.get(kim)
        if slot is None:
            slot = {
                "id": kim,
                "hedef": hedef,
                "kilit": threading.Lock(),
                "kuyruk": queue.Queue(maxsize=8),
                "proc": None,
                "durum": "bekliyor",
                "hata": "",
                "nesil": 0,
                "dur": threading.Event(),
                "son_satir": "",
            }
            _SLOTLAR[kim] = slot
            threading.Thread(target=_isci, args=(slot,), daemon=True).start()
            return slot
    with slot["kilit"]:
        if slot["hedef"] != hedef:
            slot["hedef"] = hedef
            slot["nesil"] = slot.get("nesil", 0) + 1
            _proc_birak(slot)
            slot["durum"] = "bekliyor"
            slot["hata"] = ""
    return slot


def _slot_sonlandir(slot):
    if slot is None:
        return
    slot["dur"].set()
    try:
        slot["kuyruk"].put_nowait(None)
    except queue.Full:
        pass
    with slot["kilit"]:
        _proc_birak(slot)
        slot["durum"] = "pasif"


def _isci(slot):
    while not slot["dur"].is_set():
        try:
            paket = slot["kuyruk"].get(timeout=0.5)
        except queue.Empty:
            _proc_dustu_mu(slot)
            continue
        if paket is None or slot["dur"].is_set():
            break
        govde, baslik, mime = paket
        try:
            _slot_yaz(slot, govde, baslik, mime)
        except Exception:
            _hata_isaretle(slot, "Yayın koptu")


def _slot_yaz(slot, paket, baslik, mime):
    nesil = 0
    with slot["kilit"]:
        if slot["dur"].is_set():
            return
        nesil = slot.get("nesil", 0)
        proc = slot.get("proc")
        if proc is not None and proc.poll() is not None:
            _proc_birak(slot)
            proc = None
            if not baslik:
                _hata_kilit_icinde(slot, slot.get("son_satir") or "Yayın koptu")
                return
        if proc is None:
            on_ek = b""
            if not baslik:
                on_ek, on_mime = _baslik_kopya()
                if not on_ek:
                    slot["durum"] = "bekliyor"
                    return
                mime = mime or on_mime
            proc = _surec_ac(slot.get("hedef") or "", mime, slot)
            if proc is None:
                _hata_kilit_icinde(slot, "FFmpeg açılamadı")
                return
            slot["proc"] = proc
        else:
            on_ek = b""
        stdin = proc.stdin
    try:
        if on_ek:
            _stdin_yaz(stdin, on_ek)
        _stdin_yaz(stdin, paket)
    except Exception:
        _hata_isaretle(slot, "Yayın koptu", nesil)
        return
    with slot["kilit"]:
        if slot.get("proc") is proc and not slot["dur"].is_set():
            slot["durum"] = "yayinda"
            slot["hata"] = ""


def _hata_isaretle(slot, metin, nesil=None):
    with slot["kilit"]:
        if nesil is not None and slot.get("nesil") != nesil:
            return
        if slot["dur"].is_set():
            _proc_birak(slot)
            return
        _proc_birak(slot)
        _hata_kilit_icinde(slot, metin)


def _hata_kilit_icinde(slot, metin):
    slot["durum"] = "hata"
    slot["hata"] = _maske(metin)[:120] or "Yayın koptu"


def _baslik_kopya():
    with _KILIT:
        return _BASLIK, _MIME


def _stdin_yaz(stdin, paket):
    kalan = paket
    while kalan:
        yazilan = stdin.write(kalan)
        if not yazilan:
            raise OSError("ffmpeg stdin kapandi")
        kalan = kalan[yazilan:]
    stdin.flush()


def _proc_dustu_mu(slot):
    with slot["kilit"]:
        proc = slot.get("proc")
        if proc is None or proc.poll() is None:
            return
        _proc_birak(slot)
        _hata_kilit_icinde(slot, slot.get("son_satir") or "Yayın koptu")


def _proc_birak(slot):
    proc = slot.get("proc")
    slot["proc"] = None
    if proc is not None:
        _proc_kapat(proc)


def _proc_kapat(proc):
    if proc is None:
        return
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass


def _surec_ac(hedef, mime, slot):
    if not (str(hedef).startswith("rtmp://") or str(hedef).startswith("rtmps://")):
        return None
    bicim = "mp4" if "mp4" in str(mime or "").lower() else "webm"
    komut = [
        _ffmpeg_yolu(),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts+discardcorrupt",
        "-seekable",
        "0",
        "-f",
        bicim,
        "-i",
        "pipe:0",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "25",
        "-g",
        "50",
        "-keyint_min",
        "50",
        "-vf",
        "scale=1280:-2",
        "-b:v",
        "2500k",
        "-maxrate",
        "2500k",
        "-bufsize",
        "5000k",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-b:a",
        "128k",
        "-f",
        "flv",
        "-flvflags",
        "no_duration_filesize",
        hedef,
    ]
    ayar = {}
    if os.name == "nt":
        bayrak = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if bayrak:
            ayar["creationflags"] = bayrak
    try:
        proc = subprocess.Popen(
            komut,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            **ayar,
        )
    except Exception as hata:
        print(f"[SOSYAL HAVUZ] ffmpeg acilamadi: {hata}", flush=True)
        return None
    threading.Thread(target=_stderr_oku, args=(slot, proc), daemon=True).start()
    return proc


def _stderr_oku(slot, proc):
    try:
        akis = proc.stderr
        if akis is None:
            return
        for satir in iter(akis.readline, b""):
            if not satir:
                break
            metin = _maske(satir.decode("utf-8", "replace")).strip()
            if not metin:
                continue
            with slot["kilit"]:
                slot["son_satir"] = metin[:180]
    except Exception:
        return


def _ffmpeg_yolu():
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    yer = ""
    try:
        yer = shutil.which("ffmpeg") or ""
    except Exception:
        yer = ""
    if not yer:
        try:
            import imageio_ffmpeg

            yer = str(imageio_ffmpeg.get_ffmpeg_exe() or "")
        except Exception as hata:
            print(f"[SOSYAL HAVUZ] imageio-ffmpeg atlandi: {hata}", flush=True)
            yer = ""
    _FFMPEG = yer or "ffmpeg"
    return _FFMPEG


def _oku():
    try:
        with open(_DOSYA, "r", encoding="utf-8") as dosya:
            ham = json.load(dosya)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        print("[SOSYAL HAVUZ] kayit okunamadi", flush=True)
        return []
    satirlar = ham.get("hesaplar") if isinstance(ham, dict) else None
    if not isinstance(satirlar, list):
        return []
    cikti = []
    for ham_kayit in satirlar:
        kayit = _kayit_temizle(ham_kayit)
        if kayit:
            cikti.append(kayit)
    return cikti


def _yaz(hesaplar):
    klasor = os.path.dirname(_DOSYA)
    os.makedirs(klasor, exist_ok=True)
    gecici = _DOSYA + ".tmp"
    with open(gecici, "w", encoding="utf-8") as dosya:
        json.dump({"hesaplar": hesaplar}, dosya, ensure_ascii=False, indent=2)
        dosya.flush()
        os.fsync(dosya.fileno())
    os.replace(gecici, _DOSYA)


def _kayit_temizle(ham):
    if not isinstance(ham, dict):
        return None
    kim = _kimlik(ham.get("id"))
    ad = _ad_temizle(ham.get("ad"))
    url = _url_temizle(ham.get("rtmp_url"))
    anahtar = _anahtar_temizle(ham.get("stream_key"))
    if not kim or not ad or not url or not anahtar:
        return None
    return {
        "id": kim,
        "ad": ad,
        "rtmp_url": url,
        "stream_key": anahtar,
        "dahil": bool(ham.get("dahil")),
    }


def _hedef_url(hesap):
    url = str((hesap or {}).get("rtmp_url") or "").rstrip("/")
    anahtar = str((hesap or {}).get("stream_key") or "")
    if not url or not anahtar:
        return ""
    if url.endswith("/" + anahtar):
        return url
    return url + "/" + anahtar


def _ad_temizle(deger):
    ad = re.sub(r"\s+", " ", str(deger or "")).strip()
    ad = re.sub(r"[\x00-\x1f]", "", ad)
    return ad[:80]


def _url_temizle(deger):
    adres = str(deger or "").strip()
    if not _URL.fullmatch(adres):
        return ""
    return adres


def _anahtar_temizle(deger):
    anahtar = str(deger or "").strip()
    if len(anahtar) < 4 or len(anahtar) > 400:
        return ""
    if re.search(r"[\s\"'\\]", anahtar) or ".." in anahtar:
        return ""
    return anahtar


def _kimlik(deger):
    kim = str(deger or "").strip().lower()
    if re.fullmatch(r"sy[a-f0-9]{16}", kim):
        return kim
    return ""


def _yeni_id(hesaplar):
    var_olan = {kayit["id"] for kayit in hesaplar}
    for _ in range(8):
        kim = "sy" + secrets.token_hex(8)
        if kim not in var_olan:
            return kim
    return "sy" + secrets.token_hex(8)


def _bool(deger):
    if isinstance(deger, bool):
        return deger
    return str(deger or "").strip().lower() in ("1", "true", "evet", "on", "dahil")


def _maske(metin):
    satir = str(metin or "").replace("\n", " ").strip()
    satir = re.sub(r"(rtmps?://)\S+", r"\1***", satir, flags=re.IGNORECASE)
    return satir
