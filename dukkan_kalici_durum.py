"""Dükkan otonom ve canlı bayraklarını veritabanında, tanımlıysa Redis'te tutar."""

import os
import time

from sqlalchemy import text

_db = None
_redis = None
_kilit_sure = 45


def bagla(db):
    """Şemayı açar ve REDIS_URL varsa aynı bayrakları Redis'e de yazar."""
    global _db, _redis
    _db = db
    _redis = None
    adres = (os.environ.get("REDIS_URL") or "").strip()
    if adres:
        try:
            import redis

            istemci = redis.Redis.from_url(adres, decode_responses=True, socket_timeout=1.5)
            istemci.ping()
            _redis = istemci
            print("[DUKKAN DURUM] Redis baglandi", flush=True)
        except Exception as hata:
            _redis = None
            print(f"[DUKKAN DURUM] Redis atlandi: {hata}", flush=True)
    semayi_hazirla()


def _oturum():
    if _db is None:
        raise RuntimeError("Dükkan durum deposu bağlı değil.")
    return _db.session


def _sqlite_mi():
    try:
        return _db.engine.dialect.name == "sqlite"
    except Exception:
        return True


def _kolon_adlari():
    if _sqlite_mi():
        satirlar = _oturum().execute(text("PRAGMA table_info(dukkan_durum)")).fetchall()
        return {str(satir[1]) for satir in satirlar}
    satirlar = _oturum().execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'dukkan_durum'"
        )
    ).fetchall()
    return {str(satir[0]) for satir in satirlar}


