"""Diğer odası için çoklu sosyal yorum toplayıcı.

Yalnızca aktif_dukkan diger iken çalışır. RTMP havuzuna yazmaz.
YouTube ve TikTok hesap başına ağ işçisidir. Facebook ve Instagram
önce hafif HTTP dener. Tarayıcı gerekirse tek Chrome içinde sekme açar.
Sayfa linki boş veya geçersizse satır sessizce atlanır.
"""

import hashlib
import re
import threading
import time
from collections import deque
from urllib.parse import urlparse

_KILIT = threading.RLock()
_ACIK = False
_DUR = threading.Event()
_GOZCU = None
_ISLER = {}
_GORULEN = {}
_TARAYICI = None
_TARAYICI_DENENDI = 0.0
_TARAYICI_YOK = False
_TUR = ("youtube", "tiktok", "facebook", "instagram")
_HTTP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_SEKME_JS = """
const cikti = [];
const gor = new Set();
function ekle(kullanici, mesaj) {
  const ad = String(kullanici || '').trim().slice(0, 80);
  const metin = String(mesaj || '').replace(/\\s+/g, ' ').trim().slice(0, 500);
  if (!ad || !metin || metin.length < 1) return;
  const anahtar = ad.toLowerCase() + '|' + metin.toLowerCase();
  if (gor.has(anahtar)) return;
  gor.add(anahtar);
  cikti.push({kullanici: ad, mesaj: metin});
}
document.querySelectorAll("div[role='article']").forEach(function (blok) {
  const ham = (blok.innerText || '').split(/\\n+/).map(function (s) { return s.trim(); }).filter(Boolean);
  if (ham.length >= 2) ekle(ham[0], ham.slice(1, 4).join(' '));
});
document.querySelectorAll('*').forEach(function (el) {
  if (el.children.length) return;
  if ((el.innerText || '').trim().toLowerCase() !== 'yanıtla') return;
  let blok = el.parentElement;
  for (let i = 0; i < 4 && blok && blok.parentElement; i++) blok = blok.parentElement;
  const ham = ((blok && blok.innerText) || '').split(/\\n+/).map(function (s) { return s.trim(); }).filter(function (s) {
    return s && s.toLowerCase() !== 'yanıtla';
  });
  if (ham.length >= 2) ekle(ham[0], ham[1]);
});
return cikti.slice(0, 40);
"""


def yayin_kapisi(acik):
    """Diğer yayını açılınca gözcü döner, kapanınca işçileri ve tek tarayıcıyı bırakır."""
    global _ACIK, _GOZCU, _DUR
    if acik:
        with _KILIT:
            _ACIK = True
            if _GOZCU is not None and _GOZCU.is_alive():
                return
            dur = threading.Event()
            _DUR = dur
            _GOZCU = threading.Thread(target=_gozcu, args=(dur,), name="diger-coklu-yorum", daemon=True)
            _GOZCU.start()
        return
    with _KILIT:
        _ACIK = False
        dur = _DUR
    dur.set()
    _isleri_kapat()
    _tarayici_kapat()


def _gozcu(dur):
    while not dur.is_set():
        try:
            if _ACIK and _diger_acik():
                _esitle()
            else:
                _isleri_kapat()
                _tarayici_kapat()
        except Exception as hata:
            print(f"[DIGER YORUM] gozcu: {hata}", flush=True)
        if dur.wait(5):
            break
    _isleri_kapat()
    _tarayici_kapat()


def _diger_acik():
    try:
        import canli_yayin_havuzu as havuz
    except Exception:
        return False
    return str(havuz._kopya().get("aktif_dukkan") or "") == "diger"


def _esitle():
    istenen = _istenen_satirlar()
    with _KILIT:
        kimler = list(_ISLER.keys())
    for kim in kimler:
        if kim not in istenen:
            _is_kapat(kim)
    meta = []
    for kim, satir in istenen.items():
        if satir["tur"] in ("facebook", "instagram"):
            meta.append(satir)
            continue
        with _KILIT:
            kayit = _ISLER.get(kim)
        if kayit is not None and kayit["imza"] == satir["imza"] and kayit["isci"].is_alive():
            continue
        _is_kapat(kim)
        dur = threading.Event()
        hedef = _yt_isci if satir["tur"] == "youtube" else _tt_isci
        isci = threading.Thread(
            target=hedef,
            args=(satir, dur),
            name="diger-yorum-" + kim,
            daemon=True,
        )
        with _KILIT:
            _ISLER[kim] = {"imza": satir["imza"], "dur": dur, "isci": isci}
        isci.start()
    _meta_adim(meta)


