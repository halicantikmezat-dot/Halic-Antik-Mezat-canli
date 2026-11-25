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
import time

_DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "sosyal_yayin_havuzu.json")
_KILIT = threading.RLock()
_YAYIN = False
_SLOTLAR = {}
_FFMPEG = ""
_BASLIK = b""
_MIME = ""
_YENIDEN = False
_PARCA_SAYISI = 0
_UST_SINIR = 100
_KALICI_OKU = None
_KALICI_YAZ = None
_ORTAK = None
_ORTAK_KILIT = threading.Lock()

_OLUMCUL = (
    "error opening",
    "cannot open",
    "connection refused",
    "connection timed out",
    "timed out",
    "i/o error",
    "protocol not found",
    "handshake",
    "server error",
    "broken pipe",
)
_BEKLEME_SN = 20


def _olumcul_mu(metin):
    satir = str(metin or "").lower()
    return any(iz in satir for iz in _OLUMCUL)


_URL = re.compile(
    r"^(rtmps?)://([A-Za-z0-9._-]{1,253})(?::\d{2,5})?(/[^\s\"'\\]{0,420})?$",
    re.IGNORECASE,
)
_LINK = re.compile(r"^https?://[^\s\"'\\]{4,490}$", re.IGNORECASE)
_TURLER = (
    "youtube",
    "instagram",
    "facebook",
    "tiktok",
    "kick",
    "twitch",
    "x",
    "telegram",
    "restream",
    "ozel",
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
    global _YAYIN, _BASLIK, _MIME, _YENIDEN, _PARCA_SAYISI
    acik = bool(acik)
    _ortak_kapat()
    with _KILIT:
        _YAYIN = acik
        _YENIDEN = False
        _PARCA_SAYISI = 0
        if not acik:
            _BASLIK = b""
            _MIME = ""
            slotlar = list(_SLOTLAR.values())
            _SLOTLAR.clear()
        else:
            slotlar = []
    for slot in slotlar:
        _slot_sonlandir(slot)


def _ebml_mi(paket):
    veri = bytes(paket or b"")
    return len(veri) >= 4 and veri[:4] == b"\x1a\x45\xdf\xa3"


def _gecersiz_girdi_mi(metin):
    return "invalid data" in str(metin or "").lower()


def _yeniden_iste(metin):
    """Demuxer pipe girdisini reddederse tarayıcıdan bir kez yeni kayıt istenir."""
    global _YENIDEN
    if not _gecersiz_girdi_mi(metin):
        return
    with _KILIT:
        _YENIDEN = True


def _parca_say(adet):
    global _PARCA_SAYISI
    if not adet:
        return
    with _KILIT:
        _PARCA_SAYISI += 1


def _yeniden_yanit(adet, yeniden, hedef_var):
    if yeniden and hedef_var and not adet:
        adet = 1
    return {"adet": adet, "yeniden": bool(yeniden and hedef_var)}


def parca_yaz(paket, baslik, mime):
    """Aynı medya parçasını işaretli her hesaba ayrı süreçle dağıtır.

    Girdi düzeltmesi yalnız Diğer kapısı (_YAYIN) açıkken çalışır.
    """
    global _BASLIK, _MIME, _YENIDEN
    if not paket:
        return {"adet": 0, "yeniden": False}
    paket = bytes(paket)
    with _KILIT:
        if not _YAYIN:
            return {"adet": 0, "yeniden": False}
        yeniden = bool(_YENIDEN)
        _YENIDEN = False
        if _ebml_mi(paket):
            baslik = True
            _BASLIK = paket
            _MIME = str(mime or "")
        elif not _BASLIK:
            return {"adet": 0, "yeniden": False}
        else:
            baslik = False
        hesaplar = [dict(kayit) for kayit in _oku() if kayit.get("dahil")]
    hedefler = []
    for hesap in hesaplar:
        hedef = _hedef_url(hesap)
        if hedef:
            hedefler.append({"id": hesap["id"], "hedef": hedef})
    if yeniden and not baslik:
        return _yeniden_yanit(0, True, bool(hedefler))
    if len(hedefler) >= 2:
        sonuc = _ortak_dagit(hedefler, paket, baslik, mime)
        _parca_say(sonuc.get("adet") if isinstance(sonuc, dict) else 0)
        return sonuc
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
            slot["kuyruk"].put_nowait((paket, bool(baslik), str(mime or "")))
            adet += 1
        except queue.Full:
            with slot["kilit"]:
                slot["durum"] = "hata"
                slot["hata"] = "Hedef yanıt vermedi"
                _proc_birak(slot)
    _parca_say(adet)
    return _yeniden_yanit(adet, False, bool(hedefler))


def api(islem, veri):
    veri = veri if isinstance(veri, dict) else {}
    if islem == "sosyal_havuz_liste":
        return {"success": True, "hesaplar": liste()}
    if islem == "sosyal_havuz_ekle":
        return _ekle(veri)
    if islem == "sosyal_havuz_guncelle":
        return _guncelle(veri)
    if islem == "sosyal_havuz_sil":
        return _sil(veri.get("id"))
    if islem == "sosyal_havuz_dahil":
        return _dahil(veri.get("id"), veri.get("dahil"))
    return {"success": False, "mesaj": "Geçersiz havuz işlemi."}


def liste():
    with _KILIT:
        yayin = _YAYIN
        parca_sayisi = _PARCA_SAYISI
        hesaplar = _oku()
        slotlar = {
            kim: {
                "durum": slot.get("durum") or "bekliyor",
                "hata": slot.get("hata") or slot.get("son_satir") or "",
            }
            for kim, slot in _SLOTLAR.items()
        }
    cikti = []
    for kayit in hesaplar:
        durum = "pasif"
        hata = ""
        parca_notu = ""
        if kayit.get("dahil") and yayin:
            parca_notu = "Parça geldi" if parca_sayisi else "Parça bekleniyor"
            slot = slotlar.get(kayit["id"])
            if slot is None or slot["durum"] == "pasif":
                durum = "bekliyor"
            else:
                durum = slot["durum"] if slot["durum"] in ("bekliyor", "yayinda", "hata") else "bekliyor"
                hata = slot["hata"] if durum == "hata" else ""
                if durum == "bekliyor" and _olumcul_mu(slot.get("hata")):
                    durum = "hata"
                    hata = slot["hata"]
        cikti.append(
            {
                "id": kayit["id"],
                "ad": kayit["ad"],
                "tur": kayit.get("tur") or "ozel",
                "sayfa_linki": kayit.get("sayfa_linki") or "",
                "rtmp_url": kayit["rtmp_url"],
                "stream_key": kayit["stream_key"],
                "dahil": bool(kayit.get("dahil")),
                "durum": durum,
                "hata": hata,
                "parca_notu": parca_notu,
            }
        )
    return cikti


def restream_satiri_emin_ol(url, anahtar):
    """Eski tek Restream hedefini havuza bir kez taşır. Aynı adres varsa eklemez."""
    temiz_url = _url_temizle(url)
    temiz_anahtar = _anahtar_temizle(anahtar)
    if not temiz_url or not temiz_anahtar:
        return False
    with _KILIT:
        hesaplar = _oku()
        for kayit in hesaplar:
            if kayit.get("rtmp_url") == temiz_url and kayit.get("stream_key") == temiz_anahtar:
                return False
        if len(hesaplar) >= _UST_SINIR:
            return False
        hesaplar.append(
            {
                "id": _yeni_id(hesaplar),
                "ad": "Restream",
                "tur": "restream",
                "sayfa_linki": "",
                "rtmp_url": temiz_url,
                "stream_key": temiz_anahtar,
                "dahil": True,
            }
        )
        _yaz(hesaplar)
    return True


def _ekle(veri):
    veri = veri if isinstance(veri, dict) else {}
    temiz_ad = _ad_temizle(veri.get("ad"))
    temiz_url = _url_temizle(veri.get("rtmp_url"))
    temiz_anahtar = _anahtar_temizle(veri.get("stream_key"))
    link_ham = str(veri.get("sayfa_linki") or "").strip()
    temiz_link = _link_temizle(link_ham)
    if not temiz_ad:
        return {"success": False, "mesaj": "Sayfa adı gerekli."}
    if not temiz_url:
        return {"success": False, "mesaj": "Geçerli bir RTMP sunucu URL'si girin."}
    if not temiz_anahtar:
        return {"success": False, "mesaj": "Yayın anahtarı geçersiz."}
    if link_ham and not temiz_link:
        return {"success": False, "mesaj": "Sayfa linki https ile başlamalı."}
    with _KILIT:
        hesaplar = _oku()
        if len(hesaplar) >= _UST_SINIR:
            return {"success": False, "mesaj": "Havuz üst sınıra ulaştı."}
        hesaplar.append(
            {
                "id": _yeni_id(hesaplar),
                "ad": temiz_ad,
                "tur": _tur_temizle(veri.get("tur")),
                "sayfa_linki": temiz_link,
                "rtmp_url": temiz_url,
                "stream_key": temiz_anahtar,
                "dahil": False,
            }
        )
        _yaz(hesaplar)
    return {"success": True, "mesaj": "Platform listeye eklendi.", "hesaplar": liste()}


def _guncelle(veri):
    veri = veri if isinstance(veri, dict) else {}
    kim = _kimlik(veri.get("id"))
    if not kim:
        return {"success": False, "mesaj": "Hesap bulunamadı."}
    if "stream_key" in veri and not _anahtar_temizle(veri.get("stream_key")):
        return {"success": False, "mesaj": "Yayın anahtarı geçersiz."}
    link_ham = str(veri.get("sayfa_linki") or "").strip() if "sayfa_linki" in veri else ""
    if "sayfa_linki" in veri and link_ham and not _link_temizle(link_ham):
        return {"success": False, "mesaj": "Sayfa linki https ile başlamalı."}
    with _KILIT:
        hesaplar = _oku()
        bulunan = None
        eski_hedef = ""
        for kayit in hesaplar:
            if kayit["id"] != kim:
                continue
            eski_hedef = _hedef_url(kayit)
            if "stream_key" in veri:
                kayit["stream_key"] = _anahtar_temizle(veri.get("stream_key"))
            if "tur" in veri:
                kayit["tur"] = _tur_temizle(veri.get("tur"))
            if "sayfa_linki" in veri:
                kayit["sayfa_linki"] = _link_temizle(link_ham)
            if "ad" in veri:
                ad = _ad_temizle(veri.get("ad"))
                if not ad:
                    return {"success": False, "mesaj": "Sayfa adı gerekli."}
                kayit["ad"] = ad
            if "rtmp_url" in veri:
                url = _url_temizle(veri.get("rtmp_url"))
                if not url:
                    return {"success": False, "mesaj": "Geçerli bir RTMP sunucu URL'si girin."}
                kayit["rtmp_url"] = url
            bulunan = kayit
            break
        if bulunan is None:
            return {"success": False, "mesaj": "Hesap bulunamadı."}
        yeni_hedef = _hedef_url(bulunan)
        _yaz(hesaplar)
        slot = _SLOTLAR.get(kim)
    if slot is not None and yeni_hedef != eski_hedef:
        with slot["kilit"]:
            slot["hedef"] = yeni_hedef
            slot["nesil"] = slot.get("nesil", 0) + 1
            slot["durum"] = "bekliyor" if _YAYIN and bulunan.get("dahil") else "pasif"
            slot["hata"] = ""
            _proc_birak(slot)
    return {"success": True, "mesaj": "Platform güncellendi.", "hesaplar": liste()}


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
            if not _bekleyis_doldu(slot):
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
    if _ebml_mi(paket):
        baslik = True
    nesil = 0
    on_ek = b""
    stdin = None
    proc = None
    yeniden_metin = ""
    atla = False
    with slot["kilit"]:
        if slot.get("ortak") or slot["dur"].is_set():
            atla = True
        else:
            nesil = slot.get("nesil", 0)
            proc = slot.get("proc")
            if proc is not None and proc.poll() is not None:
                yeniden_metin = slot.get("son_satir") or ""
                _proc_birak(slot)
                proc = None
                if not baslik:
                    _hata_kilit_icinde(slot, yeniden_metin or "Yayın koptu")
                    atla = True
            if not atla and proc is not None and baslik:
                _proc_birak(slot)
                proc = None
            if not atla and proc is None:
                if baslik and not _ebml_mi(paket):
                    slot["durum"] = "bekliyor"
                    atla = True
                elif not baslik:
                    on_ek, on_mime = _baslik_kopya()
                    if not on_ek or not _ebml_mi(on_ek):
                        slot["durum"] = "bekliyor"
                        atla = True
                    else:
                        mime = mime or on_mime
                if not atla:
                    proc = _surec_ac(slot.get("hedef") or "", mime, slot)
                    if proc is None:
                        _hata_kilit_icinde(slot, "FFmpeg açılamadı")
                        atla = True
                    else:
                        slot["proc"] = proc
                        slot["acilis"] = time.monotonic()
            elif not atla:
                on_ek = b""
            if not atla and proc is not None:
                stdin = proc.stdin
    if yeniden_metin:
        _yeniden_iste(yeniden_metin)
    if atla or stdin is None:
        return
    try:
        if on_ek:
            _stdin_yaz(stdin, on_ek)
        _stdin_yaz(stdin, paket)
    except Exception:
        metin = slot.get("son_satir") or "Yayın koptu"
        _hata_isaretle(slot, metin, nesil)
        _yeniden_iste(metin)
        return
    with slot["kilit"]:
        if slot.get("proc") is proc and not slot["dur"].is_set():
            if baslik or slot.get("durum") != "hata":
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
        if _BASLIK and not _ebml_mi(_BASLIK):
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


def _bekleyis_doldu(slot):
    """RTMPS el sıkışması takılırsa rozet Bekliyor'da kalmasın."""
    with slot["kilit"]:
        if slot.get("durum") != "bekliyor" or slot["dur"].is_set():
            return False
        proc = slot.get("proc")
        acilis = slot.get("acilis") or 0
        if proc is None or proc.poll() is not None or acilis <= 0:
            return False
        if time.monotonic() - acilis < _BEKLEME_SN:
            return False
    _hata_isaretle(slot, slot.get("son_satir") or "RTMP bağlantısı zaman aşımı")
    return True


def _proc_dustu_mu(slot):
    metin = ""
    with slot["kilit"]:
        proc = slot.get("proc")
        if proc is None or proc.poll() is None:
            return
        metin = slot.get("son_satir") or "Yayın koptu"
        _proc_birak(slot)
        _hata_kilit_icinde(slot, metin)
    _yeniden_iste(metin)


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


def _giris_bicimi(mime):
    """Boş MIME WebM sayılmaz. WebM canlı akış matroska olarak okunur."""
    metin = str(mime or "").strip().lower()
    if not metin:
        return ""
    if "mp4" in metin:
        return "mp4"
    if "webm" in metin or "matroska" in metin:
        return "matroska"
    return ""


def _giris_argumanlari(mime):
    arg = [
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts",
        "-probesize",
        "5000000",
        "-analyzeduration",
        "5000000",
    ]
    bicim = _giris_bicimi(mime)
    if bicim:
        arg.extend(["-f", bicim])
    arg.extend(["-i", "pipe:0"])
    return arg


def _surec_ac(hedef, mime, slot):
    if not (str(hedef).startswith("rtmp://") or str(hedef).startswith("rtmps://")):
        return None
    komut = [
        _ffmpeg_yolu(),
        *_giris_argumanlari(mime),
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
        *_cikis_bayraklari(hedef),
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
            olumcul = False
            with slot["kilit"]:
                slot["son_satir"] = metin[:180]
                olumcul = slot.get("durum") != "hata" and _olumcul_mu(metin)
            _yeniden_iste(metin)
            if olumcul:
                _hata_isaretle(slot, metin[:120])
                return
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
    if _ebml_mi(paket):
        baslik = True
    if baslik:
        _ortak_hatalari_sil(ortak)
    on_ek = b""
    baslik_ek, baslik_mime = (b"", "")
    if not baslik:
        baslik_ek, baslik_mime = _baslik_kopya()
        if baslik_ek and not _ebml_mi(baslik_ek):
            baslik_ek, baslik_mime = b"", ""
    kapanan = None
    durdu = False
    nesil = 0
    metin = "Kodlayıcı koptu"
    yeniden_metin = ""
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
                metin = ortak.get("son_satir") or "Kodlayıcı koptu"
                yeniden_metin = ortak.get("son_satir") or ""
        if proc is not None and baslik:
            kapanan = proc
            ortak["proc"] = None
            proc = None
        if proc is None and not durdu:
            if baslik and not _ebml_mi(paket):
                durdu = "bekliyor"
            elif not baslik:
                if not baslik_ek:
                    durdu = "bekliyor"
                else:
                    on_ek = baslik_ek
                    mime = mime or baslik_mime
            if proc is None and durdu is not True and durdu != "bekliyor":
                proc = _encoder_ac(mime)
                if proc is None:
                    durdu = "acilamadi"
                else:
                    ortak["proc"] = proc
                    ortak["nesil"] = int(ortak.get("nesil") or 0) + 1
                    nesil = ortak["nesil"]
                    threading.Thread(target=_cikis_isci, args=(ortak, proc, nesil), daemon=True).start()
                    threading.Thread(target=_stderr_ortak, args=(ortak, proc), daemon=True).start()
        elif proc is not None:
            nesil = ortak["nesil"]
        stdin = proc.stdin if proc is not None else None
    if kapanan is not None:
        _proc_kapat(kapanan)
    if yeniden_metin:
        _yeniden_iste(yeniden_metin)
    if durdu is True:
        _ortak_hedef_durumu(ortak, "hata", metin)
        return
    if durdu == "bekliyor":
        _ortak_hedef_durumu(ortak, "bekliyor", "")
        return
    if durdu == "acilamadi":
        _ortak_hedef_durumu(ortak, "hata", "FFmpeg açılamadı")
        return
    _kopyalari_ac(ortak)
    try:
        if on_ek:
            _stdin_yaz(stdin, on_ek)
        _stdin_yaz(stdin, paket)
    except Exception:
        with ortak["kilit"]:
            metin = ortak.get("son_satir") or "Kodlayıcı koptu"
        _yeniden_iste(metin)
        _encoder_hata(ortak, metin if _gecersiz_girdi_mi(metin) else "Kodlayıcı koptu", nesil)


def _encoder_ac(mime):
    komut = [
        _ffmpeg_yolu(),
        *_giris_argumanlari(mime),
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


def _kopyalari_ac(ortak):
    with ortak["kilit"]:
        istenen = dict(ortak.get("hedefler") or {})
        mevcut = dict(ortak.get("kopyalar") or {})
    for kim, hedef in istenen.items():
        slot = _SLOTLAR.get(kim)
        if slot is not None and slot.get("durum") == "hata":
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
        "+genpts+discardcorrupt",
        "-f",
        "mpegts",
        "-i",
        "pipe:0",
        "-c",
        "copy",
        *_cikis_bayraklari(hedef),
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
        "acilis": time.monotonic(),
    }
    threading.Thread(target=_stderr_kopya, args=(ortak, kopya, proc), daemon=True).start()
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
            acilis = kopya.get("acilis") or 0
            if acilis and time.monotonic() - acilis >= _BEKLEME_SN:
                slot = _SLOTLAR.get(kopya["id"])
                if slot is None or slot.get("durum") == "bekliyor":
                    _kopya_hata(ortak, kopya, kopya.get("son_satir") or "RTMP bağlantısı zaman aşımı")
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
        slot = _SLOTLAR.get(kopya["id"])
        if slot is None or slot.get("durum") != "hata":
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
    _slot_durum(kopya["id"], "hata", metin)


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
    _proc_kapat(proc)
    for kopya in kopyalar:
        _kopya_sonlandir(kopya)
    _ortak_hedef_durumu(ortak, "hata", metin)


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
    for kim in kimler:
        _slot_durum(kim, durum, metin)


def _slot_durum(kim, durum, hata):
    slot = _SLOTLAR.get(kim)
    if slot is None:
        return
    with slot["kilit"]:
        if slot["dur"].is_set():
            return
        slot["durum"] = durum
        slot["hata"] = _maske(hata)[:120] if hata else ""


def _stderr_ortak(ortak, proc):
    try:
        akis = proc.stderr
        if akis is None:
            return
        for satir in iter(akis.readline, b""):
            if not satir:
                break
            metin = _maske(satir.decode("utf-8", "replace")).strip()
            if metin:
                with ortak["kilit"]:
                    ortak["son_satir"] = metin[:180]
                _yeniden_iste(metin)
    except Exception:
        return


def _stderr_kopya(ortak, kopya, proc):
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
            kopya["son_satir"] = metin[:180]
            if _olumcul_mu(metin):
                _kopya_hata(ortak, kopya, metin[:120])
                return
    except Exception:
        return


def _cikis_bayraklari(hedef):
    """rtmps hedeflerinde sertifika takılması yayın rozetini bekletmesin."""
    bayrak = ["-f", "flv", "-flvflags", "no_duration_filesize"]
    if str(hedef).startswith("rtmps://"):
        bayrak.extend(["-rw_timeout", "15000000", "-tls_verify", "0"])
    bayrak.append(str(hedef))
    return bayrak


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
        "tur": _tur_temizle(ham.get("tur")),
        "sayfa_linki": _link_temizle(ham.get("sayfa_linki")),
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


def _tur_temizle(deger):
    tur = str(deger or "").strip().lower()
    if tur == "twitter":
        tur = "x"
    if tur in _TURLER:
        return tur
    return "ozel"


def _link_temizle(deger):
    adres = str(deger or "").strip()
    if not adres or not _LINK.fullmatch(adres):
        return ""
    return adres[:500]


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
