"""Canlı yayın yönlendirme motoru ve çekirdek havuz iskeleti.

Mezat sayacı, pey kilidi ve dükkan otonom bayrakları bu modülde değişmez.
Eksik sohbet kütüphaneleri uygulamayı düşürmez; pasif kalır.
"""

import json
import os
import re
import threading
from datetime import datetime, timezone

from flask import jsonify, request, session
from sqlalchemy import text

try:
    import pytchat  # noqa: F401
    _PYTCHAT_VAR = True
except ImportError:
    pytchat = None
    _PYTCHAT_VAR = False

try:
    from TikTokLive import TikTokLiveClient  # noqa: F401
    _TIKTOK_VAR = True
except ImportError:
    TikTokLiveClient = None
    _TIKTOK_VAR = False

_KILIT = threading.RLock()
_BELLEK = {
    "aktif_dukkan": "",
    "yayin_basligi": "",
    "yayin_url": "",
    "baslangic_zamani": "",
    "kanallar": {},
    "webhook_url": "",
    "duyuru_metni": "",
    "taslaklar": {},
}
_THREADPOOL = None
_REDIS = None
_HAZIR = False

_app = None
_db = None
_socketio = None
_dukkanlar = {}
_dukkan_dogrula = None
_tutar_coz = None
_odalar = None
_oda_kilitleri = None
_urun_model = None
_teklif_model = None
_pey_kabul = None
_pey_duyur = None
_gizli_esik = None
_erken_sat = None
_nabiz = None
_iki_oda = None
_sicak_soguk = None
_pey_ses = None
_acilis_tabani = None
_kopru_kabul = None

_KANALLAR = (
    "youtube",
    "instagram",
    "facebook",
    "tiktok",
    "kick",
    "twitch",
    "x",
    "telegram",
)
_KANAL_ETIKET = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "kick": "Kick",
    "twitch": "Twitch",
    "x": "X",
    "telegram": "Telegram",
}
_PLATFORM_AD = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "kick": "Kick",
    "twitch": "Twitch",
    "x": "X",
    "twitter": "X",
    "telegram": "Telegram",
}
_SITE = "https://halicantikcanli.com"
_REDIS_ANAHTAR = "halic:canli_yayin:durum"
_TABLO = "canli_yayin_havuz"


def bagla(
    app,
    db,
    socketio,
    dukkanlar,
    dukkan_dogrula,
    tutar_coz,
    odalar,
    oda_kilitleri,
    urun_model,
    teklif_model,
    pey_kabul,
    pey_duyur,
    gizli_esik,
    erken_sat,
    nabiz,
    iki_oda,
    sicak_soguk,
    pey_ses,
    acilis_tabani,
    kopru_kabul,
):
    """Bağlantıları alır, şemayı açar ve kalıcı kaydı belleğe yükler."""
    global _app, _db, _socketio, _dukkanlar, _dukkan_dogrula, _tutar_coz
    global _odalar, _oda_kilitleri, _urun_model, _teklif_model
    global _pey_kabul, _pey_duyur, _gizli_esik, _erken_sat, _nabiz, _iki_oda
    global _sicak_soguk, _pey_ses, _acilis_tabani, _kopru_kabul, _HAZIR
    _app = app
    _db = db
    _socketio = socketio
    _dukkanlar = dict(dukkanlar or {})
    _dukkan_dogrula = dukkan_dogrula
    _tutar_coz = tutar_coz
    _odalar = odalar
    _oda_kilitleri = oda_kilitleri
    _urun_model = urun_model
    _teklif_model = teklif_model
    _pey_kabul = pey_kabul
    _pey_duyur = pey_duyur
    _gizli_esik = gizli_esik
    _erken_sat = erken_sat
    _nabiz = nabiz
    _iki_oda = iki_oda
    _sicak_soguk = sicak_soguk
    _pey_ses = pey_ses
    _acilis_tabani = acilis_tabani
    _kopru_kabul = kopru_kabul
    _redis_bagla()
    try:
        with app.app_context():
            _semayi_hazirla()
            _bellege_yukle()
    except Exception as hata:
        print(f"[CANLI YAYIN] kalici yukleme atlandi: {hata}", flush=True)
    _HAZIR = True
    print(
        "[CANLI YAYIN] pytchat="
        + ("acik" if _PYTCHAT_VAR else "pasif")
        + " tiktoklive="
        + ("acik" if _TIKTOK_VAR else "pasif"),
        flush=True,
    )


