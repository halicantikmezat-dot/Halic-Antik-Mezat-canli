# -*- coding: utf-8 -*-
"""Izole eszamanli pey yazma testi.

Gecici urun ve stress_user_1..50 hesaplari disinda satir silmez.
Canli hedefe, yerel SQLite kimligi tasimaz. Acik mezat gorulurse pey gondermez.
"""
from __future__ import annotations

import math
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask
from flask.sessions import SecureCookieSessionInterface
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

KOK = Path(__file__).resolve().parents[1]
HEDEF_VARSAYILAN = "https://halicantikcanli.com"
URUN_ADI = "[STRESS-TEST] Otomatik Yuk Testi Urunu"
EPOSTA_ALANI = "@test.local"
KULLANICI_SAYISI = 50
DALGA = 5
ACILIS = 1000.0
DUKKAN_YEDEK = "diger"
SUNUCU_HATALARI = {500, 502, 503, 504}


def _cikti_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _ortam_yukle():
    yol = KOK / ".env"
    if not yol.is_file():
        return
    for satir in yol.read_text(encoding="utf-8").splitlines():
        satir = satir.strip()
        if not satir or satir.startswith("#") or "=" not in satir:
            continue
        ad, _, deger = satir.partition("=")
        ad = ad.strip()
        deger = deger.strip().strip('"').strip("'")
        if ad and ad not in os.environ:
            os.environ[ad] = deger


def _eposta(sira):
    return "stress_user_%s%s" % (sira, EPOSTA_ALANI)


def _ad(sira):
    return "STRESS %02d" % sira


def _telefon(sira):
    return "9055599%04d" % sira


def _epostalar():
    return [_eposta(i) for i in range(1, KULLANICI_SAYISI + 1)]


def _veritabani_adresi(hedef):
    ham = (os.environ.get("DATABASE_URL") or "").strip()
    if ham.startswith("postgres://"):
        ham = "postgresql://" + ham[len("postgres://"):]
    yerel = _yerel_mi(hedef)
    if yerel and not ham:
        klasor = KOK / "instance"
        klasor.mkdir(parents=True, exist_ok=True)
        return "sqlite:///" + (klasor / "halic_mezat.db").as_posix()
    return ham


