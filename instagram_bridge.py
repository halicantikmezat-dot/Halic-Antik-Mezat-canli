import os
import sys
import time
from collections import deque
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "instagram_profile")

okunan_kayitlar = deque(maxlen=2000)
okunan_kayitlar_set = set()

def parse_instagram_comment_block(element):
    try:
        raw_text = element.text.strip()
        if not raw_text:
            return None, None

        # Sistem bildirimlerini ve gereksiz metinleri atla
        kara_liste = ["katıldı", "beğendi", "istek gönderdi", "canlı video", "yorumlar", "olay kaydı", "bağlantı ekle"]
        if any(x in raw_text.lower() for x in kara_liste):
            return None, None

        # Çok satırlı ayrıştırma
        satirlar = [s.strip() for s in raw_text.split("\n") if s.strip()]
        satirlar = [s for s in satirlar if s.lower() not in ["yanıtla", "beğen", "gönderildi", "gör", "çevirisine bak"]]
        
        if len(satirlar) >= 2:
            user = satirlar[0]
            mesaj = " ".join(satirlar[1:])
            if user and mesaj:
                return user, mesaj

        # "golden34horn 1800" veya "kullanici: 1500" gibi tek satırlı durumlar
        parcalar = raw_text.split()
        if len(parcalar) >= 2 and parcalar[1].isdigit():
            user = parcalar[0].replace(":", "")
            mesaj = parcalar[1]
            return user, mesaj

        # Link (kullanıcı adı) ve yanındaki mesaj yapısı
        links = element.find_elements(By.TAG_NAME, "a")
        if links:
            user = links[0].text.strip()
            mesaj = raw_text.replace(user, "", 1).strip(" :")
            if user and mesaj:
                return user, mesaj

        if ":" in raw_text:
            bol = raw_text.split(":", 1)
            return bol[0].strip(), bol[1].strip()

        return None, None
    except (StaleElementReferenceException, Exception):
        return None, None

def start_listening(callback_func, ig_target=None):
    options = webdriver.ChromeOptions()
    # Açık olan porta bağlan (yeni tarayıcı/sekme açmaz)
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")

    driver = None
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

        print("\n" + "=" * 55)
        print("🚀 [Instagram Scraper] Açık Chrome sekmesine bağlandı!")
        print("👉 Sohbet taranıyor...")
        print("=" * 55 + "\n")

        seciciler = [
            "div[role='dialog'] div[tabindex='0'] > div",
            "div[role='dialog'] ul li",
            "div[role='log'] > div",
            "div[role='region'] div[tabindex='0'] > div",
            "div[style*='overflow-y'] > div",
            "div[tabindex='0'] > div > div",
            "section main div[tabindex='0'] ul li",
            "div[tabindex='0'] div[role='button']"
        ]

        while True:
            try:
                bulunan_elemanlar = []
                for secici in seciciler:
                    el_list = driver.find_elements(By.CSS_SELECTOR, secici)
                    el_list = [e for e in el_list if e.text.strip()]
                    if len(el_list) >= 1:
                        bulunan_elemanlar = el_list
                        break

                for el in bulunan_elemanlar[-25:]:
                    try:
                        user, msg = parse_instagram_comment_block(el)
                        if not user or not msg:
                            continue

                        zaman_dilimi = int(time.time() / 4)
                        imza = f"{user.lower()}_{msg.lower()}_{zaman_dilimi}"

                        if imza not in okunan_kayitlar_set:
                            if len(okunan_kayitlar) >= 2000:
                                eski_imza = okunan_kayitlar.popleft()
                                okunan_kayitlar_set.discard(eski_imza)

                            okunan_kayitlar.append(imza)
                            okunan_kayitlar_set.add(imza)

                            if callback_func:
                                callback_func("Instagram", user, msg)

                    except StaleElementReferenceException:
                        continue

            except WebDriverException:
                print("🛑 [Instagram] Tarayıcı bağlantısı koptu.")
                break
            except Exception:
                pass

            time.sleep(0.8)

    except Exception as e:
        print(f"⚠️ [Instagram] Hata: {e}")
    finally:
        # Açık olan pencerene dokunmaması için quit kaldırıldı
        pass

if __name__ == "__main__":
    def konsol_ve_sunucu_test(platform, user, text):
        print(f"💬 [{platform}] {user}: {text}")
        try:
            requests.post(
                "http://127.0.0.1:5000/api/tiktok-web-chat",
                json={"platform": platform, "username": user, "message": text},
                timeout=2
            )
        except Exception:
            pass

    hedef = sys.argv[1] if len(sys.argv) > 1 else None
    start_listening(konsol_ve_sunucu_test, ig_target=hedef)