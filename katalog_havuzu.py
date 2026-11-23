# Katalog havuzu ve kendi XML çıktısı.
# Tekli AI stüdyosu ile XML laboratuvarının koduna girmez.

import os
import threading
import uuid
import json
import html
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file


_KOK = os.path.abspath(os.path.dirname(__file__))
_INSTANCE = os.path.join(_KOK, "instance")
_HAVUZ_YOL = os.path.join(_INSTANCE, "katalog_havuzu.json")
_XML_YOL = os.path.join(_INSTANCE, "kendi_xml.json")
_MEDYA = os.path.join(_INSTANCE, "katalog_havuzu_medya")
_KILIT = threading.Lock()
_TARAMA = {"id": ""}

_UZANTI = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
}
_RESIM = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_VIDEO = {".mp4", ".webm", ".mov", ".m4v"}
_DURUMLAR = ("Bekliyor", "Kontrole Hazır", "Onaylandı")
_BAYT_SINIR = 80 * 1024 * 1024
_AI_BAYT = 8 * 1024 * 1024

bp = Blueprint("katalog_havuzu", __name__)
_analiz = None
_siniflandir = None
_dukkanlar = {}


def katalog_havuzu_bagla(app, admin_required, analiz_fn, siniflandir_fn, dukkanlar):
    global _analiz, _siniflandir, _dukkanlar
    _analiz = analiz_fn
    _siniflandir = siniflandir_fn
    _dukkanlar = dict(dukkanlar or {})
    os.makedirs(_MEDYA, exist_ok=True)

    def _koru(fn):
        return admin_required(fn)

    bp.add_url_rule("/api/katalog-havuzu", view_func=_koru(_liste), methods=["GET"])
    bp.add_url_rule("/api/katalog-havuzu/ekle", view_func=_koru(_ekle), methods=["POST"])
    bp.add_url_rule("/api/katalog-havuzu/tara-siradaki", view_func=_koru(_tara_siradaki), methods=["POST"])
    bp.add_url_rule("/api/katalog-havuzu/xml-aktar", view_func=_koru(_xml_aktar), methods=["POST"])
    bp.add_url_rule("/api/katalog-havuzu/<oge_id>", view_func=_koru(_guncelle), methods=["POST"])
    bp.add_url_rule("/api/katalog-havuzu/<oge_id>/onayla", view_func=_koru(_onayla), methods=["POST"])
    bp.add_url_rule("/api/katalog-havuzu/<oge_id>/sil", view_func=_koru(_sil), methods=["POST"])
    bp.add_url_rule("/api/katalog-havuzu/medya/<oge_id>", view_func=_medya, methods=["GET"])
    bp.add_url_rule("/api/kendi-xml", view_func=_kendi_xml, methods=["GET"])
    app.register_blueprint(bp)


def _oku(yol):
    if not os.path.isfile(yol):
        return []
    try:
        with open(yol, "r", encoding="utf-8") as dosya:
            veri = json.load(dosya)
    except (OSError, ValueError):
        return []
    return veri if isinstance(veri, list) else []


def _yaz(yol, kayitlar):
    os.makedirs(_INSTANCE, exist_ok=True)
    gecici = yol + ".tmp"
    with open(gecici, "w", encoding="utf-8") as dosya:
        json.dump(kayitlar, dosya, ensure_ascii=False)
    os.replace(gecici, yol)


def _havuz():
    return _oku(_HAVUZ_YOL)


def _xml_kayitlari():
    return _oku(_XML_YOL)


def _bul(kayitlar, oge_id):
    for kayit in kayitlar:
        if str(kayit.get("id") or "") == str(oge_id or ""):
            return kayit
    return None


def _uzanti(ad):
    return os.path.splitext(str(ad or ""))[1].lower()


def _sayi(ham):
    try:
        deger = float(ham or 0)
    except (TypeError, ValueError):
        return 0.0
    if deger < 0:
        return 0.0
    return round(deger, 2)


def _metin(ham, sinir):
    return str(ham or "").strip()[:sinir]