def _yerel_mi(hedef):
    host = (urlparse(hedef).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _uzak_postgres_mu(adres):
    return adres.startswith("postgresql://") or adres.startswith("postgres://")


def _motor(adres):
    return create_engine(adres, pool_pre_ping=True, pool_size=2, max_overflow=0)


def _genis(sql, ad):
    return text(sql).bindparams(bindparam(ad, expanding=True))


def _id_listesi(conn, sql, ad, degerler):
    if not degerler:
        return []
    satirlar = conn.execute(_genis(sql, ad), {ad: list(degerler)}).fetchall()
    return [int(satir[0]) for satir in satirlar]


def _test_urun_idleri(conn):
    satirlar = conn.execute(
        text("SELECT id FROM urun WHERE urun_adi = :ad"),
        {"ad": URUN_ADI},
    ).fetchall()
    return [int(satir[0]) for satir in satirlar]


def _test_kullanici_idleri(conn):
    return _id_listesi(
        conn,
        "SELECT id FROM kullanici WHERE lower(email) IN :emails",
        "emails",
        [e.lower() for e in _epostalar()],
    )


def _yabanci_iz(conn, kullanicilar, urunler):
    """Test kullanicisinin baska bir urunde teklif veya kazanan izi varsa dokunma."""
    if not kullanicilar:
        return []
    urun_filtre = urunler or [-1]
    teklif = conn.execute(
        _genis(
            "SELECT musteri_id, urun_id FROM teklif "
            "WHERE musteri_id IN :ids AND urun_id NOT IN :urunler",
            "ids",
        ).bindparams(bindparam("urunler", expanding=True)),
        {"ids": kullanicilar, "urunler": urun_filtre},
    ).fetchall()
    kazanan = conn.execute(
        _genis(
            "SELECT id, kazanan_id FROM urun "
            "WHERE kazanan_id IN :ids AND id NOT IN :urunler",
            "ids",
        ).bindparams(bindparam("urunler", expanding=True)),
        {"ids": kullanicilar, "urunler": urun_filtre},
    ).fetchall()
    return list(teklif) + list(kazanan)


def _dene(conn, ifade, parametre):
    """Olmayan yan tabloda tum temizligi geri alma."""
    try:
        with conn.begin_nested():
            conn.execute(ifade, parametre)
    except Exception:
        return


def _sil_baglar(conn, kullanicilar, urunler):
    if not urunler:
        return
    conn.execute(_genis("DELETE FROM teklif WHERE urun_id IN :ids", "ids"), {"ids": urunler})
    for sql in (
        "DELETE FROM on_teklif WHERE urun_id IN :ids",
        "DELETE FROM sepet_item WHERE urun_id IN :ids",
        "DELETE FROM urun_takip WHERE urun_id IN :ids",
    ):
        _dene(conn, _genis(sql, "ids"), {"ids": urunler})
    conn.execute(
        _genis("UPDATE urun SET kazanan_id = NULL WHERE id IN :ids", "ids"),
        {"ids": urunler},
    )


def _temizle(engine):
    """Yalnizca birebir test e-postasi ve test urun adini siler."""
    with engine.begin() as conn:
        kullanicilar = _test_kullanici_idleri(conn)
        urunler = _test_urun_idleri(conn)
        iz = _yabanci_iz(conn, kullanicilar, urunler)
        if iz:
            return False, "test kimliginin baska urunde izi var, silinmedi: %s" % (len(iz),)
        _sil_baglar(conn, kullanicilar, urunler)
        if urunler:
            conn.execute(
                _genis(
                    "DELETE FROM urun WHERE id IN :ids AND urun_adi = :ad",
                    "ids",
                ),
                {"ids": urunler, "ad": URUN_ADI},
            )
        if kullanicilar:
            _dene(
                conn,
                _genis(
                    "DELETE FROM kara_liste WHERE lower(email) IN :emails",
                    "emails",
                ),
                {"emails": [e.lower() for e in _epostalar()]},
            )
            conn.execute(
                _genis(
                    "DELETE FROM kullanici WHERE id IN :ids AND lower(email) IN :emails",
                    "ids",
                ).bindparams(bindparam("emails", expanding=True)),
                {"ids": kullanicilar, "emails": [e.lower() for e in _epostalar()]},
            )
        kalan_urun = _test_urun_idleri(conn)
        kalan_kullanici = _test_kullanici_idleri(conn)
    if kalan_urun or kalan_kullanici:
        return False, "temizlik sonrasi iz kaldi urun=%s kullanici=%s" % (
            len(kalan_urun), len(kalan_kullanici)
        )
    return True, "iz yok"


def _hazirla(engine, dukkan):
    with engine.begin() as conn:
        kullanicilar = []
        for sira in range(1, KULLANICI_SAYISI + 1):
            eposta = _eposta(sira)
            bulunan = conn.execute(
                text("SELECT id FROM kullanici WHERE lower(email) = :e"),
                {"e": eposta.lower()},
            ).fetchone()
            if bulunan:
                conn.execute(
                    text(
                        "UPDATE kullanici SET ad_soyad=:ad, telefon=:tel, puan=100000, "
                        "bonus=100000, onayli_mi=:evet, is_verified=:evet, is_banned=:hayir, "
                        "durum='onayli', silindi_mi=:hayir, silinme_tarihi=NULL "
                        "WHERE id=:id AND lower(email)=:e"
                    ),
                    {
                        "ad": _ad(sira),
                        "tel": _telefon(sira),
                        "evet": True,
                        "hayir": False,
                        "id": int(bulunan[0]),
                        "e": eposta.lower(),
                    },
                )
                kullanicilar.append({"id": int(bulunan[0]), "sira": sira, "ad": _ad(sira)})
                continue
            conn.execute(
                text(
                    "INSERT INTO kullanici ("
                    "ad_soyad, telefon, email, puan, bonus, onayli_mi, is_verified, "
                    "is_banned, durum, sozlesme_onay, silindi_mi"
                    ") VALUES ("
                    ":ad, :tel, :email, 100000, 100000, :evet, :evet, :hayir, "
                    "'onayli', :evet, :hayir"
                    ")"
                ),
                {
                    "ad": _ad(sira),
                    "tel": _telefon(sira),
                    "email": eposta,
                    "evet": True,
                    "hayir": False,
                },
            )
            bulunan = conn.execute(
                text("SELECT id FROM kullanici WHERE lower(email) = :e"),
                {"e": eposta.lower()},
            ).fetchone()
            if not bulunan:
                raise RuntimeError("test kullanicisi okunamadi: %s" % eposta)
            kullanicilar.append({"id": int(bulunan[0]), "sira": sira, "ad": _ad(sira)})
        urunler = _test_urun_idleri(conn)
        if urunler:
            urun_id = urunler[0]
            conn.execute(
                text(
                    "UPDATE urun SET dukkan_kodu=:d, acilis_fiyati=:f, guncel_fiyat=:f, "
                    "hemen_al_fiyati=0, durum='Aktif', kazanan_adi='Yok', kazanan_id=NULL, "
                    "silindi_mi=:hayir, stok_adedi=1, kaynak_tipi='manuel' "
                    "WHERE id=:id AND urun_adi=:ad"
                ),
                {"d": dukkan, "f": ACILIS, "hayir": False, "id": urun_id, "ad": URUN_ADI},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO urun ("
                    "dukkan_kodu, lot_no, urun_adi, kategori, acilis_fiyati, guncel_fiyat, "
                    "hemen_al_fiyati, durum, kazanan_adi, kaynak_tipi, stok_adedi, "
                    "siparis_durumu, silindi_mi"
                    ") VALUES ("
                    ":d, 990001, :ad, 'Genel', :f, :f, 0, 'Aktif', 'Yok', 'manuel', 1, "
                    "'Odeme_Bekliyor', :hayir)"
                ),
                {"d": dukkan, "ad": URUN_ADI, "f": ACILIS, "hayir": False},
            )
            urun_id = _test_urun_idleri(conn)[0]
        conn.execute(text("DELETE FROM teklif WHERE urun_id = :id"), {"id": urun_id})
    return urun_id, kullanicilar


def _fiyat_oku(engine, urun_id):
    with engine.connect() as conn:
        satir = conn.execute(
            text(
                "SELECT guncel_fiyat FROM urun WHERE id=:id AND urun_adi=:ad"
            ),
            {"id": urun_id, "ad": URUN_ADI},
        ).fetchone()
    if not satir:
        raise RuntimeError("test urunu kayboldu")
    return float(satir[0] or 0)


def _sonraki_pey(fiyat):
    taban = float(fiyat or 0)
    if taban <= 0:
        taban = ACILIS
    sonraki = int(math.ceil(taban * 1.10))
    if sonraki <= taban:
        sonraki = int(taban) + 1
    return sonraki


def _cerez(gizli, kullanici):
    uygulama = Flask("stress_test_write")
    uygulama.secret_key = gizli
    seri = SecureCookieSessionInterface().get_signing_serializer(uygulama)
    if seri is None:
        raise RuntimeError("oturum imzasi kurulamadi")
    return seri.dumps({
        "kullanici_id": kullanici["id"],
        "musteri_id": kullanici["id"],
        "kullanici_adi": kullanici["ad"],
        "musteri_ad": kullanici["ad"],
    })


def _istek(tur, yontem, url, baslik, govde=None):
    bas = time.perf_counter()
    try:
        yanit = requests.request(yontem, url, headers=baslik, json=govde, timeout=20)
        sure = (time.perf_counter() - bas) * 1000
        ic = {}
        try:
            ic = yanit.json()
        except Exception:
            ic = {}
        if yanit.status_code in SUNUCU_HATALARI or yanit.status_code >= 500:
            sonuc = "sunucu_hatasi"
        elif tur == "GET" and yanit.status_code == 200:
            sonuc = "basarili"
        elif tur == "POST" and ic.get("success") is True:
            sonuc = "basarili"
        else:
            sonuc = "gecersiz"
        return {
            "tur": tur,
            "kod": yanit.status_code,
            "ms": round(sure, 1),
            "sonuc": sonuc,
            "mesaj": str(ic.get("mesaj") or "")[:180],
            "kayit_gerekli": bool(ic.get("kayit_gerekli")),
        }
    except Exception as hata:
        sure = (time.perf_counter() - bas) * 1000
        return {
            "tur": tur,
            "kod": 0,
            "ms": round(sure, 1),
            "sonuc": "ulasim",
            "mesaj": str(hata)[:180],
            "kayit_gerekli": False,
        }


def _baslik(cerez):
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Cookie": "session=" + cerez,
    }


