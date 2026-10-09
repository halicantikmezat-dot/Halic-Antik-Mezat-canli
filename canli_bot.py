import sys
import time
import threading
import requests
import re
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "https://halicantikcanli.com/api/tiktok-web-chat"

# Argümanları oku: python canli_bot.py <TIKTOK_USER> <FB_LINK_OR_ID> <IG_USER>
args = sys.argv[1:]
TIKTOK_KULLANICI = args[0] if len(args) > 0 and args[0] not in ["-", ""] else "halicantikmezat34"

# Facebook Girdisi Kontrolü (Tam Link veya Düz Rakam ID'yi Ayıklar)
raw_fb = args[1] if len(args) > 1 and args[1] not in ["-", ""] else ""
if raw_fb and raw_fb != "AUTO":
    # Link içindeki en az 10 haneli video ID rakamlarını bul
    match = re.search(r'(\d{10,})', raw_fb)
    FB_PARAM = match.group(1) if match else raw_fb
else:
    FB_PARAM = raw_fb

IG_USERNAME = args[2] if len(args) > 2 and args[2] not in ["-", ""] else ""

# Asenkron ağ kuyruğu (max 10 paralel istek)
executor = ThreadPoolExecutor(max_workers=10)

def _async_post(platform, user, comment):
    try:
        clean_msg = str(comment).strip()
        clean_user = str(user).strip()
        
        payload = {
            "platform": platform,
            "username": clean_user,
            "message": clean_msg
        }
        
        res = requests.post(BASE_URL, json=payload, timeout=2.5)
        if res.status_code == 200:
            print(f"✅ [{platform}] {clean_user}: {clean_msg}")
        else:
            print(f"⚠️ [{platform}] Sunucu ({res.status_code}): {clean_msg}")
    except Exception as e:
        print(f"❌ [{platform}] İletim Hatası: {e}")

def pey_gonder(platform, user, comment):
    """Pey iletimini ana thread'i bloke etmeden kuyruğa fırlatır."""
    if not comment:
        return
    executor.submit(_async_post, platform, user, comment)

# 1. TIKTOK DİNLEYİCİ
def tiktok_dinleyici():
    if not TIKTOK_KULLANICI or TIKTOK_KULLANICI == "-":
        return
    while True:
        try:
            from TikTokLive import TikTokLiveClient
            from TikTokLive.events import CommentEvent
            
            client = TikTokLiveClient(unique_id=TIKTOK_KULLANICI)

            @client.on(CommentEvent)
            async def on_comment(event: CommentEvent):
                try:
                    user = getattr(event.user, 'unique_id', 'Misafir')
                    comment = getattr(event, 'comment', '')
                    pey_gonder("TikTok", user, comment)
                except Exception:
                    pass

            print(f"📡 TikTok Canlı Yayını Dinleniyor: @{TIKTOK_KULLANICI}")
            client.run()
        except Exception as e:
            print(f"⚠️ TikTok Bağlantı Hatası: {e}. 5 sn sonra yeniden bağlanılıyor...")
            time.sleep(5)

# 2. FACEBOOK DİNLEYİCİ
def facebook_dinleyici():
    if not FB_PARAM or FB_PARAM == "-":
        return
    try:
        import facebook_bridge
    except ImportError:
        print("❌ facebook_bridge.py dosyası klasörde bulunamadı!")
        return

    while True:
        try:
            vid_param = None if FB_PARAM == "AUTO" else FB_PARAM
            print(f"📡 Facebook Dinleyici Devrede ({'Otomatik Mod' if FB_PARAM == 'AUTO' else 'ID: ' + str(FB_PARAM)})...")
            
            if hasattr(facebook_bridge, "start_listening"):
                facebook_bridge.start_listening(pey_gonder, video_id=vid_param)
            else:
                print("⚠️ facebook_bridge.py içinde 'start_listening' fonksiyonu bulunamadı!")
                time.sleep(10)
        except Exception as e:
            print(f"⚠️ Facebook Hatası: {e}. 5 sn sonra tekrar deneniyor...")
            time.sleep(5)

# 3. INSTAGRAM DİNLEYİCİ
def instagram_dinleyici():
    if not IG_USERNAME or IG_USERNAME == "-":
        return
    try:
        import instagram_bridge
    except ImportError:
        print("❌ instagram_bridge.py dosyası klasörde bulunamadı!")
        return

    while True:
        try:
            print(f"📡 Instagram Dinleyici Devrede...")
            if hasattr(instagram_bridge, "start_listening"):
                try:
                    instagram_bridge.start_listening(pey_gonder, IG_USERNAME)
                except TypeError:
                    instagram_bridge.start_listening(pey_gonder)
            else:
                print("⚠️ instagram_bridge.py içinde 'start_listening' fonksiyonu bulunamadı!")
                time.sleep(10)
        except Exception as e:
            print(f"⚠️ Instagram Hatası: {e}. 5 sn sonra tekrar deneniyor...")
            time.sleep(5)

if __name__ == '__main__':
    fb_gorunum = "Aktif (Otomatik)" if FB_PARAM == "AUTO" else (FB_PARAM if FB_PARAM and FB_PARAM != "-" else "Devre Dışı")

    print("==================================================")
    print("🚀 HALİÇ ANTIK ÇOKLU PLATFORM PEY MOTORU AKTİF")
    print(f"   TikTok    : @{TIKTOK_KULLANICI if TIKTOK_KULLANICI != '-' else 'Devre Dışı'}")
    print(f"   Facebook  : {fb_gorunum}")
    print(f"   Instagram : {IG_USERNAME if IG_USERNAME and IG_USERNAME != '-' else 'Harici Pencerede Aktif'}")
    print("==================================================")

    threads = []
    if TIKTOK_KULLANICI and TIKTOK_KULLANICI != "-":
        threads.append(threading.Thread(target=tiktok_dinleyici, daemon=True))
    if FB_PARAM and FB_PARAM != "-":
        threads.append(threading.Thread(target=facebook_dinleyici, daemon=True))
    if IG_USERNAME and IG_USERNAME != "-":
        threads.append(threading.Thread(target=instagram_dinleyici, daemon=True))

    for t in threads:
        t.start()

    print("🟢 Botlar dinlemede. Çıkmak için: Ctrl + C")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nSistem güvenli şekilde kapatılıyor...")
        executor.shutdown(wait=False)
        sys.exit(0)