# Kargo fanusu raunt yarışı.
# Pey, sayaç ve mevcut tablolara yazmaz. Kendi tablolarını açar,
# isteğin bitiminde oturumu havuza geri bırakır.

import secrets
import threading
import time

from flask import Blueprint, jsonify, request, session
from sqlalchemy import text


bp = Blueprint("fanus_yarisi", __name__)

_db = None
_socketio = None
_dukkan_dogrula = None
_kilit_ana = threading.Lock()
_kullanici_kilitleri = {}
_arma_onbellek = {}
_YORUM_ARALIGI = 15.0
_RENKLER = ("#38BDF8", "#10B981", "#F59E0B", "#818CF8")
_ALTIN = "#FACC15"


def bagla(app, db, socketio, dukkan_dogrula):
    global _db, _socketio, _dukkan_dogrula
    _db = db
    _socketio = socketio
    _dukkan_dogrula = dukkan_dogrula
    with app.app_context():
        _tablolari_ac()
    bp.add_url_rule("/api/fanus/pano", view_func=pano, methods=["GET"])
    bp.add_url_rule("/api/fanus/armalar", view_func=armalar, methods=["GET"])
    bp.add_url_rule("/api/fanus/davet", view_func=davet_geldi, methods=["POST"])
    bp.add_url_rule("/api/fanus/kazan", view_func=kazan, methods=["POST"])
    app.register_blueprint(bp)
    app.after_request(_arkaplan_istek)


def _tablolari_ac():
    cumleler = (
        """
        CREATE TABLE IF NOT EXISTS fanus_raunt (
            kullanici_id INTEGER NOT NULL,
            dukkan VARCHAR(32) NOT NULL,
            ad VARCHAR(100) NOT NULL DEFAULT '',
            puan INTEGER NOT NULL DEFAULT 0,
            raunt INTEGER NOT NULL DEFAULT 1,
            kalp INTEGER NOT NULL DEFAULT 0,
            son_yorum DOUBLE PRECISION,
            PRIMARY KEY (kullanici_id, dukkan)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fanus_davet (
            kullanici_id INTEGER NOT NULL PRIMARY KEY,
            kod VARCHAR(24) NOT NULL UNIQUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fanus_gelen (
            ziyaretci VARCHAR(80) NOT NULL PRIMARY KEY,
            kod VARCHAR(24) NOT NULL,
            zaman DOUBLE PRECISION
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fanus_kazanc (
            kullanici_id INTEGER NOT NULL,
            urun_id INTEGER NOT NULL,
            PRIMARY KEY (kullanici_id, urun_id)
        )
        """,
    )
    with _db.engine.begin() as baglanti:
        for cumle in cumleler:
            baglanti.execute(text(cumle))


def _kullanici_kilidi(kimlik):
    with _kilit_ana:
        kilit = _kullanici_kilitleri.get(kimlik)
        if kilit is None:
            kilit = threading.Lock()
            _kullanici_kilitleri[kimlik] = kilit
        return kilit


def maskeli_ad(ad):
    parcalar = [p for p in str(ad or "").replace(".", " ").split() if p]
    if not parcalar:
        return "İzleyici"
    if len(parcalar) == 1:
        return parcalar[0][:24]
    return parcalar[0][:24] + " " + parcalar[-1][0].upper() + "."


def arma_sec(toplam_raunt):
    n = int(toplam_raunt or 0)
    if n >= 100:
        return {"emoji": "👑", "unvan": "Efsane Taç", "altin": False}
    if n >= 70:
        return {"emoji": "💎", "unvan": "Elmas İmparator", "altin": False}
    if n >= 30:
        return {"emoji": "🥇", "unvan": "Altın Aslan", "altin": True}
    if n >= 10:
        return {"emoji": "🥈", "unvan": "Gümüş Kurt", "altin": False}
    return {"emoji": "🥉", "unvan": "Bronz Kalkan", "altin": False}


