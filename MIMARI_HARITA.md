# Mimari Harita — Haliç Antik Canlı Mezat

Bu dosya projenin röntgenidir. Arama veya düzenlemeden önce buraya bakın; büyük dosyaları baştan sona taramayın. Satır numaraları okuma anındaki konumdur (Kasım 2026).

Çalışan Python, HTML ve JS dosyaları bu haritayla değiştirilmedi. Yeni iş, ayrı bir Blueprint modülü ve ayrı bir şablon parçası olarak eklenir.

## Nasıl kullanılır

1. İşi aşağıdaki tabloya yerleştirin.
2. Yalnızca o dosyanın ilgili satır aralığını açın.
3. Canlı yayın, sayaç, pey motoru ve mevcut tablolar kırmızı çizgidir.

| İş | İlk bakılacak yer |
| --- | --- |
| Canlı mezat ekranı, seyirci sohbeti, pey butonu | `templates/index.html` + `app.py` `/pey-ver` satır 6131 |
| Sayaç (120 pazarlık, 60 karar) | `mezat_sayac.py` ve `app.py` `_uc_dakikalik_pazarlik` satır 2800 |
| Yönetim paneli | `templates/admin.html` |
| Canlı yayın / sosyal dinleyici | `canli_yayin_havuzu.py` (bağlantı `app.py` satır 13663) |
| RTMP / FFmpeg çoklu yayın | `sosyal_yayin_havuzu.py` |
| Ek sosyal kanal ve çoklu yorum | `sosyal_kopru_havuzu.py` + `templates/admin_sosyal_kopru_bolumu.html` |
| Tedarikçi paneli | `tedarikci_havuzu.py` + `templates/admin_tedarikci_bolumu.html` ve `templates/tedarikci_panel_modal.html` |
| Katalog / kendi XML | `katalog_havuzu.py` |
| XML toptancı laboratuvarı | `app.py` satır 10725–11758 |
| Pazaryeri siparişleri | `pazaryeri.py` |
| Kargo etiketi | `lojistik_sevkiyat.py` (rota `app.py` satır 8800) |
| Kamera WebRTC | `kamera_sinyal.py` |
| Diğer dükkan sahnesi | `diger_canli_sahne.py` ve `diger_canli_kapi.py` |
| Veritabanı modelleri | `app.py` satır 1820–2052 |

## Bağlantı omurgası

`app.py` Flask uygulamasını, SQLAlchemy `db` nesnesini, Socket.IO `socketio` nesnesini ve dükkan sözlüğünü `DUKKANLAR` (satır 136) kurar. Alt modüller import sırasında veya dosyanın sonunda bağlanır:

| Satır (`app.py`) | Ne bağlanır |
| --- | --- |
| 68–70 | `bizimhesap_service.bizimhesap_siparis_aktar`, `config.Config`, `diger_canli_kapi` |
| 228–230 | `diger_canli_sahne.bagla(db)` — kumbara deposu |
| 13567–13571 | `pazaryeri.init_pazaryeri` + blueprint `/admin/pazaryeri` |
| 13646–13653 | `katalog_havuzu.katalog_havuzu_bagla` |
| 13663–13684 | `canli_yayin_havuzu.bagla` — pey, sayaç nabzı, oda kilitleri enjekte edilir |
| 13687–13734 | Canlı yayın HTTP rotaları bu bağın içinde tanımlanır |
| 13742–13744 | `kamera_sinyal.bagla` |
| 13749–13751 | `tedarikci_havuzu.tedarikci_bp` ve `sosyal_kopru_havuzu.bp` |

`canli_yayin_havuzu.bagla` (kendi dosyasında satır 153) mezat çekirdeğine şu kancalarla tutunur: `odalar`, `oda_kilitleri`, `Urun`, `Teklif`, `pey_kabul`, `pey_duyur`, `erken_sat` (`_pazarlik_erken_sat`), `nabiz` (`_nabiz_dokun`). Yorumdan gelen pey burada yazılır; sayaç kuralı `mezat_sayac.py` ve `app.py` içindedir.

`sosyal_kopru_havuzu.coklu_yorum_isle` (satır 317) gelen yorumu `canli_yayin_havuzu` üzerinden odaya bırakır. `facebook_bridge.py` ve `instagram_bridge.py` dinledikleri sohbeti `canli_yayin_havuzu` havuzuna verir. `sosyal_yayin_havuzu.py` aynı havuzdan RTMP hedefi olarak çağrılır (`canli_yayin_havuzu.py` satır 226 ve 3412 civarı).

