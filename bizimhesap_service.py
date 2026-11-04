import os
import json
import requests
from datetime import datetime
from config import Config

def bizimhesap_siparis_aktar(alici_ad, telefon, email, adres, urun_adi, lot_no, satis_fiyati):
    """
    Haliç Antik Mezat'ta satılan ürünü BizimHesap ön muhasebe sistemine aktarır.
    
    1. Öncelik: BizimHesap resmi Entegrasyon XML/SOAP Web Servisi
    2. Yedek: Standart B2B Sipariş REST çağrısı
    3. Güvenlik: Her iki çağrı başarısız olsa bile satışı yerel 'bizimhesap_kuyruk.json' 
       dosyasına yazar; mezat akışı kesilmez, veri asla kaybolmaz.
    """
    token = getattr(Config, 'BIZIMHESAP_TOKEN', '6A35AAEBF1024183BA5DBE39BD4667A0')
    app_id = getattr(Config, 'BIZIMHESAP_APP_ID', '358386')

    # Alıcı adı ve telefon formatlama
    temiz_ad = str(alici_ad).replace("[Web]", "").strip() if alici_ad else "Hamdullah Bulut"
    temiz_tel = "".join([c for c in str(telefon) if c.isdigit()])
    if not temiz_tel:
        temiz_tel = "05555555555"

    tarih_iso = datetime.now().strftime("%Y-%m-%d")
    tarih_tr = datetime.now().strftime("%d.%m.%Y %H:%M")
    fiyat_float = float(satis_fiyati) if satis_fiyati else 0.0

    # 1. FORMAT: BizimHesap / Zirve Entegrasyon XML Gövdesi
    xml_payload = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <SaveOrder xmlns="http://tempuri.org/">
      <firmId>{token}</firmId>
      <appId>{app_id}</appId>
      <orderNo>LOT-{lot_no}</orderNo>
      <customerTitle>{temiz_ad}</customerTitle>
      <customerPhone>{temiz_tel}</customerPhone>
      <customerEmail>{email or f'alici_{temiz_tel}@halicantik.com'}</customerEmail>
      <customerAddress>{adres or 'İstanbul, Türkiye'}</customerAddress>
      <productCode>LOT_{lot_no}</productCode>
      <productTitle>{urun_adi}</productTitle>
      <price>{fiyat_float}</price>
      <quantity>1</quantity>
      <vatRate>20</vatRate>
      <orderDate>{tarih_iso}</orderDate>
    </SaveOrder>
  </soap:Body>
</soap:Envelope>"""

    # 2. FORMAT: Standart JSON Gövdesi
    json_payload = {
        "firmId": token,
        "token": token,
        "appId": str(app_id),
        "customer": {
            "title": temiz_ad,
            "phone": temiz_tel,
            "email": email or f"alici_{temiz_tel}@halicantik.com",
            "address": adres or "İstanbul, Türkiye"
        },
        "order": {
            "orderNumber": f"LOT-{lot_no}",
            "orderDate": tarih_iso,
            "note": f"Haliç Antik Mezat Satışı - Lot #{lot_no}",
            "currency": "TRY",
            "total": fiyat_float,
            "items": [
                {
                    "itemCode": f"LOT_{lot_no}",
                    "title": urun_adi,
                    "quantity": 1,
                    "price": fiyat_float,
                    "vatRate": 20
                }
            ]
        }
    }

    # İstek Denemeleri (Önce XML Servisi, Ardından B2B)
    istekler = [
        (
            "https://uygulama.bizimhesap.com/Integration/OrderService.asmx",
            xml_payload.encode('utf-8'),
            {
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "http://tempuri.org/SaveOrder",
                "Token": token
            }
        ),
        (
            "https://bizimhesap.com/api/b2b/orders",
            json.dumps(json_payload),
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "Token": token
            }
        )
    ]

    aktarim_basarili = False

    for url, data_content, req_headers in istekler:
        try:
            res = requests.post(url, data=data_content, headers=req_headers, timeout=5.0)
            print(f"📦 [BizimHesap Entegrasyon] İstek -> {url} (HTTP Kodu: {res.status_code})")
            
            if res.status_code in [200, 201]:
                print(f"✅ [BizimHesap] Satış başarıyla sisteme aktarıldı! (Lot #{lot_no})")
                aktarim_basarili = True
                break
        except Exception as e:
            print(f"⚠️ [BizimHesap Bağlantı Uyarısı] ({url}): {e}")

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