def durum_yaniti():
    paket = herkese_acik_durum()
    if _yonetici_mi():
        paket["ayarlar"] = _ayarlar_paketi()
    return jsonify(paket)


def kontrol_yaniti():
    veri = request.get_json(silent=True) or {}
    islem = str(veri.get("islem") or "").strip().lower()
    if islem in ("baslat", "ac", "start"):
        return jsonify(_baslat(veri))
    if islem in ("durdur", "kapat", "stop"):
        return jsonify(_durdur(veri))
    if islem in ("ayar", "ayar_kaydet", "kaydet"):
        return jsonify(_ayar_kaydet(veri))
    return jsonify({"success": False, "mesaj": "Geçersiz canlı yayın işlemi."}), 400


def webhook_yorum_yaniti():
    if _kopru_kabul is not None and not _kopru_kabul():
        return jsonify({"success": False, "mesaj": "Köprü anahtarı geçersiz."}), 401
    veri = request.get_json(silent=True) or {}
    dukkan = str(veri.get("dukkan") or veri.get("shop") or "").strip()
    platform = veri.get("platform") or veri.get("kaynak") or ""
    kullanici = veri.get("kullanici") or veri.get("username") or veri.get("user") or ""
    mesaj = veri.get("mesaj") or veri.get("message") or veri.get("comment") or ""
    sonuc = havuz_yorumu_isle(dukkan, platform, kullanici, mesaj)
    kod = 200 if sonuc.get("success") else int(sonuc.get("kod") or 400)
    govde = {anahtar: deger for anahtar, deger in sonuc.items() if anahtar != "kod"}
    return jsonify(govde), kod


def herkese_acik_durum():
    kayit = _kopya()
    kod = str(kayit.get("aktif_dukkan") or "")
    return {
        "success": True,
        "aktif": bool(kod),
        "aktif_dukkan": kod,
        "dukkan_adi": _dukkan_adi(kod) if kod else "",
        "yayin_basligi": kayit.get("yayin_basligi") or "",
        "yayin_url": kayit.get("yayin_url") or "",
        "platformlar": _kanallar(kayit),
        "baslangic_zamani": kayit.get("baslangic_zamani") or "",
        "dinleyiciler": {"pytchat": _PYTCHAT_VAR, "tiktoklive": _TIKTOK_VAR},
    }


def havuz_yorumu_isle(dukkan, platform, kullanici, mesaj):
    """Dış yorumu sohbete düşürür, kalp yollar ve uygunsa mevcut pey kapısını çağırır."""
    platform_adi = _platform_coz(platform)
    if not platform_adi:
        return {"success": False, "kod": 400, "mesaj": "Platform tanınmadı."}
    ad = str(kullanici or "İzleyici").strip()[:80] or "İzleyici"
    metin = str(mesaj or "").strip()[:500]
    if not metin:
        return {"success": False, "kod": 400, "mesaj": "Boş yorum işlenmez."}
    kod = _hedef_dukkan(dukkan)
    if not kod:
        return {"success": False, "kod": 409, "mesaj": "Aktif canlı yayın yok."}
    if not _yorum_hizi_uygun(platform_adi, ad):
        return {"success": False, "kod": 429, "mesaj": "Yorumlar çok hızlı geliyor."}

    _yorum_yayinla(kod, platform_adi, ad, metin)
    _kalp_yayinla(kod, platform_adi)

    tutar = yorumdan_pey_coz(metin, _tutar_coz)
    if not tutar or tutar <= 0:
        return {"success": True, "tip": "yorum", "pey": False, "dukkan": kod}

    pey = _pey_karsilastir_ve_uygula(kod, platform_adi, ad, float(tutar))
    return {
        "success": True,
        "tip": "pey" if pey.get("uygulandi") else "yorum",
        "pey": bool(pey.get("uygulandi")),
        "tutar": float(tutar),
        "dukkan": kod,
        "mesaj": pey.get("neden") or "",
    }