`tiktok_bridge.js` ayrı bir Node sürecidir. TikTok canlı sohbetini okuyup Flask’a (`/api/tiktok-web-chat`, `app.py` satır 5419) ve bot portuna POST eder. Python tarafındaki start/stop: `/api/social/tiktok/start` satır 7288, `/api/social/tiktok/stop` satır 7305.

## Kırmızı çizgi — sayaç, pey, satış olayları

Görünen sayaç: pazarlık `120 → 1`, karar `60 → 0`. İç saat 180 saniyedir (`mezat_sayac.py` başlığı ve `gorunen_kalan` satır 9).

| Parça | Konum |
| --- | --- |
| Görünen saniye paketi | `mezat_sayac.sayac_paketi` satır 21 |
| 120→60 geçişi bir kez | `mezat_sayac.gecis_bir_kez` satır 34 |
| Nabız / görev kilidi | `mezat_sayac.py` satır 110–176 (`nabiz_anahtari`, `sayac_kesilsin_mi`) |
| Geri sayım döngüsü | `app.py` `_uc_dakikalik_pazarlik` satır 2800 |
| Saniye düşürme | `app.py` `_sayac_saniye_dus` satır 2786 |
| Görev yeşil hattı | `app.py` `dukkan_geri_sayim_gorevi` satır 3110 |
| Görev bırakma | `app.py` `_sayac_gorevini_birak` satır 3099 |
| Nabız dokunuşu | `app.py` `_nabiz_dokun` satır 3164 |
| Erken satış (gizli taban) | `app.py` `_pazarlik_erken_sat` satır 2733 |
| HTTP pey | `app.py` `/pey-ver` → `pey_ver` satır 6131 |
| Minimum artış | `app.py` `kademeli_min_artis` satır 1426 |
| Yorumdan pey | `canli_yayin_havuzu.py` `yorumdan_pey_coz` satır 488, `_pey_yaz` satır 707 |
| Mezat başlat | `app.py` `/mezat-baslat` satır 7540 |
| Sahneye al | `app.py` `/sahneye-al` satır 7406 |
| Satışı bitir | `app.py` `/satis-bitir` satır 8357 |
| Son peyi iptal | `app.py` `/son-peyi-iptal-et` satır 8454 |
| Otonom aç/kapa | `app.py` `/api/admin/otonom-mezat-kontrol` satır 9290 |
| Kalıcı otonom/canlı bayrak | `dukkan_kalici_durum.py` tablo `dukkan_durum` |

Socket.IO olay adları (bozulmaz): `sayac_guncelle`, `pazarlik_kapandi`, `sayac_bitti`, `urun_satildi_erken`, `admin_satis_guncelle`, `ses_oynat`, `ses_kes`. Yayın kapısı `_iki_odayi` (`app.py` satır 611) dükkan odası ve `admin_room` için kullanılır. İzleyici tarafı `templates/index.html`, yönetim tarafı `templates/admin.html` (sayaç dinleyicileri yaklaşık satır 4703, 4781, 4930, 4948).

Ek sahne olayları (aynı akışa bağlı): `sahne_bosta`, `yeni_urun_sahneye_alindi`, `yeni_sahne_urunu`, `pey_guncellendi`, `sahne_durumu`, `tanitim_basladi`, `mezat_basladi_muzik`.

## `app.py` — çekirdek (yaklaşık 13762 satır)

Tek dosyada uygulama, modeller, mezat, ses, üyelik, POS, XML ve yönetim rotaları durur. Yeni özellik buraya gömülmez.

### Açılış ve ortaklar

| Aralık | İş |
| --- | --- |
| 1–80 | gevent, Azure konuşma, Flask |
| 85–131 | CORS, güvenlik başlıkları, gzip |
| 136–154 | `DUKKANLAR` (gumus, antika, toptan, kozmetik, bijuteri, hirdavat, tasarim, modelaraba, karma, diger, porselen, bakir, elektronik, plak, retro, evyasam, tekstil) |
| 158–244 | SQLite `instance/halic_mezat.db` veya `DATABASE_URL`, `db`, `socketio` |
| 287–567 | Ahmet Usta TTS (ElevenLabs, OpenAI, Edge, yerel) |
| 568 | `POST /api/tts` |
| 592–1262 | Sahne sesi, pey anonsu, `pey_guncellendi` |
| 1417–1439 | Pey tabanı ve `bota_haber_ver` |
| 1484–1655 | Entegrasyon tokeni, hız limiti, `admin_required`, 2FA ortamı |
| 1699–1781 | Cloudinary / yerel medya (`medyayi_kaydet`) |

