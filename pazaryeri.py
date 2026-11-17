import json
import threading
import urllib.parse
from datetime import datetime, timezone
from flask import Blueprint, has_request_context, jsonify, render_template_string, request
from sqlalchemy import text


def _pazaryeri_medya_listesi(ham):
    if ham is None:
        return []
    if isinstance(ham, (bytes, bytearray)):
        ham = ham.decode("utf-8", "ignore")
    if isinstance(ham, str):
        metin = ham.strip()
        if not metin:
            return []
        try:
            ham = json.loads(metin)
        except (TypeError, ValueError):
            return [metin] if metin.startswith("http") else []
    if isinstance(ham, dict):
        return [ham]
    if isinstance(ham, list):
        return ham
    return []


def _pazaryeri_ilk_gorsel(ham):
    adaylar = []
    for oge in _pazaryeri_medya_listesi(ham):
        adres = ""
        if isinstance(oge, str):
            adres = oge.strip()
        elif isinstance(oge, dict):
            adres = str(oge.get("url") or oge.get("secure_url") or "").strip()
        if adres.startswith("http://") or adres.startswith("https://"):
            adaylar.append(adres)
    for adres in adaylar:
        dusuk = adres.lower().split("?", 1)[0]
        if "/video/upload/" in dusuk or dusuk.endswith((".mp4", ".webm", ".mov", ".m4v")):
            continue
        return adres
    return adaylar[0] if adaylar else ""

pazaryeri_bp = Blueprint("pazaryeri_bp", __name__)
_sema_kilit = threading.Lock()
_sema_hazir = False
_oturum_hazir = set()
_PAZARYERI_TEDARIKCI_KOLONLARI = (
    ("ad_soyad", "VARCHAR(100)"),
    ("telefon", "VARCHAR(20)"),
    ("iban", "VARCHAR(40)"),
    ("komisyon_orani", "FLOAT DEFAULT 15.0"),
    ("notlar", "TEXT"),
    ("durum", "VARCHAR(20) DEFAULT 'aktif'"),
)
_PAZARYERI_URUN_KOLONLARI = (
    ("tedarikci_id", "INTEGER"),
    ("siparis_durumu", "VARCHAR(30) DEFAULT 'Odeme_Bekliyor'"),
    ("kargo_takip_no", "VARCHAR(100)"),
    ("kargo_firmasi", "VARCHAR(50) DEFAULT 'Yurtiçi Kargo'"),
)


def _pazaryeri_oturum_anahtari():
    if not has_request_context():
        return None
    return request.cookies.get("session") or ""


def _pazaryeri_katalog_oku(db, is_sqlite):
    """Salt okunur katalog. DDL ve kilit üretmez."""
    if is_sqlite:
        tablolar = {
            str(satir[0])
            for satir in db.session.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('tedarikci', 'urun')"
                )
            ).fetchall()
        }
        kolonlar = {}
        for tablo in tablolar:
            kolonlar[tablo] = {
                str(satir[1])
                for satir in db.session.execute(text(f"PRAGMA table_info({tablo})")).fetchall()
            }
        return kolonlar
    tablolar = {
        str(satir[0])
        for satir in db.session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() "
                "AND table_type = 'BASE TABLE' "
                "AND table_name IN ('tedarikci', 'urun')"
            )
        ).fetchall()
    }
    kolonlar = {tablo: set() for tablo in tablolar}
    for tablo, kolon in db.session.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name IN ('tedarikci', 'urun')"
        )
    ).fetchall():
        kolonlar.setdefault(str(tablo), set()).add(str(kolon))
    return kolonlar


def _pazaryeri_eksikler(kolonlar):
    tedarikci = kolonlar.get("tedarikci")
    eksik_tablo = tedarikci is None
    eksik_tedarikci = []
    if tedarikci is not None:
        eksik_tedarikci = [
            (ad, tip)
            for ad, tip in _PAZARYERI_TEDARIKCI_KOLONLARI
            if ad not in tedarikci
        ]
    urun = kolonlar.get("urun")
    eksik_urun = []
    if urun is not None:
        eksik_urun = [
            (ad, tip)
            for ad, tip in _PAZARYERI_URUN_KOLONLARI
            if ad not in urun
        ]
    return eksik_tablo, eksik_tedarikci, eksik_urun, urun is None