def semayi_hazirla():
    """Tablo yoksa kurar, sonradan eklenen kolonları eksikse tamamlar."""
    _oturum().execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS dukkan_durum (
                dukkan_kodu VARCHAR(30) PRIMARY KEY,
                otonom_aktif BOOLEAN NOT NULL DEFAULT FALSE,
                canli_durum BOOLEAN NOT NULL DEFAULT FALSE,
                son_hata TEXT,
                lider_pid VARCHAR(40),
                lider_nabiz FLOAT,
                guncelleme VARCHAR(40)
            )
            """
        )
    )
    _oturum().commit()
    varolan = _kolon_adlari()
    ekler = {
        "otonom_aktif": "BOOLEAN NOT NULL DEFAULT FALSE",
        "canli_durum": "BOOLEAN NOT NULL DEFAULT FALSE",
        "son_hata": "TEXT",
        "lider_pid": "VARCHAR(40)",
        "lider_nabiz": "FLOAT",
        "guncelleme": "VARCHAR(40)",
    }
    for ad, tip in ekler.items():
        if ad in varolan:
            continue
        _oturum().execute(text(f"ALTER TABLE dukkan_durum ADD COLUMN {ad} {tip}"))
    _oturum().commit()


def _bool(deger):
    if isinstance(deger, str):
        return deger.strip().lower() in ("1", "true", "t", "evet", "yes")
    return bool(deger)


def _bos():
    return {
        "otonom_aktif": False,
        "canli_durum": False,
        "son_hata": "",
        "lider_pid": "",
        "lider_nabiz": 0.0,
    }


def _redis_anahtar(dukkan):
    return f"halic:dukkan_durum:{dukkan}"


def _redis_oku(dukkan):
    if _redis is None:
        return None
    try:
        veri = _redis.hgetall(_redis_anahtar(dukkan))
    except Exception as hata:
        print(f"[DUKKAN DURUM] Redis okunamadi: {hata}", flush=True)
        return None
    if not veri:
        return None
    return {
        "otonom_aktif": _bool(veri.get("otonom_aktif")),
        "canli_durum": _bool(veri.get("canli_durum")),
        "son_hata": veri.get("son_hata") or "",
        "lider_pid": "",
        "lider_nabiz": 0.0,
    }


def _redis_yaz(dukkan, kayit):
    if _redis is None:
        return
    try:
        _redis.hset(
            _redis_anahtar(dukkan),
            mapping={
                "otonom_aktif": "1" if kayit.get("otonom_aktif") else "0",
                "canli_durum": "1" if kayit.get("canli_durum") else "0",
                "son_hata": str(kayit.get("son_hata") or "")[:500],
            },
        )
    except Exception as hata:
        print(f"[DUKKAN DURUM] Redis yazilamadi: {hata}", flush=True)


def _db_oku(dukkan):
    satir = _oturum().execute(
        text(
            "SELECT otonom_aktif, canli_durum, son_hata, lider_pid, lider_nabiz "
            "FROM dukkan_durum WHERE dukkan_kodu = :kod"
        ),
        {"kod": dukkan},
    ).mappings().first()
    if satir is None:
        return _bos()
    return {
        "otonom_aktif": _bool(satir.get("otonom_aktif")),
        "canli_durum": _bool(satir.get("canli_durum")),
        "son_hata": satir.get("son_hata") or "",
        "lider_pid": satir.get("lider_pid") or "",
        "lider_nabiz": float(satir.get("lider_nabiz") or 0),
    }


def oku(dukkan):
    """Önce Redis, yoksa veritabanı. İkisi de aynı alanları taşır."""
    onbellek = _redis_oku(dukkan)
    if onbellek is not None:
        return onbellek
    kayit = _db_oku(dukkan)
    _redis_yaz(dukkan, kayit)
    return kayit


def kaydet(dukkan, otonom_aktif=None, canli_durum=None, son_hata=None):
    """Verilen alanları günceller, gönderilmeyenleri yerinde bırakır."""
    mevcut = _db_oku(dukkan)
    if otonom_aktif is not None:
        mevcut["otonom_aktif"] = bool(otonom_aktif)
    if canli_durum is not None:
        mevcut["canli_durum"] = bool(canli_durum)
    if son_hata is not None:
        mevcut["son_hata"] = str(son_hata or "")[:500]
    _oturum().execute(
        text(
            """
            INSERT INTO dukkan_durum
                (dukkan_kodu, otonom_aktif, canli_durum, son_hata, lider_pid, lider_nabiz, guncelleme)
            VALUES
                (:kod, :otonom, :canli, :hata, :lider, :nabiz, :an)
            ON CONFLICT(dukkan_kodu) DO UPDATE SET
                otonom_aktif = excluded.otonom_aktif,
                canli_durum = excluded.canli_durum,
                son_hata = excluded.son_hata,
                guncelleme = excluded.guncelleme
            """
        ),
        {
            "kod": dukkan,
            "otonom": bool(mevcut["otonom_aktif"]),
            "canli": bool(mevcut["canli_durum"]),
            "hata": mevcut["son_hata"],
            "lider": mevcut.get("lider_pid") or "",
            "nabiz": float(mevcut.get("lider_nabiz") or 0),
            "an": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
    _oturum().commit()
    _redis_yaz(dukkan, mevcut)
    return mevcut


def otonom_mu(dukkan):
    return bool(oku(dukkan).get("otonom_aktif"))


def canli_mi(dukkan):
    return bool(oku(dukkan).get("canli_durum"))


def acilista_yukle(dukkan):
    """Yeniden açılışta otonom bayrağını korur. Sahne belleği boş olduğu için canlılık sıfırlanır."""
    kayit = oku(dukkan)
    otonom = bool(kayit.get("otonom_aktif"))
    kaydet(dukkan, otonom_aktif=otonom, canli_durum=False, son_hata="")
    return otonom


def _lider_db_al(dukkan, pid, simdi, zorla, pencere):
    """Veritabanı lider satırını alır. zorla=True takılı kilidi bu sürece devreder."""
    if zorla:
        sorgu = """
            UPDATE dukkan_durum
            SET lider_pid = :pid, lider_nabiz = :simdi
            WHERE dukkan_kodu = :kod
        """
        parametre = {"pid": pid, "simdi": simdi, "kod": dukkan}
    else:
        sorgu = """
            UPDATE dukkan_durum
            SET lider_pid = :pid, lider_nabiz = :simdi
            WHERE dukkan_kodu = :kod
              AND (
                lider_pid IS NULL OR lider_pid = '' OR lider_pid = :pid
                OR lider_nabiz IS NULL OR lider_nabiz < :esik
              )
        """
        parametre = {
            "pid": pid,
            "simdi": simdi,
            "kod": dukkan,
            "esik": simdi - float(pencere),
        }
    sonuc = _oturum().execute(text(sorgu), parametre)
    _oturum().commit()
    if int(sonuc.rowcount or 0) == 1:
        return True
    if not zorla:
        return False
    _oturum().execute(
        text(
            """
            INSERT INTO dukkan_durum
                (dukkan_kodu, otonom_aktif, canli_durum, son_hata, lider_pid, lider_nabiz, guncelleme)
            VALUES
                (:kod, :otonom, :canli, :hata, :pid, :simdi, :an)
            ON CONFLICT(dukkan_kodu) DO UPDATE SET
                lider_pid = excluded.lider_pid,
                lider_nabiz = excluded.lider_nabiz
            """
        ),
        {
            "kod": dukkan,
            "otonom": False,
            "canli": False,
            "hata": "",
            "pid": pid,
            "simdi": simdi,
            "an": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
    _oturum().commit()
    return True


def lider_ol(dukkan, sure=None, zorla=False):
    """Döngüyü tek süreç yürütür. Kilit süresi dolunca başka işçi devralabilir.

    zorla=True: "17 Salonu Başlat" takılı kalmış lider kilidini bu sürece devreder.
    """
    pid = str(os.getpid())
    simdi = time.time()
    pencere = float(sure or _kilit_sure)
    if _redis is not None:
        try:
            anahtar = f"halic:dukkan:lider:{dukkan}"
            sahip = _redis.get(anahtar)
            if zorla or sahip in (None, "", pid):
                _redis.set(anahtar, pid, ex=int(pencere))
                if _redis.get(anahtar) == pid:
                    if zorla:
                        try:
                            _lider_db_al(dukkan, pid, simdi, True, pencere)
                        except Exception as hata:
                            print(f"[DUKKAN DURUM] lider devralma yazilamadi: {hata}", flush=True)
                            try:
                                _oturum().rollback()
                            except Exception:
                                pass
                    return True
            return False
        except Exception as hata:
            print(f"[DUKKAN DURUM] Redis lider atlandi: {hata}", flush=True)
    return _lider_db_al(dukkan, pid, simdi, zorla, pencere)


def lider_birak(dukkan):
    pid = str(os.getpid())
    if _redis is not None:
        try:
            anahtar = f"halic:dukkan:lider:{dukkan}"
            if _redis.get(anahtar) == pid:
                _redis.delete(anahtar)
        except Exception as hata:
            print(f"[DUKKAN DURUM] Redis lider birakilamadi: {hata}", flush=True)
    _oturum().execute(
        text(
            """
            UPDATE dukkan_durum
            SET lider_pid = '', lider_nabiz = 0
            WHERE dukkan_kodu = :kod AND lider_pid = :pid
            """
        ),
        {"kod": dukkan, "pid": pid},
    )
    _oturum().commit()
