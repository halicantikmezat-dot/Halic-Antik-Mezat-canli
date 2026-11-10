"""Yerel kullanici kayitlarini PostgreSQL'e aktarir.

Calistirma:
    set POSTGRES_URL=postgresql://kullanici:sifre@host:5432/veritabani
    python veri_pompala.py
"""
import os
import sqlite3
import sys

import psycopg2


def postgres_adresi():
    adres = (os.environ.get("POSTGRES_URL") or "").strip()
    if adres.startswith("postgres://"):
        adres = adres.replace("postgres://", "postgresql://", 1)
    return adres


def main():
    kok = os.path.abspath(os.path.dirname(__file__))
    sqlite_yol = os.path.join(kok, "instance", "halic_mezat.db")
    hedef = postgres_adresi()
    if not hedef:
        print("POSTGRES_URL tanimli degil. Aktarim yapilmadi.")
        return 1
    if not os.path.isfile(sqlite_yol):
        print("SQLite dosyasi bulunamadi:", sqlite_yol)
        return 1

    kaynak = sqlite3.connect(sqlite_yol)
    try:
        cur = kaynak.cursor()
        cur.execute("SELECT * FROM kullanici")
        sutunlar = [bilgi[0] for bilgi in cur.description]
        satirlar = cur.fetchall()
    except sqlite3.Error as hata:
        print("SQLite okunamadi:", hata)
        return 1
    finally:
        kaynak.close()

    if "id" not in sutunlar:
        print("kullanici tablosunda id sutunu yok. Aktarim yapilmadi.")
        return 1

    aktarilan = 0
    try:
        baglanti = psycopg2.connect(hedef)
        baglanti.autocommit = False
        with baglanti:
            with baglanti.cursor() as pg:
                pg.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'kullanici'"
                )
                hedef_sutunlar = {satir[0] for satir in pg.fetchall()}
                if not hedef_sutunlar:
                    print("PostgreSQL tarafinda kullanici tablosu bulunamadi.")
                    return 1
                ortak = [ad for ad in sutunlar if ad in hedef_sutunlar]
                if "id" not in ortak:
                    print("Hedef tabloda id sutunu yok. Aktarim yapilmadi.")
                    return 1
                fazla = [ad for ad in sutunlar if ad not in hedef_sutunlar]
                if fazla:
                    print("Hedefte olmadigi icin atlanan sutunlar:", ", ".join(fazla))
                indeksler = [sutunlar.index(ad) for ad in ortak]
                adlar = ", ".join('"%s"' % ad for ad in ortak)
                yerler = ", ".join(["%s"] * len(ortak))
                sql = (
                    "INSERT INTO kullanici (%s) VALUES (%s) "
                    "ON CONFLICT (id) DO NOTHING" % (adlar, yerler)
                )
                for satir in satirlar:
                    pg.execute(sql, [satir[i] for i in indeksler])
                    if pg.rowcount and pg.rowcount > 0:
                        aktarilan += 1
                pg.execute(
                    "SELECT setval(pg_get_serial_sequence('kullanici', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM kullanici), 1), true)"
                )
    except psycopg2.Error as hata:
        print("PostgreSQL aktarimi basarisiz:", hata)
        return 1

    print("%s kayit aktarildi." % aktarilan)
    print("Okunan: %s. Zaten vardi, atlandi: %s." % (len(satirlar), len(satirlar) - aktarilan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