### Veritabanı modelleri (`db.Model`) — dokunulmaz

| Satır | Sınıf | Tablo |
| --- | --- | --- |
| 1820 | `Kullanici` | `kullanici` |
| 1845 | `KaraListe` | `kara_liste` |
| 1856 | `Urun` | `urun` |
| 1939 | `OnTeklif` | `on_teklif` |
| 1954 | `Teklif` | `teklif` |
| 1970 | `SikayetOneri` | `sikayet_oneri` |
| 1982 | `Elestiri` | `elestiriler` |
| 1994 | `Muzik` | `muzik` |
| 2000 | `SepetItem` | `sepet_item` |
| 2012 | `UrunTakip` | `urun_takip` |
| 2024 | `UrunAlimSozlesmesi` | `urun_alim_sozlesmesi` |
| 2042 | `Ayar` | `ayar` |
| 2050 | `IcerikMaddesi` | `icerik_maddesi` |

Ayrıca modüllerin kendi tabloları: `dukkan_durum`, `canli_yayin_havuz`, `canli_yayin_push`, `sosyal_kanallar`, `tedarikci_ayarlari`, `tedarikci_urunleri`, `tedarikci_hesaplari`, pazaryeri `tedarikci`.

### HTTP rotaları

| Satır | Rota | İş |
| --- | --- | --- |
| 3885 | `/static/uploads/<path>` | Yüklenen dosya |
| 3993 | `/` | Canlı mezat (`index.html`) |
| 4017–4047 | favicon, sw.js, manifest, robots | PWA / SEO |
| 4058 | `/dukkan/<dukkan_adi>` | Dükkan seçimi |
| 4069 | `/vitrin` | Vitrin |
| 4075 | `/kurallar` | Sözleşme sayfası |
| 4080 | `GET /icerik-maddeleri` | Kurallar maddeleri |
| 4091 | `GET /urun-incele/<id>` | Ürün kartı |
| 4126–4141 | `/admin/uyeler`, `/admin/uye-kaydet` | Üye yönetimi |
| 4205–4289 | `/admin/icerik-madde-*` | Sözleşme maddeleri |
| 4323–4328 | `/ahmet-tanitim-geri`, `/ahmet-metin-oku` | Spiker |
| 4340 | `/overlay` | Yayın katmanı |
| 4346 | `GET /api/dukkanlar` | Dükkan listesi |
| 4358 | `GET /api/doviz-kurlari` | Kur |
| 4369 | `GET /vitrin-urunler` | Vitrin verisi |
| 4539–4544 | `/kurumsal-bilgiler` | Kurumsal metin |
| 4570 | `GET /admin/urun-ara` | Admin ürün arama |
| 4612 | `GET /canli-durum` | Sahne anlık durumu |
| 4670 | `GET /api/durum` | Durum API |
| 4718 | `GET /durum-getir` | Oda durumu |
| 4922 | `GET /kategoriler-listesi` | Kategoriler |
| 5216 | `POST /api/chat/mesaj-gonder` | Sohbet / pey yorumu |
| 5419 | `POST /api/tiktok-web-chat` | TikTok köprüsü |
| 5442 | `POST /api/hemen-al-tetikle` | Hemen al |
| 5512 | `POST /api/otomatik-urun-ekle` | Otomatik lot |
| 5571–5644 | bas-konuş ve `/api/bot/ahmet-usta-yanit` | Ses ve bot |
| 5675–5753 | `/admin/sozlesme-*` | Alım sözleşmesi |
| 5786–5866 | sepete ekle / çıkar / getir | Sepet |
| 5957 | `POST /odeme-baslat` | Ödeme başlangıcı |
| 6035–6058 | `/pos`, `/pos/siparis-onayla` | Havale/EFT |
| 6131 | `POST /pey-ver` | Pey motoru |
| 6508 | `POST /urun-takip-et` | Takip |
| 6539–6848 | kayıt, profil, doğrulama | Üyelik |
| 6910–7028 | ön teklif ver / sil / güncelle | Vitrin ön teklif |
| 7165–7253 | admin giriş, 2FA, şifre | Oturum |
| 7288–7305 | TikTok start/stop | Sosyal süreç |
| 7314 | `GET /admin` | Yönetim (`admin.html`) |
| 7359 | `GET /admin/yedek-indir` | Yedek |
| 7395 | `/admin-cikis-yap` | Çıkış |
| 7406 | `POST /sahneye-al` | Ürünü sahneye koy |
| 7540 | `POST /mezat-baslat` | Sayacı başlat |
| 7697–8339 | `/api/diger/*` | Diğer dükkan tarama, kuyruk, kumbara |
| 8357–8513 | satis-bitir, son-peyi-iptal, tek-urun-iptal | Satış kapanışı |
| 8748 | `POST /admin/kargo-olustur` | Kargo |
| 8800–8812 | `/admin/lojistik/etiket`, `/icmal` | `lojistik_sevkiyat` |
| 8889 | `POST /api/cloudinary-imza` | Medya imzası |
| 8917–9054 | ürün ekle / güncelle / sil | Katalog yazma |
| 9079–9255 | müşteri ve çöp kutusu | Silinen ürünler |
| 9290 | `POST /api/admin/otonom-mezat-kontrol` | 7/24 otonom |
| 10028 | `GET /excel-indir` | Rapor |
| 10698–10716 | excel sil / sıfırla / yükle | Rapor dosyası |
| 10725–11758 | XML laboratuvar, dağıtım, toptancı, stok senkron, `/api/manuel-kar` | Toptancı aktarımı |
| 13687–13734 | `/api/canli-yayin/*`, meta webhook | Canlı yayın kapısı |

