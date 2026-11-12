import os
import json
import requests
from datetime import datetime
from config import Config

def bizimhesap_siparis_aktar(alici_ad, telefon, email, adres, urun_adi, lot_no, satis_fiyati):
    """
    Haliç Antik Mezat'ta satılan ürünü BizimHesap ön muhasebe sistemine aktarır.
    
    Resmi metod: POST https://bizimhesap.com/api/b2b/addinvoice
    Çağrı başarısız olsa bile satış 'bizimhesap_kuyruk.json' dosyasına yazılır.
    """
    token = getattr(Config, "BIZIMHESAP_TOKEN", "") or ""

    # Alıcı adı ve telefon formatlama
    temiz_ad = str(alici_ad).replace("[Web]", "").strip() if alici_ad else "Hamdullah Bulut"
    temiz_tel = "".join([c for c in str(telefon) if c.isdigit()])
    if not temiz_tel:
        temiz_tel = "05555555555"

    tarih_tr = datetime.now().strftime("%d.%m.%Y %H:%M")
    fiyat_float = float(satis_fiyati) if satis_fiyati else 0.0

    # Resmi sipariş/fatura metodu: https://apidocs.bizimhesap.com/addinvoice
    taban = os.environ.get("BIZIMHESAP_API_URL") or getattr(
        Config, "BIZIMHESAP_API_URL", "https://bizimhesap.com/api/b2b"
    )
    taban = str(taban).rstrip("/")
    if taban.endswith("/addinvoice"):
        url = taban
    else:
        url = taban + "/addinvoice"

    firm_id = os.environ.get("BIZIMHESAP_FIRM_ID") or token
    birim = round(fiyat_float, 2)
    vergi = round(birim * 0.20, 2)
    net = birim
    dip_toplam = round(net + vergi, 2)
    simdi_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    json_payload = {
        "firmId": firm_id,
        "invoiceNo": f"LOT-{lot_no}",
        "invoiceType": 3,
        "note": f"Haliç Antik Mezat satışı - Lot #{lot_no}",
        "dates": {
            "invoiceDate": simdi_iso,
            "dueDate": simdi_iso,
            "deliveryDate": simdi_iso,
        },
        "customer": {
            "customerId": temiz_tel,
            "title": temiz_ad,
            "address": adres or "İstanbul, Türkiye",
            "email": email or f"alici_{temiz_tel}@halicantik.com",
            "phone": temiz_tel,
        },
        "amounts": {
            "currency": "TL",
            "gross": f"{birim:.2f}",
            "discount": "0.00",
            "net": f"{net:.2f}",
            "tax": f"{vergi:.2f}",
            "total": f"{dip_toplam:.2f}",
        },
        "details": [
            {
                "productId": f"LOT-{lot_no}",
                "productName": urun_adi,
                "note": f"Lot #{lot_no}",
                "taxRate": "20.00",
                "quantity": 1,
                "unitPrice": f"{birim:.2f}",
                "grossPrice": f"{birim:.2f}",
                "discount": "0.00",
                "net": f"{net:.2f}",
                "tax": f"{vergi:.2f}",
                "total": f"{dip_toplam:.2f}",
            }
        ],
    }

    aktarim_basarili = False
    try:
        res = requests.post(
            url,
            data=json.dumps(json_payload),
            headers={"Content-Type": "application/json", "token": token},
            timeout=8.0,
        )
        govde = ""
        try:
            govde = res.json()
        except Exception:
            govde = (res.text or "")[:180]
        hata_metni = govde.get("error") if isinstance(govde, dict) else ""
        if res.status_code in (200, 201) and not hata_metni:
            print(f"✅ [BizimHesap] Satış aktarıldı (Lot #{lot_no})")
            aktarim_basarili = True
        else:
            print(f"⚠️ [BizimHesap] Lot #{lot_no} aktarılamadı. HTTP {res.status_code}. Sipariş kuyruğa yazıldı.")
    except Exception as e:
        print(f"⚠️ [BizimHesap] Bağlantı kurulamadı, sipariş kuyruğa yazıldı: {e}")

    # 3. YEDEKLEME: İletişim başarısız olsa bile yerel sipariş kuyruğuna kaydet
    kayit_satiri = {
        "tarih": tarih_tr,
        "lot_no": lot_no,
        "urun_adi": urun_adi,
        "alici": temiz_ad,
        "telefon": temiz_tel,
        "tutar": fiyat_float,
        "bizimhesap_durum": "Aktarıldı" if aktarim_basarili else "Kuyrukta (Bekliyor)"
    }

    try:
        kuyruk_dosyasi = "bizimhesap_kuyruk.json"
        kuyruk = []
        if os.path.exists(kuyruk_dosyasi):
            with open(kuyruk_dosyasi, "r", encoding="utf-8") as f:
                kuyruk = json.load(f)
        
        kuyruk.append(kayit_satiri)
        with open(kuyruk_dosyasi, "w", encoding="utf-8") as f:
            json.dump(kuyruk, f, ensure_ascii=False, indent=2)
    except Exception as io_err:
        print(f"⚠️ [Kuyruk Kayıt Hatası]: {io_err}")

    return aktarim_basarili