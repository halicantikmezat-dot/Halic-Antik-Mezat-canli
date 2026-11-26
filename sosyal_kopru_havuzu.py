"""Çoklu sosyal kanal ve yorum köprüsü havuzu.

Sabit TikTok, Instagram ve Facebook köprüleri olduğu gibi kalır.
Ek sayfalar bu tabloda tutulur; yorumlar merkezi webhook ile ilgili odanın sohbetine iner.
"""

import re
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text

bp = Blueprint("sosyal_kopru_havuzu", __name__)

_PLATFORMLAR = {
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "youtube": "YouTube",
    "kick": "Kick",
    "x": "X",
    "telegram": "Telegram",
}
_YAYINDAKI = "yayindaki"
_SEMA_HAZIR = False
_KONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _db():
    eklenti = current_app.extensions.get("sqlalchemy")
    if eklenti is not None:
        return eklenti
    from app import db

    return db


def _dukkan_haritasi():
    ozel = current_app.config.get("SOSYAL_KOPRU_DUKKANLAR")
    if isinstance(ozel, dict) and ozel:
        return ozel
    from app import DUKKANLAR

    return DUKKANLAR


def _simdi():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _temiz_metin(ham, sinir):
    metin = _KONTROL.sub(" ", str(ham or ""))
    return " ".join(metin.split())[:sinir]


def hesap_anahtari(ham):
    """Eşleştirme anahtarı. Baştaki @ ve büyük/küçük harf farkını yok sayar."""
    kod = _temiz_metin(ham, 200).lstrip("@")
    return kod.casefold()


def etiket_metni(platform_adi, sayfa_adi):
    return "[" + str(platform_adi or "").strip() + " - " + str(sayfa_adi or "").strip() + "]"


