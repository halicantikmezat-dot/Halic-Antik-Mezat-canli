import urllib.parse
from flask import Blueprint, jsonify, render_template_string, request
from sqlalchemy import text

pazaryeri_bp = Blueprint("pazaryeri_bp", __name__)


def init_pazaryeri(app, db, admin_required_decorator):
  """Pazaryeri modülünü ana sisteme bağlar ve tabloları hem SQLite hem PostgreSQL ile tam uyumlu günceller."""

  with app.app_context():
    # 1. Veritabanı motoru SQLite mı kontrol et
    db_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    is_sqlite = "sqlite" in db_uri

    id_column_def = (
        "id INTEGER PRIMARY KEY AUTOINCREMENT"
        if is_sqlite
        else "id SERIAL PRIMARY KEY"
    )

    try:
      db.session.execute(
          text(f"""
                CREATE TABLE IF NOT EXISTS tedarikci (
                    {id_column_def},
                    ad_soyad VARCHAR(100) NOT NULL,
                    telefon VARCHAR(20) NOT NULL,
                    iban VARCHAR(40),
                    komisyon_orani FLOAT DEFAULT 15.0,
                    notlar TEXT,
                    durum VARCHAR(20) DEFAULT 'aktif'
                );
            """)
      )
      db.session.commit()
    except Exception:
      db.session.rollback()

    # Kolonları ekle (Zaten varsa pas geçer)
    yeni_kolonlar = [
        ("urun", "tedarikci_id", "INTEGER"),
        ("urun", "siparis_durumu", "VARCHAR(30) DEFAULT 'Odeme_Bekliyor'"),
        ("urun", "kargo_takip_no", "VARCHAR(100)"),
        ("urun", "kargo_firmasi", "VARCHAR(50) DEFAULT 'Yurtiçi Kargo'"),
    ]
    for tbl, col, col_type in yeni_kolonlar:
      try:
        db.session.execute(
            text(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_type};")
        )
        db.session.commit()
      except Exception:
        db.session.rollback()

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
            button:hover, .btn:hover { background: #d97706; }
            .btn-wa { background: #25D366; color: #fff; margin-right: 5px; }
            .btn-wa:hover { background: #1ebe5d; }
            .badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }
            .badge-bekliyor { background: #b45309; color: #fff; }
            .badge-kargoda { background: #2563eb; color: #fff; }
            .badge-tamam { background: #15803d; color: #fff; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            @media(max-width: 900px){ .grid { grid-template-columns: 1fr; } }
        </style>
    </head>
    <body>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <h2>🏛️ Tedarikçi, Kargo ve Güvenli Satış Takibi</h2>
            <a href="/admin" class="btn" style="background: #475569; color: #fff;">⬅️ Ana Panele Dön</a>
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
                        <input type="number" id="t_komisyon" placeholder="Komisyon % (Örn: 15)" value="15" style="width: 95%;">
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
            <h3>📦 Satılan Ürünler, Kargo ve Ödeme Statüleri</h3>
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
                    </tr>
                </thead>
                <tbody id="siparisTbody">
                    <tr><td colspan="8">Yükleniyor...</td></tr>
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
                    </tr>
                </thead>
                <tbody id="tedarikciTbody">
                    <tr><td colspan="6">Yükleniyor...</td></tr>
                </tbody>
            </table>
        </div>

        <script>
            async function verileriGetir() {
                const res = await fetch('/admin/pazaryeri/api/veriler');
                const data = await res.json();
                
                let sHtml = '';
                data.siparisler.forEach(s => {
                    let aliciMesaj = encodeURIComponent(`Sayın ${s.alici_adi}, Haliç Antik'ten kazandığınız LOT #${s.lot} (${s.urun_adi}) için ödeme beklenmektedir. Tutar: ${s.fiyat} TL.`);
                    let saticiMesaj = encodeURIComponent(`Tebrikler ${s.tedarikci_adi || 'Satıcımız'}, LOT #${s.lot} (${s.urun_adi}) ${s.fiyat} TL'ye satılmıştır. Alıcı ödemesi güvence altındadır. Lütfen paketleme videosunu çekerek kargoya veriniz.`);

                    sHtml += `<tr>
                        <td><b>#${s.lot}</b></td>
                        <td>${s.urun_adi}</td>
                        <td><b>${s.fiyat} TL</b></td>
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
                            <input type="text" value="${s.kargo_takip || ''}" placeholder="Takip Kodu" style="width: 100px;" onblur="kargoGuncelle(${s.id}, this.value)">
                        </td>
                        <td>
                            <a href="https://wa.me/?text=${aliciMesaj}" target="_blank" class="btn btn-wa">📲 Alıcıya Bildir</a>
                            ${s.tedarikci_tel ? `<a href="https://wa.me/${s.tedarikci_tel}?text=${saticiMesaj}" target="_blank" class="btn btn-wa" style="background:#0284c7;">📦 Satıcıya Bildir</a>` : ''}
                        </td>
                    </tr>`;
                });
                document.getElementById('siparisTbody').innerHTML = sHtml || '<tr><td colspan="8">Henüz satılmış ürün bulunmuyor.</td></tr>';

                let tHtml = '';
                data.tedarikciler.forEach(t => {
                    let kuralMesaji = encodeURIComponent(`Sayın ${t.ad_soyad}, Haliç Antik Mezat tedarikçi şartnamesi gereğince sistemimize yüklediğiniz ürünler satış tamamlanana kadar başka yerde satılamaz. Ürün paketleme videosunu arşivlemeniz gerekmektedir.`);
                    tHtml += `<tr>
                        <td>${t.id}</td>
                        <td><b>${t.ad_soyad}</b></td>
                        <td>${t.telefon}</td>
                        <td>${t.iban || '-'}</td>
                        <td>%${t.komisyon}</td>
                        <td>
                            <a href="https://wa.me/${t.telefon}?text=${kuralMesaji}" target="_blank" class="btn btn-wa">📜 Şartnameyi Gönder</a>
                        </td>
                    </tr>`;
                });
                document.getElementById('tedarikciTbody').innerHTML = tHtml || '<tr><td colspan="6">Kayıtlı tedarikçi yok.</td></tr>';
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

    # PostgreSQL & SQLite uyumlu silindi_mi kontrolü (IS NOT TRUE)
    u_rows = (
        db.session.execute(
            text("""
            SELECT id, lot_no, urun_adi, guncel_fiyat, acilis_fiyati, kazanan_adi, 
                   tedarikci_id, siparis_durumu, kargo_takip_no
            FROM urun 
            WHERE (durum = 'Satıldı' OR durum = 'satildi') 
              AND (silindi_mi IS FALSE OR silindi_mi IS NULL)
            ORDER BY id DESC LIMIT 50
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

    return jsonify({"tedarikciler": tedarikciler, "siparisler": siparisler})

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
            "ad": v.get("ad_soyad"),
            "tel": v.get("telefon"),
            "iban": v.get("iban"),
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