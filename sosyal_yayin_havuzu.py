"""Çoklu sosyal medya RTMP havuzu.

Tek hedef kendi FFmpeg sürecine yazılır. İki ve daha fazla hedef tek
H.264/AAC kodundan kopyalanır. Bir hedefin kopması diğerlerini durdurmaz.
Kalıcı kayıt veritabanı ve Redis'tedir. JSON yalnız depo yoksa kullanılır.
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
_KALICI_OKU = None
_KALICI_YAZ = None
_ORTAK = None
_ORTAK_KILIT = threading.Lock()

_URL = re.compile(
    r"^(rtmps?)://([A-Za-z0-9._-]{1,253})(?::\d{2,5})?(/[^\s\"'\\]{0,420})?$",
    re.IGNORECASE,
)
_EBML = b"\x1a\x45\xdf\xa3"


def _ebml_mi(paket):
    """MediaRecorder WebM başlığı EBML imzasıyla başlar."""
    veri = bytes(paket or b"")
    return len(veri) >= 4 and veri[:4] == _EBML


def _giris_bicimi(mime):
    """Parçalı WebM, pipe üzerinde matroska demuxer ile okunur."""
    if "mp4" in str(mime or "").lower():
        return "mp4"
    return "matroska"


def _giris_argumanlari(mime):
    """Varsayılan 5 sn analyzeduration, canlı WebM'i probe sonunda düşürür.

    Sıfır değer FFmpeg'te 'ayarsız' sayılır ve yine 5 saniyeye döner.
    """
    return [
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts+nobuffer+igndts",
        "-flags",
        "low_delay",
        "-use_wallclock_as_timestamps",
        "1",
        "-probesize",
        "5000000",
        "-analyzeduration",
        "100000",
        "-f",
        _giris_bicimi(mime),
        "-i",
        "pipe:0",
    ]


def _kod_argumanlari():
    return [
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
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
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-max_interleave_delta",
        "0",
        "-max_muxing_queue_size",
        "1024",
    ]


def _flv_bayraklari(hedef):
    bayrak = ["-f", "flv", "-flvflags", "no_duration_filesize"]
    if str(hedef).startswith("rtmps://"):
        bayrak.extend(["-rw_timeout", "15000000", "-tls_verify", "0"])
    bayrak.append(str(hedef))
    return bayrak


def _kopya_cikis_bayraklari(hedef):
    """Her hedefin kendi RTMP zaman aşımı. Ortak kodlayıcıyı düşürmez."""
    bayrak = ["-f", "flv", "-flvflags", "no_duration_filesize", "-rw_timeout", "15000000"]
    if str(hedef).startswith("rtmps://"):
        bayrak.extend(["-tls_verify", "0"])
    bayrak.append(str(hedef))
    return bayrak


def _onemli_satir(metin):
    satir = str(metin or "").lower()
    return any(
        iz in satir
        for iz in (
            "error",
            "invalid",
            "failed",
            "timed out",
            "timeout",
            "cannot",
            "refused",
            "broken pipe",
            "no such",
            "denied",
            "unreachable",
            "could not",
            "conversion failed",
        )
    )


def kalici_bagla(oku, yaz):
    """Veritabanı veya Redis varsa JSON yedeğinin önüne geçer."""
    global _KALICI_OKU, _KALICI_YAZ
    _KALICI_OKU = oku
    _KALICI_YAZ = yaz
    try:
        with _KILIT:
            depo = _depo_oku()
            if depo is None or depo.get("kayitli"):
                return
            yerel = _json_oku()
            if yerel and _depo_yaz(yerel):
                print("[SOSYAL HAVUZ] json kaydi kalici depoya tasindi", flush=True)
    except Exception as hata:
        print(f"[SOSYAL HAVUZ] kalici aktarim atlandi: {hata}", flush=True)


def yayin_kapisi(acik):
    """Diğer yayını açılınca havuzu beklemeye alır, kapanınca süreçleri sonlandırır."""
    global _YAYIN, _BASLIK, _MIME
    acik = bool(acik)
    _ortak_kapat()
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
            if not _ebml_mi(paket):
                return {"adet": 0, "yeniden": True}
            _BASLIK = bytes(paket)
            _MIME = str(mime or "")
        hesaplar = [dict(kayit) for kayit in _oku() if kayit.get("dahil")]
    hedefler = []
    for hesap in hesaplar:
        hedef = _hedef_url(hesap)
        if hedef:
            hedefler.append({"id": hesap["id"], "hedef": hedef})
    if len(hedefler) >= 2:
        return _ortak_dagit(hedefler, paket, baslik, mime)
    _ortak_kapat()
    _ortak_bayrak([], False)
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
                "son_hata": hata,
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
                "ortak": False,
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
    if slot.get("ortak"):
        return
    if baslik and not _ebml_mi(paket):
        with slot["kilit"]:
            if slot.get("proc") is None:
                slot["durum"] = "bekliyor"
                slot["hata"] = ""
        return
    nesil = 0
    kapanan = None
    dustu = False
    on_ek = b""
    stdin = None
    proc = None
    with slot["kilit"]:
        if slot.get("ortak") or slot["dur"].is_set():
            return
        nesil = slot.get("nesil", 0)
        proc = slot.get("proc")
        if proc is not None and proc.poll() is not None:
            slot["proc"] = None
            kapanan = proc
            proc = None
            if not baslik:
                _hata_kilit_icinde(slot, slot.get("son_hata") or slot.get("son_satir") or "Yayın koptu")
                dustu = True
        elif proc is not None and baslik:
            slot["proc"] = None
            slot["nesil"] = nesil + 1
            nesil = slot["nesil"]
            kapanan = proc
            proc = None
        if not dustu and proc is None:
            if not baslik:
                on_ek, on_mime = _baslik_kopya()
                if not on_ek:
                    slot["durum"] = "bekliyor"
                    slot["hata"] = ""
                    dustu = True
                else:
                    mime = mime or on_mime
            elif not _ebml_mi(paket):
                slot["durum"] = "bekliyor"
                slot["hata"] = ""
                dustu = True
            if not dustu:
                proc = _surec_ac(slot.get("hedef") or "", mime, slot)
                if proc is None:
                    _hata_kilit_icinde(slot, "FFmpeg açılamadı")
                    dustu = True
                else:
                    slot["proc"] = proc
        if proc is not None:
            stdin = proc.stdin
    if kapanan is not None:
        _proc_kapat(kapanan)
    if dustu or stdin is None:
        return
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
    slot["hata"] = _maske(metin)[:240] or "Yayın koptu"


def _baslik_kopya():
    with _KILIT:
        if not _ebml_mi(_BASLIK):
            return b"", ""
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
        _hata_kilit_icinde(slot, slot.get("son_hata") or slot.get("son_satir") or "Yayın koptu")


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
    komut = [_ffmpeg_yolu(), *_giris_argumanlari(mime), *_kod_argumanlari(), *_flv_bayraklari(hedef)]
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
                slot["son_satir"] = metin[:240]
                if _onemli_satir(metin):
                    slot["son_hata"] = metin[:240]
                    print(f"[SOSYAL HAVUZ] ffmpeg: {metin[:240]}", flush=True)
    except Exception:
        return


def _ortak_bayrak(hedefler, acik):
    secili = {satir["id"] for satir in hedefler} if acik else set()
    with _KILIT:
        for kim, slot in list(_SLOTLAR.items()):
            slot["ortak"] = kim in secili


def _ortak_kapat():
    global _ORTAK
    with _ORTAK_KILIT:
        ortak = _ORTAK
        _ORTAK = None
    if ortak is None:
        return
    ortak["dur"].set()
    try:
        ortak["giris"].put_nowait(None)
    except queue.Full:
        pass
    with ortak["kilit"]:
        proc = ortak.get("proc")
        ortak["proc"] = None
        kopyalar = list(ortak["kopyalar"].values())
        ortak["kopyalar"].clear()
    _proc_kapat(proc)
    for kopya in kopyalar:
        _kopya_sonlandir(kopya)


def _ortak_getir():
    global _ORTAK
    with _ORTAK_KILIT:
        if _ORTAK is not None and not _ORTAK["dur"].is_set():
            return _ORTAK
        ortak = {
            "dur": threading.Event(),
            "kilit": threading.Lock(),
            "giris": queue.Queue(maxsize=12),
            "proc": None,
            "nesil": 0,
            "hedefler": {},
            "kopyalar": {},
            "son_satir": "",
        }
        _ORTAK = ortak
        threading.Thread(target=_ortak_giris_isci, args=(ortak,), daemon=True).start()
        return ortak


def _ortak_dagit(hedefler, paket, baslik, mime):
    """İki ve daha fazla hedef: tek kodlama, her hedefe -c copy."""
    _ortak_bayrak(hedefler, True)
    for satir in hedefler:
        slot = _slot_hazirla(satir["id"], satir["hedef"])
        slot["ortak"] = True
        with slot["kilit"]:
            if slot.get("proc") is not None:
                _proc_birak(slot)
    ortak = _ortak_getir()
    with ortak["kilit"]:
        ortak["hedefler"] = {satir["id"]: satir["hedef"] for satir in hedefler}
    try:
        ortak["giris"].put_nowait((bytes(paket), bool(baslik), str(mime or "")))
    except queue.Full:
        return {"adet": 0, "yeniden": False}
    return {"adet": len(hedefler), "yeniden": False}


def _ortak_giris_isci(ortak):
    while not ortak["dur"].is_set():
        try:
            islem = ortak["giris"].get(timeout=0.5)
        except queue.Empty:
            continue
        if islem is None or ortak["dur"].is_set():
            break
        paket, baslik, mime = islem
        try:
            _encoder_yaz(ortak, paket, baslik, mime)
        except Exception:
            _encoder_hata(ortak, "Kodlayıcı koptu")


def _encoder_yaz(ortak, paket, baslik, mime):
    if baslik and not _ebml_mi(paket):
        with ortak["kilit"]:
            proc = ortak.get("proc")
            calisiyor = proc is not None and proc.poll() is None
        if not calisiyor:
            _ortak_hedef_durumu(ortak, "bekliyor", "")
        return
    if baslik:
        _ortak_hatalari_sil(ortak)
    on_ek = b""
    baslik_ek, baslik_mime = (b"", "")
    if not baslik:
        baslik_ek, baslik_mime = _baslik_kopya()
    kapanan = None
    eski_kopyalar = []
    durdu = False
    yeni = False
    nesil = 0
    metin = "Kodlayıcı koptu"
    with ortak["kilit"]:
        if ortak["dur"].is_set():
            return
        proc = ortak.get("proc")
        if proc is not None and proc.poll() is not None:
            ortak["proc"] = None
            kapanan = proc
            proc = None
            durdu = not baslik
            if durdu:
                metin = (ortak.get("son_hata") or ortak.get("son_satir") or "Kodlayıcı koptu")
        elif proc is not None and baslik:
            ortak["proc"] = None
            kapanan = proc
            proc = None
            eski_kopyalar = list(ortak["kopyalar"].values())
            ortak["kopyalar"].clear()
        if proc is None and not durdu:
            if not baslik:
                if not baslik_ek:
                    durdu = "bekliyor"
                else:
                    on_ek = baslik_ek
                    mime = mime or baslik_mime
            elif not _ebml_mi(paket):
                durdu = "bekliyor"
            if proc is None and durdu is not True and durdu != "bekliyor":
                if ortak["kopyalar"]:
                    eski_kopyalar.extend(list(ortak["kopyalar"].values()))
                    ortak["kopyalar"].clear()
                proc = _encoder_ac(mime)
                if proc is None:
                    durdu = "acilamadi"
                else:
                    ortak["proc"] = proc
                    ortak["nesil"] = int(ortak.get("nesil") or 0) + 1
                    nesil = ortak["nesil"]
                    yeni = True
                    olay = threading.Event()
                    ortak["stderr_bitti"] = olay
                    ortak["son_hata"] = ""
                    ortak["son_satir"] = ""
                    threading.Thread(target=_cikis_isci, args=(ortak, proc, nesil), daemon=True).start()
                    threading.Thread(target=_stderr_ortak, args=(ortak, proc, olay), daemon=True).start()
        elif proc is not None:
            nesil = ortak["nesil"]
        stdin = proc.stdin if proc is not None else None
    if kapanan is not None:
        _proc_kapat(kapanan)
    for kopya in eski_kopyalar:
        _kopya_sonlandir(kopya)
    if durdu is True:
        print(f"[SOSYAL HAVUZ] kodlayici kapandi: {metin}", flush=True)
        _ortak_hedef_durumu(ortak, "hata", "")
        return
    if durdu == "bekliyor":
        _ortak_hedef_durumu(ortak, "bekliyor", "")
        return
    if durdu == "acilamadi":
        _ortak_hedef_durumu(ortak, "hata", "FFmpeg açılamadı")
        return
    if not yeni:
        _kopyalari_ac(ortak, False)
    try:
        if on_ek:
            _stdin_yaz(stdin, on_ek)
        _stdin_yaz(stdin, paket)
    except Exception:
        _encoder_hata(ortak, "Kodlayıcı koptu", nesil)


def _encoder_ac(mime):
    komut = [
        _ffmpeg_yolu(),
        *_giris_argumanlari(mime),
        *_kod_argumanlari(),
        "-f",
        "mpegts",
        "-muxdelay",
        "0",
        "-muxpreload",
        "0",
        "pipe:1",
    ]
    try:
        return _surec_baslat(komut, subprocess.PIPE)
    except Exception as hata:
        print(f"[SOSYAL HAVUZ] ortak ffmpeg acilamadi: {hata}", flush=True)
        return None


def _kopyalari_ac(ortak, zorla=False):
    with ortak["kilit"]:
        istenen = dict(ortak.get("hedefler") or {})
        mevcut = dict(ortak.get("kopyalar") or {})
    for kim, hedef in istenen.items():
        slot = _SLOTLAR.get(kim)
        if not zorla and slot is not None and slot.get("durum") == "hata":
            continue
        kopya = mevcut.get(kim)
        proc = kopya.get("proc") if kopya is not None else None
        if (
            kopya is not None
            and not kopya["dur"].is_set()
            and kopya.get("hedef") == hedef
            and proc is not None
            and proc.poll() is None
        ):
            continue
        _kopya_yenile(ortak, kim, hedef)


def _kopya_yenile(ortak, kim, hedef):
    eski = None
    with ortak["kilit"]:
        eski = ortak["kopyalar"].pop(kim, None)
    if eski is not None:
        _kopya_sonlandir(eski)
    if not (str(hedef).startswith("rtmp://") or str(hedef).startswith("rtmps://")):
        _slot_durum(kim, "hata", "Geçersiz yayın adresi")
        return
    komut = [
        _ffmpeg_yolu(),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts+igndts",
        "-f",
        "mpegts",
        "-probesize",
        "32768",
        "-analyzeduration",
        "15000000",
        "-i",
        "pipe:0",
        "-c",
        "copy",
        *_kopya_cikis_bayraklari(hedef),
    ]
    try:
        proc = _surec_baslat(komut, subprocess.DEVNULL)
    except Exception as hata:
        print(f"[SOSYAL HAVUZ] kopya ffmpeg acilamadi: {hata}", flush=True)
        _slot_durum(kim, "hata", "FFmpeg açılamadı")
        return
    kopya = {
        "id": kim,
        "hedef": hedef,
        "proc": proc,
        "kuyruk": queue.Queue(maxsize=24),
        "dur": threading.Event(),
        "son_satir": "",
        "stderr_bitti": threading.Event(),
    }
    threading.Thread(target=_stderr_kopya, args=(kopya, proc), daemon=True).start()
    threading.Thread(target=_kopya_isci, args=(ortak, kopya), daemon=True).start()
    with ortak["kilit"]:
        if ortak["dur"].is_set() or ortak["hedefler"].get(kim) != hedef:
            _kopya_sonlandir(kopya)
            return
        ortak["kopyalar"][kim] = kopya
    _slot_durum(kim, "bekliyor", "")


def _kopya_isci(ortak, kopya):
    while not kopya["dur"].is_set() and not ortak["dur"].is_set():
        try:
            blok = kopya["kuyruk"].get(timeout=0.5)
        except queue.Empty:
            proc = kopya.get("proc")
            if proc is not None and proc.poll() is not None:
                _kopya_hata(ortak, kopya, kopya.get("son_satir") or "Yayın koptu")
                return
            continue
        if blok is None or kopya["dur"].is_set():
            return
        proc = kopya.get("proc")
        if proc is None or proc.stdin is None:
            return
        try:
            _stdin_yaz(proc.stdin, blok)
        except Exception:
            _kopya_hata(ortak, kopya, kopya.get("son_satir") or "Yayın koptu")
            return
        _slot_durum(kopya["id"], "yayinda", "")


def _cikis_isci(ortak, proc, nesil):
    try:
        while not ortak["dur"].is_set():
            with ortak["kilit"]:
                if ortak.get("nesil") != nesil or ortak.get("proc") is not proc:
                    return
            blok = proc.stdout.read(32768)
            if not blok:
                break
            with ortak["kilit"]:
                if ortak.get("nesil") != nesil:
                    return
                ilk_paket = ortak.get("beslenen_nesil") != nesil
            if ilk_paket:
                _kopyalari_ac(ortak, True)
                with ortak["kilit"]:
                    if ortak.get("nesil") != nesil:
                        return
                    ortak["beslenen_nesil"] = nesil
            with ortak["kilit"]:
                if ortak.get("nesil") != nesil:
                    return
                kopyalar = [kopya for kopya in ortak["kopyalar"].values() if not kopya["dur"].is_set()]
            for kopya in kopyalar:
                try:
                    kopya["kuyruk"].put_nowait(blok)
                except queue.Full:
                    _kopya_hata(ortak, kopya, "Hedef yanıt vermedi")
    except Exception:
        return
    if ortak["dur"].is_set():
        return
    with ortak["kilit"]:
        gecerli = ortak.get("proc") is proc and ortak.get("nesil") == nesil
        if gecerli:
            ortak["proc"] = None
    if gecerli:
        _encoder_hata(ortak, "Kodlayıcı koptu", nesil)


def _kopya_hata(ortak, kopya, metin):
    if kopya is None or kopya["dur"].is_set():
        return
    with ortak["kilit"]:
        if ortak["kopyalar"].get(kopya["id"]) is kopya:
            ortak["kopyalar"].pop(kopya["id"], None)
    _kopya_sonlandir(kopya)
    _kopya_sonucu_yaz(kopya, metin)


def _kopya_satiri(kopya):
    if kopya is None:
        return ""
    olay = kopya.get("stderr_bitti")
    proc = kopya.get("proc")
    if olay is not None and (proc is None or proc.poll() is not None):
        olay.wait(timeout=0.8)
    return (kopya.get("son_hata") or kopya.get("son_satir") or "").strip()


def _kopya_saglam(kopya):
    if kopya is None or kopya["dur"].is_set():
        return False
    proc = kopya.get("proc")
    return proc is not None and proc.poll() is None


def _kopya_sonucu_yaz(kopya, yedek=""):
    """Yalnız bu kopyanın stderr satırı yazılır. Sağlam hedeflere dokunulmaz."""
    goster = _kopya_satiri(kopya) or str(yedek or "").strip()
    if not goster:
        _slot_durum(kopya["id"], "bekliyor", "")
        return
    print(f"[SOSYAL HAVUZ] hedef koptu: {goster}", flush=True)
    _slot_durum(kopya["id"], "hata", goster)


def _kopya_sonlandir(kopya):
    if kopya is None:
        return
    kopya["dur"].set()
    try:
        kopya["kuyruk"].put_nowait(None)
    except queue.Full:
        pass
    _proc_kapat(kopya.get("proc"))
    kopya["proc"] = None


def _encoder_hata(ortak, metin, nesil=None):
    with ortak["kilit"]:
        if ortak["dur"].is_set():
            return
        if nesil is not None and ortak.get("nesil") != nesil:
            return
        proc = ortak.get("proc")
        ortak["proc"] = None
        kopyalar = list(ortak["kopyalar"].values())
        ortak["kopyalar"].clear()
    bitti = None
    with ortak["kilit"]:
        bitti = ortak.get("stderr_bitti")
    _proc_kapat(proc)
    if bitti is not None:
        bitti.wait(timeout=1)
    with ortak["kilit"]:
        gercek = (ortak.get("son_hata") or ortak.get("son_satir") or "").strip()
    goster = gercek or metin or "Kodlayıcı koptu"
    print(f"[SOSYAL HAVUZ] kodlayici kapandi: {goster}", flush=True)
    ayrilan = []
    for kopya in kopyalar:
        saglam = _kopya_saglam(kopya)
        kopya["dur"].set()
        ayrilan.append((kopya, saglam))
    for kopya, saglam in ayrilan:
        _kopya_sonlandir(kopya)
        if saglam:
            _slot_durum(kopya["id"], "bekliyor", "")
            continue
        _kopya_sonucu_yaz(kopya, "")


def _ortak_hatalari_sil(ortak):
    with ortak["kilit"]:
        kimler = list(ortak.get("hedefler") or {})
    for kim in kimler:
        slot = _SLOTLAR.get(kim)
        if slot is None:
            continue
        with slot["kilit"]:
            if slot.get("durum") == "hata":
                slot["durum"] = "bekliyor"
                slot["hata"] = ""


def _ortak_hedef_durumu(ortak, durum, metin):
    with ortak["kilit"]:
        kimler = list(ortak.get("hedefler") or {})
        kopyalar = dict(ortak.get("kopyalar") or {})
    if durum == "hata":
        for kim in kimler:
            kopya = kopyalar.get(kim)
            if _kopya_saglam(kopya):
                continue
            ozel = _kopya_satiri(kopya) if kopya is not None else ""
            if ozel:
                _slot_durum(kim, "hata", ozel)
            elif kopya is not None:
                _slot_durum(kim, "bekliyor", "")
            elif metin == "FFmpeg açılamadı":
                _slot_durum(kim, "hata", metin)
        return
    for kim in kimler:
        if durum == "bekliyor":
            if _kopya_saglam(kopyalar.get(kim)):
                continue
            slot = _SLOTLAR.get(kim)
            if slot is not None and slot.get("durum") == "hata":
                continue
        _slot_durum(kim, durum, metin)


def _slot_durum(kim, durum, hata):
    slot = _SLOTLAR.get(kim)
    if slot is None:
        return
    with slot["kilit"]:
        if slot["dur"].is_set():
            return
        slot["durum"] = durum
        slot["hata"] = _maske(hata)[:240] if hata else ""


def _stderr_ortak(ortak, proc, bitti=None):
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
            with ortak["kilit"]:
                ortak["son_satir"] = metin[:240]
                if _onemli_satir(metin):
                    ortak["son_hata"] = metin[:240]
                    print(f"[SOSYAL HAVUZ] ffmpeg: {metin[:240]}", flush=True)
    except Exception:
        return
    finally:
        if bitti is not None:
            bitti.set()


def _stderr_kopya(kopya, proc):
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
            kopya["son_satir"] = metin[:240]
            if _onemli_satir(metin):
                kopya["son_hata"] = metin[:240]
                print(f"[SOSYAL HAVUZ] hedef: {metin[:240]}", flush=True)
    except Exception:
        return
    finally:
        olay = kopya.get("stderr_bitti")
        if olay is not None:
            olay.set()


def _surec_baslat(komut, stdout):
    ayar = {
        "stdin": subprocess.PIPE,
        "stdout": stdout,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        bayrak = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if bayrak:
            ayar["creationflags"] = bayrak
    return subprocess.Popen(komut, **ayar)


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
    depo = _depo_oku()
    if depo is not None and depo.get("kayitli"):
        return depo["hesaplar"]
    return _json_oku()


def _yaz(hesaplar):
    if _depo_yaz(hesaplar):
        return
    _json_yaz(hesaplar)


def _depo_oku():
    if not callable(_KALICI_OKU):
        return None
    try:
        gelen = _KALICI_OKU()
    except Exception as hata:
        print(f"[SOSYAL HAVUZ] depo okunamadi: {hata}", flush=True)
        return None
    if gelen is None:
        return None
    if isinstance(gelen, dict):
        kayitli = bool(gelen.get("kayitli"))
        satirlar = gelen.get("hesaplar")
    elif isinstance(gelen, list):
        kayitli = True
        satirlar = gelen
    else:
        return None
    if not isinstance(satirlar, list):
        return {"kayitli": kayitli, "hesaplar": []}
    return {"kayitli": kayitli, "hesaplar": _liste_temizle(satirlar)}


def _depo_yaz(hesaplar):
    if not callable(_KALICI_YAZ):
        return False
    try:
        return bool(_KALICI_YAZ(hesaplar))
    except Exception as hata:
        print(f"[SOSYAL HAVUZ] depo yazilamadi: {hata}", flush=True)
        return False


def _json_oku():
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
    return _liste_temizle(satirlar)


def _json_yaz(hesaplar):
    klasor = os.path.dirname(_DOSYA)
    os.makedirs(klasor, exist_ok=True)
    gecici = _DOSYA + ".tmp"
    with open(gecici, "w", encoding="utf-8") as dosya:
        json.dump({"hesaplar": hesaplar}, dosya, ensure_ascii=False, indent=2)
        dosya.flush()
        os.fsync(dosya.fileno())
    os.replace(gecici, _DOSYA)


def _liste_temizle(satirlar):
    cikti = []
    for ham_kayit in satirlar:
        kayit = _kayit_temizle(ham_kayit)
        if kayit:
            cikti.append(kayit)
    return cikti


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