def _dukkan(ham):
    kod = str(ham or "").strip().lower()
    if kod in _dukkanlar:
        return kod
    return ""


def _acik(kayit):
    return {
        "id": kayit.get("id"),
        "ad": kayit.get("ad") or "",
        "tur": kayit.get("tur") or "",
        "durum": kayit.get("durum") or "Bekliyor",
        "baslik": kayit.get("baslik") or "",
        "malzeme": kayit.get("malzeme") or "",
        "boyut": kayit.get("boyut") or "",
        "secere": kayit.get("secere") or "",
        "kondisyon": kayit.get("kondisyon") or "",
        "baslangic_fiyati": _sayi(kayit.get("baslangic_fiyati")),
        "hemen_al_fiyati": _sayi(kayit.get("hemen_al_fiyati")),
        "dukkan": _dukkan(kayit.get("dukkan")),
        "medya_url": "/api/katalog-havuzu/medya/" + str(kayit.get("id") or ""),
        "olusturma": kayit.get("olusturma") or "",
    }


def _ozet():
    kayitlar = _havuz()
    return {
        "success": True,
        "ogeler": [_acik(kayit) for kayit in kayitlar],
        "bekleyen": sum(1 for kayit in kayitlar if kayit.get("durum") == "Bekliyor"),
        "hazir": sum(1 for kayit in kayitlar if kayit.get("durum") == "Kontrole Hazır"),
        "onaylanan": sum(1 for kayit in kayitlar if kayit.get("durum") == "Onaylandı"),
        "toplam": len(kayitlar),
    }


def _alanlari_yaz(kayit, veri):
    if not isinstance(veri, dict):
        return
    kayit["baslik"] = _metin(veri.get("baslik"), 200)
    kayit["malzeme"] = _metin(veri.get("malzeme"), 100)
    kayit["boyut"] = _metin(veri.get("boyut"), 120)
    kayit["secere"] = _metin(veri.get("secere"), 300)
    kayit["kondisyon"] = _metin(veri.get("kondisyon"), 2000)
    kayit["baslangic_fiyati"] = _sayi(veri.get("baslangic_fiyati"))
    kayit["hemen_al_fiyati"] = _sayi(veri.get("hemen_al_fiyati"))
    secilen = _dukkan(veri.get("dukkan"))
    if secilen:
        kayit["dukkan"] = secilen


def _liste():
    with _KILIT:
        return jsonify(_ozet())


def _ekle():
    gelen = request.files.getlist("dosyalar") or request.files.getlist("dosya")
    if not gelen:
        return jsonify({"success": False, "mesaj": "Havuza eklenecek fotoğraf veya video seçin."}), 400
    eklenen = []
    atlanan = []
    with _KILIT:
        kayitlar = _havuz()
        for dosya in gelen:
            ad = str(getattr(dosya, "filename", "") or "")
            uzanti = _uzanti(ad)
            if uzanti not in _UZANTI:
                atlanan.append(ad or "dosya")
                continue
            try:
                icerik = dosya.read()
            except Exception:
                atlanan.append(ad)
                continue
            if not icerik or len(icerik) > _BAYT_SINIR:
                atlanan.append(ad)
                continue
            oge_id = uuid.uuid4().hex
            saklanan = oge_id + uzanti
            yol = os.path.join(_MEDYA, saklanan)
            with open(yol, "wb") as hedef:
                hedef.write(icerik)
            kayit = {
                "id": oge_id,
                "ad": os.path.basename(ad)[:180] or saklanan,
                "tur": "video" if uzanti in _VIDEO else "fotograf",
                "mime": _UZANTI[uzanti],
                "dosya": saklanan,
                "durum": "Bekliyor",
                "baslik": "",
                "malzeme": "",
                "boyut": "",
                "secere": "",
                "kondisyon": "",
                "baslangic_fiyati": 0,
                "hemen_al_fiyati": 0,
                "dukkan": "",
                "olusturma": datetime.now().isoformat(timespec="seconds"),
            }
            kayitlar.append(kayit)
            eklenen.append(_acik(kayit))
        _yaz(_HAVUZ_YOL, kayitlar)
        ozet = _ozet()
    ozet["eklenen"] = len(eklenen)
    ozet["atlanan"] = atlanan
    if not eklenen:
        ozet["success"] = False
        ozet["mesaj"] = "Uygun fotoğraf veya video bulunamadı."
        return jsonify(ozet), 400
    ozet["success"] = True
    return jsonify(ozet)