def _pey_govdesi(dukkan, urun_id, kullanici, miktar):
    return {
        "dukkan": dukkan,
        "urun_id": urun_id,
        "musteri_id": kullanici["id"],
        "kullanici_id": kullanici["id"],
        "musteri_adi": kullanici["ad"],
        "islem": "pey",
        "miktar": miktar,
        "platform": "Web",
    }


def _durum_url(hedef):
    return hedef.rstrip("/") + "/api/durum?stres=" + uuid.uuid4().hex


def _durum_oku(hedef):
    url = hedef.rstrip("/") + "/api/durum?on=" + uuid.uuid4().hex
    baslik = {"Cache-Control": "no-store", "Pragma": "no-cache", "Accept": "application/json"}
    try:
        yanit = requests.get(url, headers=baslik, timeout=20)
    except Exception as hata:
        return False, {}, str(hata)
    try:
        govde = yanit.json()
    except Exception:
        return False, {}, "JSON degil HTTP %s" % yanit.status_code
    return yanit.status_code == 200, govde, "HTTP %s" % yanit.status_code


def _bosta_dukkan(govde):
    dukkanlar = (govde or {}).get("dukkanlar") or {}
    canlilar = []
    bosta = []
    for kod, bilgi in dukkanlar.items():
        if bilgi.get("canli") or int(bilgi.get("kalan_sayac") or 0) > 0:
            canlilar.append(kod)
        elif (bilgi.get("durum") or "Bekliyor") in ("Bekliyor", "Pas", ""):
            bosta.append(kod)
    return canlilar, bosta