def _pazaryeri_ddl(db, is_sqlite, sql):
    if not is_sqlite:
        db.session.execute(text("SET LOCAL lock_timeout = '1500ms'"))
    db.session.execute(text(sql))
    db.session.commit()


def _pazaryeri_semayi_denetle(app, db):
    db_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    is_sqlite = "sqlite" in db_uri
    kolonlar = _pazaryeri_katalog_oku(db, is_sqlite)
    db.session.rollback()
    eksik_tablo, eksik_tedarikci, eksik_urun, urun_yok = _pazaryeri_eksikler(kolonlar)
    if not eksik_tablo and not eksik_tedarikci and not eksik_urun and not urun_yok:
        return True
    if eksik_tablo:
        kimlik = (
            "id INTEGER PRIMARY KEY AUTOINCREMENT"
            if is_sqlite
            else "id SERIAL PRIMARY KEY"
        )
        _pazaryeri_ddl(
            db,
            is_sqlite,
            f"""
            CREATE TABLE tedarikci (
                {kimlik},
                ad_soyad VARCHAR(100) NOT NULL,
                telefon VARCHAR(20) NOT NULL,
                iban VARCHAR(40),
                komisyon_orani FLOAT DEFAULT 15.0,
                notlar TEXT,
                durum VARCHAR(20) DEFAULT 'aktif'
            )
            """,
        )
    for ad, tip in eksik_tedarikci:
        _pazaryeri_ddl(db, is_sqlite, f"ALTER TABLE tedarikci ADD COLUMN {ad} {tip}")
    if not urun_yok:
        for ad, tip in eksik_urun:
            _pazaryeri_ddl(db, is_sqlite, f"ALTER TABLE urun ADD COLUMN {ad} {tip}")
    kolonlar = _pazaryeri_katalog_oku(db, is_sqlite)
    db.session.rollback()
    eksik_tablo, eksik_tedarikci, eksik_urun, urun_yok = _pazaryeri_eksikler(kolonlar)
    return not eksik_tablo and not eksik_tedarikci and not eksik_urun and not urun_yok


def pazaryeri_semasini_hazirla(app, db):
    """Eksik tablo veya sütun yoksa DDL çalıştırmaz. Import sırasında çağrılmaz."""
    global _sema_hazir
    if _sema_hazir:
        return
    oturum = _pazaryeri_oturum_anahtari()
    if oturum and oturum in _oturum_hazir:
        return
    if not _sema_kilit.acquire(timeout=2):
        return
    try:
        if _sema_hazir:
            return
        with app.app_context():
            try:
                tamam = _pazaryeri_semayi_denetle(app, db)
            except Exception as hata:
                db.session.rollback()
                print(f"[BOOT] pazaryeri semasi atlandi: {hata}", flush=True)
                if oturum:
                    _oturum_hazir.add(oturum)
                return
        if tamam:
            _sema_hazir = True
            print("[BOOT] pazaryeri semasi hazir", flush=True)
            if oturum:
                _oturum_hazir.add(oturum)
                if len(_oturum_hazir) > 4000:
                    _oturum_hazir.clear()
    except Exception as hata:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[BOOT] pazaryeri semasi atlandi: {hata}", flush=True)
    finally:
        _sema_kilit.release()