def penceredeki_siralar(sira, toplam):
    """1 tabanlı sıra. 1. ve 2. sıra ilk beşi görür; diğerleri kendi iki komşusunu."""
    if sira <= 2:
        bas = 1
    else:
        bas = int(sira) - 2
    if toplam <= 0:
        bas = 1
    return [bas + i for i in range(5)]


def _dukkan(ham):
    if _dukkan_dogrula is None:
        return "diger"
    return _dukkan_dogrula(ham or "diger")


def _kimlik_ham(veri=None):
    veri = veri or {}
    ham = session.get("kullanici_id") or session.get("musteri_id")
    if ham in (None, "", "null"):
        ham = veri.get("kullanici_id") or veri.get("musteri_id")
    try:
        kimlik = int(ham)
    except (TypeError, ValueError):
        return None
    if kimlik <= 0:
        return None
    return kimlik


def _ad_getir(kimlik):
    ad = _db.session.execute(
        text("SELECT ad_soyad FROM kullanici WHERE id = :i"),
        {"i": kimlik},
    ).scalar()
    return str(ad or "").strip()[:100]


def _satir_ac(kimlik, dukkan, ad):
    _db.session.execute(
        text(
            """
            INSERT INTO fanus_raunt (kullanici_id, dukkan, ad, puan, raunt, kalp)
            VALUES (:i, :d, :a, 0, 1, 0)
            ON CONFLICT DO NOTHING
            """
        ),
        {"i": kimlik, "d": dukkan, "a": (ad or "")[:100]},
    )
    if ad:
        _db.session.execute(
            text(
                """
                UPDATE fanus_raunt SET ad = :a
                WHERE kullanici_id = :i AND dukkan = :d AND ad <> :a
                """
            ),
            {"i": kimlik, "d": dukkan, "a": ad[:100]},
        )


def _satir_oku(kimlik, dukkan):
    return _db.session.execute(
        text(
            """
            SELECT kullanici_id, dukkan, ad, puan, raunt, kalp, son_yorum
            FROM fanus_raunt
            WHERE kullanici_id = :i AND dukkan = :d
            """
        ),
        {"i": kimlik, "d": dukkan},
    ).mappings().first()


def _toplam_raunt(kimlik):
    deger = _db.session.execute(
        text("SELECT COALESCE(SUM(raunt), 0) FROM fanus_raunt WHERE kullanici_id = :i"),
        {"i": kimlik},
    ).scalar()
    return int(deger or 0)


def _son_yorum(kimlik):
    deger = _db.session.execute(
        text("SELECT MAX(son_yorum) FROM fanus_raunt WHERE kullanici_id = :i"),
        {"i": kimlik},
    ).scalar()
    try:
        return float(deger or 0)
    except (TypeError, ValueError):
        return 0.0


def _dal_listesi(dukkan):
    return _db.session.execute(
        text(
            """
            SELECT kullanici_id, ad, puan, raunt, kalp
            FROM fanus_raunt
            WHERE dukkan = :d
            ORDER BY raunt DESC, puan DESC, kullanici_id ASC
            """
        ),
        {"d": dukkan},
    ).mappings().all()


def _puan_uygula(kayit, eklenecek, kalp):
    """100'de sayaç sıfırlanır, raunt artar. 50 çizgisi ve raunt şeritlerini döner."""
    puan = int(kayit["puan"] or 0)
    raunt = int(kayit["raunt"] or 1)
    kalp_toplam = int(kayit["kalp"] or 0) + int(kalp or 0)
    kalan = int(eklenecek or 0)
    serit_turleri = []
    if kalan < 0:
        kalan = 0
    while kalan > 0:
        once = puan
        adim = min(100 - puan, kalan)
        puan += adim
        kalan -= adim
        if once < 50 <= puan and puan < 100:
            serit_turleri.append(("elli", raunt))
        if puan >= 100:
            puan = 0
            raunt += 1
            serit_turleri.append(("raunt", raunt))
    return puan, raunt, kalp_toplam, serit_turleri