### Socket.IO dinleyicileri (`app.py`)

| Satır | Olay | İş |
| --- | --- | --- |
| 12104 | `connect` | Bağlantı |
| 12114 | `disconnect` | Ayrılma |
| 12123 | `join_admin` | Yönetici odası |
| 12217 | `yonetici_duzeltmesi` | Reji düzeltmesi |
| 12453 | `reji_ses_komutu` | Reji sesi |
| 12462 | `patron_ses_komut` | Patron ses komutu |
| 12471 | `patron_yayin_metni` | Patron metni |
| 12487 | `moderator_ses_parcasi` | Moderatör ses parçası |
| 12527–12587 | `admin_voice_broadcast` / `_chunk` / `_end` | Yönetici mikrofonu |
| 12652 | `join_dukkan` | Dükkan odası |
| 12702 | `leave_dukkan` | Odadan çık |
| 12717–12797 | `diger_izleyici_*`, `diger_spiker_anahtar`, `diger_mikrofon_durum`, `diger_reji_mikrofon_mute` | Diğer dükkan canlı ses |
| 12872–12930 | `ozel_sohbet_*` | Yönetici–izleyici sohbeti |
| 12959 | `anons_bitti` | Anons sırası |
| 12972 | `ahmet_ses_iste` | Spiker isteği |
| 12993 | `admin_sesli_komut` | Sesli yönetim |

Arka plan: bellek temizliği satır 13512, otonom gözcü `_arkaplan_agir_baslangic` satır 13521 (8 sn gecikmeyle, satır 13559). Sunucu girişi satır 13758: `socketio.run`, port `PORT` veya 5000. Üretimde `gunicorn.conf.py` (`post_fork` satır 28).

## Modüller

### `canli_yayin_havuzu.py` (yaklaşık 3925 satır)

Canlı yayın durumu, platform dinleyicileri (YouTube satır 1701, TikTok satır 1746, Instagram satır 1823), yorum→pey, webhook ve push aboneliği. Rotalar `app.py` sonunda bu modüle delege edilir:

- `durum_yaniti` satır 240 → `GET /api/canli-yayin/durum`
- `kontrol_yaniti` satır 250 → `POST /api/canli-yayin/kontrol`
- `meta_webhook_yaniti` satır 276 → `GET/POST /api/canli-havuz/meta-webhook`
- `webhook_yorum_yaniti` satır 313 → `POST /api/canli-yayin/webhook-yorum`
- `push_abone_yaniti` satır 1572 → `POST /api/canli-yayin/push-abone`
- `reji_platform_yaniti` — `POST /api/canli-yayin/reji-platform` (app satır 13731)

Kalıcı kayıt: tablo `canli_yayin_havuz`, `canli_yayin_push` (satır 132–133). Redis varsa önce Redis.