def _video_kare(yol):
    import subprocess
    komut = [
        "ffmpeg", "-y", "-ss", "0.5", "-i", yol,
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
    ]
    try:
        proc = subprocess.run(komut, capture_output=True, timeout=25)
    except Exception:
        return b""
    if proc.returncode != 0 or not proc.stdout:
        return b""
    return proc.stdout


def _onerilen_dukkan(baslik, aciklama, kategori):
    if not callable(_siniflandir):
        return "karma"
    try:
        kod = str(_siniflandir(baslik, aciklama, kategori) or "").strip().lower()
    except Exception:
        return "karma"
    if kod in _dukkanlar and kod != "diger":
        return kod
    return "karma"


def _ai_uygula(kayit, yol):
    uzanti = _uzanti(kayit.get("dosya"))
    mime = "image/jpeg"
    bayt = b""
    if uzanti in _RESIM:
        if os.path.getsize(yol) > _AI_BAYT:
            kayit["kondisyon"] = "Fotoğraf 8 MB sınırını aşıyor. Alanları kontrol edin."
            kayit["baslik"] = kayit.get("baslik") or os.path.splitext(kayit.get("ad") or "Ürün")[0][:200]
            kayit["dukkan"] = kayit.get("dukkan") or _onerilen_dukkan(kayit["baslik"], kayit["kondisyon"], "")
            kayit["durum"] = "Kontrole Hazır"
            return
        with open(yol, "rb") as dosya:
            bayt = dosya.read()
        mime = _UZANTI.get(uzanti, "image/jpeg")
    else:
        bayt = _video_kare(yol)
        if not bayt:
            ad = os.path.splitext(kayit.get("ad") or "Video")[0][:200]
            kayit["baslik"] = kayit.get("baslik") or ad
            kayit["kondisyon"] = "Video kaydı. Kare görüntü ayrılamadı; başlık, fiyat ve dükkanı kontrol edin."
            kayit["dukkan"] = kayit.get("dukkan") or _onerilen_dukkan(kayit["baslik"], kayit["kondisyon"], "")
            kayit["durum"] = "Kontrole Hazır"
            return
    if not callable(_analiz):
        kayit["kondisyon"] = "AI şecere altyapısı bağlı değil."
        kayit["durum"] = "Kontrole Hazır"
        return
    alanlar, hata = _analiz(mime, bayt)
    if hata or not isinstance(alanlar, dict):
        kayit["baslik"] = kayit.get("baslik") or os.path.splitext(kayit.get("ad") or "Ürün")[0][:200]
        kayit["kondisyon"] = hata or "Analiz sonucu boş. Kontrol edin."
        kayit["dukkan"] = kayit.get("dukkan") or _onerilen_dukkan(kayit["baslik"], kayit["kondisyon"], "")
        kayit["durum"] = "Kontrole Hazır"
        return
    kayit["baslik"] = _metin(alanlar.get("baslik"), 200)
    kayit["malzeme"] = _metin(alanlar.get("malzeme"), 100)
    kayit["boyut"] = _metin(alanlar.get("boyut"), 120)
    kayit["secere"] = _metin(alanlar.get("secere"), 300)
    kayit["kondisyon"] = _metin(alanlar.get("kondisyon"), 2000)
    kayit["baslangic_fiyati"] = _sayi(alanlar.get("baslangic_fiyati"))
    kayit["hemen_al_fiyati"] = _sayi(alanlar.get("hemen_al_fiyati"))
    kayit["dukkan"] = _onerilen_dukkan(
        kayit["baslik"],
        " ".join([kayit["kondisyon"], kayit["secere"], kayit["boyut"]]),
        kayit["malzeme"],
    )
    kayit["durum"] = "Kontrole Hazır"