def _serit_metni(tur, maske, raunt):
    if tur == "elli":
        return "⚡ " + maske + " 50 puana ulaştı ve yükselişe geçti!"
    if tur == "raunt":
        return "🏆 " + maske + " 100'ü devirdi ve " + str(raunt) + ". Raunda yükseldi!"
    if tur == "davet":
        return maske
    return ""


def _yayinla(dukkan, seritler):
    if _socketio is None:
        return
    try:
        _socketio.emit("fanus_tazele", {"dukkan": dukkan}, room=dukkan)
        for metin in seritler:
            if metin:
                _socketio.emit("fanus_serit", {"dukkan": dukkan, "metin": metin}, room=dukkan)
    except Exception as hata:
        print("[FANUS] soket atlandi: " + str(hata), flush=True)


def _odul_yaz(kimlik, dukkan, puan, kalp, neden, urun_id=None):
    """Kısa kilit, kısa işlem, sonra oturum havuza döner."""
    if not kimlik or puan <= 0 and kalp <= 0:
        return []
    dukkan = _dukkan(dukkan)
    seritler = []
    with _kullanici_kilidi(kimlik):
        try:
            if neden == "yorum" and (time.time() - _son_yorum(kimlik)) < _YORUM_ARALIGI:
                return []
            if neden == "kazanc" and urun_id:
                sonuc = _db.session.execute(
                    text(
                        """
                        INSERT INTO fanus_kazanc (kullanici_id, urun_id)
                        VALUES (:i, :u)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {"i": kimlik, "u": int(urun_id)},
                )
                if not getattr(sonuc, "rowcount", 0):
                    _db.session.rollback()
                    return []
            ad = _ad_getir(kimlik)
            _satir_ac(kimlik, dukkan, ad)
            kayit = _satir_oku(kimlik, dukkan)
            if not kayit:
                _db.session.rollback()
                return []
            yeni_puan, yeni_raunt, yeni_kalp, turler = _puan_uygula(kayit, puan, kalp)
            simdi = time.time() if neden == "yorum" else kayit["son_yorum"]
            _db.session.execute(
                text(
                    """
                    UPDATE fanus_raunt
                    SET puan = :p, raunt = :r, kalp = :k, ad = :a, son_yorum = :s
                    WHERE kullanici_id = :i AND dukkan = :d
                    """
                ),
                {
                    "p": yeni_puan,
                    "r": yeni_raunt,
                    "k": yeni_kalp,
                    "a": (ad or kayit["ad"] or "")[:100],
                    "s": simdi,
                    "i": kimlik,
                    "d": dukkan,
                },
            )
            _db.session.commit()
            maske = maskeli_ad(ad or kayit["ad"])
            for tur, raunt in turler:
                metin = _serit_metni(tur, maske, raunt)
                if metin:
                    seritler.append(metin)
        except Exception as hata:
            _db.session.rollback()
            print("[FANUS] puan atlandi: " + str(hata), flush=True)
            seritler = []
        finally:
            _db.session.remove()
    _arma_onbellek.pop(dukkan, None)
    _yayinla(dukkan, seritler)
    return seritler


def _urun_satildi_mi(urun_id, kimlik):
    if not urun_id:
        return False
    satir = _db.session.execute(
        text("SELECT durum, kazanan_id FROM urun WHERE id = :i"),
        {"i": int(urun_id)},
    ).mappings().first()
    if not satir:
        return False
    if str(satir["kazanan_id"] or "") != str(kimlik):
        return False
    return str(satir["durum"] or "") == "Satıldı"


def _arkaplan_istek(yanit):
    try:
        yol = (request.path or "").rstrip("/")
        if request.method != "POST" or yol not in ("/pey-ver", "/api/chat/mesaj-gonder"):
            return yanit
        if yanit.status_code >= 400:
            return yanit
        govde = yanit.get_json(silent=True) or {}
        if not govde.get("success"):
            return yanit
        veri = request.get_json(silent=True) or {}
        kimlik = _kimlik_ham(veri)
        if not kimlik:
            return yanit
        dukkan = _dukkan(veri.get("dukkan"))
        if yol == "/pey-ver":
            if str(veri.get("islem") or "pey") != "hemen_al":
                _odul_yaz(kimlik, dukkan, 2, 1, "pey")
            urun_ham = veri.get("urun_id")
            try:
                urun_id = int(urun_ham) if urun_ham else None
            except (TypeError, ValueError):
                urun_id = None
            if _urun_satildi_mi(urun_id, kimlik):
                _odul_yaz(kimlik, dukkan, 15, 0, "kazanc", urun_id=urun_id)
        else:
            if govde.get("tip") == "pey":
                _odul_yaz(kimlik, dukkan, 2, 1, "pey")
            else:
                _odul_yaz(kimlik, dukkan, 2, 1, "yorum")
    except Exception as hata:
        print("[FANUS] arka plan atlandi: " + str(hata), flush=True)
    return yanit


def _davet_kodu(kimlik):
    kod = _db.session.execute(
        text("SELECT kod FROM fanus_davet WHERE kullanici_id = :i"),
        {"i": kimlik},
    ).scalar()
    if kod:
        return str(kod)
    kod = secrets.token_hex(4)
    _db.session.execute(
        text(
            """
            INSERT INTO fanus_davet (kullanici_id, kod)
            VALUES (:i, :k)
            ON CONFLICT DO NOTHING
            """
        ),
        {"i": kimlik, "k": kod},
    )
    kayitli = _db.session.execute(
        text("SELECT kod FROM fanus_davet WHERE kullanici_id = :i"),
        {"i": kimlik},
    ).scalar()
    return str(kayitli or kod)


def _ilerleme_notu(ben, onde):
    yuzde = int(ben["puan"] or 0)
    temel = "Raunt " + str(int(ben["raunt"] or 1)) + " (%" + str(yuzde) + ")"
    if not onde:
        return temel + " • Bu dalda liderisin!"
    fark = (int(onde["raunt"]) * 100 + int(onde["puan"])) - (int(ben["raunt"]) * 100 + int(ben["puan"]))
    if fark < 1:
        fark = 1
    return temel + " • " + str(onde["sira"]) + ". sıradakini geçmene " + str(fark) + " puan kaldı!"


def _pano_kur(dukkan, kimlik):
    ad = _ad_getir(kimlik) if kimlik else ""
    if kimlik:
        _satir_ac(kimlik, dukkan, ad)
    liste = list(_dal_listesi(dukkan))
    toplamlar = {}
    if liste:
        kimlikler = [int(s["kullanici_id"]) for s in liste]
        yer = ",".join(str(i) for i in kimlikler)
        for satir in _db.session.execute(
            text(
                "SELECT kullanici_id, COALESCE(SUM(raunt), 0) AS toplam "
                "FROM fanus_raunt WHERE kullanici_id IN (" + yer + ") GROUP BY kullanici_id"
            )
        ).mappings():
            toplamlar[int(satir["kullanici_id"])] = int(satir["toplam"] or 0)
    sira_ben = None
    onde = None
    sirali = []
    for indeks, satir in enumerate(liste, start=1):
        paket = {
            "sira": indeks,
            "kullanici_id": int(satir["kullanici_id"]),
            "ad": satir["ad"] or "",
            "puan": int(satir["puan"] or 0),
            "raunt": int(satir["raunt"] or 1),
            "kalp": int(satir["kalp"] or 0),
            "toplam": toplamlar.get(int(satir["kullanici_id"]), int(satir["raunt"] or 1)),
        }
        sirali.append(paket)
        if kimlik and paket["kullanici_id"] == kimlik:
            sira_ben = indeks
    if sira_ben and sira_ben > 1:
        onde = sirali[sira_ben - 2]
    if sira_ben is None:
        gorunen = penceredeki_siralar(1, len(sirali))
    else:
        gorunen = penceredeki_siralar(sira_ben, len(sirali))
    harita = {p["sira"]: p for p in sirali}
    renk_sira = 0
    bantlar = []
    for sira in gorunen:
        paket = harita.get(sira)
        ben = bool(paket and kimlik and paket["kullanici_id"] == kimlik)
        if ben:
            renk = _ALTIN
        else:
            renk = _RENKLER[renk_sira % 4]
            renk_sira += 1
        if not paket:
            bantlar.append({
                "sira": sira,
                "ad": "—",
                "raunt": 0,
                "yuzde": 0,
                "renk": renk,
                "ben": False,
                "emoji": "",
                "unvan": "",
                "altin": False,
                "bos": True,
            })
            continue
        arma = arma_sec(paket["toplam"])
        bantlar.append({
            "sira": sira,
            "ad": maskeli_ad(paket["ad"]),
            "raunt": paket["raunt"],
            "yuzde": max(0, min(100, paket["puan"])),
            "renk": renk,
            "ben": ben,
            "emoji": arma["emoji"],
            "unvan": arma["unvan"],
            "altin": arma["altin"],
            "bos": False,
            "kalp": paket["kalp"],
        })
    ben_paket = sirali[sira_ben - 1] if sira_ben else None
    notu = _ilerleme_notu(ben_paket, onde) if ben_paket else "Giriş yapınca bu daldaki raunt yarışın burada açılır."
    kod = _davet_kodu(kimlik) if kimlik else ""
    return {
        "success": True,
        "giris": bool(kimlik),
        "bantlar": bantlar,
        "not": notu,
        "davet_kodu": kod,
        "oduller": [
            {"raunt": 10, "ad": "Ücretsiz Kargo"},
            {"raunt": 50, "ad": "Altın VIP"},
            {"raunt": 100, "ad": "Ömür Boyu Efsane VIP + Sertifika & Hediye + Ömür Boyu Davet Çek Kazancı"},
        ],
    }


def pano():
    dukkan = _dukkan(request.args.get("dukkan"))
    kimlik = _kimlik_ham()
    try:
        paket = _pano_kur(dukkan, kimlik)
        _db.session.commit()
    except Exception as hata:
        _db.session.rollback()
        print("[FANUS] pano atlandi: " + str(hata), flush=True)
        paket = {"success": False, "bantlar": [], "not": "", "davet_kodu": ""}
    finally:
        _db.session.remove()
    return jsonify(paket)


def _arma_listesi(dukkan):
    simdi = time.time()
    hazir = _arma_onbellek.get(dukkan)
    if hazir and simdi - hazir["zaman"] < 4:
        return hazir["liste"]
    try:
        gelen = list(_dal_listesi(dukkan))
        toplamlar = {}
        if gelen:
            yer = ",".join(str(int(s["kullanici_id"])) for s in gelen)
            for satir in _db.session.execute(
                text(
                    "SELECT kullanici_id, COALESCE(SUM(raunt), 0) AS toplam "
                    "FROM fanus_raunt WHERE kullanici_id IN (" + yer + ") GROUP BY kullanici_id"
                )
            ).mappings():
                toplamlar[int(satir["kullanici_id"])] = int(satir["toplam"] or 0)
        liste = []
        for satir in gelen:
            arma = arma_sec(toplamlar.get(int(satir["kullanici_id"]), int(satir["raunt"] or 1)))
            liste.append({
                "id": int(satir["kullanici_id"]),
                "ad": satir["ad"] or "",
                "maske": maskeli_ad(satir["ad"]),
                "emoji": arma["emoji"],
                "unvan": arma["unvan"],
                "altin": arma["altin"],
            })
        _db.session.rollback()
    except Exception as hata:
        _db.session.rollback()
        print("[FANUS] arma atlandi: " + str(hata), flush=True)
        liste = []
    finally:
        _db.session.remove()
    _arma_onbellek[dukkan] = {"zaman": simdi, "liste": liste}
    return liste


def armalar():
    dukkan = _dukkan(request.args.get("dukkan"))
    return jsonify({"success": True, "kayitlar": _arma_listesi(dukkan)})


def davet_geldi():
    veri = request.get_json(silent=True) or {}
    kod = str(veri.get("kod") or "").strip().lower()
    ziyaretci = str(veri.get("ziyaretci") or "").strip()[:80]
    if not kod or not ziyaretci or not veri.get("ilk"):
        return jsonify({"success": True, "islendi": False})
    gelen_kimlik = _kimlik_ham(veri)
    gelen_ad = str(veri.get("gelen_ad") or "").strip()
    seritler = []
    try:
        davet = _db.session.execute(
            text("SELECT kullanici_id FROM fanus_davet WHERE kod = :k"),
            {"k": kod},
        ).mappings().first()
        if not davet:
            _db.session.rollback()
            return jsonify({"success": True, "islendi": False})
        davet_eden = int(davet["kullanici_id"])
        if gelen_kimlik and gelen_kimlik == davet_eden:
            _db.session.rollback()
            return jsonify({"success": True, "islendi": False})
        sonuc = _db.session.execute(
            text(
                """
                INSERT INTO fanus_gelen (ziyaretci, kod, zaman)
                VALUES (:z, :k, :t)
                ON CONFLICT DO NOTHING
                """
            ),
            {"z": ziyaretci, "k": kod, "t": time.time()},
        )
        if not getattr(sonuc, "rowcount", 0):
            _db.session.rollback()
            return jsonify({"success": True, "islendi": False})
        _db.session.commit()
    except Exception as hata:
        _db.session.rollback()
        print("[FANUS] davet atlandi: " + str(hata), flush=True)
        return jsonify({"success": True, "islendi": False})
    finally:
        _db.session.remove()
    dukkan = _dukkan(veri.get("dukkan"))
    try:
        ad_eden = ""
        with _kullanici_kilidi(davet_eden):
            try:
                ad_eden = _ad_getir(davet_eden)
            finally:
                _db.session.remove()
    except Exception:
        ad_eden = ""
    _odul_yaz(davet_eden, dukkan, 10, 0, "davet")
    metin = (
        "🚀 "
        + maskeli_ad(ad_eden)
        + " → "
        + maskeli_ad(gelen_ad or "Yeni ziyaretçi")
        + "'yi odaya getirdi (+10 Puan)"
    )
    if _socketio is not None:
        try:
            _socketio.emit("fanus_serit", {"dukkan": dukkan, "metin": metin}, room=dukkan)
        except Exception as hata:
            print("[FANUS] davet seridi atlandi: " + str(hata), flush=True)
    seritler.append(metin)
    return jsonify({"success": True, "islendi": True, "serit": metin})


def kazan():
    veri = request.get_json(silent=True) or {}
    kimlik = _kimlik_ham(veri)
    try:
        urun_id = int(veri.get("urun_id") or 0)
    except (TypeError, ValueError):
        urun_id = 0
    if not kimlik or urun_id <= 0:
        return jsonify({"success": False})
    dukkan = _dukkan(veri.get("dukkan"))
    try:
        satildi = _urun_satildi_mi(urun_id, kimlik)
    except Exception as hata:
        _db.session.rollback()
        print("[FANUS] kazan okunamadi: " + str(hata), flush=True)
        satildi = False
    finally:
        _db.session.remove()
    if not satildi:
        return jsonify({"success": True, "islendi": False})
    _odul_yaz(kimlik, dukkan, 15, 0, "kazanc", urun_id=urun_id)
    return jsonify({"success": True, "islendi": True})