def _dalga(hedef, dukkan, urun_id, kullanicilar, cerezler, miktar):
    def tek(kullanici, cerez):
        baslik = _baslik(cerez)
        post = _istek(
            "POST",
            hedef.rstrip("/") + "/pey-ver",
            baslik,
            _pey_govdesi(dukkan, urun_id, kullanici, miktar),
        )
        get = _istek("GET", _durum_url(hedef), baslik)
        return post, get

    cikan = []
    with ThreadPoolExecutor(max_workers=KULLANICI_SAYISI) as havuz:
        isler = [
            havuz.submit(tek, kullanici, cerezler[kullanici["id"]])
            for kullanici in kullanicilar
        ]
        for is_ in as_completed(isler):
            post, get = is_.result()
            cikan.append(post)
            cikan.append(get)
    return cikan


def _yuzde(degerler, yuzde):
    if not degerler:
        return 0
    sirali = sorted(degerler)
    yer = min(len(sirali) - 1, max(0, int(round((yuzde / 100) * (len(sirali) - 1)))))
    return sirali[yer]


def _tablo(olcumler):
    print("")
    print("%-6s %6s %6s %6s %6s %6s %8s %8s %8s" % (
        "TUR", "ADET", "BASARI", "GECERSIZ", "5xx", "ULASIM", "p50ms", "p95ms", "maxms"
    ))
    for tur in ("POST", "GET"):
        grup = [o for o in olcumler if o["tur"] == tur]
        sureler = [o["ms"] for o in grup]
        print("%-6s %6s %6s %6s %6s %6s %8s %8s %8s" % (
            tur,
            len(grup),
            sum(1 for o in grup if o["sonuc"] == "basarili"),
            sum(1 for o in grup if o["sonuc"] == "gecersiz"),
            sum(1 for o in grup if o["sonuc"] == "sunucu_hatasi"),
            sum(1 for o in grup if o["sonuc"] == "ulasim"),
            _yuzde(sureler, 50),
            _yuzde(sureler, 95),
            max(sureler) if sureler else 0,
        ))


def _denetle(engine, urun_id, olcumler):
    with engine.connect() as conn:
        satirlar = conn.execute(
            text(
                "SELECT id, tutar, musteri_id FROM teklif "
                "WHERE urun_id=:id ORDER BY id ASC"
            ),
            {"id": urun_id},
        ).fetchall()
    tutarlar = [float(satir[1]) for satir in satirlar]
    artan = all(tutarlar[i] > tutarlar[i - 1] for i in range(1, len(tutarlar)))
    cift = len(tutarlar) != len({round(t, 2) for t in tutarlar})
    sunucu = [
        o for o in olcumler
        if o["kod"] in SUNUCU_HATALARI or o["sonuc"] == "sunucu_hatasi"
    ]
    basarili = sum(1 for o in olcumler if o["tur"] == "POST" and o["sonuc"] == "basarili")
    print("")
    print("DENETIM teklif=%s basarili_post=%s" % (len(tutarlar), basarili))
    print("  artan_sira: %s" % ("GECTI" if artan else "KALDI"))
    print("  cift_tutar_yok: %s" % ("GECTI" if not cift else "KALDI"))
    print("  sunucu_hatasi_yok: %s (%s)" % ("GECTI" if not sunucu else "KALDI", len(sunucu)))
    print("  kilit_gozlendi: %s" % ("GECTI" if basarili else "KALDI"))
    return artan and not cift and not sunucu and basarili > 0