### `sosyal_yayin_havuzu.py` (yaklaşık 1332 satır)

FFmpeg ile tek veya çoklu RTMP. Kapı: `yayin_kapisi` satır 175, `parca_yaz` satır 193, `api` satır 242. Kalıcı hesap listesi veritabanı/Redis; yedek dosya `instance/sosyal_yayin_havuzu.json`.

### `sosyal_kopru_havuzu.py` (yaklaşık 522 satır)

Blueprint `sosyal_kopru_havuzu`. Tablo `sosyal_kanallar` (satır 81).

| Satır | Rota |
| --- | --- |
| 398 | `GET /admin/sosyal-kopru/durum` |
| 411 | `POST /admin/sosyal-kopru/ekle` |
| 434 | `POST /admin/sosyal-kopru/durum-ayarla` |
| 456 | `POST /admin/sosyal-kopru/tumunu-baslat` |
| 471 | `POST /admin/sosyal-kopru/tumunu-durdur` |
| 486 | `POST /admin/sosyal-kopru/sil` |
| 507 | `POST /api/canli_havuz/coklu_yorum` |

Şablon: `templates/admin_sosyal_kopru_bolumu.html`, `admin.html` satır 563 içinde include.

### `tedarikci_havuzu.py`

Blueprint `tedarikci_havuzu`. Tablolar satır 308–360: `tedarikci_ayarlari`, `tedarikci_urunleri`, `tedarikci_hesaplari`. Onaylanan ürün vitrine ve `katalog_havuzu` içine yazılır (`_vitrine_ekle` satır 804, `_kataloga_ekle` satır 880).

Satıcı: `/api/tedarikci/giris` 1343, oturum 1400, ürün 1459, kargola 1657, AI ön analiz 2048. Yönetici: `/api/tedarikci/admin/ayarlar` 1708, ürünler 1754, onayla 1825, reddet 1951, pin 2011, AI denetim 2083.

Şablonlar: `admin_tedarikci_bolumu.html` (`admin.html` satır 1077), `tedarikci_panel_modal.html` (`index.html` satır 2385).

### `katalog_havuzu.py`

Blueprint `katalog_havuzu`, bağlama `katalog_havuzu_bagla` satır 45. Dosyalar: `instance/katalog_havuzu.json`, `instance/kendi_xml.json`, medya `instance/katalog_havuzu_medya`.

| Rota | İş |
| --- | --- |
| `GET /api/katalog-havuzu` | Liste |
| `POST /api/katalog-havuzu/ekle` | Ekle |
| `POST /api/katalog-havuzu/tara-siradaki` | AI tarama |
| `POST /api/katalog-havuzu/xml-aktar` | XML’e aktar |
| `POST /api/katalog-havuzu/<id>` | Güncelle |
| `POST /api/katalog-havuzu/<id>/onayla` | Onay |
| `POST /api/katalog-havuzu/<id>/sil` | Sil |
| `GET /api/katalog-havuzu/medya/<id>` | Medya |
| `GET /api/kendi-xml` | Dışa XML (`_kendi_xml` satır 485) |

`app.py` `_kendi_xml_canli_bagla` (satır 13609) bu XML’e sahnedeki lotu ekler.

### `pazaryeri.py`

Blueprint öneki `/admin/pazaryeri`. Şema `pazaryeri_semasini_hazirla` satır 51, tablo `tedarikci`. Panel HTML’i dosyanın içinde `render_template_string` ile durur (ayrı templates dosyası yok).

| Satır | Rota (öneksiz) |
| --- | --- |
| 500 | `GET /` panel |
| 505 | `GET /api/veriler` |
| 614 | `POST /api/tedarikci-ekle` |
| 633 | `POST /api/durum-guncelle` |
| 644 | `POST /api/kargo-guncelle` |
| 655 | `POST /api/siparis-duzenle` |
| 673 | `POST /api/siparis-iptal` |
| 691 | `POST /api/siparis-sil` |

### `diger_canli_sahne.py` ve `diger_canli_kapi.py`

Sahne: fiyat adımı, kamera karesinden ürün analizi (`urunu_analiz_et` satır 214), sahne kuyruğu, kumbara satışı (`satis_kaydi_ekle` satır 515, `bagla` satır 623). Kapı: spiker susturma, izleyici nabzı (`dokun` satır 105, `temizlik_dongusu` satır 193). HTTP karşılıkları `app.py` `/api/diger/*` bloğu satır 7697–8339.