def _istenen_satirlar():
    try:
        import sosyal_yayin_havuzu as havuz
    except Exception:
        return {}
    cikti = {}
    for kayit in havuz.liste():
        if not kayit.get("dahil"):
            continue
        tur = str(kayit.get("tur") or "")
        if tur not in _TUR:
            continue
        link = str(kayit.get("sayfa_linki") or "").strip()
        hedef = _hedef_coz(tur, link)
        if not hedef:
            continue
        if _tekli_kapsiyor(tur, hedef):
            continue
        kim = str(kayit.get("id") or "")
        if not kim:
            continue
        cikti[kim] = {
            "id": kim,
            "ad": str(kayit.get("ad") or tur)[:80],
            "tur": tur,
            "link": link,
            "hedef": hedef,
            "imza": tur + "|" + hedef,
        }
    return cikti


def _hedef_coz(tur, link):
    if not link or not re.match(r"^https?://", link, flags=re.IGNORECASE):
        return ""
    try:
        parca = urlparse(link)
    except ValueError:
        return ""
    host = (parca.netloc or "").lower()
    if tur == "youtube":
        if "youtube.com" not in host and "youtu.be" not in host:
            return ""
        try:
            import canli_yayin_havuzu as havuz

            return havuz._youtube_video_id(link) or ""
        except Exception:
            return ""
    if tur == "tiktok":
        if "tiktok.com" not in host:
            return ""
        try:
            import canli_yayin_havuzu as havuz

            return havuz._tiktok_kullanici(link) or ""
        except Exception:
            return ""
    if tur == "facebook":
        if not any(iz in host for iz in ("facebook.com", "fb.com", "fb.watch")):
            return ""
        return link.split("#")[0][:500]
    if tur == "instagram":
        if "instagram.com" not in host:
            return ""
        if len((parca.path or "").strip("/")) < 1:
            return ""
        return link.split("#")[0][:500]
    return ""


def _tekli_kapsiyor(tur, hedef):
    try:
        import canli_yayin_havuzu as havuz
    except Exception:
        return False
    with havuz._DINLEYICI_KILIT:
        mevcut = str(havuz._DINLEYICI_HEDEF.get(tur) or "")
        isci = havuz._DINLEYICI_IS.get(tur)
    if not mevcut or isci is None or not getattr(isci, "is_alive", lambda: False)():
        return False
    if tur == "youtube":
        try:
            return havuz._youtube_video_id(mevcut) == hedef or mevcut == hedef
        except Exception:
            return mevcut == hedef
    if tur == "tiktok":
        try:
            return havuz._tiktok_kullanici(mevcut).lower() == str(hedef).lower()
        except Exception:
            return mevcut.lower() == str(hedef).lower()
    return mevcut.split("#")[0].rstrip("/").lower() == str(hedef).split("#")[0].rstrip("/").lower()


def _is_kapat(kim):
    with _KILIT:
        kayit = _ISLER.pop(kim, None)
    if kayit is None:
        return
    kayit["dur"].set()


def _isleri_kapat():
    with _KILIT:
        kimler = list(_ISLER.keys())
    for kim in kimler:
        _is_kapat(kim)