def _gizli_anahtar():
    return os.environ.get("SECRET_KEY") or "halic_hamid_antik_mezat_gizli_anahtar_1453"


def calistir(hedef):
    _cikti_utf8()
    _ortam_yukle()
    hedef = hedef.rstrip("/")
    print("HEDEF %s" % hedef)
    durum_ok, govde, durum_not = _durum_oku(hedef)
    canlilar, bosta = _bosta_dukkan(govde) if durum_ok else ([], [])
    print("DURUM %s canli=%s bosta=%s" % (durum_not, ",".join(canlilar) or "-", ",".join(bosta) or "-"))
    adres = _veritabani_adresi(hedef)
    if not _yerel_mi(hedef) and not _uzak_postgres_mu(adres):
        print("KARAR Yazma gonderilmedi. DATABASE_URL postgres degil; yerel kimlik canliya yazilmaz.")
        return 2
    if not durum_ok:
        print("KARAR Yazma gonderilmedi. Durum ucu okunamadi.")
        return 2
    if canlilar or not bosta:
        print("KARAR Yazma gonderilmedi. Acik mezat varken pey, sahne bellegini bozar.")
        return 2
    dukkan = DUKKAN_YEDEK if DUKKAN_YEDEK in bosta else bosta[0]
    print("DUKKAN %s" % dukkan)
    engine = _motor(adres)
    olcumler = []
    urun_id = None
    try:
        temiz, temiz_not = _temizle(engine)
        if not temiz:
            print("KARAR Kurulum durdu. %s" % temiz_not)
            return 2
        urun_id, kullanicilar = _hazirla(engine, dukkan)
        gizli = _gizli_anahtar()
        cerezler = {k["id"]: _cerez(gizli, k) for k in kullanicilar}
        miktar = _sonraki_pey(_fiyat_oku(engine, urun_id))
        ornek = _istek(
            "POST",
            hedef + "/pey-ver",
            _baslik(cerezler[kullanicilar[0]["id"]]),
            _pey_govdesi(dukkan, urun_id, kullanicilar[0], miktar),
        )
        fiyat = _fiyat_oku(engine, urun_id)
        if ornek.get("kayit_gerekli") or fiyat <= ACILIS:
            print("KARAR On yoklama test urunune inmedi (HTTP %s, fiyat %s). Saldiri yok." % (
                ornek.get("kod"), fiyat
            ))
            print("  mesaj: %s" % ornek.get("mesaj"))
            return 2
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM teklif WHERE urun_id=:id"), {"id": urun_id})
            conn.execute(
                text(
                    "UPDATE urun SET guncel_fiyat=:f, acilis_fiyati=:f, durum='Aktif', "
                    "kazanan_adi='Yok', kazanan_id=NULL WHERE id=:id AND urun_adi=:ad"
                ),
                {"f": ACILIS, "id": urun_id, "ad": URUN_ADI},
            )
        time.sleep(2.1)
        for dalga_no in range(DALGA):
            miktar = _sonraki_pey(_fiyat_oku(engine, urun_id))
            olcumler.extend(_dalga(hedef, dukkan, urun_id, kullanicilar, cerezler, miktar))
            if any(o.get("kayit_gerekli") for o in olcumler):
                print("KARAR Oturum hedef veritabaninda yok. Kalan dalgalar kesildi.")
                break
            if dalga_no + 1 < DALGA:
                time.sleep(2.1)
        _tablo(olcumler)
        gecti = _denetle(engine, urun_id, olcumler)
        return 0 if gecti else 1
    except SQLAlchemyError as hata:
        print("KARAR Veritabani hatasi, saldiri yok veya kesildi: %s" % hata)
        return 2
    finally:
        try:
            temiz, temiz_not = _temizle(engine)
            print("TEMIZLIK %s" % temiz_not)
            if not temiz:
                print("TEMIZLIK KALDI")
        except Exception as hata:
            print("TEMIZLIK hata: %s" % hata)
        engine.dispose()


def main():
    hedef = HEDEF_VARSAYILAN
    argv = sys.argv[1:]
    if "--hedef" in argv:
        yer = argv.index("--hedef")
        hedef = argv[yer + 1]
    raise SystemExit(calistir(hedef))


if __name__ == "__main__":
    main()
