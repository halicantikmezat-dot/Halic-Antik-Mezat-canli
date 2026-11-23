"""Canlı yayın yönlendirme motoru ve çekirdek havuz iskeleti.

Mezat sayacı, pey kilidi ve dükkan otonom bayrakları bu modülde değişmez.
Eksik sohbet kütüphaneleri uygulamayı düşürmez; pasif kalır.
"""

import base64
import hashlib
import html
import json
import os
import re
import socket
import ssl
import subprocess
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

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
    "restream_rtmp_url": "",
    "restream_stream_key": "",
    "restream_video_id": "",
    "restream_audio_id": "",
    "reji_platformlari": [],
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
_otonom_pas = None

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
_SABIT_CANLI_DUKKAN = "diger"
_REDIS_ANAHTAR = "halic:canli_yayin:durum"
_DINLEYICI_ANAHTAR = "halic:canli_yayin:dinleyici"
_VAPID_ANAHTAR = "halic:canli_yayin:vapid"
_PUSH_ANAHTAR = "halic:canli_yayin:push"
_TABLO = "canli_yayin_havuz"
_PUSH_TABLO = "canli_yayin_push"
_DINLEYICI_KILIT = threading.RLock()
_DINLEYICI_ADLAR = ("youtube", "tiktok", "twitch", "kick", "meta")
_DINLEYICI_DURUM = {ad: "kapali" for ad in _DINLEYICI_ADLAR}
_DINLEYICI_MESAJ = {ad: "" for ad in _DINLEYICI_ADLAR}
_DINLEYICI_HEDEF = {ad: "" for ad in _DINLEYICI_ADLAR}
_DINLEYICI_DUKKAN = {ad: "" for ad in _DINLEYICI_ADLAR}
_DINLEYICI_DUR = {ad: None for ad in _DINLEYICI_ADLAR}
_DINLEYICI_IS = {ad: None for ad in _DINLEYICI_ADLAR}
_DINLEYICI_ISTEMCI = {ad: None for ad in _DINLEYICI_ADLAR}
_OLCUM = {"toplam_yorum": 0, "otomatik_pey": 0}
_WEBHOOK_SON = {"basarili": False, "zaman": ""}
_SON_OLCUM_YAYIN = 0.0
_PUSH = {}
_PUSH_KILIT = threading.RLock()
_VAPID = None


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
    otonom_pas=None,
):
    """Bağlantıları alır, şemayı açar ve kalıcı kaydı belleğe yükler."""
    global _app, _db, _socketio, _dukkanlar, _dukkan_dogrula, _tutar_coz
    global _odalar, _oda_kilitleri, _urun_model, _teklif_model
    global _pey_kabul, _pey_duyur, _gizli_esik, _erken_sat, _nabiz, _iki_oda
    global _sicak_soguk, _pey_ses, _acilis_tabani, _kopru_kabul, _otonom_pas, _HAZIR
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
    _otonom_pas = otonom_pas
    _redis_bagla()
    try:
        with app.app_context():
            _semayi_hazirla()
            _bellege_yukle()
            _olcum_yukle()
            _vapid_hazirla()
            _push_yukle()
    except Exception as hata:
        print(f"[CANLI YAYIN] kalici yukleme atlandi: {hata}", flush=True)
        if _VAPID is None:
            _vapid_hazirla()
    _HAZIR = True
    try:
        if str(_kopya().get("aktif_dukkan") or ""):
            platform_dinleyici_baslat()
            _otonom_pas_uygula(True)
    except Exception as hata:
        print(f"[CANLI YAYIN] dinleyici acilisi atlandi: {hata}", flush=True)
    try:
        _restream_istek_guncelle()
    except Exception as hata:
        print(f"[RESTREAM] acilis atlandi: {hata}", flush=True)
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
    yanit = jsonify(paket)
    yanit.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    yanit.headers["Pragma"] = "no-cache"
    return yanit


def kontrol_yaniti():
    veri = request.get_json(silent=True) or {}
    islem = str(veri.get("islem") or "").strip().lower()
    if islem in ("baslat", "ac", "start"):
        return jsonify(_baslat(veri))
    if islem in ("durdur", "kapat", "stop"):
        return jsonify(_durdur(veri))
    if islem in ("ayar", "ayar_kaydet", "kaydet"):
        return jsonify(_ayar_kaydet(veri))
    if islem in ("webhook_test", "webhook_dene"):
        return jsonify(_webhook_test())
    if islem == "restream_parca":
        return jsonify(_restream_parca_al(veri))
    return jsonify({"success": False, "mesaj": "Geçersiz canlı yayın işlemi."}), 400


def meta_webhook_yaniti():
    """Facebook ve Instagram canlı yorumlarını ortak kuyruğa alır."""
    if request.method == "GET":
        return _meta_dogrulama_yaniti()
    if _kopru_kabul is not None and not _kopru_kabul():
        return jsonify({"success": False, "mesaj": "Köprü anahtarı geçersiz."}), 401
    veri = request.get_json(silent=True) or {}
    satirlar = _meta_satirlari(veri)
    if not satirlar:
        return jsonify({"success": False, "mesaj": "Yorum bulunamadı."}), 400
    sonuclar = []
    for satir in satirlar[:20]:
        sonuclar.append(
            havuz_yorumu_isle(
                "",
                satir.get("platform") or satir.get("kaynak") or "",
                satir.get("kullanici") or satir.get("username") or satir.get("user") or "",
                satir.get("mesaj") or satir.get("message") or satir.get("comment") or "",
            )
        )
    kabul = [oge for oge in sonuclar if oge.get("success")]
    govdeler = [{anahtar: deger for anahtar, deger in oge.items() if anahtar != "kod"} for oge in sonuclar]
    if kabul:
        _meta_not("Son yorum alındı", True)
        return jsonify({"success": True, "adet": len(kabul), "sonuclar": govdeler})
    kodlar = [int(oge.get("kod") or 400) for oge in sonuclar]
    kod = 409 if 409 in kodlar else (429 if 429 in kodlar else 400)
    return jsonify({"success": False, "adet": 0, "sonuclar": govdeler}), kod


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
        "dinleyiciler": _dinleyici_ozet(kayit),
        "push_public_key": _vapid_public() or "",
    }


def ozel_yayin_acik_mi():
    """Yönetici özel yayını Diğer dükkanında açık mı. Redis varsa işçiler onu esas alır."""
    if _REDIS is not None:
        uzak = _redis_oku()
        if isinstance(uzak, dict):
            norm = _kaydi_normallestir(uzak)
            kod = str(norm.get("aktif_dukkan") or "")
            yerel = str(_kopya().get("aktif_dukkan") or "")
            if kod != yerel:
                with _KILIT:
                    _BELLEK.update(norm)
            return kod == _SABIT_CANLI_DUKKAN
    return str(_kopya().get("aktif_dukkan") or "") == _SABIT_CANLI_DUKKAN


def _otonom_pas_uygula(pas):
    """Özel yayın açılıp kapanınca otonom sırayı app.py geri çağırır. Sayaç burada değişmez."""
    fn = _otonom_pas
    if not callable(fn):
        return
    try:
        fn(bool(pas))
    except Exception as hata:
        print(f"[CANLI YAYIN] otonom pas atlandi: {hata}", flush=True)


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
        _olcum_artir(False)
        return {"success": True, "tip": "yorum", "pey": False, "dukkan": kod}

    pey = _pey_karsilastir_ve_uygula(kod, platform_adi, ad, float(tutar))
    uygulandi = bool(pey.get("uygulandi"))
    _olcum_artir(uygulandi)
    return {
        "success": True,
        "tip": "pey" if pey.get("uygulandi") else "yorum",
        "pey": uygulandi,
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
    if kod != _SABIT_CANLI_DUKKAN:
        return {
            "success": False,
            "mesaj": "Özel canlı yayın yalnızca Diğer - Her Şey dükkanında başlatılır.",
        }
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
    _olcum_sifirla()
    try:
        platform_dinleyici_baslat(anlik)
    except Exception as hata:
        print(f"[CANLI YAYIN] dinleyici baslatilamadi: {hata}", flush=True)
    _olay_yayinla(anlik)
    _otomasyon_sinyali(anlik)
    try:
        _push_yayin_basladi(anlik)
    except Exception as hata:
        print(f"[CANLI YAYIN] web push atlandi: {hata}", flush=True)
    _otonom_pas_uygula(True)
    try:
        _restream_istek_guncelle()
    except Exception as hata:
        print(f"[RESTREAM] baslangic atlandi: {hata}", flush=True)
    paket = herkese_acik_durum()
    paket["success"] = True
    paket["ayarlar"] = _ayarlar_paketi()
    paket["mesaj"] = _dukkan_adi(kod) + " dükkanında canlı yayın başladı."
    return paket


def _durdur(veri):
    """Özel yayını kapatır. Mezat sayacı ve kalıcı otonom bayrağı değişmez; dükkan sıraya döner."""
    kod = _dukkan_kodu(veri.get("dukkan")) if veri.get("dukkan") else ""
    with _KILIT:
        kayit = _kopya()
        aktif = str(kayit.get("aktif_dukkan") or "")
        if kod and kod != _SABIT_CANLI_DUKKAN and kod != aktif:
            return {
                "success": False,
                "mesaj": "Özel canlı yayın yalnızca Diğer - Her Şey dükkanına aittir.",
            }
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
    try:
        platform_dinleyici_durdur()
    except Exception as hata:
        print(f"[CANLI YAYIN] dinleyici kapatilamadi: {hata}", flush=True)
    _kalici_yaz(anlik)
    _olay_yayinla(anlik)
    _otonom_pas_uygula(False)
    try:
        _restream_kopru_durdur()
    except Exception as hata:
        print(f"[RESTREAM] kapanis atlandi: {hata}", flush=True)
    paket = herkese_acik_durum()
    paket["success"] = True
    paket["ayarlar"] = _ayarlar_paketi()
    paket["mesaj"] = "Canlı yayın durduruldu. Diğer - Her Şey normal mezat düzenine döndü."
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
        try:
            platform_dinleyici_baslat(anlik)
        except Exception as hata:
            print(f"[CANLI YAYIN] dinleyici guncellenemedi: {hata}", flush=True)
        _olay_yayinla(anlik)
    try:
        _restream_istek_guncelle()
    except Exception as hata:
        print(f"[RESTREAM] ayar atlandi: {hata}", flush=True)
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
    if "restream_rtmp_url" in veri or "restream_stream_key" in veri:
        url, anahtar = _restream_url_ve_anahtar(
            veri.get("restream_rtmp_url", kayit.get("restream_rtmp_url")),
            veri.get("restream_stream_key", kayit.get("restream_stream_key")),
        )
        kayit["restream_rtmp_url"] = url
        kayit["restream_stream_key"] = anahtar
    if "restream_video_id" in veri:
        kayit["restream_video_id"] = str(veri.get("restream_video_id") or "").strip()[:256]
    if "restream_audio_id" in veri:
        kayit["restream_audio_id"] = str(veri.get("restream_audio_id") or "").strip()[:256]
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
            oda["sayac_uzatma_nesli"] = int(oda.get("sayac_uzatma_nesli") or 0) + 1
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


def _yorum_yayinla(dukkan, platform, kullanici, mesaj, reji_ek=None):
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
            "rozet": "[" + platform + "]",
        },
        room=dukkan,
    )
    try:
        _reji_duvar_satiri_yayinla(dukkan, platform, kullanici, mesaj, reji_ek)
    except Exception as hata:
        print(f"[CANLI YAYIN] reji duvari atlandi: {hata}", flush=True)


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
    durum_payload = {
        "aktif": bool(kod),
        "aktif_dukkan": kod,
        "dukkan_adi": _dukkan_adi(kod) if kod else "",
        "yayin_basligi": kayit.get("yayin_basligi") or "",
        "yayin_url": kayit.get("yayin_url") or "",
        "platformlar": _kanallar(kayit),
        "baslangic_zamani": kayit.get("baslangic_zamani") or "",
        "dinleyiciler": _dinleyici_ozet(kayit),
        "push_public_key": _vapid_public() or "",
    }
    _socketio.emit("aktif_canli_yayin", durum_payload, namespace="/")