### `lojistik_sevkiyat.py`

Müşteri dosyası özeti, Code128, QR ve etiket/icmal HTML. Çağrı `app.py` satır 4878 (`lojistik_dosya_ozeti`) ve rotalar 8800–8812. Ağır hesap `etiket_html` satır 505, `icmal_html` satır 560.

### `kamera_sinyal.py`

WebRTC sinyalı. Olaylar: `kamera_yayin_basla`, `kamera_yayin_bitti`, `kamera_izle`, `kamera_teklif`, `kamera_yanit`, `kamera_buz` (satır 16–66). Karşı taraf `templates/admin.html` yaklaşık satır 3621–3684.

### `dukkan_kalici_durum.py`

`oku` / `kaydet` / `lider_ol` (satır 167, 177, 231). Redis ve `dukkan_durum` tablosu. `app.py` `dukkan_otonom_mu` satır 636, `dukkan_canli_mi` satır 646.

### `facebook_bridge.py` / `instagram_bridge.py`

Tarayıcı köprüsü. Giriş `start_listening` (facebook satır 91, instagram satır 90). Yorum `canli_yayin_havuzu` içine düşer.

### `canli_bot.py`

Ayrı spiker süreci: pey metni, TTS kuyruğu, `ortak_chat_yoneticisi` satır 544, `web_servisi_baslat` satır 812. Çekirdek mezat `app.py` içindedir; bu dosya yan bottur.

### `bizimhesap_service.py`

`bizimhesap_siparis_aktar` satır 133. Sipariş aktarımı. `app.py` satır 68 import.

### `config.py`

`.env` yükler, `Config` satır 73, veritabanı havuzu `SQLALCHEMY_ENGINE_OPTIONS`.

### `gunicorn.conf.py`

Worker sonrası `post_fork` satır 28.

### Yardımcı betikler (canlı rota değil)

| Dosya | İş |
| --- | --- |
| `veri_pompala.py` | Yerel SQLite kullanıcılarını PostgreSQL’e aktarır |
| `model_indir.py` | Piper ses modeli indirir |
| `test_ekle.py` | Yerel mezata deneme ürünü yollar |
| `test_azure.py` | Azure konuşma denemesi |
| `static/sayac_nabiz_canli_test.py` | Sayaç nabız denemesi |
| `static/stress_test_write.py`, `static/stress_test_1000.py`, `static/otonom_dongu_simulasyon.py` | Yük / döngü denemesi |

## Şablonlar (`templates/`)

| Dosya | Rota | İş |
| --- | --- | --- |
| `index.html` | `/` ve `/dukkan/...` | Canlı mezat. Socket.IO, pey, sayaç, sohbet, kamera. Tedarikçi modalı satır 2385. |
| `admin.html` | `/admin` | Reji, sayaç, XML, kargo, çöp kutusu, otonom. Sosyal köprü parçası satır 563, tedarikçi parçası satır 1077. |
| `admin_sosyal_kopru_bolumu.html` | include | Sosyal kanal paneli. `/admin/sosyal-kopru/*` çağırır. |
| `admin_tedarikci_bolumu.html` | include | Yönetici tedarikçi sekmesi. `/api/tedarikci/admin/*`. |
| `tedarikci_panel_modal.html` | include | Seyirci tarafı satıcı paneli. `/api/tedarikci/*`. |
| `overlay.html` | `/overlay` | OBS / yayın üst katmanı. |
| `vitrin.html` | `/vitrin` | Lot vitrini ve ön teklif. |
| `pos.html` | `/pos` | Havale / EFT. |
| `kurallar.html` | `/kurallar` | Şartname. |

İstemci olay dinleyicilerinin yoğun olduğu yer `admin.html` yaklaşık satır 1699–5030 ve `index.html` içindeki socket bloğu. Sunucu olay adını değiştirirseniz her iki şablonu da aynı anda okumak gerekir; adlar kırmızı çizgidedir.

## Statik ve yan dosyalar

- `static/sw.js`, `static/manifest.json` — PWA. Rotalar `app.py` 4022 ve 4035.
- `static/uploads`, `static/sesler` — medya ve üretilen ses.
- `tiktok_bridge.js` — Node TikTok dinleyici.
- `KULLANMA_TALIMATI.md`, `PROJE_NOTLARI.md` — mevcut notlar. Bu harita onların yerine geçmez; kod konumu için bu dosya esas alınır.