def _tara_siradaki():
    with _KILIT:
        if _TARAMA["id"]:
            paket = _ozet()
            paket["bitti"] = False
            paket["mesgul"] = True
            return jsonify(paket)
        kayitlar = _havuz()
        hedef = next((kayit for kayit in kayitlar if kayit.get("durum") == "Bekliyor"), None)
        if hedef is None:
            paket = _ozet()
            paket["bitti"] = True
            return jsonify(paket)
        _TARAMA["id"] = hedef["id"]
        yol = os.path.join(_MEDYA, os.path.basename(hedef.get("dosya") or ""))
    try:
        if not os.path.isfile(yol):
            with _KILIT:
                kayitlar = _havuz()
                kayit = _bul(kayitlar, _TARAMA["id"])
                if kayit:
                    kayit["durum"] = "Kontrole Hazır"
                    kayit["kondisyon"] = "Medya dosyası bulunamadı."
                    _yaz(_HAVUZ_YOL, kayitlar)
        else:
            with _KILIT:
                kayitlar = _havuz()
                kayit = _bul(kayitlar, _TARAMA["id"])
            if kayit:
                try:
                    _ai_uygula(kayit, yol)
                except Exception as hata:
                    kayit["durum"] = "Kontrole Hazır"
                    kayit["baslik"] = kayit.get("baslik") or os.path.splitext(kayit.get("ad") or "Ürün")[0][:200]
                    kayit["kondisyon"] = "Tarama sırasında hata: " + str(hata)[:500]
                    kayit["dukkan"] = kayit.get("dukkan") or _onerilen_dukkan(kayit["baslik"], kayit["kondisyon"], "")
                with _KILIT:
                    guncel = _havuz()
                    yer = _bul(guncel, kayit.get("id"))
                    if yer:
                        yer.update(kayit)
                        _yaz(_HAVUZ_YOL, guncel)
    finally:
        with _KILIT:
            _TARAMA["id"] = ""
            paket = _ozet()
    paket["bitti"] = paket.get("bekleyen", 0) == 0
    return jsonify(paket)


def _guncelle(oge_id):
    veri = request.get_json(silent=True) or {}
    with _KILIT:
        kayitlar = _havuz()
        kayit = _bul(kayitlar, oge_id)
        if not kayit:
            return jsonify({"success": False, "mesaj": "Havuz öğesi yok."}), 404
        _alanlari_yaz(kayit, veri)
        _yaz(_HAVUZ_YOL, kayitlar)
        acik = _acik(kayit)
    return jsonify({"success": True, "oge": acik})


def _onayla(oge_id):
    veri = request.get_json(silent=True) or {}
    with _KILIT:
        kayitlar = _havuz()
        kayit = _bul(kayitlar, oge_id)
        if not kayit:
            return jsonify({"success": False, "mesaj": "Havuz öğesi yok."}), 404
        _alanlari_yaz(kayit, veri)
        if not _dukkan(kayit.get("dukkan")):
            return jsonify({"success": False, "mesaj": "Onay için bir dükkan seçin."}), 400
        kayit["durum"] = "Onaylandı"
        _yaz(_HAVUZ_YOL, kayitlar)
        acik = _acik(kayit)
    return jsonify({"success": True, "oge": acik})


def _dosyayi_birak(kayit, xml_kayitlari):
    kimlik = str(kayit.get("id") or "")
    if any(str(oge.get("id") or "") == kimlik for oge in xml_kayitlari):
        return
    ad = os.path.basename(kayit.get("dosya") or "")
    if not ad:
        return
    yol = os.path.join(_MEDYA, ad)
    if os.path.isfile(yol):
        try:
            os.remove(yol)
        except OSError:
            pass