def _semayi_hazirla():
    global _SEMA_HAZIR
    if _SEMA_HAZIR:
        return
    db = _db()
    uri = str(current_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    kimlik = (
        "id INTEGER PRIMARY KEY AUTOINCREMENT"
        if "sqlite" in uri
        else "id SERIAL PRIMARY KEY"
    )
    try:
        db.session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS sosyal_kanallar (
                    {kimlik},
                    platform VARCHAR(32) NOT NULL,
                    sayfa_adi VARCHAR(120) NOT NULL,
                    hesap_kodu VARCHAR(200) NOT NULL,
                    hedef_dukkan VARCHAR(40) NOT NULL,
                    aktif INTEGER NOT NULL DEFAULT 0,
                    durum VARCHAR(40) NOT NULL DEFAULT 'durduruldu',
                    son_etkinlik VARCHAR(40),
                    olusturma_tarihi VARCHAR(40) NOT NULL
                )
                """
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    _SEMA_HAZIR = True


def _satirlar():
    _semayi_hazirla()
    return (
        _db()
        .session.execute(
            text(
                """
                SELECT id, platform, sayfa_adi, hesap_kodu, hedef_dukkan,
                       aktif, durum, son_etkinlik, olusturma_tarihi
                FROM sosyal_kanallar
                ORDER BY id ASC
                """
            )
        )
        .mappings()
        .all()
    )


def _kanal_bul(ham_kod):
    aranan = hesap_anahtari(ham_kod)
    if not aranan:
        return None
    for satir in _satirlar():
        if hesap_anahtari(satir.get("hesap_kodu")) == aranan:
            return satir
    return None


def _dukkan_secenekleri():
    harita = _dukkan_haritasi()
    secenekler = [{"kod": _YAYINDAKI, "ad": "O An Yayındaki Dükkan"}]
    for kod, bilgi in harita.items():
        ad = str((bilgi or {}).get("ad") or kod)
        secenekler.append({"kod": str(kod), "ad": ad})
    return secenekler


def _hedef_gecerli(kod):
    temiz = str(kod or "").strip().lower()
    if temiz == _YAYINDAKI:
        return temiz
    if temiz in _dukkan_haritasi():
        return temiz
    return ""


def _dukkan_adi(kod):
    if kod == _YAYINDAKI:
        return "O An Yayındaki Dükkan"
    bilgi = _dukkan_haritasi().get(kod) or {}
    return str(bilgi.get("ad") or kod or "")


def _kayit(satir):
    hedef = str(satir.get("hedef_dukkan") or "")
    aktif = int(satir.get("aktif") or 0) == 1
    return {
        "id": int(satir.get("id") or 0),
        "platform": satir.get("platform") or "",
        "platform_adi": _PLATFORMLAR.get(str(satir.get("platform") or ""), satir.get("platform") or ""),
        "sayfa_adi": satir.get("sayfa_adi") or "",
        "hesap_kodu": satir.get("hesap_kodu") or "",
        "hedef_dukkan": hedef,
        "hedef_adi": _dukkan_adi(hedef),
        "aktif": aktif,
        "durum": "canli" if aktif else "durduruldu",
        "son_etkinlik": satir.get("son_etkinlik") or "",
        "olusturma_tarihi": satir.get("olusturma_tarihi") or "",
    }


def kanal_ekle(platform, sayfa_adi, hesap_kodu, hedef_dukkan):
    platform_kodu = str(platform or "").strip().lower()
    if platform_kodu not in _PLATFORMLAR:
        return None, "Platform tanınmadı."
    ad = _temiz_metin(sayfa_adi, 120)
    kod = _temiz_metin(hesap_kodu, 200)
    if not ad:
        return None, "Sayfa görünen adı gerekli."
    if not hesap_anahtari(kod):
        return None, "Kullanıcı adı veya sayfa kodu gerekli."
    hedef = _hedef_gecerli(hedef_dukkan)
    if not hedef:
        return None, "Hedef dükkan tanınmadı."
    if _kanal_bul(kod) is not None:
        return None, "Bu sayfa kodu zaten kayıtlı."
    db = _db()
    olusan = _simdi()
    satir = db.session.execute(
        text(
            """
            INSERT INTO sosyal_kanallar
                (platform, sayfa_adi, hesap_kodu, hedef_dukkan, aktif, durum, son_etkinlik, olusturma_tarihi)
            VALUES
                (:platform, :sayfa_adi, :hesap_kodu, :hedef_dukkan, 0, 'durduruldu', '', :olusturma)
            RETURNING id, platform, sayfa_adi, hesap_kodu, hedef_dukkan, aktif, durum, son_etkinlik, olusturma_tarihi
            """
        ),
        {
            "platform": platform_kodu,
            "sayfa_adi": ad,
            "hesap_kodu": kod,
            "hedef_dukkan": hedef,
            "olusturma": olusan,
        },
    ).mappings().first()
    db.session.commit()
    return _kayit(satir), ""


def kanal_durumu(kanal_id, aktif):
    db = _db()
    bayrak = 1 if aktif else 0
    durum = "canli" if aktif else "durduruldu"
    sonuc = db.session.execute(
        text(
            """
            UPDATE sosyal_kanallar
            SET aktif = :aktif, durum = :durum
            WHERE id = :id
            """
        ),
        {"aktif": bayrak, "durum": durum, "id": int(kanal_id)},
    )
    db.session.commit()
    return int(sonuc.rowcount or 0) > 0


def tum_kanallar(aktif):
    db = _db()
    bayrak = 1 if aktif else 0
    durum = "canli" if aktif else "durduruldu"
    db.session.execute(
        text("UPDATE sosyal_kanallar SET aktif = :aktif, durum = :durum"),
        {"aktif": bayrak, "durum": durum},
    )
    db.session.commit()


def kanal_sil(kanal_id):
    db = _db()
    sonuc = db.session.execute(
        text("DELETE FROM sosyal_kanallar WHERE id = :id"),
        {"id": int(kanal_id)},
    )
    db.session.commit()
    return int(sonuc.rowcount or 0) > 0


def _yayindaki_kod():
    try:
        import canli_yayin_havuzu as havuz

        durum = havuz.herkese_acik_durum() or {}
        return str(durum.get("aktif_dukkan") or "").strip().lower()
    except Exception as hata:
        print(f"[SOSYAL KOPRU] yayindaki dukkan okunamadi: {hata}", flush=True)
        return ""


def _oda_kodu(hedef):
    if str(hedef or "") == _YAYINDAKI:
        return _yayindaki_kod()
    return str(hedef or "").strip().lower()


def _yorum_alanlari(veri):
    govde = veri if isinstance(veri, dict) else {}
    hesap = govde.get("hesap_kodu") or govde.get("sayfa_kodu") or govde.get("page_id") or ""
    kullanici = govde.get("kullanici") or govde.get("username") or govde.get("user") or ""
    mesaj = govde.get("mesaj") if govde.get("mesaj") is not None else None
    if mesaj is None:
        mesaj = govde.get("message")
    if mesaj is None:
        mesaj = govde.get("comment")
    if mesaj is None:
        mesaj = govde.get("text")
    if isinstance(mesaj, dict):
        if not kullanici:
            kullanici = mesaj.get("kullanici") or mesaj.get("username") or mesaj.get("user") or ""
        mesaj = mesaj.get("mesaj") or mesaj.get("message") or mesaj.get("text") or mesaj.get("comment") or ""
    return hesap, kullanici, mesaj


def _son_etkinlik(kanal_id):
    db = _db()
    db.session.execute(
        text("UPDATE sosyal_kanallar SET son_etkinlik = :an WHERE id = :id"),
        {"an": _simdi(), "id": int(kanal_id)},
    )
    db.session.commit()


def _sohbete_yaz(oda, platform_adi, sayfa_adi, kullanici, mesaj):
    socketio = current_app.extensions.get("socketio")
    if socketio is None:
        return False
    etiket = etiket_metni(platform_adi, sayfa_adi)
    socketio.emit(
        "yeni_canli_yorum",
        {
            "platform": platform_adi,
            "kullanici": kullanici,
            "mesaj": mesaj,
            "zaman": datetime.now().strftime("%H:%M:%S"),
            "dukkan": oda,
            "havuz": True,
            "rozet": etiket,
        },
        room=oda,
    )
    return True


def coklu_yorum_isle(veri):
    """Hesap kodunu havuzla eşler, aktifse hedef odanın sohbetine bırakır."""
    hesap, kullanici, mesaj = _yorum_alanlari(veri)
    kanal = _kanal_bul(hesap)
    if kanal is None:
        return {"success": False, "kod": 404, "mesaj": "Kanal bulunamadı."}
    if int(kanal.get("aktif") or 0) != 1:
        return {"success": False, "kod": 409, "mesaj": "Kanal durdurulmuş."}
    ad = _temiz_metin(kullanici, 80) or "İzleyici"
    metin = _temiz_metin(mesaj, 500)
    if not metin:
        return {"success": False, "kod": 400, "mesaj": "Boş yorum işlenmez."}
    oda = _oda_kodu(kanal.get("hedef_dukkan"))
    if not oda:
        return {"success": False, "kod": 409, "mesaj": "Aktif canlı yayın yok."}
    platform_kodu = str(kanal.get("platform") or "")
    platform_adi = _PLATFORMLAR.get(platform_kodu, platform_kodu)
    if current_app.extensions.get("socketio") is None:
        return {"success": False, "kod": 503, "mesaj": "Sohbet hattı hazır değil."}
    from canli_yayin_havuzu import mukerrer_kapisi

    if mukerrer_kapisi(platform_kodu, ad, metin):
        return {
            "success": True,
            "kod": 200,
            "tip": "mukerrer",
            "dukkan": oda,
            "mesaj": "Mükerrer yorum yutuldu.",
        }
    if not _sohbete_yaz(oda, platform_adi, kanal.get("sayfa_adi") or "", ad, metin):
        return {"success": False, "kod": 503, "mesaj": "Sohbet hattı hazır değil."}
    try:
        _son_etkinlik(kanal.get("id"))
    except Exception as hata:
        print(f"[SOSYAL KOPRU] son etkinlik yazilamadi: {hata}", flush=True)
        try:
            _db().session.rollback()
        except Exception:
            pass
    return {
        "success": True,
        "kod": 200,
        "tip": "yorum",
        "dukkan": oda,
        "kanal_id": int(kanal.get("id") or 0),
        "rozet": etiket_metni(platform_adi, kanal.get("sayfa_adi") or ""),
    }


def _yonetici(fn):
    from functools import wraps

    @wraps(fn)
    def sarmal(*args, **kwargs):
        from app import admin_required

        return admin_required(fn)(*args, **kwargs)

    return sarmal


def _panel_govdesi():
    return {
        "success": True,
        "kanallar": [_kayit(satir) for satir in _satirlar()],
        "dukkanlar": _dukkan_secenekleri(),
        "platformlar": [{"kod": kod, "ad": ad} for kod, ad in _PLATFORMLAR.items()],
        "webhook_url": request.host_url.rstrip("/") + "/api/canli_havuz/coklu_yorum",
    }


@bp.record_once
def _kayitta_hazirla(state):
    try:
        with state.app.app_context():
            _semayi_hazirla()
        print("[SOSYAL KOPRU] coklu kanal havuzu hazir", flush=True)
    except Exception as hata:
        print(f"[SOSYAL KOPRU] tablo atlandi: {hata}", flush=True)


@bp.route("/admin/sosyal-kopru/durum", methods=["GET"])
@_yonetici
def durum():
    try:
        return jsonify(_panel_govdesi())
    except Exception as hata:
        try:
            _db().session.rollback()
        except Exception:
            pass
        return jsonify({"success": False, "mesaj": "Kanal listesi okunamadı."}), 500


@bp.route("/admin/sosyal-kopru/ekle", methods=["POST"])
@_yonetici
def ekle():
    veri = request.get_json(silent=True) or {}
    try:
        kayit, hata = kanal_ekle(
            veri.get("platform"),
            veri.get("sayfa_adi"),
            veri.get("hesap_kodu"),
            veri.get("hedef_dukkan"),
        )
    except Exception as hata:
        try:
            _db().session.rollback()
        except Exception:
            pass
        print(f"[SOSYAL KOPRU] ekleme hatasi: {hata}", flush=True)
        return jsonify({"success": False, "mesaj": "Kanal kaydedilemedi."}), 500
    if kayit is None:
        return jsonify({"success": False, "mesaj": hata}), 400
    return jsonify({"success": True, "kanal": kayit, "mesaj": "Sayfa eklendi."})


@bp.route("/admin/sosyal-kopru/durum-ayarla", methods=["POST"])
@_yonetici
def durum_ayarla():
    veri = request.get_json(silent=True) or {}
    try:
        kanal_id = int(veri.get("id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "mesaj": "Kanal bulunamadı."}), 404
    aktif = bool(veri.get("aktif"))
    try:
        if not kanal_durumu(kanal_id, aktif):
            return jsonify({"success": False, "mesaj": "Kanal bulunamadı."}), 404
    except Exception as hata:
        try:
            _db().session.rollback()
        except Exception:
            pass
        print(f"[SOSYAL KOPRU] durum hatasi: {hata}", flush=True)
        return jsonify({"success": False, "mesaj": "Durum güncellenemedi."}), 500
    return jsonify({"success": True, "mesaj": "Kanal başlatıldı." if aktif else "Kanal durduruldu."})


@bp.route("/admin/sosyal-kopru/tumunu-baslat", methods=["POST"])
@_yonetici
def tumunu_baslat():
    try:
        tum_kanallar(True)
    except Exception as hata:
        try:
            _db().session.rollback()
        except Exception:
            pass
        print(f"[SOSYAL KOPRU] toplu baslatma hatasi: {hata}", flush=True)
        return jsonify({"success": False, "mesaj": "Kanallar başlatılamadı."}), 500
    return jsonify({"success": True, "mesaj": "Tüm kanallar başlatıldı."})


@bp.route("/admin/sosyal-kopru/tumunu-durdur", methods=["POST"])
@_yonetici
def tumunu_durdur():
    try:
        tum_kanallar(False)
    except Exception as hata:
        try:
            _db().session.rollback()
        except Exception:
            pass
        print(f"[SOSYAL KOPRU] toplu durdurma hatasi: {hata}", flush=True)
        return jsonify({"success": False, "mesaj": "Kanallar durdurulamadı."}), 500
    return jsonify({"success": True, "mesaj": "Tüm kanallar durduruldu."})


@bp.route("/admin/sosyal-kopru/sil", methods=["POST"])
@_yonetici
def sil():
    veri = request.get_json(silent=True) or {}
    try:
        kanal_id = int(veri.get("id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "mesaj": "Kanal bulunamadı."}), 404
    try:
        if not kanal_sil(kanal_id):
            return jsonify({"success": False, "mesaj": "Kanal bulunamadı."}), 404
    except Exception as hata:
        try:
            _db().session.rollback()
        except Exception:
            pass
        print(f"[SOSYAL KOPRU] silme hatasi: {hata}", flush=True)
        return jsonify({"success": False, "mesaj": "Kanal silinemedi."}), 500
    return jsonify({"success": True, "mesaj": "Kanal silindi."})


@bp.route("/api/canli_havuz/coklu_yorum", methods=["POST"])
def coklu_yorum():
    veri = request.get_json(silent=True) or {}
    try:
        sonuc = coklu_yorum_isle(veri)
    except Exception as hata:
        try:
            _db().session.rollback()
        except Exception:
            pass
        print(f"[SOSYAL KOPRU] yorum hatasi: {hata}", flush=True)
        return jsonify({"success": False, "mesaj": "Yorum işlenemedi."}), 500
    kod = int(sonuc.get("kod") or (200 if sonuc.get("success") else 400))
    govde = {anahtar: deger for anahtar, deger in sonuc.items() if anahtar != "kod"}
    return jsonify(govde), kod