def yorumdan_pey_coz(mesaj, tutar_coz=None):
    """Yorumdaki geçerli sayısal teklifi ayıklar. Cümle içindeki saat gibi kısa sayıları geçmez."""
    metin = str(mesaj or "").strip()
    if not metin:
        return None
    if tutar_coz is not None:
        try:
            tam = tutar_coz(metin)
        except Exception:
            tam = None
        deger = _sayiya(tam)
        if deger and deger > 0:
            return deger
    tl_aday = re.findall(
        r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})+|\d{2,7})\s*(?:tl|lira|₺)\b",
        metin,
        flags=re.IGNORECASE,
    )
    if tl_aday:
        return _sayiya(tl_aday[-1])
    adaylar = re.findall(r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})+|\d{3,7})(?!\d)", metin)
    if not adaylar:
        return None
    return _sayiya(adaylar[-1])


def _baslat(veri):
    kod = _dukkan_kodu(veri.get("dukkan"))
    if not kod:
        return {"success": False, "mesaj": "Dükkan seçilemedi."}
    with _KILIT:
        kayit = _kopya()
        _alanlari_isle(kayit, veri, kod)
        taslak = dict((kayit.get("taslaklar") or {}).get(kod) or {})
        kayit["aktif_dukkan"] = kod
        kayit["yayin_basligi"] = str(
            veri.get("yayin_basligi") or taslak.get("yayin_basligi") or ""
        ).strip()[:180]
        kayit["yayin_url"] = _guvenli_url(
            veri.get("yayin_url") or taslak.get("yayin_url") or ""
        )
        kayit["baslangic_zamani"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        taslaklar = dict(kayit.get("taslaklar") or {})
        taslaklar[kod] = {
            "yayin_basligi": kayit["yayin_basligi"],
            "yayin_url": kayit["yayin_url"],
        }
        kayit["taslaklar"] = taslaklar
        _BELLEK.update(kayit)
        anlik = _kopya()
    _kalici_yaz(anlik)
    _olay_yayinla(anlik)
    _otomasyon_sinyali(anlik)
    paket = herkese_acik_durum()
    paket["success"] = True
    paket["ayarlar"] = _ayarlar_paketi()
    paket["mesaj"] = _dukkan_adi(kod) + " dükkanında canlı yayın başladı."
    return paket


def _durdur(veri):
    kod = _dukkan_kodu(veri.get("dukkan")) if veri.get("dukkan") else ""
    with _KILIT:
        kayit = _kopya()
        aktif = str(kayit.get("aktif_dukkan") or "")
        if kod and aktif and kod != aktif:
            return {"success": False, "mesaj": "Canlı yayın başka dükkanda."}
        if aktif:
            taslaklar = dict(kayit.get("taslaklar") or {})
            taslaklar[aktif] = {
                "yayin_basligi": kayit.get("yayin_basligi") or "",
                "yayin_url": kayit.get("yayin_url") or "",
            }
            kayit["taslaklar"] = taslaklar
        kayit["aktif_dukkan"] = ""
        kayit["baslangic_zamani"] = ""
        _BELLEK.update(kayit)
        anlik = _kopya()
    _kalici_yaz(anlik)
    _olay_yayinla(anlik)
    paket = herkese_acik_durum()
    paket["success"] = True
    paket["ayarlar"] = _ayarlar_paketi()
    paket["mesaj"] = "Canlı yayın durduruldu."
    return paket


def _ayar_kaydet(veri):
    kod = _dukkan_kodu(veri.get("dukkan")) if veri.get("dukkan") else ""
    with _KILIT:
        kayit = _kopya()
        _alanlari_isle(kayit, veri, kod)
        if kod:
            taslaklar = dict(kayit.get("taslaklar") or {})
            taslak = dict(taslaklar.get(kod) or {})
            if "yayin_basligi" in veri:
                taslak["yayin_basligi"] = str(veri.get("yayin_basligi") or "").strip()[:180]
            if "yayin_url" in veri:
                taslak["yayin_url"] = _guvenli_url(veri.get("yayin_url") or "")
            if str(kayit.get("aktif_dukkan") or "") == kod:
                kayit["yayin_basligi"] = taslak.get("yayin_basligi") or ""
                kayit["yayin_url"] = taslak.get("yayin_url") or ""
            taslaklar[kod] = taslak
            kayit["taslaklar"] = taslaklar
        elif "yayin_basligi" in veri or "yayin_url" in veri:
            if "yayin_basligi" in veri:
                kayit["yayin_basligi"] = str(veri.get("yayin_basligi") or "").strip()[:180]
            if "yayin_url" in veri:
                kayit["yayin_url"] = _guvenli_url(veri.get("yayin_url") or "")
        _BELLEK.update(kayit)
        anlik = _kopya()
    _kalici_yaz(anlik)
    if str(anlik.get("aktif_dukkan") or ""):
        _olay_yayinla(anlik)
    paket = herkese_acik_durum()
    paket["success"] = True
    paket["ayarlar"] = _ayarlar_paketi()
    paket["mesaj"] = "Canlı yayın ayarları kaydedildi."
    return paket


def _alanlari_isle(kayit, veri, kod):
    if isinstance(veri.get("kanallar"), dict):
        kayit["kanallar"] = _kanallari_temizle(veri.get("kanallar"))
    elif isinstance(veri.get("platformlar"), dict):
        kayit["kanallar"] = _kanallari_temizle(veri.get("platformlar"))
    if "webhook_url" in veri:
        kayit["webhook_url"] = _guvenli_url(veri.get("webhook_url") or "")
    if "duyuru_metni" in veri:
        kayit["duyuru_metni"] = str(veri.get("duyuru_metni") or "").strip()[:2000]
    if kod and not str(kayit.get("aktif_dukkan") or ""):
        taslak = (kayit.get("taslaklar") or {}).get(kod) or {}
        if "yayin_basligi" not in veri and taslak.get("yayin_basligi"):
            kayit["yayin_basligi"] = taslak.get("yayin_basligi") or ""
        if "yayin_url" not in veri and taslak.get("yayin_url"):
            kayit["yayin_url"] = taslak.get("yayin_url") or ""


def _pey_karsilastir_ve_uygula(dukkan, platform, kullanici, tutar):
    if _odalar is None or _pey_kabul is None or _oda_kilitleri is None:
        return {"uygulandi": False, "neden": "Pey kapısı bağlı değil."}
    oda = _odalar.get(dukkan) or {}
    urun_id = oda.get("aktif_urun_id")
    if not urun_id:
        return {"uygulandi": False, "neden": "Sahnede ürün yok."}
    urun = None
    try:
        if _db is not None and _urun_model is not None:
            urun = _db.session.get(_urun_model, urun_id)
        mevcut = float((oda.get("durum") or {}).get("pey") or 0)
        olur, uyari = _pey_kabul(oda, urun, tutar, mevcut)
        if not olur:
            return {"uygulandi": False, "neden": uyari or "Teklif mevcut fiyata uymuyor."}
        return _pey_yaz(dukkan, oda, urun, platform, kullanici, tutar, urun_id)
    except Exception as hata:
        print(f"[CANLI YAYIN] pey kapisi: {hata}", flush=True)
        try:
            if _db is not None:
                _db.session.rollback()
        except Exception:
            pass
        return {"uygulandi": False, "neden": "Pey işlenemedi."}
    finally:
        try:
            if _db is not None:
                _db.session.remove()
        except Exception:
            pass


def _pey_yaz(dukkan, oda, urun, platform, kullanici, tutar, urun_id):
    etiket = f"[{platform}] {kullanici}"[:100]
    with _oda_kilitleri[dukkan]:
        guncel_id = oda.get("aktif_urun_id")
        if guncel_id != urun_id:
            return {"uygulandi": False, "neden": "Sahne değişti."}
        mevcut = float((oda.get("durum") or {}).get("pey") or 0)
        olur, uyari = _pey_kabul(oda, urun, tutar, mevcut)
        if not olur:
            return {"uygulandi": False, "neden": uyari or "Teklif mevcut fiyata uymuyor."}
        oda["durum"]["pey"] = tutar
        oda["durum"]["kazanan"] = etiket
        oda["durum"]["kazanan_id"] = None
        urun_adi = getattr(urun, "urun_adi", "") if urun is not None else ""
        if urun is not None and _db is not None:
            urun.guncel_fiyat = tutar
            urun.kazanan_adi = etiket
            urun.kazanan_id = None
            _db.session.add(urun)
            if _teklif_model is not None:
                _db.session.add(
                    _teklif_model(
                        dukkan_kodu=dukkan,
                        urun_id=urun.id,
                        musteri_id=None,
                        musteri_adi=etiket,
                        tutar=tutar,
                    )
                )
            _db.session.commit()
        if oda.get("mevat_asama") == "karar":
            oda["karar_pey_geldi"] = True
        elif _gizli_esik is not None and _erken_sat is not None and _gizli_esik(oda, tutar):
            _erken_sat(dukkan, oda, urun, etiket, None, tutar, kullanici)
            return {"uygulandi": True, "neden": ""}

        if _pey_duyur is not None:
            _pey_duyur(
                dukkan,
                {
                    "urun_id": urun_id,
                    "pey": tutar,
                    "kazanan": etiket,
                    "kazanan_id": None,
                    "platform": platform,
                    "ad": kullanici,
                },
            )
        if (
            oda.get("mevat_asama") == "karar"
            and oda.get("sayac_aktif")
            and 0 < int(oda.get("sayac_kalan") or 0) <= 15
            and _iki_oda is not None
        ):
            oda["sayac_kalan"] = 15
            import time

            oda["durum"]["sure_bitis"] = time.time() + 15
            asama = oda.get("asama") or oda.get("mevat_asama") or "karar"
            _iki_oda(
                "sayac_uzatildi",
                {
                    "dukkan": dukkan,
                    "kalan": 15,
                    "kalan_sure": 15,
                    "asama": asama,
                    "mesaj": "Son saniye teklifi nedeniyle süre 15 sn uzatıldı!",
                },
                dukkan,
            )
            _iki_oda(
                "sayac_guncelle",
                {
                    "dukkan": dukkan,
                    "kalan": 15,
                    "kalan_sure": 15,
                    "sayac_gorunsun": True,
                    "asama": asama,
                },
                dukkan,
            )
    if _nabiz is not None:
        try:
            _nabiz(dukkan)
        except Exception:
            pass
    try:
        if _sicak_soguk is None or not _sicak_soguk(dukkan, oda, kullanici, tutar):
            if _pey_ses is not None:
                acilis = 0
                if _acilis_tabani is not None:
                    acilis = _acilis_tabani(oda, urun)
                hemen = float(getattr(urun, "hemen_al_fiyati", 0) or 0) if urun is not None else 0
                aciklama = getattr(urun, "tanitim_yazisi", "") if urun is not None else ""
                _pey_ses(dukkan, kullanici, tutar, urun_adi, acilis, hemen, aciklama)
    except Exception as hata:
        print(f"[CANLI YAYIN] pey sesi atlandi: {hata}", flush=True)
    return {"uygulandi": True, "neden": ""}


def _yorum_yayinla(dukkan, platform, kullanici, mesaj):
    if _socketio is None:
        return
    import time

    _socketio.emit(
        "yeni_canli_yorum",
        {
            "platform": platform,
            "kullanici": kullanici,
            "mesaj": mesaj,
            "zaman": time.strftime("%H:%M:%S"),
            "dukkan": dukkan,
            "havuz": True,
            "rozet": platform,
        },
        room=dukkan,
    )


def _kalp_yayinla(dukkan, platform):
    if _socketio is None:
        return
    _socketio.emit(
        "canli_kalp",
        {"dukkan": dukkan, "platform": platform},
        room=dukkan,
    )


def _olay_yayinla(kayit):
    if _socketio is None:
        return
    kod = str(kayit.get("aktif_dukkan") or "")
    _socketio.emit(
        "aktif_canli_yayin",
        {
            "aktif": bool(kod),
            "aktif_dukkan": kod,
            "dukkan_adi": _dukkan_adi(kod) if kod else "",
            "yayin_basligi": kayit.get("yayin_basligi") or "",
            "yayin_url": kayit.get("yayin_url") or "",
            "platformlar": _kanallar(kayit),
            "baslangic_zamani": kayit.get("baslangic_zamani") or "",
        },
    )


def _otomasyon_sinyali(kayit):
    adres = _webhook_adresi(kayit)
    if not adres:
        return
    kod = str(kayit.get("aktif_dukkan") or "")
    gorsel = _mutlak(_aktif_gorsel(kod))
    paket = {
        "yayin_basligi": kayit.get("yayin_basligi") or (_dukkan_adi(kod) + " canlı mezat"),
        "gorsel": gorsel,
        "link": _SITE + "/?dukkan=" + kod,
        "dukkan": kod,
        "dukkan_adi": _dukkan_adi(kod),
        "yayin_url": kayit.get("yayin_url") or "",
    }
    _arkaplanda(_webhook_gonder, adres, paket)


def _webhook_gonder(adres, paket):
    try:
        import requests

        requests.post(adres, json=paket, timeout=8)
    except Exception as hata:
        print(f"[CANLI YAYIN] webhook gonderilemedi: {hata}", flush=True)


def _arkaplanda(fn, *args):
    global _THREADPOOL
    try:
        from gevent.threadpool import ThreadPool

        if _THREADPOOL is None:
            _THREADPOOL = ThreadPool(2)
        _THREADPOOL.spawn(fn, *args)
        return
    except Exception as hata:
        print(f"[CANLI YAYIN] threadpool atlandi: {hata}", flush=True)
    try:
        import gevent

        gevent.spawn(fn, *args)
    except Exception as hata:
        print(f"[CANLI YAYIN] webhook arka plan baslamadi: {hata}", flush=True)


def _aktif_gorsel(dukkan):
    try:
        oda = (_odalar or {}).get(dukkan) or {}
        urun_id = oda.get("aktif_urun_id")
        if not urun_id or _db is None or _urun_model is None:
            return ""
        urun = _db.session.get(_urun_model, urun_id)
        if urun is None:
            return ""
        fotolar = list(getattr(urun, "fotograflar", None) or [])
        if fotolar:
            return str(fotolar[0] or "")
        return str(getattr(urun, "video", "") or "")
    except Exception:
        try:
            if _db is not None:
                _db.session.rollback()
        except Exception:
            pass
        return ""


def _mutlak(yol):
    adres = str(yol or "").strip()
    if not adres:
        return _SITE + "/static/img/varsayilan_urun.png"
    if adres.startswith("http://") or adres.startswith("https://"):
        return adres
    if not adres.startswith("/"):
        adres = "/" + adres
    return _SITE + adres


def _webhook_adresi(kayit):
    kayitli = _guvenli_url((kayit or {}).get("webhook_url") or "")
    if kayitli:
        return kayitli
    return _guvenli_url(os.environ.get("MAKE_N8N_WEBHOOK_URL") or "")


def _hedef_dukkan(istenen):
    kod = _dukkan_kodu(istenen) if str(istenen or "").strip() else ""
    if kod:
        return kod
    return str(_kopya().get("aktif_dukkan") or "")


def _dukkan_kodu(ham):
    if _dukkan_dogrula is None:
        return ""
    try:
        kod = _dukkan_dogrula(ham)
    except Exception:
        return ""
    if not kod or kod not in _dukkanlar:
        return ""
    gelen = str(ham or "").strip().lower()
    if not gelen or gelen not in _dukkanlar:
        return ""
    return kod


def _dukkan_adi(kod):
    bilgi = _dukkanlar.get(kod) or {}
    return str(bilgi.get("ad") or kod or "")


def _platform_coz(ham):
    temiz = str(ham or "").strip().strip("[]").strip().lower()
    return _PLATFORM_AD.get(temiz)


def _kanallari_temizle(ham):
    temiz = {}
    for anahtar in _KANALLAR:
        deger = ham.get(anahtar)
        if deger is None:
            deger = ham.get(_KANAL_ETIKET[anahtar])
        temiz[anahtar] = _guvenli_url(deger or "")
    return temiz


def _kanallar(kayit):
    return _kanallari_temizle((kayit or {}).get("kanallar") or {})


def _guvenli_url(deger):
    adres = str(deger or "").strip()
    if not adres:
        return ""
    if adres.startswith("https://") or adres.startswith("http://"):
        return adres[:500]
    return ""


def _sayiya(deger):
    if deger is None:
        return None
    if isinstance(deger, (int, float)):
        sayi = float(deger)
        return sayi if sayi > 0 else None
    rakam = re.sub(r"[^\d]", "", str(deger))
    if not rakam:
        return None
    try:
        sayi = float(rakam)
    except ValueError:
        return None
    return sayi if sayi > 0 else None


_yorum_zamanlari = {}


def _yorum_hizi_uygun(platform, kullanici):
    import time

    anahtar = platform + ":" + kullanici.casefold()
    simdi = time.time()
    with _KILIT:
        pencere = [an for an in _yorum_zamanlari.get(anahtar, []) if simdi - an < 10]
        if len(pencere) >= 8:
            _yorum_zamanlari[anahtar] = pencere
            return False
        pencere.append(simdi)
        _yorum_zamanlari[anahtar] = pencere
    return True


def _yonetici_mi():
    try:
        return session.get("is_admin") is True and session.get("admin_2fa_ok") is True
    except Exception:
        return False


def _ayarlar_paketi():
    kayit = _kopya()
    kod = str(kayit.get("aktif_dukkan") or "")
    return {
        "kanallar": _kanallar(kayit),
        "webhook_url": kayit.get("webhook_url") or "",
        "webhook_env": bool(_guvenli_url(os.environ.get("MAKE_N8N_WEBHOOK_URL") or "")),
        "duyuru_metni": kayit.get("duyuru_metni") or "",
        "duyuru_hazir": _duyuru_hazir(kayit, kod or "gumus"),
        "taslaklar": kayit.get("taslaklar") or {},
    }


def _duyuru_hazir(kayit, kod):
    ozel = str((kayit or {}).get("duyuru_metni") or "").strip()
    ad = _dukkan_adi(kod)
    link = _SITE + "/?dukkan=" + (kod or "gumus")
    if ozel:
        return (
            ozel.replace("{dukkan_adi}", ad)
            .replace("{dukkan}", kod or "")
            .replace("{link}", link)
        )
    return (
        "🔴 CANLI MEZAT BAŞLADI! "
        + ad
        + " Dükkanında Şu An Canlı Yayın Var — Hemen Katıl\n"
        + link
        + "\nPey vermek için sohbete yalnızca tutarı yazın."
    )


def _kopya():
    with _KILIT:
        return json.loads(json.dumps(_BELLEK, ensure_ascii=False))


def _redis_bagla():
    global _REDIS
    _REDIS = None
    adres = (os.environ.get("REDIS_URL") or "").strip()
    if not adres:
        return
    try:
        import redis

        istemci = redis.Redis.from_url(adres, decode_responses=True, socket_timeout=1.5)
        istemci.ping()
        _REDIS = istemci
        print("[CANLI YAYIN] Redis baglandi", flush=True)
    except Exception as hata:
        _REDIS = None
        print(f"[CANLI YAYIN] Redis atlandi: {hata}", flush=True)


def _semayi_hazirla():
    _db.session.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLO} (
                anahtar VARCHAR(64) PRIMARY KEY,
                icerik TEXT NOT NULL,
                guncelleme VARCHAR(40)
            )
            """
        )
    )
    _db.session.commit()


def _bellege_yukle():
    uzak = _redis_oku()
    if uzak is None:
        uzak = _db_oku()
        if uzak is not None and _REDIS is not None:
            _redis_yaz(uzak)
    if not uzak:
        return
    with _KILIT:
        _BELLEK.update(_kaydi_normallestir(uzak))


def _kalici_yaz(kayit):
    try:
        _redis_yaz(kayit)
    except Exception as hata:
        print(f"[CANLI YAYIN] Redis yazilamadi: {hata}", flush=True)
    try:
        if _app is not None and _db is not None:
            with _app.app_context():
                _db_yaz(kayit)
    except Exception as hata:
        print(f"[CANLI YAYIN] veritabani yazilamadi: {hata}", flush=True)
        try:
            _db.session.rollback()
        except Exception:
            pass


def _redis_oku():
    if _REDIS is None:
        return None
    try:
        ham = _REDIS.get(_REDIS_ANAHTAR)
    except Exception as hata:
        print(f"[CANLI YAYIN] Redis okunamadi: {hata}", flush=True)
        return None
    if not ham:
        return None
    try:
        veri = json.loads(ham)
    except ValueError:
        return None
    return veri if isinstance(veri, dict) else None


def _redis_yaz(kayit):
    if _REDIS is None:
        return
    _REDIS.set(_REDIS_ANAHTAR, json.dumps(kayit, ensure_ascii=False))


def _db_oku():
    satir = _db.session.execute(
        text(f"SELECT icerik FROM {_TABLO} WHERE anahtar = :anahtar"),
        {"anahtar": "durum"},
    ).first()
    if satir is None:
        return None
    try:
        veri = json.loads(satir[0] or "{}")
    except ValueError:
        return None
    return veri if isinstance(veri, dict) else None


def _db_yaz(kayit):
    _db.session.execute(
        text(
            f"""
            INSERT INTO {_TABLO} (anahtar, icerik, guncelleme)
            VALUES (:anahtar, :icerik, :an)
            ON CONFLICT(anahtar) DO UPDATE SET
                icerik = excluded.icerik,
                guncelleme = excluded.guncelleme
            """
        ),
        {
            "anahtar": "durum",
            "icerik": json.dumps(kayit, ensure_ascii=False),
            "an": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    _db.session.commit()


def _kaydi_normallestir(ham):
    kayit = {
        "aktif_dukkan": str(ham.get("aktif_dukkan") or ""),
        "yayin_basligi": str(ham.get("yayin_basligi") or "")[:180],
        "yayin_url": _guvenli_url(ham.get("yayin_url") or ""),
        "baslangic_zamani": str(ham.get("baslangic_zamani") or ""),
        "kanallar": _kanallari_temizle(ham.get("kanallar") or {}),
        "webhook_url": _guvenli_url(ham.get("webhook_url") or ""),
        "duyuru_metni": str(ham.get("duyuru_metni") or "")[:2000],
        "taslaklar": ham.get("taslaklar") if isinstance(ham.get("taslaklar"), dict) else {},
    }
    if kayit["aktif_dukkan"] not in _dukkanlar:
        kayit["aktif_dukkan"] = ""
    return kayit