def _sil(oge_id):
    with _KILIT:
        kayitlar = _havuz()
        kayit = _bul(kayitlar, oge_id)
        if not kayit:
            return jsonify({"success": False, "mesaj": "Havuz öğesi yok."}), 404
        _dosyayi_birak(kayit, _xml_kayitlari())
        kayitlar = [oge for oge in kayitlar if oge.get("id") != kayit.get("id")]
        _yaz(_HAVUZ_YOL, kayitlar)
        return jsonify(_ozet())


def _xml_aktar():
    with _KILIT:
        havuz = _havuz()
        onaylar = [kayit for kayit in havuz if kayit.get("durum") == "Onaylandı"]
        if not onaylar:
            return jsonify({"success": False, "mesaj": "Aktarılacak onaylı ürün yok."}), 400
        depo = _xml_kayitlari()
        kimlikler = {str(kayit.get("id") or "") for kayit in onaylar}
        depo = [kayit for kayit in depo if str(kayit.get("id") or "") not in kimlikler]
        for kayit in onaylar:
            depo.append({
                "id": kayit.get("id"),
                "ad": kayit.get("ad") or "",
                "tur": kayit.get("tur") or "",
                "dosya": kayit.get("dosya") or "",
                "baslik": kayit.get("baslik") or "",
                "malzeme": kayit.get("malzeme") or "",
                "boyut": kayit.get("boyut") or "",
                "secere": kayit.get("secere") or "",
                "kondisyon": kayit.get("kondisyon") or "",
                "baslangic_fiyati": _sayi(kayit.get("baslangic_fiyati")),
                "hemen_al_fiyati": _sayi(kayit.get("hemen_al_fiyati")),
                "dukkan": _dukkan(kayit.get("dukkan")) or "karma",
            })
        _yaz(_XML_YOL, depo)
        _yaz(_HAVUZ_YOL, [kayit for kayit in havuz if kayit.get("durum") != "Onaylandı"])
        fiyatsiz = sum(1 for kayit in onaylar if _sayi(kayit.get("baslangic_fiyati")) <= 0)
    return jsonify({
        "success": True,
        "adet": len(onaylar),
        "fiyatsiz": fiyatsiz,
        "link": "/api/kendi-xml",
        "mesaj": str(len(onaylar)) + " ürün kendi XML havuzuna alındı.",
    })


def _medya(oge_id):
    with _KILIT:
        kayit = _bul(_havuz(), oge_id) or _bul(_xml_kayitlari(), oge_id)
        if not kayit:
            return jsonify({"success": False, "mesaj": "Medya yok."}), 404
        ad = os.path.basename(kayit.get("dosya") or "")
    yol = os.path.join(_MEDYA, ad) if ad else ""
    if not ad or not os.path.isfile(yol):
        return jsonify({"success": False, "mesaj": "Medya dosyası yok."}), 404
    return send_file(yol, mimetype=_UZANTI.get(_uzanti(ad), "application/octet-stream"), conditional=True)


def _kacis(metin):
    return html.escape(str(metin or ""), quote=True)


def _aciklama(kayit):
    parcalar = []
    if kayit.get("malzeme"):
        parcalar.append("Malzeme: " + str(kayit.get("malzeme")))
    if kayit.get("boyut"):
        parcalar.append("Ebat: " + str(kayit.get("boyut")))
    if kayit.get("secere"):
        parcalar.append("Şecere: " + str(kayit.get("secere")))
    if kayit.get("kondisyon"):
        parcalar.append(str(kayit.get("kondisyon")))
    hemen = _sayi(kayit.get("hemen_al_fiyati"))
    if hemen > 0:
        parcalar.append("Hemen al: " + str(int(hemen) if hemen == int(hemen) else hemen) + " TL")
    return "\n".join(parcalar)


