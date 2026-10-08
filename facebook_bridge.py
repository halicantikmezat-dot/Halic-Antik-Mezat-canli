import os
import sys
import time
import requests
from collections import deque

# ==========================================================
# ASLA BİTMEYEN SAYFA BELİRTECİ (Haliç Antik Mezat - Page Token)
# ==========================================================
PAGE_ACCESS_TOKEN = "EAATYgv3I3WMBSXHfTfHtFvCBUvjxFw1L1I6aXRaCtBSY2iOt3xKVdngSGfxljmv25t2V63bQhWCfZCZATvQb0UpBWSy2JYimpQ4VESsJjv93KN3fxdS9GdQhlc3XwcI4hPdlSMvp0RInE0cfvT0tnbJ4jfw4jTNGcmzHsZABUphJ9wuFZB59rQYK1OB5h4jsdr6BHqTIEnPYZC4qrn21L"

DEFAULT_VIDEO_ID = ""

okunan_yorumlar = deque(maxlen=2500)
okunan_yorumlar_set = set()

def get_active_live_video_id(token):
    """Sayfadaki o an aktif olan canlı videonun ID'sini otomatik çeker."""
    url = "https://graph.facebook.com/v20.0/me/live_videos"
    params = {
        "access_token": token,
        "fields": "id,status,creation_time"
    }
    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", [])
            for video in data:
                if video.get("status") == "LIVE":
                    return video.get("id")
            if data:
                return data[0].get("id")
    except Exception as e:
        print(f"⚠️ [Facebook] Video ID sorgulama hatası: {e}")
    return None

def start_listening(callback_func, video_id=None, token=None):
    aktif_token = token or PAGE_ACCESS_TOKEN

    if not aktif_token:
        print("\n❌ [Facebook] HATA: ACCESS TOKEN tanımlanmadı!\n")
        return

    target_video_id = (video_id or DEFAULT_VIDEO_ID).strip() if (video_id or DEFAULT_VIDEO_ID) else None

    if not target_video_id:
        print("🔍 [Facebook] Canlı yayın tespiti yapılıyor...")
        while not target_video_id:
            target_video_id = get_active_live_video_id(aktif_token)
            if not target_video_id:
                print("⏳ [Facebook] Aktif canlı yayın bulunamadı, bekleniyor (8 sn)...")
                time.sleep(8)

    print(f"📡 [Facebook] MEZAT CANLI AKIŞI BAŞLADI (ID: {target_video_id})")

    url = f"https://graph.facebook.com/v20.0/{target_video_id}/comments"
    params = {
        "access_token": aktif_token,
        "fields": "id,from,message,created_time",
        "filter": "toplevel",
        "order": "reverse_chronological",
        "limit": 50
    }

    ardisik_hata = 0

    while True:
        try:
            response = requests.get(url, params=params, timeout=5)

            if response.status_code == 200:
                ardisik_hata = 0
                data = response.json()
                comments = data.get("data", [])

                for c in reversed(comments):
                    c_id = c.get("id")
                    if c_id and c_id not in okunan_yorumlar_set:
                        if len(okunan_yorumlar) >= 2500:
                            eski_id = okunan_yorumlar.popleft()
                            okunan_yorumlar_set.discard(eski_id)

                        okunan_yorumlar.append(c_id)
                        okunan_yorumlar_set.add(c_id)

                        user_info = c.get("from") or {}
                        user_name = user_info.get("name") or "Facebook İzleyicisi"
                        message = (c.get("message") or "").strip()

                        if message and callback_func:
                            callback_func("Facebook", user_name, message)

                # Meta kotasını şişirmeyen güvenli sorgu aralığı
                time.sleep(2.0)

            else:
                hata_json = response.json().get("error", {})
                hata_kodu = hata_json.get("code")
                hata_mesaji = hata_json.get("message", response.text)

                # 1. Kota / İstek Limiti Aşımı Koruması (Hata 403 veya Code 4 / 17)
                if response.status_code == 403 or hata_kodu in [4, 17, 32]:
                    print(f"\n⚠️ [Facebook Rate Limit]: İstek kotası doldu. Hesap 35 sn dinlendiriliyor...")
                    time.sleep(35)
                    continue

                # 2. Token Süresi Dolan Hatası
                if hata_kodu == 190:
                    print(f"\n🛑 [Facebook Yetki Hatası (190)]: Belirteç süresi dolmuş!")
                    time.sleep(15)
                    continue

                # 3. Parametre Hatası (filter/order desteklenmiyorsa kaldır)
                if ("filter" in params or "order" in params) and response.status_code == 400:
                    params.pop("filter", None)
                    params.pop("order", None)
                    params["limit"] = 50
                    time.sleep(2.0)
                    continue

                print(f"⚠️ [Facebook Hatası {response.status_code}]: {hata_mesaji}")
                ardisik_hata += 1
                time.sleep(min(15, 3 * ardisik_hata))

        except requests.exceptions.Timeout:
            time.sleep(2.0)
        except Exception as e:
            print(f"⚠️ Hata: {e}")
            time.sleep(3.0)

if __name__ == "__main__":
    def konsol_ve_sunucu_test(platform, user, text):
        print(f"💬 [{platform}] {user}: {text}")
        try:
            requests.post(
                "http://127.0.0.1:5000/api/tiktok-web-chat",
                json={"platform": platform, "username": user, "message": text},
                timeout=1.5
            )
        except Exception:
            pass

    vid = sys.argv[1] if len(sys.argv) > 1 else None
    tok = sys.argv[2] if len(sys.argv) > 2 else PAGE_ACCESS_TOKEN

    start_listening(konsol_ve_sunucu_test, video_id=vid, token=tok)