def register_routes(app, db, admin_required_decorator):
    """Blueprint rotaları. Veritabanına burada dokunulmaz."""

    @pazaryeri_bp.before_request
    def _pazaryeri_ilk_istek():
        yol = request.path or ""
        if "pazaryeri" not in yol and "tedarikci" not in yol:
            return
        pazaryeri_semasini_hazirla(app, db)

    # --- HTML ARAYÜZÜ ---
    PAZARYERI_HTML = """
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Haliç Antik - Tedarikçi & Kargo Yönetimi</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { background: #0f172a; color: #f8fafc; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 20px; }
            h2, h3 { color: #f59e0b; margin-top: 0; }
            .card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 20px; margin-bottom: 25px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); }
            table { width: 100%; border-collapse: collapse; margin-top: 15px; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #334155; font-size: 14px; }
            th { background: #0b1120; color: #94a3b8; }
            input, select, textarea { background: #0f172a; border: 1px solid #475569; color: #fff; padding: 8px 12px; border-radius: 6px; }
            button, .btn { background: #f59e0b; color: #000; border: none; padding: 8px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; text-decoration: none; display: inline-block; font-size: 13px; }
            .btn-kucuk { padding: 5px 8px; margin: 2px; }
            .btn-iptal { background: #475569; color: #fff; }
            .btn-sil { background: #dc2626; color: #fff; }
            button:hover, .btn:hover { background: #d97706; }
            .btn-wa { background: #25D366; color: #fff; margin-right: 5px; }
            .btn-wa:hover { background: #1ebe5d; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            @media(max-width: 900px){ .grid { grid-template-columns: 1fr; } }
            .btn-arsiv { background: #0f766e; color: #fff; }
            .btn-arsiv:hover { background: #115e59; }
            .arsiv-perde { display: none; position: fixed; inset: 0; background: rgba(2, 6, 23, 0.82); z-index: 50; align-items: center; justify-content: center; padding: 20px; }
            .arsiv-kutu { background: #1e293b; border: 1px solid #f59e0b; border-radius: 12px; width: min(980px, 100%); max-height: min(86vh, 820px); overflow: auto; padding: 18px; box-shadow: 0 16px 40px rgba(0,0,0,0.45); }
            .arsiv-ust { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
            .arsiv-kart { display: grid; grid-template-columns: 64px 1fr; gap: 12px; align-items: center; border-bottom: 1px solid #334155; padding: 10px 0; }
            .arsiv-kart img { width: 64px; height: 64px; object-fit: cover; border-radius: 8px; background: #0f172a; }
            .arsiv-bos { width: 64px; height: 64px; border-radius: 8px; background: #0f172a; color: #64748b; display: flex; align-items: center; justify-content: center; font-size: 11px; }
            .arsiv-meta { color: #94a3b8; font-size: 12px; margin-top: 4px; }
            .durum-aktif { color: #4ade80; font-weight: bold; }
            .durum-satildi { color: #fbbf24; font-weight: bold; }
        </style>
    </head>
    <body>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; gap: 10px; flex-wrap: wrap;">
            <h2>🏛️ Tedarikçi, Kargo ve Güvenli Satış Takibi</h2>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
                <button type="button" class="btn btn-arsiv" onclick="arsivAc('')">Kendi Bünyemiz Arşivi</button>
                <a href="/admin" class="btn" style="background: #475569; color: #fff;">⬅️ Ana Panele Dön</a>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h3>➕ Yeni Tedarikçi / Antikacı Ekle</h3>
                <form id="tedarikciForm" onsubmit="tedarikciKaydet(event)">
                    <div style="margin-bottom: 10px;">
                        <input type="text" id="t_ad" placeholder="Ad Soyad / Firma Adı" style="width: 95%;" required>
                    </div>
                    <div style="margin-bottom: 10px;">
                        <input type="text" id="t_tel" placeholder="Telefon (05xxxxxxxxx)" style="width: 95%;" required>
                    </div>
                    <div style="margin-bottom: 10px;">
                        <input type="text" id="t_iban" placeholder="IBAN Numarası" style="width: 95%;">
                    </div>
                    <div style="margin-bottom: 10px;">
                        <input type="number" step="any" id="t_komisyon" placeholder="Komisyon % (Örn: 15)" value="15" style="width: 95%;">
                    </div>
                    <button type="submit">Tedarikçiyi Kaydet</button>
                </form>
            </div>

            <div class="card">
                <h3>📜 Otomatik Şartname & Güvenlik Kuralı</h3>
                <p style="font-size: 13px; color: #94a3b8; line-height: 1.6;">
                    • <b>Alıcı Kuralı:</b> Kargo açılışında kesintisiz video çekmeyen alıcının hasar talebi onaylanmaz.<br>
                    • <b>Satıcı Kuralı:</b> Paketlenme aşaması videoya çekilmeli; mezatta satılan ürün başka yerde satılamaz.<br>
                    • <b>Havuz Sistemi:</b> Alıcı ürünü teslim alıp onaylayana kadar satıcıya para aktarılmaz.
                </p>
            </div>
        </div>

        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <h3 style="margin:0;">📦 Satılan Ürünler, Kargo ve Ödeme Statüleri</h3>
                <input type="text" id="siparisArama" placeholder="🔍 Lot, Ürün veya Alıcı Ara..." oninput="tabloFiltrele()" style="width:260px;">
            </div>
            <table id="siparisTablo">
                <thead>
                    <tr>
                        <th>Lot #</th>
                        <th>Ürün Adı</th>
                        <th>Fiyat</th>
                        <th>Alıcı / Kazanan</th>
                        <th>Tedarikçi</th>
                        <th>Durum</th>
                        <th>Kargo Takip</th>
                        <th>WhatsApp Hızlı İşlem</th>
                        <th>İşlem</th>
                    </tr>
                </thead>
                <tbody id="siparisTbody">
                    <tr><td colspan="9">Yükleniyor...</td></tr>
                </tbody>
            </table>
        </div>

        <div class="card">
            <h3>👥 Kayıtlı Tedarikçiler</h3>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Ad Soyad</th>
                        <th>Telefon</th>
                        <th>IBAN</th>
                        <th>Komisyon</th>
                        <th>WhatsApp İletişim</th>
                        <th>Arşiv</th>
                    </tr>
                </thead>
                <tbody id="tedarikciTbody">
                    <tr><td colspan="7">Yükleniyor...</td></tr>
                </tbody>
            </table>
        </div>

        <div id="arsivModal" class="arsiv-perde" onclick="if (event.target === this) arsivKapat()">
            <div class="arsiv-kutu">
                <div class="arsiv-ust">
                    <h3 id="arsivBaslik" style="margin:0;">Ürün Arşivi</h3>
                    <button type="button" class="btn" style="background:#475569; color:#fff;" onclick="arsivKapat()">Kapat</button>
                </div>
                <div id="arsivIcerik"></div>
            </div>
        </div>

        <script>
            let tumSiparisler = [];
            let tumUrunler = [];
            let tedarikciListesi = [];

            function formatliTelefon(tel) {
                if (!tel) return '';
                let t = String(tel).replace(/\\D/g, '');
                if (t.startsWith('0')) t = '90' + t.substring(1);
                else if (t.length === 10) t = '90' + t;
                return t;
            }

            function tabloFiltrele() {
                const q = (document.getElementById('siparisArama').value || '').toLowerCase().trim();
                const tbody = document.getElementById('siparisTbody');
                
                let filtrelenmis = tumSiparisler.filter(s => {
                    return String(s.lot).includes(q) || 
                           (s.urun_adi && s.urun_adi.toLowerCase().includes(q)) ||
                           (s.alici_adi && s.alici_adi.toLowerCase().includes(q));
                });

                if (filtrelenmis.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:#94a3b8;">Aramaya uygun sipariş bulunamadı.</td></tr>';
                    return;
                }

                tbody.innerHTML = filtrelenmis.map(s => {
                    let aliciMesaj = encodeURIComponent(`Sayın ${s.alici_adi}, Haliç Antik'ten kazandığınız LOT #${s.lot} (${s.urun_adi}) için ödeme beklenmektedir. Tutar: ${s.fiyat} TL.`);
                    let saticiMesaj = encodeURIComponent(`Tebrikler ${s.tedarikci_adi || 'Satıcımız'}, LOT #${s.lot} (${s.urun_adi}) ${s.fiyat} TL'ye satılmıştır. Alıcı ödemesi güvence altındadır. Lütfen paketleme videosunu çekerek kargoya veriniz.`);
                    let saticiTel = formatliTelefon(s.tedarikci_tel);

                    return `<tr>
                        <td><b>#${s.lot}</b></td>
                        <td>${s.urun_adi}</td>
                        <td><b>${Number(s.fiyat).toLocaleString('tr-TR')} TL</b></td>
                        <td>${s.alici_adi}</td>
                        <td>${s.tedarikci_adi || '<span style="color:#ef4444;">Atanmamış</span>'}</td>
                        <td>
                            <select onchange="durumDegistir(${s.id}, this.value)">
                                <option value="Odeme_Bekliyor" ${s.durum === 'Odeme_Bekliyor' ? 'selected' : ''}>Ödeme Bekliyor</option>
                                <option value="Kargoda" ${s.durum === 'Kargoda' ? 'selected' : ''}>Kargoya Verildi</option>
                                <option value="Teslim_Edildi" ${s.durum === 'Teslim_Edildi' ? 'selected' : ''}>Teslim Edildi</option>
                                <option value="Tamamlandi" ${s.durum === 'Tamamlandi' ? 'selected' : ''}>Tedarikçiye Ödendi (Bitti)</option>
                            </select>
                        </td>
                        <td>
                            <input type="text" value="${s.kargo_takip || ''}" placeholder="Takip Kodu" style="width: 110px;" onblur="kargoGuncelle(${s.id}, this.value)">
                        </td>
                        <td>
                            <a href="https://wa.me/?text=${aliciMesaj}" target="_blank" class="btn btn-wa">📲 Alıcıya Bildir</a>
                            ${saticiTel ? `<a href="https://wa.me/${saticiTel}?text=${saticiMesaj}" target="_blank" class="btn btn-wa" style="background:#0284c7;">📦 Satıcıya Bildir</a>` : ''}
                        </td>
                        <td>
                            <button type="button" class="btn btn-kucuk" onclick="siparisDuzenle(${s.id})">Düzenle</button>
                            <button type="button" class="btn btn-kucuk btn-iptal" onclick="siparisIptal(${s.id})">İptal Et</button>
                            <button type="button" class="btn btn-kucuk btn-sil" onclick="siparisSil(${s.id})">Sil</button>
                        </td>
                    </tr>`;
                }).join('');
            }

            async function verileriGetir() {
                try {
                    const res = await fetch('/admin/pazaryeri/api/veriler');
                    const data = await res.json();
                    
                    tumSiparisler = data.siparisler || [];
                    tumUrunler = data.tum_urunler || [];
                    tedarikciListesi = data.tedarikciler || [];
                    tabloFiltrele();

                    let tHtml = '';
                    (data.tedarikciler || []).forEach(t => {
                        let kuralMesaji = encodeURIComponent(`Sayın ${t.ad_soyad}, Haliç Antik Mezat tedarikçi şartnamesi gereğince sistemimize yüklediğiniz ürünler satış tamamlanana kadar başka yerde satılamaz. Ürün paketleme videosunu arşivlemeniz gerekmektedir.`);
                        let tTel = formatliTelefon(t.telefon);

                        tHtml += `<tr>
                            <td>${t.id}</td>
                            <td><b>${t.ad_soyad}</b></td>
                            <td>${t.telefon}</td>
                            <td>${t.iban || '-'}</td>
                            <td>%${t.komisyon}</td>
                            <td>
                                <a href="https://wa.me/${tTel}?text=${kuralMesaji}" target="_blank" class="btn btn-wa">📜 Şartnameyi Gönder</a>
                            </td>
                            <td>
                                <button type="button" class="btn btn-kucuk btn-arsiv" onclick="arsivAc('${t.id}')">Ürün Arşivi</button>
                            </td>
                        </tr>`;
                    });
                    document.getElementById('tedarikciTbody').innerHTML = tHtml || '<tr><td colspan="7">Kayıtlı tedarikçi yok.</td></tr>';
                } catch(e) {}
            }

            async function tedarikciKaydet(e) {
                e.preventDefault();
                const payload = {
                    ad_soyad: document.getElementById('t_ad').value,
                    telefon: document.getElementById('t_tel').value,
                    iban: document.getElementById('t_iban').value,
                    komisyon: document.getElementById('t_komisyon').value
                };
                await fetch('/admin/pazaryeri/api/tedarikci-ekle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                document.getElementById('tedarikciForm').reset();
                verileriGetir();
            }

            async function durumDegistir(urunId, durum) {
                await fetch('/admin/pazaryeri/api/durum-guncelle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ urun_id: urunId, durum: durum })
                });
                verileriGetir();
            }

            async function kargoGuncelle(urunId, takipNo) {
                if(!takipNo) return;
                await fetch('/admin/pazaryeri/api/kargo-guncelle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ urun_id: urunId, kargo_takip_no: takipNo })
                });
            }

            async function siparisDuzenle(urunId) {
                const s = tumSiparisler.find(x => x.id === urunId);
                if (!s) return;
                const kargo = prompt('Kargo takip kodu', s.kargo_takip || '');
                if (kargo === null) return;
                const durum = prompt('Durum (Odeme_Bekliyor, Kargoda, Teslim_Edildi, Tamamlandi, Iptal)', s.durum || 'Odeme_Bekliyor');
                if (durum === null) return;
                await fetch('/admin/pazaryeri/api/siparis-duzenle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ urun_id: urunId, kargo_takip_no: kargo, durum: durum })
                });
                verileriGetir();
            }

            async function siparisIptal(urunId) {
                if (!confirm('Bu sipariş iptal edilsin mi? Ürün satış havuzuna döner.')) return;
                await fetch('/admin/pazaryeri/api/siparis-iptal', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ urun_id: urunId })
                });
                verileriGetir();
            }

            async function siparisSil(urunId) {
                if (!confirm('Bu sipariş çöp kutusuna taşınsın mı?')) return;
                await fetch('/admin/pazaryeri/api/siparis-sil', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ urun_id: urunId })
                });
                verileriGetir();
            }

            function htmlKacis(metin) {
                return String(metin == null ? '' : metin)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;');
            }

            function arsivKapat() {
                const perde = document.getElementById('arsivModal');
                if (perde) perde.style.display = 'none';
            }

            function arsivAc(tedarikciId) {
                const hedef = String(tedarikciId == null ? '' : tedarikciId);
                const kendi = hedef === '';
                const liste = (tumUrunler || []).filter(u => {
                    const tid = u && u.tedarikci_id != null && String(u.tedarikci_id) !== '' ? String(u.tedarikci_id) : '';
                    return kendi ? tid === '' : tid === hedef;
                });
                let baslik = 'Kendi Bünyemiz Arşivi';
                if (!kendi) {
                    const kayit = (tedarikciListesi || []).find(t => String(t.id) === hedef);
                    baslik = ((kayit && kayit.ad_soyad) || 'Tedarikçi') + ' — Ürün Arşivi';
                }
                const baslikKutu = document.getElementById('arsivBaslik');
                const icerik = document.getElementById('arsivIcerik');
                if (baslikKutu) baslikKutu.textContent = baslik;
                if (!icerik) return;
                if (!liste.length) {
                    icerik.innerHTML = '<p style="color:#94a3b8;">Bu hesaba bağlı ürün yok.</p>';
                } else {
                    icerik.innerHTML = liste.map(u => {
                        const gorsel = String((u && u.gorsel) || '');
                        const guvenli = (gorsel.indexOf('https://') === 0 || gorsel.indexOf('http://') === 0) ? gorsel : '';
                        const onizleme = guvenli
                            ? '<img src="' + htmlKacis(guvenli) + '" alt="">'
                            : '<div class="arsiv-bos">Görsel yok</div>';
                        const durum = String((u && u.durum) || '');
                        const durumSinif = durum === 'Satıldı' || durum.toLowerCase() === 'satildi' ? 'durum-satildi' : (durum === 'Aktif' ? 'durum-aktif' : '');
                        const fiyat = Number((u && u.baslangic_fiyati) || 0);
                        return '<div class="arsiv-kart">' + onizleme +
                            '<div><b>Lot #' + htmlKacis(u.lot_no) + '</b> — ' + htmlKacis(u.urun_adi) +
                            '<div class="arsiv-meta">Dükkan: ' + htmlKacis(u.dukkan_kodu || '-') +
                            ' · Fiyat: ' + fiyat.toLocaleString('tr-TR') + ' TL · Durum: <span class="' + durumSinif + '">' + htmlKacis(durum || '-') + '</span></div></div></div>';
                    }).join('');
                }
                const perde = document.getElementById('arsivModal');
                if (perde) perde.style.display = 'flex';
            }

            verileriGetir();
        </script>
    </body>
    </html>
    """

    # --- ROTALAR ---
    @pazaryeri_bp.route("/")
    @admin_required_decorator
    def pazaryeri_paneli():
        return render_template_string(PAZARYERI_HTML)

    @pazaryeri_bp.route("/api/veriler", methods=["GET"])
    @admin_required_decorator
    def pazaryeri_verileri():
        t_rows = (
            db.session.execute(
                text(
                    "SELECT id, ad_soyad, telefon, iban, komisyon_orani FROM"
                    " tedarikci ORDER BY id DESC"
                )
            )
            .mappings()
            .all()
        )
        tedarikciler = [
            {
                "id": r["id"],
                "ad_soyad": r["ad_soyad"],
                "telefon": r["telefon"],
                "iban": r["iban"],
                "komisyon": r["komisyon_orani"],
            }
            for r in t_rows
        ]
        t_dict = {t["id"]: t for t in tedarikciler}

        # PostgreSQL ve SQLite ile tam uyumlu filtreleme (silindi_mi = 0 kaldırıldı)
        u_rows = (
            db.session.execute(
                text("""
                SELECT id, lot_no, urun_adi, guncel_fiyat, acilis_fiyati, kazanan_adi, 
                       tedarikci_id, siparis_durumu, kargo_takip_no
                FROM urun 
                WHERE (durum = 'Satıldı' OR durum = 'satildi') 
                  AND (silindi_mi = FALSE OR silindi_mi IS NULL)
                ORDER BY id DESC LIMIT 100
            """)
            )
            .mappings()
            .all()
        )

        siparisler = []
        for u in u_rows:
            ted = t_dict.get(u.get("tedarikci_id"))
            fiyat = u["guncel_fiyat"] if u["guncel_fiyat"] else u["acilis_fiyati"]
            siparisler.append({
                "id": u["id"],
                "lot": u["lot_no"],
                "urun_adi": u["urun_adi"],
                "fiyat": fiyat,
                "alici_adi": u["kazanan_adi"] or "Bilinmiyor",
                "tedarikci_adi": ted["ad_soyad"] if ted else None,
                "tedarikci_tel": ted["telefon"] if ted else None,
                "durum": u.get("siparis_durumu") or "Odeme_Bekliyor",
                "kargo_takip": u.get("kargo_takip_no") or "",
            })

        tum_urunler = []
        try:
            arsiv_rows = (
                db.session.execute(
                    text("""
                    SELECT id, lot_no, urun_adi, dukkan_kodu, acilis_fiyati, durum,
                           tedarikci_id, fotograflar
                    FROM urun
                    WHERE (silindi_mi = FALSE OR silindi_mi IS NULL)
                    ORDER BY id DESC
                    """)
                )
                .mappings()
                .all()
            )
            for u in arsiv_rows:
                ham_tid = u.get("tedarikci_id")
                try:
                    tid = (
                        int(ham_tid)
                        if ham_tid is not None and str(ham_tid).strip() != ""
                        else None
                    )
                except (TypeError, ValueError):
                    tid = None
                if tid is not None and tid <= 0:
                    tid = None
                try:
                    baslangic = float(u.get("acilis_fiyati") or 0)
                except (TypeError, ValueError):
                    baslangic = 0.0
                tum_urunler.append(
                    {
                        "id": u["id"],
                        "tedarikci_id": tid,
                        "lot_no": u.get("lot_no"),
                        "urun_adi": u.get("urun_adi") or "",
                        "dukkan_kodu": u.get("dukkan_kodu") or "",
                        "baslangic_fiyati": baslangic,
                        "durum": u.get("durum") or "",
                        "gorsel": _pazaryeri_ilk_gorsel(u.get("fotograflar")),
                    }
                )
        except Exception:
            tum_urunler = []

        return jsonify({
            "tedarikciler": tedarikciler,
            "siparisler": siparisler,
            "tum_urunler": tum_urunler,
        })

    @pazaryeri_bp.route("/api/tedarikci-ekle", methods=["POST"])
    @admin_required_decorator
    def api_tedarikci_ekle():
        v = request.json or {}
        db.session.execute(
            text("""
                INSERT INTO tedarikci (ad_soyad, telefon, iban, komisyon_orani)
                VALUES (:ad, :tel, :iban, :komisyon)
            """),
            {
                "ad": str(v.get("ad_soyad", "")).strip(),
                "tel": str(v.get("telefon", "")).strip(),
                "iban": str(v.get("iban", "")).strip(),
                "komisyon": float(v.get("komisyon") or 15.0),
            },
        )
        db.session.commit()
        return jsonify({"success": True})

    @pazaryeri_bp.route("/api/durum-guncelle", methods=["POST"])
    @admin_required_decorator
    def api_durum_guncelle():
        v = request.json or {}
        db.session.execute(
            text("UPDATE urun SET siparis_durumu = :durum WHERE id = :urun_id"),
            {"durum": v.get("durum"), "urun_id": v.get("urun_id")},
        )
        db.session.commit()
        return jsonify({"success": True})

    @pazaryeri_bp.route("/api/kargo-guncelle", methods=["POST"])
    @admin_required_decorator
    def api_kargo_guncelle():
        v = request.json or {}
        db.session.execute(
            text("UPDATE urun SET kargo_takip_no = :takip WHERE id = :urun_id"),
            {"takip": v.get("kargo_takip_no"), "urun_id": v.get("urun_id")},
        )
        db.session.commit()
        return jsonify({"success": True})

    @pazaryeri_bp.route("/api/siparis-duzenle", methods=["POST"])
    @admin_required_decorator
    def api_siparis_duzenle():
        v = request.json or {}
        db.session.execute(
            text(
                "UPDATE urun SET siparis_durumu = :durum, kargo_takip_no = :takip "
                "WHERE id = :urun_id"
            ),
            {
                "durum": v.get("durum") or "Odeme_Bekliyor",
                "takip": v.get("kargo_takip_no") or "",
                "urun_id": v.get("urun_id"),
            },
        )
        db.session.commit()
        return jsonify({"success": True})

    @pazaryeri_bp.route("/api/siparis-iptal", methods=["POST"])
    @admin_required_decorator
    def api_siparis_iptal():
        v = request.json or {}
        db.session.execute(
            text(
                "UPDATE urun SET siparis_durumu = 'Iptal', durum = 'Aktif', "
                "kazanan_adi = 'Yok', kazanan_id = NULL WHERE id = :urun_id"
            ),
            {"urun_id": v.get("urun_id")},
        )
        db.session.commit()
        return jsonify({"success": True})

    @pazaryeri_bp.route("/api/siparis-sil", methods=["POST"])
    @admin_required_decorator
    def api_siparis_sil():
        v = request.json or {}
        db.session.execute(
            text(
                "UPDATE urun SET silindi_mi = 1, silinme_tarihi = :simdi "
                "WHERE id = :urun_id"
            ),
            {"urun_id": v.get("urun_id"), "simdi": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")},
        )
        db.session.commit()
        return jsonify({"success": True})


def init_pazaryeri(app, db, admin_required_decorator):
    """Rotaları kaydeder. Şema kontrolü import sırasında çalışmaz."""
    register_routes(app, db, admin_required_decorator)