def _kendi_xml():
    istenen = _dukkan(request.args.get("dukkan"))
    ham_dukkan = str(request.args.get("dukkan") or "").strip()
    with _KILIT:
        kayitlar = _xml_kayitlari()
    if ham_dukkan and not istenen:
        kayitlar = []
    elif istenen:
        kayitlar = [kayit for kayit in kayitlar if _dukkan(kayit.get("dukkan")) == istenen]
    kok = request.host_url.rstrip("/")
    satirlar = ['<?xml version="1.0" encoding="UTF-8"?>', "<urunler>"]
    for kayit in kayitlar:
        dukkan = _dukkan(kayit.get("dukkan")) or "karma"
        kategori = (_dukkanlar.get(dukkan) or {}).get("ad") or kayit.get("malzeme") or "Genel"
        if kayit.get("malzeme"):
            kategori = str(kategori) + " / " + str(kayit.get("malzeme"))
        fiyat = _sayi(kayit.get("baslangic_fiyati"))
        fiyat_yazi = str(int(fiyat) if fiyat == int(fiyat) else fiyat)
        gorsel = kok + "/api/katalog-havuzu/medya/" + str(kayit.get("id") or "")
        satirlar.append("  <urun>")
        satirlar.append("    <baslik>" + _kacis(kayit.get("baslik") or kayit.get("ad") or "Ürün") + "</baslik>")
        satirlar.append("    <aciklama>" + _kacis(_aciklama(kayit)) + "</aciklama>")
        satirlar.append("    <kategori>" + _kacis(kategori) + "</kategori>")
        satirlar.append("    <fiyat>" + _kacis(fiyat_yazi) + "</fiyat>")
        satirlar.append("    <stok>1</stok>")
        satirlar.append("    <gorsel>" + _kacis(gorsel) + "</gorsel>")
        satirlar.append("    <dukkan>" + _kacis(dukkan) + "</dukkan>")
        satirlar.append("  </urun>")
    satirlar.append("</urunler>")
    govde = "\n".join(satirlar)
    return app_yaniti(govde)


def _havuz_satiri(dukkan_adlari, satir):
    kod = str((satir or {}).get("dukkan") or "")
    ad = ""
    bilgi = (dukkan_adlari or {}).get(kod)
    if isinstance(bilgi, dict):
        ad = str(bilgi.get("ad") or "")
    teklif_id = int((satir or {}).get("teklif_id") or 0)
    on_teklif = _sayi((satir or {}).get("on_teklif")) if teklif_id else 0.0
    acilis = _sayi((satir or {}).get("acilis_fiyat"))
    return {
        "urun_id": (satir or {}).get("urun_id"),
        "lot": (satir or {}).get("lot"),
        "dukkan": kod,
        "dukkan_adi": ad or kod,
        "baslik": str((satir or {}).get("baslik") or ""),
        "aciklama": str((satir or {}).get("aciklama") or ""),
        "gorsel": str((satir or {}).get("gorsel") or ""),
        "acilis_fiyat": acilis,
        "hemen_al_fiyat": _sayi((satir or {}).get("hemen_al_fiyat")),
        "on_teklif": on_teklif,
        "teklif_veren": str((satir or {}).get("teklif_veren") or ""),
        "teklif_id": teklif_id,
        "tutar": on_teklif if teklif_id else acilis,
    }


def capraz_havuz_cevap(dukkan_adlari, on_teklif_satirlari, katalog_satirlari):
    """Tüm dükkanların katalog ve ön teklif özeti. XML dosyasına ve kurallarına yazmaz."""
    on_teklifliler = [
        _havuz_satiri(dukkan_adlari, satir)
        for satir in (on_teklif_satirlari or [])
        if isinstance(satir, dict)
    ]
    on_teklifliler.sort(key=lambda satir: satir.get("teklif_id") or 0, reverse=True)
    tum_urunler = [
        _havuz_satiri(dukkan_adlari, satir)
        for satir in (katalog_satirlari or [])
        if isinstance(satir, dict)
    ]
    return {"on_teklifliler": on_teklifliler, "tum_urunler": tum_urunler}


def app_yaniti(govde):
    from flask import Response
    return Response(govde, mimetype="application/xml; charset=utf-8")