def _otomasyon_sinyali(kayit):
    if str((kayit or {}).get("aktif_dukkan") or "") != _SABIT_CANLI_DUKKAN:
        return
    adres = _webhook_adresi(kayit)
    if not adres:
        return
    _arkaplanda(_webhook_gonder, adres, _paylasim_paketi(kayit))


def _webhook_test():
    if not _yonetici_mi():
        return {"success": False, "mesaj": "Yetki yok."}
    kayit = _kopya()
    adres = _webhook_adresi(kayit)
    if not adres:
        return {
            "success": False,
            "mesaj": "Webhook URL tanımlı değil. Panel alanını veya MAKE_N8N_WEBHOOK_URL değerini doldurun.",
        }
    paket = _paylasim_paketi(kayit)
    paket["test"] = True
    _arkaplanda(_webhook_gonder, adres, paket)
    cevap = herkese_acik_durum()
    cevap["success"] = True
    cevap["ayarlar"] = _ayarlar_paketi()
    cevap["mesaj"] = "Test webhook gönderimi başladı."
    return cevap


def _paylasim_paketi(kayit):
    """Diğer özel yayını için hikaye, X ve Telegram duyuru yükü."""
    kod = _SABIT_CANLI_DUKKAN
    sahne = _sahne_urun_ozeti(kod)
    baslik = str((kayit or {}).get("yayin_basligi") or "").strip()[:180]
    if not baslik:
        baslik = "Diğer - Her Şey Canlı Mezat"
    link = _SITE + "/?dukkan=" + kod
    gorsel = sahne.get("urun_gorsel_url") or (_SITE + "/static/img/varsayilan_urun.png")
    fiyat = _fiyat_json(sahne.get("guncel_fiyat"))
    lot = sahne.get("urun_lot") if sahne.get("urun_lot") not in (None, "") else ""
    urun_baslik = str(sahne.get("urun_baslik") or "")
    return {
        "event": "live_auction_started",
        "dukkan_kodu": kod,
        "dukkan_adi": "Diğer - Her Şey",
        "yayin_basligi": baslik,
        "urun_baslik": urun_baslik,
        "urun_lot": lot,
        "guncel_fiyat": fiyat,
        "urun_gorsel_url": gorsel,
        "katilim_linki": link,
        "hazir_metin": _hazir_paylasim_metni(baslik, urun_baslik, lot, fiyat, link),
        "gorsel": gorsel,
        "link": link,
        "dukkan": kod,
        "yayin_url": (kayit or {}).get("yayin_url") or "",
    }


def _hazir_paylasim_metni(baslik, urun_baslik, lot, fiyat, link):
    sahne = urun_baslik or "Sıradaki eser"
    lot_yazi = (" · LOT " + str(lot)) if lot not in (None, "") else ""
    return (
        "🔴 CANLI MEZAT BAŞLADI\n"
        "Diğer - Her Şey\n"
        + baslik
        + "\n"
        + sahne
        + lot_yazi
        + " — "
        + str(fiyat)
        + " TL\n"
        "Facebook, Instagram, X ve Telegram için katıl:\n"
        + link
        + "\n#canlimezat #halicantik"
    )


def _fiyat_json(sayi):
    try:
        deger = float(sayi or 0)
    except (TypeError, ValueError):
        return 0
    if deger.is_integer():
        return int(deger)
    return round(deger, 2)


def _sahne_urun_ozeti(dukkan):
    bos = {
        "urun_baslik": "",
        "urun_lot": "",
        "guncel_fiyat": 0,
        "urun_gorsel_url": _SITE + "/static/img/varsayilan_urun.png",
    }
    try:
        oda = (_odalar or {}).get(dukkan) or {}
        try:
            fiyat = float((oda.get("durum") or {}).get("pey") or 0)
        except (TypeError, ValueError):
            fiyat = 0.0
        urun_id = oda.get("aktif_urun_id")
        if not urun_id or _db is None or _urun_model is None:
            if fiyat > 0:
                bos["guncel_fiyat"] = _fiyat_json(fiyat)
            return bos
        urun = _db.session.get(_urun_model, urun_id)
        if urun is None:
            bos["guncel_fiyat"] = _fiyat_json(fiyat)
            return bos
        if fiyat <= 0:
            try:
                fiyat = float(getattr(urun, "guncel_fiyat", 0) or 0)
            except (TypeError, ValueError):
                fiyat = 0.0
        fotolar = list(getattr(urun, "fotograflar", None) or [])
        gorsel = str(fotolar[0] or "") if fotolar else str(getattr(urun, "video", "") or "")
        return {
            "urun_baslik": str(getattr(urun, "urun_adi", "") or "")[:180],
            "urun_lot": getattr(urun, "lot_no", "") or "",
            "guncel_fiyat": _fiyat_json(fiyat),
            "urun_gorsel_url": _mutlak(gorsel),
        }
    except Exception as hata:
        print(f"[CANLI YAYIN] sahne ozeti atlandi: {hata}", flush=True)
        try:
            if _db is not None:
                _db.session.rollback()
        except Exception:
            pass
        return bos
    finally:
        try:
            if _db is not None:
                _db.session.remove()
        except Exception:
            pass


def _webhook_gonder(adres, paket):
    try:
        import requests

        yanit = requests.post(adres, json=paket, timeout=8)
        if 200 <= int(getattr(yanit, "status_code", 0) or 0) < 300:
            _webhook_son_yaz(True)
            return
        _webhook_son_yaz(False)
        print(f"[CANLI YAYIN] webhook yanit: {getattr(yanit, 'status_code', '')}", flush=True)
    except Exception as hata:
        _webhook_son_yaz(False)
        print(f"[CANLI YAYIN] webhook gonderilemedi: {hata}", flush=True)