def _yt_isci(satir, dur):
    try:
        import pytchat
    except Exception:
        return
    try:
        chat = pytchat.create(video_id=satir["hedef"])
    except Exception:
        return
    try:
        while not dur.is_set() and _ACIK:
            try:
                if hasattr(chat, "is_alive") and not chat.is_alive():
                    break
                demet = chat.get()
                ogeler = demet.sync_items() if hasattr(demet, "sync_items") else []
                for oge in ogeler:
                    if dur.is_set():
                        break
                    yazar = getattr(oge, "author", None)
                    ad = getattr(yazar, "name", None) or "YouTube"
                    _isle(satir, ad, getattr(oge, "message", "") or "")
            except Exception:
                break
            if dur.wait(1.0):
                break
    finally:
        for metod in ("terminate", "stop"):
            fn = getattr(chat, metod, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                break


def _tt_isci(satir, dur):
    try:
        from TikTokLive import TikTokLiveClient
    except Exception:
        return
    try:
        client = TikTokLiveClient(unique_id=satir["hedef"])
    except Exception:
        return

    async def _yorum(event):
        user = getattr(event, "user", None)
        ad = "TikTok"
        if user is not None:
            ad = getattr(user, "nickname", None) or getattr(user, "unique_id", None) or ad
        _isle(satir, ad, getattr(event, "comment", None) or "")

    try:
        from TikTokLive.events import CommentEvent

        client.on(CommentEvent)(_yorum)
    except Exception:
        try:
            client.on("comment")(_yorum)
        except Exception:
            return

    def _izle():
        dur.wait()
        for ad in ("stop", "disconnect", "close"):
            fn = getattr(client, ad, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                return

    threading.Thread(target=_izle, name="diger-tt-kapat-" + satir["id"], daemon=True).start()
    try:
        client.run()
    except Exception:
        return


def _meta_adim(satirlar):
    tarayiciya = []
    for satir in satirlar:
        bulunan = _http_yorumlar(satir)
        if bulunan is None:
            tarayiciya.append(satir)
            continue
        for cift in bulunan:
            _isle(satir, cift.get("kullanici"), cift.get("mesaj"))
    if not tarayiciya:
        _tarayici_kapat()
        return
    tarayici = _tarayici_getir()
    if tarayici is None:
        return
    tarayici.esitle(tarayiciya)
    for cift in tarayici.oku():
        satir = cift.get("satir")
        _isle(satir, cift.get("kullanici"), cift.get("mesaj"))


def _http_yorumlar(satir):
    """Gömülü yorum varsa liste, sayfa JS istiyorsa None, boşsa boş liste döner."""
    try:
        import requests
    except Exception:
        return None
    try:
        yanit = requests.get(
            satir["link"],
            headers={"User-Agent": _HTTP_UA, "Accept-Language": "tr-TR,tr;q=0.9"},
            timeout=8,
        )
    except Exception:
        return None
    if int(getattr(yanit, "status_code", 0) or 0) >= 400:
        return None
    html = str(getattr(yanit, "text", "") or "")[:400000]
    bulunan = _html_yorum_ayikla(html)
    if bulunan:
        return bulunan
    if len(html) < 800 or "id=\"mount" in html or "<noscript" in html.lower():
        return None
    return []


def _html_yorum_ayikla(html):
    bulunan = []
    if not html:
        return bulunan
    desenler = (
        re.compile(
            r'"name"\s*:\s*"((?:\\.|[^"\\]){1,80})".{0,280}?"text"\s*:\s*"((?:\\.|[^"\\]){1,300})"',
            re.DOTALL,
        ),
        re.compile(
            r'"username"\s*:\s*"([A-Za-z0-9._]{2,32})".{0,280}?"text"\s*:\s*"((?:\\.|[^"\\]){1,300})"',
            re.DOTALL,
        ),
    )
    for desen in desenler:
        for eslesen in desen.finditer(html):
            ad = _json_kacis(eslesen.group(1))
            metin = _json_kacis(eslesen.group(2))
            if ad and metin:
                bulunan.append({"kullanici": ad[:80], "mesaj": metin[:500]})
            if len(bulunan) >= 30:
                return bulunan
    return bulunan


def _json_kacis(metin):
    try:
        return bytes(str(metin), "utf-8").decode("unicode_escape").strip()
    except Exception:
        return str(metin or "").strip()


def _tarayici_getir():
    global _TARAYICI, _TARAYICI_DENENDI, _TARAYICI_YOK
    with _KILIT:
        if _TARAYICI is not None:
            return _TARAYICI
        if _TARAYICI_YOK and time.monotonic() - _TARAYICI_DENENDI < 60:
            return None
        _TARAYICI_DENENDI = time.monotonic()
    try:
        tarayici = _TekTarayici()
    except Exception:
        tarayici = None
    if tarayici is None or not tarayici.acik():
        with _KILIT:
            _TARAYICI_YOK = True
        if tarayici is not None:
            tarayici.kapat()
        return None
    with _KILIT:
        _TARAYICI = tarayici
        _TARAYICI_YOK = False
    return tarayici


def _tarayici_kapat():
    global _TARAYICI
    with _KILIT:
        tarayici = _TARAYICI
        _TARAYICI = None
    if tarayici is not None:
        tarayici.kapat()


class _TekTarayici:
    """Tek Chrome süreci. Her sayfa aynı pencerede ayrı sekmedir."""

    def __init__(self):
        self.driver = None
        self.sekmeler = {}
        self.kilit = threading.Lock()
        self._ac()

    def acik(self):
        return self.driver is not None

    def _ac(self):
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except Exception:
            return
        ayar = Options()
        ayar.add_argument("--headless=new")
        ayar.add_argument("--disable-gpu")
        ayar.add_argument("--disable-extensions")
        ayar.add_argument("--disable-dev-shm-usage")
        ayar.add_argument("--no-sandbox")
        ayar.add_argument("--blink-settings=imagesEnabled=false")
        ayar.add_argument("--window-size=800,600")
        try:
            self.driver = webdriver.Chrome(options=ayar)
        except Exception as hata:
            print(f"[DIGER YORUM] tek tarayici acilamadi: {hata}", flush=True)
            self.driver = None

    def esitle(self, satirlar):
        if self.driver is None:
            return
        istenen = {satir["id"]: satir for satir in satirlar}
        with self.kilit:
            for kim in list(self.sekmeler):
                if kim not in istenen:
                    self._sekme_kapat(kim)
            for kim, satir in istenen.items():
                kayit = self.sekmeler.get(kim)
                if kayit is not None and kayit.get("link") == satir["link"]:
                    continue
                if kayit is not None:
                    self._sekme_kapat(kim)
                self._sekme_ac(satir)

    def _sekme_ac(self, satir):
        try:
            if self.sekmeler:
                self.driver.switch_to.new_window("tab")
            else:
                self.driver.switch_to.window(self.driver.window_handles[0])
            self.driver.get(satir["link"])
            tutamak = self.driver.current_window_handle
        except Exception:
            return
        self.sekmeler[satir["id"]] = {"handle": tutamak, "link": satir["link"], "satir": satir}

    def _sekme_kapat(self, kim):
        kayit = self.sekmeler.pop(kim, None)
        if kayit is None or self.driver is None:
            return
        try:
            if len(self.driver.window_handles) <= 1:
                return
            self.driver.switch_to.window(kayit["handle"])
            self.driver.close()
            kalan = self.driver.window_handles
            if kalan:
                self.driver.switch_to.window(kalan[0])
        except Exception:
            return

    def oku(self):
        if self.driver is None:
            return []
        cikti = []
        with self.kilit:
            for kayit in list(self.sekmeler.values()):
                try:
                    self.driver.switch_to.window(kayit["handle"])
                    satirlar = self.driver.execute_script(_SEKME_JS) or []
                except Exception:
                    continue
                if not isinstance(satirlar, list):
                    continue
                for oge in satirlar:
                    if not isinstance(oge, dict):
                        continue
                    cikti.append(
                        {
                            "satir": kayit["satir"],
                            "kullanici": oge.get("kullanici") or "",
                            "mesaj": oge.get("mesaj") or "",
                        }
                    )
        return cikti

    def kapat(self):
        with self.kilit:
            driver = self.driver
            self.driver = None
            self.sekmeler.clear()
        if driver is None:
            return
        try:
            driver.quit()
        except Exception:
            pass


def _isle(satir, kullanici, mesaj):
    if not satir or not _ACIK or not _diger_acik():
        return
    metin = " ".join(str(mesaj or "").split())[:500]
    ad = str(kullanici or "").strip()[:80]
    if not metin or not ad:
        return
    if not _yeni_mi(satir["id"], ad, metin):
        return
    try:
        import canli_yayin_havuzu as havuz
    except Exception:
        return

    def _calistir():
        return havuz.havuz_yorumu_isle(
            "diger",
            satir["tur"],
            ad,
            metin,
            yerel_kopru=True,
            sayfa=satir["ad"],
            sayfa_id=satir["id"],
        )

    if havuz._app is None:
        _calistir()
        return
    with havuz._app.app_context():
        try:
            _calistir()
        finally:
            try:
                if havuz._db is not None:
                    havuz._db.session.remove()
            except Exception:
                pass


def _yeni_mi(sayfa_id, kullanici, mesaj):
    anahtar = hashlib.sha1((kullanici.casefold() + "\n" + mesaj.casefold()).encode("utf-8")).hexdigest()
    with _KILIT:
        kuyruk = _GORULEN.get(sayfa_id)
        if kuyruk is None:
            kuyruk = deque(maxlen=300)
            _GORULEN[sayfa_id] = kuyruk
        if anahtar in kuyruk:
            return False
        kuyruk.append(anahtar)
        if len(_GORULEN) > 120:
            for kim in list(_GORULEN)[:20]:
                if kim != sayfa_id:
                    _GORULEN.pop(kim, None)
    return True
