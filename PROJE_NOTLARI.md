# Proje Notları

## Sunucu

Render ücretli Starter plan (`Halic-Antik-Mezat-canli`, domain: `halicantikcanli.com`). `Halic-Antik-Mezat-canli-1` askıda (Suspended).

## Veritabanı

Ücretli Render PostgreSQL (`halic-mezat-db`). Asla ücretsiz plan veya geçici disk varsayımı yapılmayacak.

## Kritik Kural

`app.py` SQLAlchemy modellerine yeni sütun eklendiğinde mutlaka `veritabani_tablolari_onar()` listesine de eklenmelidir; aksi halde canlı PostgreSQL `UndefinedColumn` hatası verir ve panel boş görünür.

## Kargo

Geliver v1 API + manuel kargo takip (`kargo_barkod_url` sütunu aktif).