def _webhook_son_yaz(basarili):
    with _DINLEYICI_KILIT:
        _WEBHOOK_SON["basarili"] = bool(basarili)
        _WEBHOOK_SON["zaman"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _olcum_yayinla(True)


def _webhook_rozet(kayit):
    if not _webhook_adresi(kayit):
        return "kapali"
    with _DINLEYICI_KILIT:
        basarili = bool(_WEBHOOK_SON.get("basarili"))
    return "aktif" if basarili else "kapali"


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
        if anahtar == "youtube":
            temiz[anahtar] = _youtube_kaynak(deger)
        elif anahtar == "tiktok":
            temiz[anahtar] = _tiktok_kaynak(deger)
        elif anahtar == "twitch":
            temiz[anahtar] = _twitch_kaynak(deger)
        elif anahtar == "kick":
            temiz[anahtar] = _kick_kaynak(deger)
        else:
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


def bellek_kovalarini_temizle(ttl_sn=600):
    """10 dakikadan eski yorum hızı kayıtlarını düşürür."""
    import time

    esik = time.time() - float(ttl_sn)
    with _KILIT:
        silinecek = []
        for anahtar, pencere in list(_yorum_zamanlari.items()):
            kalan = [an for an in pencere if an > esik]
            if kalan:
                _yorum_zamanlari[anahtar] = kalan
            else:
                silinecek.append(anahtar)
        for anahtar in silinecek:
            _yorum_zamanlari.pop(anahtar, None)


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
        "restream_rtmp_url": kayit.get("restream_rtmp_url") or "",
        "restream_stream_key": kayit.get("restream_stream_key") or "",
        "restream_video_id": kayit.get("restream_video_id") or "",
        "restream_audio_id": kayit.get("restream_audio_id") or "",
        "restream_imza": _restream_imza(kayit),
        "reji_platformlari": _reji_listesi_temizle(kayit.get("reji_platformlari") or []),
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
    _db.session.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {_PUSH_TABLO} (
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
    norm = _kaydi_normallestir(uzak)
    with _KILIT:
        _BELLEK.update(norm)
    eski = str(uzak.get("aktif_dukkan") or "")
    if eski and eski != _SABIT_CANLI_DUKKAN:
        _kalici_yaz(norm)


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


def _db_oku(anahtar="durum"):
    satir = _db.session.execute(
        text(f"SELECT icerik FROM {_TABLO} WHERE anahtar = :anahtar"),
        {"anahtar": anahtar},
    ).first()
    if satir is None:
        return None
    try:
        veri = json.loads(satir[0] or "{}")
    except ValueError:
        return None
    return veri if isinstance(veri, dict) else None


def _db_yaz(kayit):
    _db_satir_yaz("durum", kayit)


def _db_satir_yaz(anahtar, kayit):
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
            "anahtar": anahtar,
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
    url, anahtar = _restream_url_ve_anahtar(
        ham.get("restream_rtmp_url") or "",
        ham.get("restream_stream_key") or "",
    )
    kayit["restream_rtmp_url"] = url
    kayit["restream_stream_key"] = anahtar
    kayit["restream_video_id"] = str(ham.get("restream_video_id") or "")[:256]
    kayit["restream_audio_id"] = str(ham.get("restream_audio_id") or "")[:256]
    if "reji_platformlari" in ham:
        kayit["reji_platformlari"] = _reji_listesi_temizle(ham.get("reji_platformlari"))
    else:
        kayit["reji_platformlari"] = _reji_varsayilan()
    if kayit["aktif_dukkan"] != _SABIT_CANLI_DUKKAN:
        kayit["aktif_dukkan"] = ""
        kayit["baslangic_zamani"] = ""
    elif kayit["aktif_dukkan"] not in _dukkanlar:
        kayit["aktif_dukkan"] = ""
        kayit["baslangic_zamani"] = ""
    return kayit


def push_abone_yaniti():
    veri = request.get_json(silent=True) or {}
    if str(veri.get("islem") or "").strip().lower() in ("sil", "cik", "unsubscribe"):
        adres = _push_endpoint(veri)
        if adres:
            _push_sil(adres)
        return jsonify({"success": True, "abone": _push_sayisi()})
    abone = _push_ayikla(veri)
    if not abone:
        return jsonify({"success": False, "mesaj": "Abonelik eksik."}), 400
    _push_kaydet(abone)
    return jsonify({"success": True, "abone": _push_sayisi()})


def platform_dinleyici_baslat(kayit=None):
    """YouTube, TikTok, Twitch ve Kick sohbetini mezat döngüsünden ayrı işçide açar."""
    kayit = kayit or _kopya()
    kod = str(kayit.get("aktif_dukkan") or "")
    if not kod:
        platform_dinleyici_durdur()
        return _dinleyici_ozet(kayit)
    kanallar = kayit.get("kanallar") or {}
    yt_ham = str(kanallar.get("youtube") or "")
    yt = _youtube_video_id(yt_ham, kayit.get("yayin_url") or "")
    tt_ham = str(kanallar.get("tiktok") or "")
    tt = _tiktok_kullanici(tt_ham)
    tw_ham = str(kanallar.get("twitch") or "")
    tw = _twitch_kanal(tw_ham)
    kk_ham = str(kanallar.get("kick") or "")
    kk = _kick_kanal(kk_ham)
    _dinleyici_yenile("youtube", yt, kod, _youtube_dongu)
    _dinleyici_yenile("tiktok", tt, kod, _tiktok_dongu)
    _dinleyici_yenile("twitch", tw, kod, _twitch_dongu)
    _dinleyici_yenile("kick", kk, kod, _kick_dongu)
    _meta_not("Yorum ucu açık", True)
    if (yt_ham.strip() or str(kayit.get("yayin_url") or "").strip()) and not yt:
        _dinleyici_mesaj_yaz("youtube", "YouTube video kimliği ayıklanamadı")
    if tt_ham.strip() and not tt:
        _dinleyici_mesaj_yaz("tiktok", "TikTok kullanıcı adı ayıklanamadı")
    if tw_ham.strip() and not tw:
        _dinleyici_mesaj_yaz("twitch", "Twitch kullanıcı adı ayıklanamadı")
    if kk_ham.strip() and not kk:
        _dinleyici_mesaj_yaz("kick", "Kick kullanıcı adı ayıklanamadı")
    return _dinleyici_ozet(kayit)


def platform_dinleyici_durdur():
    _dinleyici_yenile("youtube", "", "", None)
    _dinleyici_yenile("tiktok", "", "", None)
    _dinleyici_yenile("twitch", "", "", None)
    _dinleyici_yenile("kick", "", "", None)
    _meta_not("", False)


def _dinleyici_yenile(ad, hedef, dukkan, fn):
    eski_istemci = None
    baslatilacak = None
    with _DINLEYICI_KILIT:
        eski_dur = _DINLEYICI_DUR.get(ad)
        eski_isci = _DINLEYICI_IS.get(ad)
        ayni = (
            bool(hedef)
            and _DINLEYICI_HEDEF.get(ad) == hedef
            and _DINLEYICI_DUKKAN.get(ad) == dukkan
            and eski_isci is not None
            and getattr(eski_isci, "is_alive", lambda: False)()
        )
        if ayni:
            return
        if eski_dur is not None:
            eski_dur.set()
        eski_istemci = _DINLEYICI_ISTEMCI.get(ad)
        _DINLEYICI_HEDEF[ad] = hedef or ""
        _DINLEYICI_DUKKAN[ad] = dukkan or ""
        if not hedef or fn is None:
            _DINLEYICI_DURUM[ad] = "kapali"
            _DINLEYICI_MESAJ[ad] = ""
            _DINLEYICI_IS[ad] = None
            _DINLEYICI_DUR[ad] = None
        else:
            dur = threading.Event()
            _DINLEYICI_DUR[ad] = dur
            _DINLEYICI_DURUM[ad] = "kapali"
            _DINLEYICI_MESAJ[ad] = "Bağlanıyor"

            def govde(hedef=hedef, dukkan=dukkan, dur=dur, fn=fn, ad=ad):
                try:
                    fn(hedef, dukkan, dur)
                except Exception as hata:
                    _dinleyici_mesaj_yaz(ad, _hata_ozeti(hata), hedef)
                finally:
                    yayinla = False
                    with _DINLEYICI_KILIT:
                        if _DINLEYICI_HEDEF.get(ad) == hedef and _DINLEYICI_DUR.get(ad) is dur:
                            if _DINLEYICI_DURUM.get(ad) == "bagli":
                                _DINLEYICI_DURUM[ad] = "kapali"
                                if (_DINLEYICI_MESAJ.get(ad) or "") == "Sohbet bağlı":
                                    _DINLEYICI_MESAJ[ad] = "Dinleyici kapandı"
                                yayinla = True
                            else:
                                _DINLEYICI_DURUM[ad] = "kapali"
                    _dinleyici_kalici()
                    if yayinla:
                        _olcum_yayinla(zorla=True)

            isci = threading.Thread(target=govde, name="canli-" + ad, daemon=True)
            _DINLEYICI_IS[ad] = isci
            baslatilacak = isci
    if eski_istemci is not None:
        _istemci_kapat(eski_istemci)
    if baslatilacak is not None:
        baslatilacak.start()
    _dinleyici_kalici()


def _youtube_dongu(video_id, dukkan, dur):
    if not _PYTCHAT_VAR or pytchat is None:
        _pasif_uyar("youtube", "[CANLI YAYIN] pytchat kurulu degil, YouTube dinleyici pasif")
        _dinleyici_mesaj_yaz("youtube", "pytchat kurulu değil", video_id)
        return
    try:
        chat = pytchat.create(video_id=video_id)
    except Exception as hata:
        _dinleyici_mesaj_yaz("youtube", "YouTube bağlanamadı: " + _hata_ozeti(hata), video_id)
        return
    _dinleyici_durum_yaz("youtube", "bagli")
    try:
        while not dur.is_set():
            try:
                if hasattr(chat, "is_alive") and not chat.is_alive():
                    _dinleyici_mesaj_yaz("youtube", "YouTube yayını veya sohbeti şu an kapalı", video_id)
                    break
                demet = chat.get()
                ogeler = demet.sync_items() if hasattr(demet, "sync_items") else []
                for satir in ogeler:
                    if dur.is_set():
                        break
                    yazar = getattr(satir, "author", None)
                    ad = getattr(yazar, "name", None) or "YouTube"
                    _dinleyici_yorum_isle(dukkan, "youtube", ad, getattr(satir, "message", "") or "")
            except Exception as hata:
                _dinleyici_mesaj_yaz("youtube", "YouTube okuma hatası: " + _hata_ozeti(hata), video_id)
                break
            if dur.wait(1.0):
                break
    finally:
        for metod in ("terminate", "stop"):
            fn = getattr(chat, metod, None)
            if not callable(fn):
                continue
            try:
                fn()
                break
            except Exception:
                pass


def _tiktok_dongu(kullanici, dukkan, dur):
    unique = str(kullanici or "").strip().lstrip("@")
    if not _TIKTOK_VAR or TikTokLiveClient is None:
        _pasif_uyar("tiktok", "[CANLI YAYIN] TikTokLive kurulu degil, TikTok dinleyici pasif")
        _dinleyici_mesaj_yaz("tiktok", "TikTokLive kurulu değil", unique)
        return
    try:
        client = TikTokLiveClient(unique_id=unique)
    except Exception as hata:
        _dinleyici_mesaj_yaz("tiktok", "TikTok bağlanamadı: " + _hata_ozeti(hata), unique)
        return
    with _DINLEYICI_KILIT:
        _DINLEYICI_ISTEMCI["tiktok"] = client

    async def _yorum(event):
        user = getattr(event, "user", None)
        ad = "TikTok"
        if user is not None:
            ad = getattr(user, "nickname", None) or getattr(user, "unique_id", None) or ad
        _dinleyici_yorum_isle(dukkan, "tiktok", ad, getattr(event, "comment", None) or "")

    async def _baglandi(_event):
        _dinleyici_durum_yaz("tiktok", "bagli")

    try:
        from TikTokLive.events import CommentEvent, ConnectEvent

        client.on(CommentEvent)(_yorum)
        client.on(ConnectEvent)(_baglandi)
    except Exception:
        try:
            client.on("comment")(_yorum)
            client.on("connect")(_baglandi)
        except Exception as hata:
            _dinleyici_mesaj_yaz("tiktok", "TikTok olay kaydı kurulamadı: " + _hata_ozeti(hata), unique)
            return

    def _izle():
        dur.wait()
        _istemci_kapat(client)

    threading.Thread(target=_izle, name="canli-tiktok-kapat", daemon=True).start()
    try:
        client.run()
    except Exception as hata:
        _dinleyici_mesaj_yaz("tiktok", "TikTok bağlantı hatası: " + _hata_ozeti(hata), unique)
    finally:
        with _DINLEYICI_KILIT:
            if _DINLEYICI_ISTEMCI.get("tiktok") is client:
                _DINLEYICI_ISTEMCI["tiktok"] = None


_PASIF_UYARI = set()


def _pasif_uyar(ad, metin):
    if ad in _PASIF_UYARI:
        return
    _PASIF_UYARI.add(ad)
    print(metin, flush=True)


def _dinleyici_yorum_isle(dukkan, platform, kullanici, mesaj):
    try:
        if _app is not None:
            with _app.app_context():
                havuz_yorumu_isle(dukkan, platform, kullanici, mesaj)
        else:
            havuz_yorumu_isle(dukkan, platform, kullanici, mesaj)
    except Exception as hata:
        print(f"[CANLI YAYIN] {platform} yorum: {hata}", flush=True)
        try:
            if _db is not None:
                _db.session.rollback()
        except Exception:
            pass
    finally:
        try:
            if _db is not None:
                _db.session.remove()
        except Exception:
            pass


def _istemci_kapat(client):
    if client is None:
        return
    for ad in ("stop", "disconnect", "close"):
        fn = getattr(client, ad, None)
        if not callable(fn):
            continue
        try:
            sonuc = fn()
        except Exception:
            continue
        if hasattr(sonuc, "__await__"):
            try:
                import asyncio

                loop = getattr(client, "loop", None)
                if loop is not None and loop.is_running():
                    asyncio.run_coroutine_threadsafe(sonuc, loop)
                else:
                    sonuc.close()
            except Exception:
                try:
                    sonuc.close()
                except Exception:
                    pass
        return


def _dinleyici_durum_yaz(ad, durum):
    with _DINLEYICI_KILIT:
        bagli = durum == "bagli"
        _DINLEYICI_DURUM[ad] = "bagli" if bagli else "kapali"
        if bagli:
            _DINLEYICI_MESAJ[ad] = "Sohbet bağlı"
    _dinleyici_kalici()
    _olcum_yayinla(zorla=True)


def _dinleyici_mesaj_yaz(ad, metin, hedef=None):
    kisa = _kisa_durum(metin)
    with _DINLEYICI_KILIT:
        if hedef is not None and (_DINLEYICI_HEDEF.get(ad) or "") != hedef:
            return
        _DINLEYICI_MESAJ[ad] = kisa
        _DINLEYICI_DURUM[ad] = "kapali"
    if kisa:
        print(f"[CANLI YAYIN] {ad}: {kisa}", flush=True)
    _dinleyici_kalici()
    _olcum_yayinla(zorla=True)


def _kisa_durum(metin):
    return " ".join(str(metin or "").split())[:160]


def _hata_ozeti(hata):
    ad = type(hata).__name__
    metin = _kisa_durum(hata)
    birlesik = (ad + " " + metin).lower()
    if "offline" in birlesik or "not online" in birlesik or "is offline" in birlesik:
        return "Yayın şu an kapalı"
    if "no module" in birlesik or "not installed" in birlesik or "modulenotfound" in birlesik:
        return "Gerekli paket kurulu değil"
    if not metin:
        return ad[:160]
    return metin


def _dinleyici_anlik():
    with _DINLEYICI_KILIT:
        return {
            "youtube": _DINLEYICI_DURUM.get("youtube") or "kapali",
            "tiktok": _DINLEYICI_DURUM.get("tiktok") or "kapali",
            "twitch": _DINLEYICI_DURUM.get("twitch") or "kapali",
            "kick": _DINLEYICI_DURUM.get("kick") or "kapali",
            "meta": _DINLEYICI_DURUM.get("meta") or "kapali",
            "youtube_mesaj": _DINLEYICI_MESAJ.get("youtube") or "",
            "tiktok_mesaj": _DINLEYICI_MESAJ.get("tiktok") or "",
            "twitch_mesaj": _DINLEYICI_MESAJ.get("twitch") or "",
            "kick_mesaj": _DINLEYICI_MESAJ.get("kick") or "",
            "meta_mesaj": _DINLEYICI_MESAJ.get("meta") or "",
            "toplam_yorum": int(_OLCUM.get("toplam_yorum") or 0),
            "otomatik_pey": int(_OLCUM.get("otomatik_pey") or 0),
        }


def _dinleyici_ozet(kayit=None):
    if kayit is None:
        kayit = _kopya()
    anlik = _dinleyici_anlik()

    def _durum(ad):
        deger = anlik.get(ad) or "kapali"
        return deger if deger in ("bagli", "kapali") else "kapali"

    return {
        "pytchat": _PYTCHAT_VAR,
        "tiktoklive": _TIKTOK_VAR,
        "youtube": _durum("youtube"),
        "tiktok": _durum("tiktok"),
        "twitch": _durum("twitch"),
        "kick": _durum("kick"),
        "meta": _durum("meta"),
        "youtube_mesaj": anlik.get("youtube_mesaj") or "",
        "tiktok_mesaj": anlik.get("tiktok_mesaj") or "",
        "twitch_mesaj": anlik.get("twitch_mesaj") or "",
        "kick_mesaj": anlik.get("kick_mesaj") or "",
        "meta_mesaj": anlik.get("meta_mesaj") or "",
        "webhook": _webhook_rozet(kayit),
        "push_abone": _push_sayisi(),
        "toplam_yorum": anlik["toplam_yorum"],
        "otomatik_pey": anlik["otomatik_pey"],
    }


def _olcum_artir(pey):
    with _DINLEYICI_KILIT:
        _OLCUM["toplam_yorum"] = int(_OLCUM.get("toplam_yorum") or 0) + 1
        if pey:
            _OLCUM["otomatik_pey"] = int(_OLCUM.get("otomatik_pey") or 0) + 1
    _dinleyici_kalici()
    _olcum_yayinla(False)


def _olcum_sifirla():
    with _DINLEYICI_KILIT:
        _OLCUM["toplam_yorum"] = 0
        _OLCUM["otomatik_pey"] = 0
    _dinleyici_kalici()
    _olcum_yayinla(True)


def _olcum_yukle():
    veri = _redis_json(_DINLEYICI_ANAHTAR)
    if not isinstance(veri, dict):
        veri = _db_oku("dinleyici") if _db is not None else None
    if not isinstance(veri, dict):
        return
    with _DINLEYICI_KILIT:
        _OLCUM["toplam_yorum"] = int(veri.get("toplam_yorum") or 0)
        _OLCUM["otomatik_pey"] = int(veri.get("otomatik_pey") or 0)


def _dinleyici_kalici():
    anlik = _dinleyici_anlik()
    if _REDIS is not None:
        try:
            _REDIS.set(_DINLEYICI_ANAHTAR, json.dumps(anlik, ensure_ascii=False))
        except Exception as hata:
            print(f"[CANLI YAYIN] dinleyici redis: {hata}", flush=True)
    if _app is None or _db is None:
        return
    try:
        with _app.app_context():
            _db_satir_yaz("dinleyici", anlik)
    except Exception as hata:
        print(f"[CANLI YAYIN] dinleyici veritabani: {hata}", flush=True)
        try:
            _db.session.rollback()
        except Exception:
            pass


def _olcum_yayinla(zorla):
    global _SON_OLCUM_YAYIN
    import time

    simdi = time.time()
    if not zorla and simdi - _SON_OLCUM_YAYIN < 1.5:
        return
    _SON_OLCUM_YAYIN = simdi
    if _socketio is None:
        return
    try:
        _socketio.emit("canli_havuz_olcum", _dinleyici_ozet())
    except Exception as hata:
        print(f"[CANLI YAYIN] olcum yayini: {hata}", flush=True)


def _redis_json(anahtar):
    if _REDIS is None:
        return None
    try:
        ham = _REDIS.get(anahtar)
    except Exception as hata:
        print(f"[CANLI YAYIN] Redis okunamadi: {hata}", flush=True)
        return None
    if not ham:
        return None
    try:
        return json.loads(ham)
    except ValueError:
        return None


def _youtube_video_id(*adaylar):
    """Tam YouTube adresinden veya çıplak kimlikten 11 karakterlik video ID döndürür."""
    for ham in adaylar:
        metin = unquote(str(ham or "").strip())
        if not metin:
            continue
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", metin):
            return metin
        try:
            parca = urlparse(metin if "://" in metin else "https://" + metin)
        except ValueError:
            parca = None
        if parca is not None:
            for anahtar in ("v", "vi"):
                for deger in parse_qs(parca.query).get(anahtar) or []:
                    if re.fullmatch(r"[A-Za-z0-9_-]{11}", deger):
                        return deger
            yol = unquote(parca.path or "")
            eslesen_yol = re.search(
                r"/(?:live|embed|shorts|v|e)/([A-Za-z0-9_-]{11})(?:[/?#]|$)",
                yol,
                flags=re.IGNORECASE,
            )
            if eslesen_yol:
                return eslesen_yol.group(1)
            if "youtu.be" in (parca.netloc or "").lower():
                aday = yol.strip("/").split("/")[0]
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", aday):
                    return aday
        eslesen = re.search(
            r"(?:[?&]v=|[?&]vi=|youtu\.be/|youtube\.com/(?:live|embed|shorts|v|e)/)([A-Za-z0-9_-]{11})",
            metin,
            flags=re.IGNORECASE,
        )
        if eslesen:
            return eslesen.group(1)
    return ""


def _youtube_kaynak(deger):
    metin = str(deger or "").strip()
    if not metin:
        return ""
    if not metin.lower().startswith("http"):
        return _youtube_video_id(metin)
    return _guvenli_url(metin)


def _tiktok_kullanici(ham):
    """Tam TikTok adresinden veya @ad girişinden @ işaretsiz kullanıcı adını döndürür."""
    metin = unquote(str(ham or "").strip())
    if not metin:
        return ""
    metin = metin.split("?")[0].split("#")[0].strip()
    eslesen = re.search(
        r"tiktok\.com/@([A-Za-z0-9._]{2,32})(?:/|$)",
        metin,
        flags=re.IGNORECASE,
    )
    if eslesen:
        return eslesen.group(1)
    sade = metin.rstrip("/").split("/")[0].strip()
    if re.fullmatch(r"@?[A-Za-z0-9._]{2,32}", sade):
        return sade.lstrip("@")
    return ""


def _tiktok_kaynak(deger):
    metin = str(deger or "").strip()
    if not metin:
        return ""
    if metin.lower().startswith("http"):
        return _guvenli_url(metin)
    return _tiktok_kullanici(metin)


def _twitch_kanal(ham):
    metin = unquote(str(ham or "").strip())
    if not metin:
        return ""
    if re.search(r"twitch\.tv/(?:videos|directory|settings)(?:/|$)", metin, flags=re.IGNORECASE):
        return ""
    eslesen = re.search(r"twitch\.tv/([A-Za-z0-9_]{3,25})(?:[/?#]|$)", metin, flags=re.IGNORECASE)
    if eslesen:
        return eslesen.group(1).lower()
    sade = metin.split("?")[0].split("#")[0].rstrip("/").split("/")[-1].strip()
    if re.fullmatch(r"@?[A-Za-z0-9_]{3,25}", sade):
        return sade.lstrip("@").lower()
    return ""


def _twitch_kaynak(deger):
    metin = str(deger or "").strip()
    if not metin:
        return ""
    if metin.lower().startswith("http"):
        return _guvenli_url(metin)
    return _twitch_kanal(metin)


def _kick_kanal(ham):
    metin = unquote(str(ham or "").strip())
    if not metin:
        return ""
    eslesen = re.search(r"kick\.com/([A-Za-z0-9_\-]{2,25})(?:[/?#]|$)", metin, flags=re.IGNORECASE)
    if eslesen:
        return eslesen.group(1).lower()
    sade = metin.split("?")[0].split("#")[0].rstrip("/").split("/")[-1].strip()
    if re.fullmatch(r"@?[A-Za-z0-9_\-]{2,25}", sade):
        return sade.lstrip("@").lower()
    return ""


def _kick_kaynak(deger):
    metin = str(deger or "").strip()
    if not metin:
        return ""
    if metin.lower().startswith("http"):
        return _guvenli_url(metin)
    return _kick_kanal(metin)


def _meta_not(metin, bagli):
    with _DINLEYICI_KILIT:
        _DINLEYICI_DURUM["meta"] = "bagli" if bagli else "kapali"
        _DINLEYICI_MESAJ["meta"] = _kisa_durum(metin) if bagli else ""
    _olcum_yayinla(True)


def _meta_dogrulama_yaniti():
    from flask import Response

    mode = str(request.args.get("hub.mode") or "")
    token = str(request.args.get("hub.verify_token") or "")
    challenge = str(request.args.get("hub.challenge") or "")
    beklenen = (
        os.environ.get("META_WEBHOOK_VERIFY_TOKEN") or os.environ.get("ENTEGRASYON_TOKEN") or ""
    ).strip()
    if mode == "subscribe" and challenge and beklenen and token == beklenen:
        return Response(challenge, mimetype="text/plain")
    return Response("forbidden", status=403, mimetype="text/plain")


def _meta_platform(ham):
    temiz = str(ham or "").strip().lower()
    if temiz in ("ig", "insta", "instagram"):
        return "instagram"
    if temiz in ("fb", "facebook"):
        return "facebook"
    return ""


def _meta_satirlari(veri):
    if not isinstance(veri, dict):
        return []
    duz = _meta_duz_satir(veri)
    if duz:
        return [duz]
    satirlar = []
    for entry in veri.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            value = (change or {}).get("value") if isinstance(change, dict) else None
            if not isinstance(value, dict):
                continue
            metin = value.get("message") or value.get("text") or ""
            if not str(metin or "").strip():
                continue
            gonderen = value.get("from") if isinstance(value.get("from"), dict) else {}
            platform = _meta_platform(value.get("platform") or value.get("item")) or "facebook"
            satirlar.append(
                {
                    "platform": platform,
                    "kullanici": gonderen.get("username") or gonderen.get("name") or platform,
                    "mesaj": metin,
                }
            )
    return satirlar


def _meta_duz_satir(veri):
    metin = veri.get("mesaj") or veri.get("message") or veri.get("comment") or ""
    if not str(metin or "").strip():
        return None
    platform = _meta_platform(veri.get("platform") or veri.get("kaynak"))
    if not platform:
        return None
    return {
        "platform": platform,
        "kullanici": veri.get("kullanici") or veri.get("username") or veri.get("user") or "",
        "mesaj": metin,
    }


def _twitch_dongu(kanal, dukkan, dur):
    try:
        bag = _ws_baglan("irc-ws.chat.twitch.tv", 443, "/")
    except Exception as hata:
        _dinleyici_mesaj_yaz("twitch", "Twitch bağlanamadı: " + _hata_ozeti(hata), kanal)
        return
    with _DINLEYICI_KILIT:
        _DINLEYICI_ISTEMCI["twitch"] = bag
    parmak = int(hashlib.sha1(str(kanal).encode("utf-8")).hexdigest()[:6], 16)
    nick = "justinfan" + str(10000 + (parmak % 80000))
    try:
        bag.yazi("PASS SCHMOOPIIE\r\nNICK " + nick + "\r\nJOIN #" + kanal + "\r\n")
        while not dur.is_set():
            try:
                metin = bag.oku(1.0)
            except TimeoutError:
                continue
            except Exception as hata:
                _dinleyici_mesaj_yaz("twitch", "Twitch bağlantı hatası: " + _hata_ozeti(hata), kanal)
                break
            if metin is None:
                _dinleyici_mesaj_yaz("twitch", "Twitch sohbeti kapandı", kanal)
                break
            for satir in str(metin).split("\r\n"):
                if not satir:
                    continue
                if satir.startswith("PING"):
                    bag.yazi("PONG :tmi.twitch.tv\r\n")
                    continue
                if " 001 " in satir or (" join #" + kanal) in satir.lower():
                    _dinleyici_durum_yaz("twitch", "bagli")
                eslesen = re.match(r":([^! ]+)![^\r\n]* PRIVMSG #[^\s]+ :(.*)$", satir)
                if eslesen:
                    _dinleyici_yorum_isle(dukkan, "twitch", eslesen.group(1), eslesen.group(2))
    finally:
        with _DINLEYICI_KILIT:
            if _DINLEYICI_ISTEMCI.get("twitch") is bag:
                _DINLEYICI_ISTEMCI["twitch"] = None
        bag.kapat()


def _kick_sohbet_id(slug):
    try:
        import requests

        yanit = requests.get(
            "https://kick.com/api/v2/channels/" + str(slug),
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=12,
        )
        if int(getattr(yanit, "status_code", 0) or 0) >= 400:
            return ""
        veri = yanit.json() if hasattr(yanit, "json") else {}
    except Exception as hata:
        print(f"[CANLI YAYIN] kick oda: {hata}", flush=True)
        return ""
    oda = veri.get("chatroom") if isinstance(veri, dict) else None
    if isinstance(oda, dict) and oda.get("id"):
        return str(oda.get("id"))
    return ""


def _kick_dongu(slug, dukkan, dur):
    chat_id = _kick_sohbet_id(slug)
    if not chat_id:
        _dinleyici_mesaj_yaz("kick", "Kick sohbet odası bulunamadı", slug)
        return
    yol = "/app/32cbd69e4b950bf97679?protocol=7&client=js&version=7.6.0&flash=false"
    try:
        bag = _ws_baglan("ws-us2.pusher.com", 443, yol)
    except Exception as hata:
        _dinleyici_mesaj_yaz("kick", "Kick bağlanamadı: " + _hata_ozeti(hata), slug)
        return
    with _DINLEYICI_KILIT:
        _DINLEYICI_ISTEMCI["kick"] = bag
    abone = False
    abonelik = json.dumps(
        {
            "event": "pusher:subscribe",
            "data": {"auth": "", "channel": "chatrooms." + chat_id + ".v2"},
        }
    )
    try:
        while not dur.is_set():
            try:
                metin = bag.oku(1.0)
            except TimeoutError:
                continue
            except Exception as hata:
                _dinleyici_mesaj_yaz("kick", "Kick bağlantı hatası: " + _hata_ozeti(hata), slug)
                break
            if metin is None:
                _dinleyici_mesaj_yaz("kick", "Kick sohbeti kapandı", slug)
                break
            if not metin:
                continue
            try:
                paket = json.loads(metin)
            except ValueError:
                continue
            if not isinstance(paket, dict):
                continue
            olay = str(paket.get("event") or "")
            if olay == "pusher:ping":
                bag.yazi(json.dumps({"event": "pusher:pong", "data": {}}))
                continue
            if not abone and olay == "pusher:connection_established":
                bag.yazi(abonelik)
                abone = True
                _dinleyici_durum_yaz("kick", "bagli")
                continue
            if "ChatMessage" not in olay:
                continue
            ham = paket.get("data")
            if isinstance(ham, str):
                try:
                    ham = json.loads(ham)
                except ValueError:
                    continue
            if not isinstance(ham, dict):
                continue
            gonderen = ham.get("sender") if isinstance(ham.get("sender"), dict) else {}
            ad = gonderen.get("username") or gonderen.get("slug") or "Kick"
            icerik = ham.get("content") or ham.get("message") or ""
            _dinleyici_yorum_isle(dukkan, "kick", ad, icerik)
    finally:
        with _DINLEYICI_KILIT:
            if _DINLEYICI_ISTEMCI.get("kick") is bag:
                _DINLEYICI_ISTEMCI["kick"] = None
        bag.kapat()


class _WsBag:
    def __init__(self, sock):
        self.sock = sock
        self._tampon = ""

    def yazi(self, metin):
        _ws_gonder(self.sock, 0x1, str(metin or "").encode("utf-8"))

    def oku(self, zaman):
        self.sock.settimeout(zaman)
        try:
            gelen = _ws_al(self.sock)
        except socket.timeout:
            raise TimeoutError("websocket zaman")
        if gelen is None:
            return None
        fin, opcode, veri = gelen
        if opcode == 0x9:
            _ws_gonder(self.sock, 0xA, veri)
            return ""
        if opcode == 0x8:
            return None
        if opcode == 0xA:
            return ""
        if opcode in (0x0, 0x1):
            self._tampon += veri.decode("utf-8", "replace")
            if fin:
                metin = self._tampon
                self._tampon = ""
                return metin
            return ""
        return ""

    def kapat(self):
        try:
            _ws_gonder(self.sock, 0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def close(self):
        self.kapat()


def _ws_baglan(host, port, yol):
    ham = socket.create_connection((host, int(port)), timeout=12)
    sock = ssl.create_default_context().wrap_socket(ham, server_hostname=host)
    anahtar = base64.b64encode(os.urandom(16)).decode("ascii")
    istek = (
        "GET " + (yol or "/") + " HTTP/1.1\r\n"
        "Host: " + host + "\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: " + anahtar + "\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "User-Agent: HalicAntikCanli/1.0\r\n\r\n"
    )
    sock.sendall(istek.encode("ascii"))
    sock.settimeout(12)
    baslik = b""
    while b"\r\n\r\n" not in baslik and len(baslik) < 8192:
        parca = sock.recv(1024)
        if not parca:
            break
        baslik += parca
    ilk = baslik.split(b"\r\n", 1)[0]
    if b" 101 " not in ilk:
        try:
            sock.close()
        except Exception:
            pass
        raise ConnectionError("websocket el sikismasi basarisiz")
    return _WsBag(sock)


def _ws_gonder(sock, opcode, veri):
    veri = veri or b""
    if len(veri) > 65535:
        veri = veri[:65535]
    bas = bytearray([0x80 | (opcode & 0x0F)])
    n = len(veri)
    if n < 126:
        bas.append(0x80 | n)
    else:
        bas.append(0x80 | 126)
        bas.extend(n.to_bytes(2, "big"))
    maske = os.urandom(4)
    bas.extend(maske)
    govde = bytes(bayt ^ maske[i % 4] for i, bayt in enumerate(veri))
    sock.sendall(bytes(bas) + govde)


def _ws_al(sock):
    def tam(adet):
        buf = b""
        while len(buf) < adet:
            parca = sock.recv(adet - len(buf))
            if not parca:
                raise ConnectionError("websocket kapandi")
            buf += parca
        return buf

    ilk = tam(2)
    fin = bool(ilk[0] & 0x80)
    opcode = ilk[0] & 0x0F
    uzun = ilk[1] & 0x7F
    if uzun == 126:
        uzun = int.from_bytes(tam(2), "big")
    elif uzun == 127:
        uzun = int.from_bytes(tam(8), "big")
    if uzun > 1000000:
        raise ConnectionError("websocket cercevesi buyuk")
    maskeli = bool(ilk[1] & 0x80)
    maske = tam(4) if maskeli else b""
    veri = tam(uzun) if uzun else b""
    if maskeli:
        veri = bytes(bayt ^ maske[i % 4] for i, bayt in enumerate(veri))
    return fin, opcode, veri


def xml_ciktisina_canli_ekle(govde, sahneler):
    """Mevcut urun etiketlerini olduğu gibi bırakıp aktif sahneyi ekler."""
    metin = str(govde or "")
    if "</urunler>" not in metin or "<canli_yayin_durumu>" in metin:
        return metin
    liste = []
    for sahne in sahneler or []:
        if not isinstance(sahne, dict):
            continue
        kod = _xml_dukkan_kodu(sahne.get("dukkan"))
        if kod:
            liste.append(sahne)
    if not liste:
        return metin
    kullanilan = set()

    def degistir(eslesen):
        blok = eslesen.group(0)
        dukkan = _xml_etiket(blok, "dukkan")
        baslik = _xml_etiket(blok, "baslik")
        for sira, sahne in enumerate(liste):
            if sira in kullanilan:
                continue
            if _xml_esit(sahne.get("dukkan"), dukkan) and baslik and _xml_esit(sahne.get("baslik"), baslik):
                kullanilan.add(sira)
                return blok.replace("</urun>", _xml_canli_etiketleri(sahne) + "</urun>", 1)
        return blok

    metin = re.sub(r"<urun>.*?</urun>", degistir, metin, flags=re.DOTALL)
    ek = [_xml_canli_sahne(sahne) for sira, sahne in enumerate(liste) if sira not in kullanilan]
    if ek:
        metin = metin.replace("</urunler>", "\n".join(ek) + "\n</urunler>", 1)
    return metin


def _xml_dukkan_kodu(ham):
    kod = str(ham or "").strip().lower()
    if re.fullmatch(r"[a-z0-9_-]{1,32}", kod):
        return kod
    return ""


def _xml_etiket(blok, ad):
    eslesen = re.search(r"<" + re.escape(ad) + r">(.*)</" + re.escape(ad) + r">", blok, flags=re.DOTALL)
    if not eslesen:
        return ""
    return html.unescape(eslesen.group(1)).strip()


def _xml_esit(sol, sag):
    return " ".join(str(sol or "").split()).casefold() == " ".join(str(sag or "").split()).casefold()


def _xml_canli_etiketleri(sahne):
    kod = _xml_dukkan_kodu(sahne.get("dukkan"))
    link = _SITE + "/?dukkan=" + kod
    return (
        "    <canli_yayin_durumu>aktif</canli_yayin_durumu>\n"
        "    <dukkan_linki>" + html.escape(link, quote=True) + "</dukkan_linki>\n"
        "  "
    )


def _xml_canli_sahne(sahne):
    kod = _xml_dukkan_kodu(sahne.get("dukkan"))
    link = _SITE + "/?dukkan=" + kod
    return (
        "  <canli_sahne>\n"
        "    <canli_yayin_durumu>aktif</canli_yayin_durumu>\n"
        "    <baslik>" + html.escape(str(sahne.get("baslik") or ""), quote=True) + "</baslik>\n"
        "    <lot>" + html.escape(str(sahne.get("lot") or ""), quote=True) + "</lot>\n"
        "    <dukkan>" + html.escape(kod, quote=True) + "</dukkan>\n"
        "    <dukkan_linki>" + html.escape(link, quote=True) + "</dukkan_linki>\n"
        "  </canli_sahne>"
    )


def _vapid_public():
    kayit = _VAPID or {}
    return str(kayit.get("public_b64") or "")


def _vapid_kaydi():
    return dict(_VAPID or {})


def _vapid_hazirla():
    global _VAPID
    public = (os.environ.get("VAPID_PUBLIC_KEY") or "").strip()
    private = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
    if public and private:
        try:
            _VAPID = {
                "public_b64": _vapid_public_norm(public),
                "private_pem": _vapid_private_norm(private),
            }
            return
        except Exception as hata:
            print(f"[CANLI YAYIN] VAPID ortam anahtari okunamadi: {hata}", flush=True)
    kayitli = _vapid_oku()
    if isinstance(kayitli, dict) and kayitli.get("public_b64") and kayitli.get("private_pem"):
        _VAPID = {"public_b64": kayitli["public_b64"], "private_pem": kayitli["private_pem"]}
        return
    try:
        uretilen = _vapid_uret()
    except Exception as hata:
        print(f"[CANLI YAYIN] VAPID uretilemedi: {hata}", flush=True)
        _VAPID = None
        return
    _VAPID = uretilen
    _vapid_yaz(uretilen)
    print("[CANLI YAYIN] VAPID anahtari uretildi", flush=True)


def _vapid_oku():
    redis_kayit = _redis_json(_VAPID_ANAHTAR)
    if isinstance(redis_kayit, dict) and redis_kayit.get("public_b64") and redis_kayit.get("private_pem"):
        return redis_kayit
    if _db is None:
        return None
    try:
        return _db_oku("vapid")
    except Exception as hata:
        print(f"[CANLI YAYIN] VAPID okunamadi: {hata}", flush=True)
        try:
            _db.session.rollback()
        except Exception:
            pass
        return None


def _vapid_yaz(kayit):
    if _REDIS is not None:
        try:
            _REDIS.set(_VAPID_ANAHTAR, json.dumps(kayit))
        except Exception as hata:
            print(f"[CANLI YAYIN] VAPID redis: {hata}", flush=True)
    if _app is None or _db is None:
        return
    try:
        with _app.app_context():
            _db_satir_yaz("vapid", kayit)
    except Exception as hata:
        print(f"[CANLI YAYIN] VAPID veritabani: {hata}", flush=True)
        try:
            _db.session.rollback()
        except Exception:
            pass


def _vapid_uret():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    anahtar = ec.generate_private_key(ec.SECP256R1())
    private_pem = anahtar.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public = anahtar.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return {
        "private_pem": private_pem,
        "public_b64": base64.urlsafe_b64encode(public).decode("ascii").rstrip("="),
    }


def _b64url_coz(deger):
    metin = str(deger or "").strip()
    if not metin:
        return None
    ek = "=" * ((4 - len(metin) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(metin + ek)
    except Exception:
        return None


def _vapid_public_norm(deger):
    metin = str(deger or "").strip().strip('"')
    if "BEGIN" not in metin:
        return metin
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    anahtar = load_pem_public_key(metin.encode("utf-8"))
    ham = anahtar.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(ham).decode("ascii").rstrip("=")


def _vapid_private_norm(deger):
    metin = str(deger or "").strip().strip('"').replace("\\n", "\n")
    if "BEGIN" in metin:
        return metin
    ham = _b64url_coz(metin)
    if not ham or len(ham) != 32:
        return metin
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    sayi = int.from_bytes(ham, "big")
    anahtar = ec.derive_private_key(sayi, ec.SECP256R1())
    return anahtar.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _push_endpoint(veri):
    adres = str((veri or {}).get("endpoint") or "").strip()
    if not adres.startswith("https://") or len(adres) > 1800:
        return ""
    return adres


def _push_ayikla(veri):
    adres = _push_endpoint(veri)
    anahtarlar = (veri or {}).get("keys") or {}
    p256 = str(anahtarlar.get("p256dh") or (veri or {}).get("p256dh") or "").strip()
    auth = str(anahtarlar.get("auth") or (veri or {}).get("auth") or "").strip()
    if not adres or not p256 or not auth or len(p256) > 300 or len(auth) > 200:
        return None
    return {"endpoint": adres, "keys": {"p256dh": p256, "auth": auth}}


def _push_anahtar(endpoint):
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:40]


def _push_sayisi():
    with _PUSH_KILIT:
        return len(_PUSH)


def _push_liste():
    with _PUSH_KILIT:
        return list(_PUSH.values())


def _push_kaydet(abone):
    with _PUSH_KILIT:
        if len(_PUSH) >= 5000 and abone["endpoint"] not in _PUSH:
            ilk = next(iter(_PUSH))
            _push_sil_kilit_disi(ilk)
        _PUSH[abone["endpoint"]] = abone
    _push_kalici(abone)


def _push_sil(endpoint):
    if not endpoint:
        return
    with _PUSH_KILIT:
        _PUSH.pop(endpoint, None)
    _push_kalici_sil(endpoint)


def _push_sil_kilit_disi(endpoint):
    _PUSH.pop(endpoint, None)
    _push_kalici_sil(endpoint)


def _push_kalici(abone):
    anahtar = _push_anahtar(abone["endpoint"])
    govde = json.dumps(abone, ensure_ascii=False)
    if _REDIS is not None:
        try:
            _REDIS.hset(_PUSH_ANAHTAR, anahtar, govde)
        except Exception as hata:
            print(f"[CANLI YAYIN] push redis: {hata}", flush=True)
    if _app is None or _db is None:
        return
    try:
        with _app.app_context():
            _db.session.execute(
                text(
                    f"""
                    INSERT INTO {_PUSH_TABLO} (anahtar, icerik, guncelleme)
                    VALUES (:anahtar, :icerik, :an)
                    ON CONFLICT(anahtar) DO UPDATE SET
                        icerik = excluded.icerik,
                        guncelleme = excluded.guncelleme
                    """
                ),
                {
                    "anahtar": anahtar,
                    "icerik": govde,
                    "an": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )
            _db.session.commit()
    except Exception as hata:
        print(f"[CANLI YAYIN] push veritabani: {hata}", flush=True)
        try:
            _db.session.rollback()
        except Exception:
            pass


def _push_kalici_sil(endpoint):
    anahtar = _push_anahtar(endpoint)
    if _REDIS is not None:
        try:
            _REDIS.hdel(_PUSH_ANAHTAR, anahtar)
        except Exception as hata:
            print(f"[CANLI YAYIN] push redis sil: {hata}", flush=True)
    if _app is None or _db is None:
        return
    try:
        with _app.app_context():
            _db.session.execute(
                text(f"DELETE FROM {_PUSH_TABLO} WHERE anahtar = :anahtar"),
                {"anahtar": anahtar},
            )
            _db.session.commit()
    except Exception as hata:
        print(f"[CANLI YAYIN] push silinemedi: {hata}", flush=True)
        try:
            _db.session.rollback()
        except Exception:
            pass


def _push_yukle():
    yuklenen = {}
    if _REDIS is not None:
        try:
            ham = _REDIS.hgetall(_PUSH_ANAHTAR) or {}
            for deger in ham.values():
                kayit = json.loads(deger)
                if isinstance(kayit, dict) and kayit.get("endpoint"):
                    yuklenen[kayit["endpoint"]] = kayit
        except Exception as hata:
            print(f"[CANLI YAYIN] push redis okuma: {hata}", flush=True)
    if not yuklenen and _db is not None:
        try:
            satirlar = _db.session.execute(text(f"SELECT icerik FROM {_PUSH_TABLO}")).fetchall()
            for satir in satirlar:
                kayit = json.loads(satir[0] or "{}")
                if isinstance(kayit, dict) and kayit.get("endpoint"):
                    yuklenen[kayit["endpoint"]] = kayit
        except Exception as hata:
            print(f"[CANLI YAYIN] push veritabani okuma: {hata}", flush=True)
            try:
                _db.session.rollback()
            except Exception:
                pass
    with _PUSH_KILIT:
        _PUSH.clear()
        _PUSH.update(yuklenen)


def _push_yayin_basladi(kayit):
    _arkaplanda(_push_dagit, dict(kayit or {}))


def _push_dagit(kayit):
    kod = str((kayit or {}).get("aktif_dukkan") or "")
    if not kod:
        return
    ad = _dukkan_adi(kod) or kod
    paket = {
        "title": "🔴 Canlı Mezat Başladı!",
        "body": "🔴 Canlı Mezat Başladı! " + ad + " yayında, hemen katıl!",
        "url": _SITE + "/?dukkan=" + kod,
    }
    anahtar = _vapid_kaydi()
    if not anahtar.get("private_pem"):
        print("[CANLI YAYIN] VAPID anahtari yok, push gonderilmedi", flush=True)
        return
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        print("[CANLI YAYIN] pywebpush kurulu degil, push gonderilmedi", flush=True)
        return
    konu = (os.environ.get("VAPID_SUBJECT") or "mailto:destek@halicantikcanli.com").strip()
    for abone in _push_liste():
        try:
            webpush(
                subscription_info={"endpoint": abone["endpoint"], "keys": abone["keys"]},
                data=json.dumps(paket, ensure_ascii=False),
                vapid_private_key=anahtar["private_pem"],
                vapid_claims={"sub": konu},
                ttl=180,
            )
        except WebPushException as hata:
            kod_hata = getattr(getattr(hata, "response", None), "status_code", None)
            if kod_hata in (404, 410):
                _push_sil(abone.get("endpoint") or "")
            else:
                print(f"[CANLI YAYIN] push gonderilemedi: {hata}", flush=True)
        except Exception as hata:
            print(f"[CANLI YAYIN] push gonderilemedi: {hata}", flush=True)


# --- Restream RTMP köprüsü (izole). Web yayını bu blok hata verse de sürer. ---

_RESTREAM_KILIT = threading.RLock()
_RESTREAM_PROC = None
_RESTREAM_ISTEK = False
_RESTREAM_HEDEF = ""
_RESTREAM_SON_HATA = ""


def _restream_anahtar_temizle(deger):
    anahtar = str(deger or "").strip()
    if re.fullmatch(r"[^\s/?#\"'\\]{4,200}", anahtar):
        return anahtar
    return ""


def _restream_url_ve_anahtar(url, anahtar):
    """Yalnızca live.restream.io kabul edilir. Boş veya yabancı adres köprüyü kapatır."""
    adres = str(url or "").strip()
    temiz_anahtar = _restream_anahtar_temizle(anahtar)
    if not adres:
        return "", temiz_anahtar
    eslesme = re.fullmatch(
        r"(rtmps?)://live\.restream\.io/live(?:/([^\s/?#\"'\\]{4,200}))?/?",
        adres,
        flags=re.IGNORECASE,
    )
    if not eslesme:
        return "", temiz_anahtar
    sema = "rtmps" if eslesme.group(1).lower() == "rtmps" else "rtmp"
    yol_anahtar = _restream_anahtar_temizle(eslesme.group(2) or "")
    if not temiz_anahtar and yol_anahtar:
        temiz_anahtar = yol_anahtar
    return sema + "://live.restream.io/live", temiz_anahtar


def _restream_hedef(url, anahtar):
    govde, temiz = _restream_url_ve_anahtar(url, anahtar)
    if not govde or not temiz:
        return ""
    return govde.rstrip("/") + "/" + temiz


def _restream_imza(kayit):
    if str((kayit or {}).get("aktif_dukkan") or "") != _SABIT_CANLI_DUKKAN:
        return ""
    hedef = _restream_hedef(
        (kayit or {}).get("restream_rtmp_url"),
        (kayit or {}).get("restream_stream_key"),
    )
    if not hedef:
        return ""
    return hashlib.sha256(hedef.encode("utf-8")).hexdigest()[:16]


def _restream_istek_guncelle():
    """Dolu anahtar ve diger yayını varsa köprüyü beklemeye alır. Eksikse süreci kapatır."""
    global _RESTREAM_ISTEK, _RESTREAM_HEDEF
    kayit = _kopya()
    canli = str(kayit.get("aktif_dukkan") or "") == _SABIT_CANLI_DUKKAN
    hedef = ""
    if canli:
        hedef = _restream_hedef(kayit.get("restream_rtmp_url"), kayit.get("restream_stream_key"))
    with _RESTREAM_KILIT:
        eski = _RESTREAM_HEDEF
        _RESTREAM_HEDEF = hedef
        _RESTREAM_ISTEK = bool(hedef)
    if (not hedef) or hedef != eski:
        _restream_sureci_kapat()


def _restream_kopru_durdur():
    global _RESTREAM_ISTEK, _RESTREAM_HEDEF
    with _RESTREAM_KILIT:
        _RESTREAM_ISTEK = False
        _RESTREAM_HEDEF = ""
    _restream_sureci_kapat()


def _restream_sureci_kapat():
    global _RESTREAM_PROC
    with _RESTREAM_KILIT:
        proc = _RESTREAM_PROC
        _RESTREAM_PROC = None
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


def _restream_hata_yaz(metin):
    """Akış anahtarını loga ve panele taşımaz."""
    global _RESTREAM_SON_HATA
    satir = str(metin or "").strip().replace("\n", " ")
    satir = re.sub(
        r"(rtmps?://live\.restream\.io/live/)[^\s\"']+",
        r"\1***",
        satir,
        flags=re.IGNORECASE,
    )
    satir = satir[:240]
    with _RESTREAM_KILIT:
        if satir:
            _RESTREAM_SON_HATA = satir
    return satir


def _restream_hata_oku():
    with _RESTREAM_KILIT:
        return _RESTREAM_SON_HATA


def _restream_stderr_oku(proc):
    try:
        akis = proc.stderr
        if akis is None:
            return
        for satir in iter(akis.readline, b""):
            if not satir:
                break
            _restream_hata_yaz(satir.decode("utf-8", "replace"))
    except Exception:
        return


def _restream_ffmpeg_yolu():
    """PATH'te ffmpeg varsa onu kullanır. Yoksa imageio-ffmpeg ikilisini yedek alır."""
    try:
        import shutil

        yer = shutil.which("ffmpeg")
        if yer:
            return yer
    except Exception:
        pass
    try:
        import imageio_ffmpeg

        yer = imageio_ffmpeg.get_ffmpeg_exe()
        if yer:
            return str(yer)
    except Exception as hata:
        print(f"[RESTREAM] imageio-ffmpeg atlandi: {hata}", flush=True)
    return "ffmpeg"


def _restream_surec_ac(hedef, mime):
    if not (
        str(hedef).startswith("rtmp://live.restream.io/live/")
        or str(hedef).startswith("rtmps://live.restream.io/live/")
    ):
        return None
    bicim = "mp4" if "mp4" in str(mime or "").lower() else "webm"
    komut = [
        _restream_ffmpeg_yolu(),
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
        _restream_hata_yaz("FFmpeg acilamadi. OBS ile gonderin.")
        print(f"[RESTREAM] ffmpeg atlandi: {hata}", flush=True)
        return None
    threading.Thread(target=_restream_stderr_oku, args=(proc,), daemon=True).start()
    return proc


def _restream_yaz(hedef, paket, baslik, mime):
    global _RESTREAM_PROC
    with _RESTREAM_KILIT:
        if not _RESTREAM_ISTEK or _RESTREAM_HEDEF != hedef:
            return False
        proc = _RESTREAM_PROC
        if proc is not None and proc.poll() is not None:
            _RESTREAM_PROC = None
            proc = None
            if not baslik:
                return "yeniden"
        if proc is None:
            if not baslik:
                return "yeniden"
            proc = _restream_surec_ac(hedef, mime)
            _RESTREAM_PROC = proc
        if proc is None or proc.stdin is None:
            if not _restream_hata_oku():
                _restream_hata_yaz("FFmpeg acilamadi. OBS ile gonderin.")
            return False
        stdin = proc.stdin
    try:
        kalan = paket
        while kalan:
            yazilan = stdin.write(kalan)
            if not yazilan:
                raise OSError("ffmpeg stdin kapandi")
            kalan = kalan[yazilan:]
        stdin.flush()
    except Exception:
        with _RESTREAM_KILIT:
            if _RESTREAM_PROC is proc:
                _RESTREAM_PROC = None
        try:
            proc.kill()
        except Exception:
            pass
        return "yeniden"
    return True


def _restream_parca_al(veri):
    """Tarayıcı medya parçasını FFmpeg stdin'ine yazar. Hata olursa sessizce atlar."""
    try:
        if not _yonetici_mi():
            return {"success": True, "atlandi": True}
        with _RESTREAM_KILIT:
            istek = _RESTREAM_ISTEK
            hedef = _RESTREAM_HEDEF
        if not istek or not hedef:
            return {
                "success": True,
                "atlandi": True,
                "restream": False,
                "mesaj": "Köprü sunucuda kapalı. Diğer yayını açıkken ayarları kaydedin.",
            }
        if str(_kopya().get("aktif_dukkan") or "") != _SABIT_CANLI_DUKKAN:
            return {
                "success": True,
                "atlandi": True,
                "restream": False,
                "mesaj": "Köprü yalnızca Diğer canlı yayınında açılır.",
            }
        ham = str((veri or {}).get("parca") or "")
        if not ham or len(ham) > 2800000:
            return {"success": True, "atlandi": True}
        try:
            paket = base64.b64decode(ham, validate=False)
        except Exception:
            return {"success": True, "atlandi": True}
        if not paket or len(paket) > 2000000:
            return {"success": True, "atlandi": True}
        sonuc = _restream_yaz(
            hedef,
            paket,
            bool((veri or {}).get("baslik")),
            str((veri or {}).get("mime") or ""),
        )
        if sonuc == "yeniden":
            return {
                "success": True,
                "yeniden": True,
                "restream": False,
                "mesaj": _restream_hata_oku() or "FFmpeg akışı kesti, yeniden bağlanıyor.",
            }
        if sonuc:
            return {
                "success": True,
                "restream": True,
                "mesaj": "Restream yayını sunucudan aktarılıyor.",
            }
        return {
            "success": True,
            "restream": False,
            "atlandi": True,
            "mesaj": _restream_hata_oku() or "Paket Restream'e yazılamadı. OBS ile gönderin.",
        }
    except Exception as hata:
        print(f"[RESTREAM] parca atlandi: {hata}", flush=True)
        return {"success": True, "atlandi": True}


_REJI_TURLER = (
    "youtube",
    "tiktok",
    "instagram",
    "facebook",
    "kick",
    "x",
    "telegram",
    "ozel",
)
_REJI_TUR_ETIKET = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "kick": "Kick",
    "x": "X",
    "telegram": "Telegram",
    "ozel": "Özel",
}


def _reji_varsayilan():
    """Yönetici silmedikçe duran başlangıç şablonu. Dinleyici kanal sözlüğünden ayrıdır."""
    sablon = (
        ("yt-ana", "YouTube Ana Kanal", "youtube"),
        ("tt-ana", "TikTok Ana Kanal", "tiktok"),
        ("ig-ana", "Instagram Ana Kanal", "instagram"),
        ("fb-ana", "Facebook Ana Kanal", "facebook"),
        ("kk-ana", "Kick Ana Kanal", "kick"),
    )
    return [
        {
            "id": kim,
            "ad": ad,
            "tur": tur,
            "yayin_url": "",
            "sohbet_id": "",
            "aktif": True,
            "son_akis": [],
        }
        for kim, ad, tur in sablon
    ]


def _reji_yeni_id():
    import time

    ham = str(time.time_ns()) + ":" + str(threading.get_ident())
    return "rp" + hashlib.sha1(ham.encode("utf-8")).hexdigest()[:12]


def _reji_kimlik(ham):
    kim = re.sub(r"[^a-z0-9_-]", "", str(ham or "").strip().lower())
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", kim or ""):
        return ""
    return kim


def _reji_tur_coz(ham):
    temiz = str(ham or "").strip().strip("[]").strip().lower()
    if temiz == "twitter":
        return "x"
    if temiz in _REJI_TURLER:
        return temiz
    for tur, etiket in _PLATFORM_AD.items():
        if str(etiket or "").strip().lower() != temiz:
            continue
        if tur == "twitter":
            return "x"
        if tur in _REJI_TURLER:
            return tur
    return ""


def _reji_akis_satiri(ham):
    if not isinstance(ham, dict):
        return None
    metin = str(ham.get("mesaj") or "").strip()[:500]
    if not metin:
        return None
    return {
        "zaman": str(ham.get("zaman") or "")[:16],
        "kullanici": str(ham.get("kullanici") or "İzleyici").strip()[:80] or "İzleyici",
        "mesaj": metin,
        "pey": bool(ham.get("pey")),
        "tutar": str(ham.get("tutar") or "")[:24],
        "yanit": bool(ham.get("yanit")),
    }


def _reji_url(deger):
    adres = str(deger or "").strip()
    if adres and not adres.startswith("http://") and not adres.startswith("https://"):
        if " " not in adres and "." in adres:
            adres = "https://" + adres
    return _guvenli_url(adres)


def _reji_platform_temizle(ham):
    if not isinstance(ham, dict):
        return None
    kim = _reji_kimlik(ham.get("id"))
    if not kim:
        return None
    tur = str(ham.get("tur") or "ozel").strip().lower()
    if tur not in _REJI_TURLER:
        tur = "ozel"
    ad = str(ham.get("ad") or "").strip()[:80]
    if not ad:
        return None
    aktif = ham.get("aktif")
    if isinstance(aktif, str):
        aktif = aktif.strip().lower() not in ("0", "false", "hayir", "pasif", "kapali", "")
    else:
        aktif = True if aktif is None else bool(aktif)
    akis = []
    for satir in list(ham.get("son_akis") or [])[-40:]:
        temiz = _reji_akis_satiri(satir)
        if temiz:
            akis.append(temiz)
    return {
        "id": kim,
        "ad": ad,
        "tur": tur,
        "yayin_url": _reji_url(ham.get("yayin_url") or ""),
        "sohbet_id": str(ham.get("sohbet_id") or "").strip()[:120],
        "aktif": bool(aktif),
        "son_akis": akis,
    }


def _reji_listesi_temizle(ham):
    if not isinstance(ham, list):
        return []
    temiz = []
    gorulen = set()
    for oge in ham:
        kayit = _reji_platform_temizle(oge)
        if not kayit or kayit["id"] in gorulen:
            continue
        gorulen.add(kayit["id"])
        temiz.append(kayit)
        if len(temiz) >= 200:
            break
    return temiz


def _reji_akisa_yaz(kim, satir):
    temiz = _reji_akis_satiri(satir)
    if not temiz:
        return False
    with _KILIT:
        for platform in _BELLEK.get("reji_platformlari") or []:
            if isinstance(platform, dict) and platform.get("id") == kim:
                akis = list(platform.get("son_akis") or [])
                akis.append(temiz)
                platform["son_akis"] = akis[-40:]
                return True
    return False


def _reji_duvar_satiri_yayinla(dukkan, platform, kullanici, mesaj, ekstra=None):
    """Telefon reji duvarı yönetici odasına düşer. Ortak sohbet olayı ayrıca durur."""
    if _socketio is None:
        return
    import time

    ek = ekstra if isinstance(ekstra, dict) else {}
    platform_id = _reji_kimlik(ek.get("platform_id"))
    tur = str(ek.get("tur") or "").strip().lower()
    if tur not in _REJI_TURLER:
        tur = _reji_tur_coz(platform)
    if platform_id:
        idler = [platform_id]
    elif tur:
        idler = [
            kayit.get("id")
            for kayit in (_kopya().get("reji_platformlari") or [])
            if kayit.get("aktif") and kayit.get("tur") == tur and kayit.get("id")
        ]
    else:
        idler = []
    _socketio.emit(
        "reji_duvar_satiri",
        {
            "platform": platform,
            "kullanici": kullanici,
            "mesaj": mesaj,
            "zaman": str(ek.get("zaman") or time.strftime("%H:%M:%S"))[:16],
            "dukkan": dukkan,
            "havuz": True,
            "tur": tur,
            "reji_idler": idler,
            "platform_id": platform_id,
            "yanit": bool(ek.get("yanit")),
            "pey": bool(ek.get("pey")),
        },
        room="admin_room",
    )


def _reji_paket(mesaj):
    anlik = _kopya()
    _kalici_yaz(anlik)
    return {
        "success": True,
        "mesaj": mesaj,
        "reji_platformlari": _reji_listesi_temizle(anlik.get("reji_platformlari") or []),
    }


def _reji_bul(kim):
    for platform in _kopya().get("reji_platformlari") or []:
        if platform.get("id") == kim:
            return platform
    return None


def _reji_ekle(veri):
    taslak = {
        "id": _reji_yeni_id(),
        "ad": (veri or {}).get("ad"),
        "tur": (veri or {}).get("tur") or "ozel",
        "yayin_url": (veri or {}).get("yayin_url"),
        "sohbet_id": (veri or {}).get("sohbet_id"),
        "aktif": (veri or {}).get("aktif", True),
        "son_akis": [],
    }
    temiz = _reji_platform_temizle(taslak)
    if not temiz:
        return {"success": False, "kod": 400, "mesaj": "Platform adı gerekli."}
    with _KILIT:
        liste = list(_BELLEK.get("reji_platformlari") or [])
        if len(liste) >= 200:
            return {"success": False, "kod": 400, "mesaj": "Platform listesi dolu."}
        liste.append(temiz)
        _BELLEK["reji_platformlari"] = liste
    return _reji_paket(temiz["ad"] + " eklendi.")


def _reji_duzenle(veri):
    kim = _reji_kimlik((veri or {}).get("id"))
    if not kim or not _reji_bul(kim):
        return {"success": False, "kod": 404, "mesaj": "Platform bulunamadı."}
    temiz = _reji_platform_temizle(
        {
            "id": kim,
            "ad": (veri or {}).get("ad"),
            "tur": (veri or {}).get("tur") or "ozel",
            "yayin_url": (veri or {}).get("yayin_url"),
            "sohbet_id": (veri or {}).get("sohbet_id"),
            "aktif": (veri or {}).get("aktif", True),
            "son_akis": [],
        }
    )
    if not temiz:
        return {"success": False, "kod": 400, "mesaj": "Platform adı gerekli."}
    with _KILIT:
        bulundu = False
        for platform in _BELLEK.get("reji_platformlari") or []:
            if not isinstance(platform, dict) or platform.get("id") != kim:
                continue
            platform["ad"] = temiz["ad"]
            platform["tur"] = temiz["tur"]
            platform["yayin_url"] = temiz["yayin_url"]
            platform["sohbet_id"] = temiz["sohbet_id"]
            platform["aktif"] = temiz["aktif"]
            bulundu = True
            break
    if not bulundu:
        return {"success": False, "kod": 404, "mesaj": "Platform bulunamadı."}
    return _reji_paket(temiz["ad"] + " güncellendi.")


def _reji_sil(veri):
    kim = _reji_kimlik((veri or {}).get("id"))
    if not kim:
        return {"success": False, "kod": 400, "mesaj": "Platform kimliği gerekli."}
    with _KILIT:
        liste = [
            platform
            for platform in (_BELLEK.get("reji_platformlari") or [])
            if not (isinstance(platform, dict) and platform.get("id") == kim)
        ]
        if len(liste) == len(_BELLEK.get("reji_platformlari") or []):
            return {"success": False, "kod": 404, "mesaj": "Platform bulunamadı."}
        _BELLEK["reji_platformlari"] = liste
    return _reji_paket("Platform silindi.")


def _reji_aktif_yaz(veri, islem):
    kim = _reji_kimlik((veri or {}).get("id"))
    kayit = _reji_bul(kim) if kim else None
    if not kayit:
        return {"success": False, "kod": 404, "mesaj": "Platform bulunamadı."}
    if islem == "pasif":
        aktif = False
    elif "aktif" in (veri or {}):
        gelen = veri.get("aktif")
        if isinstance(gelen, str):
            aktif = gelen.strip().lower() not in ("0", "false", "hayir", "pasif", "kapali", "")
        else:
            aktif = bool(gelen)
    else:
        aktif = not bool(kayit.get("aktif"))
    with _KILIT:
        for platform in _BELLEK.get("reji_platformlari") or []:
            if isinstance(platform, dict) and platform.get("id") == kim:
                platform["aktif"] = bool(aktif)
                break
    return _reji_paket("Platform açıldı." if aktif else "Platform pasife alındı.")


def _reji_cevap(veri):
    import time

    kim = _reji_kimlik((veri or {}).get("id"))
    kayit = _reji_bul(kim) if kim else None
    metin = str((veri or {}).get("mesaj") or "").strip()[:500]
    if not kayit:
        return {"success": False, "kod": 404, "mesaj": "Platform bulunamadı."}
    if not metin:
        return {"success": False, "kod": 400, "mesaj": "Yanıt metni gerekli."}
    zaman = time.strftime("%H:%M:%S")
    if not _reji_akisa_yaz(
        kim,
        {
            "zaman": zaman,
            "kullanici": "Yönetici",
            "mesaj": metin,
            "pey": False,
            "tutar": "",
            "yanit": True,
        },
    ):
        return {"success": False, "kod": 404, "mesaj": "Platform kaydına yazılamadı."}
    kod = str(_kopya().get("aktif_dukkan") or "") or _SABIT_CANLI_DUKKAN
    _yorum_yayinla(
        kod,
        kayit.get("ad") or _REJI_TUR_ETIKET.get(kayit.get("tur") or "", "Özel"),
        "Yönetici",
        metin,
        {
            "platform_id": kim,
            "tur": kayit.get("tur") or "ozel",
            "yanit": True,
            "zaman": zaman,
        },
    )
    return _reji_paket("Yanıt ortak havuza ve platform kaydına düştü.")


def _reji_islem(islem, veri):
    if islem in ("liste", "listele", ""):
        return {
            "success": True,
            "reji_platformlari": _reji_listesi_temizle(_kopya().get("reji_platformlari") or []),
        }
    if islem in ("platform_ekle", "ekle"):
        return _reji_ekle(veri)
    if islem in ("platform_duzenle", "duzenle"):
        return _reji_duzenle(veri)
    if islem in ("platform_sil", "sil"):
        return _reji_sil(veri)
    if islem in ("platform_aktif", "aktif", "pasif"):
        return _reji_aktif_yaz(veri, islem)
    if islem in ("platform_cevap", "cevap"):
        return _reji_cevap(veri)
    return {"success": False, "kod": 400, "mesaj": "Geçersiz platform işlemi."}


def reji_platform_yaniti():
    """Dinamik reji platformlarını ekler, düzenler, siler ve yanıtı havuza yazar."""
    if not _yonetici_mi():
        return jsonify({"success": False, "mesaj": "Yetki yok."}), 403
    veri = request.get_json(silent=True) or {}
    if not isinstance(veri, dict):
        veri = {}
    islem = str(veri.get("islem") or "liste").strip().lower()
    sonuc = _reji_islem(islem, veri)
    kod = 200 if sonuc.get("success") else int(sonuc.get("kod") or 400)
    govde = {anahtar: deger for anahtar, deger in sonuc.items() if anahtar != "kod"}
    return jsonify(govde), kod


def _reji_ilk_sablon():
    with _KILIT:
        if not _BELLEK.get("reji_platformlari"):
            _BELLEK["reji_platformlari"] = _reji_varsayilan()


_reji_ilk_sablon()
