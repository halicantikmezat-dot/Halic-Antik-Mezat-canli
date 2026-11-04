from collections import deque
import re
import sys
import time
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================================
# HALİÇ ANTİK MEZAT - FACEBOOK CANLI YAYIN KÖPRÜSÜ (GÜNCELLENMİŞ)
# ==========================================================

okunan_yorumlar = deque(maxlen=2500)
okunan_yorumlar_set = set()


def konsol_ve_sunucu_test(platform, user, text):
    """Yorumları ekrana basar, Flask paneline ve Ahmet Usta'ya iletir."""
    print(f"💬 [{platform}] {user}: {text}")
    temiz_kucuk = text.lower()

    # 1. Hemen Al Kontrolü
    if any(h in temiz_kucuk for h in ["hemen al", "aldım", "aldim", "aldık"]):
        try:
            requests.post(
                "http://127.0.0.1:5000/api/hemen-al-tetikle",
                json={"platform": platform, "username": user},
                timeout=0.5,
            )
        except Exception:
            pass
        return

    # 2. Normal Web Panel İletimi (Flask Web Paneli: 5000)
    try:
        requests.post(
            "http://127.0.0.1:5000/api/tiktok-web-chat",
            json={"platform": platform, "username": user, "message": text},
            timeout=0.5,
        )
    except Exception:
        pass

    # 3. Ahmet Usta Spiker ve Pey Motoru (Bot Port: 5005)
    try:
        requests.post(
            "http://127.0.0.1:5005/canli_yorum",
            json={"platform": platform, "user": user, "comment": text},
            timeout=0.5,
        )
    except Exception:
        pass


def chrome_baglan():
    options = Options()
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    try:
        return webdriver.Chrome(options=options)
    except Exception as e:
        print(f"❌ Chrome bağlantı hatası (Port 9222 açık mı?): {e}")
        return None


def start_listening(callback_func, video_id=None, token=None):
    driver = chrome_baglan()
    if not driver:
        print(
            "⚠️ Chrome 9222 debug portu bulunamadı. Lütfen Chrome'u debug modunda açtığınızdan emin olun."
        )
        return

    # Canlı yayın sekmesine odaklan (Dashboard, Producer veya aktif Facebook sekmesi)
    bulundu = False
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        url_lower = driver.current_url.lower()
        if (
            "producer" in url_lower
            or "dashboard" in url_lower
            or "facebook.com/live" in url_lower
            or "facebook.com" in url_lower
        ):
            bulundu = True
            break

    print("=" * 60)
    print("🚀 [Facebook Scraper] Chrome'a bağlandı!")
    print(f"✅ Sekme: {driver.title}")
    print("👉 Canlı sohbet taranıyor...")
    print("=" * 60)

    # Panelden veya arayüzden gelen çöp sistem kelimeleri
    gecersiz_kelimeler = {
        "yanıtla",
        "gizle",
        "sabitle",
        "beğen",
        "paylaş",
        "yorumlar",
        "yorum yaz...",
        "özellik kontrol listesi",
        "en alakalı",
        "tüm yorumlar",
        "top comments",
        "canlı videoyu öne çıkar",
        "videodan bir klip oluştur",
        "soru",
        "şıklar",
        "canlı",
        "istatistikler",
        "paneli gör",
        "canlı gösterge paneli",
        "hedefleri hedefler",
        "etkileşimi artırmak",
        "görüldü",
        "kurallar",
    }

    while True:
        try:
            # 1. Öncelik: Doğrudan yorum kartları (role='article')
            satirlar = driver.find_elements(
                By.XPATH, "//div[@role='article'] | //div[@role='feed']//div"
            )

            # 2. Öncelik: Live Chat mesaj kapsayıcıları
            if not satirlar:
                satirlar = driver.find_elements(
                    By.XPATH,
                    "//div[contains(@aria-label, 'Yorumlar') or contains(@aria-label, 'Comments')]//div[@dir='auto']",
                )

            for satir in satirlar:
                metin = satir.text.strip()
                if not metin or len(metin) < 2:
                    continue

                # Sistem metinlerini ve buton kalıntılarını temizle
                parcalar = [
                    p.strip()
                    for p in metin.split("\n")
                    if p.strip()
                    and p.strip().lower() not in gecersiz_kelimeler
                    and not re.match(
                        r"^\d+\s*(?:d|sn|s|dk|m|h)$", p.strip().lower()
                    )
                ]

                kullanici = None
                mesaj = None

                # Normal format: 1. satır İsim, diğer satırlar Mesaj
                if len(parcalar) >= 2:
                    kullanici = parcalar[0]
                    mesaj_adaylari = [
                        x
                        for x in parcalar[1:]
                        if x.lower() not in gecersiz_kelimeler
                        and not re.match(
                            r"^\d+\s*(?:d|sn|s|dk|m|h)$", x.lower()
                        )
                    ]
                    if mesaj_adaylari:
                        mesaj = " ".join(mesaj_adaylari)
                elif len(parcalar) == 1 and ":" in parcalar[0]:
                    kullanici, mesaj = parcalar[0].split(":", 1)
                    kullanici, mesaj = kullanici.strip(), mesaj.strip()

                if not kullanici or not mesaj:
                    continue

                # Kendi sayfa adınız, admin duyuruları veya Facebook sistem metinlerini atla
                kullanici_kucuk = kullanici.lower()
                mesaj_kucuk = mesaj.lower()

                if "haliç antik" in kullanici_kucuk or "facebook" in kullanici_kucuk:
                    continue

                # Sistem uyarılarını eleme
                if any(
                    yasak in mesaj_kucuk
                    for yasak in [
                        "hedefleri hedefler",
                        "yayında olduğuna göre",
                        "istatistiklerini görebilir",
                        "canlı yayın ayarları",
                    ]
                ):
                    continue

                kimlik = f"{kullanici}:{mesaj}"

                if kimlik not in okunan_yorumlar_set:
                    if len(okunan_yorumlar) >= 2500:
                        eski = okunan_yorumlar.popleft()
                        okunan_yorumlar_set.discard(eski)

                    okunan_yorumlar.append(kimlik)
                    okunan_yorumlar_set.add(kimlik)

                    if callback_func:
                        callback_func("Facebook", kullanici, mesaj)

            time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n🛑 Facebook dinleyici durduruldu.")
            break
        except Exception:
            time.sleep(1.0)


if __name__ == "__main__":
    start_listening(konsol_ve_sunucu_test)