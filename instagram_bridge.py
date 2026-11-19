from collections import deque
import re
import threading
import time
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# ==========================================================
# HALİÇ ANTİK MEZAT - INSTAGRAM KÖPRÜSÜ (NOKTA ATIŞI AYRIŞTIRICI)
# ==========================================================

MAX_YORUM_HAFIZA = 2500
okunan_yorumlar = deque()
okunan_yorumlar_set = set()


def _arka_plan_post(url, payload):
    def _gonder():
        try:
            requests.post(url, json=payload, timeout=0.8)
        except Exception:
            pass
    threading.Thread(target=_gonder, daemon=True).start()


def konsol_ve_sunucu_test(platform, user, text):
    """Yorumları ekrana basar, Flask paneline ve mezat sunucusuna iletir."""
    print(f"💬 [{platform}] {user}: {text}")
    temiz_kucuk = text.lower()

    # 1. Hemen Al Kontrolü
    if any(h in temiz_kucuk for h in ["hemen al", "aldım", "aldim", "aldık"]):
        _arka_plan_post(
            "http://127.0.0.1:5000/api/hemen-al-tetikle",
            {"platform": platform, "username": user},
        )
        return

    # 2. Normal Web Panel İletimi (Flask Web Paneli: 5000)
    _arka_plan_post(
        "http://127.0.0.1:5000/api/tiktok-web-chat",
        {"platform": platform, "username": user, "message": text},
    )

    # 3. Mezat sunucusu ve pey motoru (Bot Port: 5005)
    _arka_plan_post(
        "http://127.0.0.1:5005/canli_yorum",
        {"platform": platform, "user": user, "comment": text},
    )


def chrome_baglan():
    options = Options()
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    try:
        return webdriver.Chrome(options=options)
    except Exception as e:
        print(f"❌ Chrome bağlantı hatası (Port 9222 açık mı?): {e}")
        return None


def start_listening(callback_func):
    driver = chrome_baglan()
    if not driver:
        print("⚠️ Chrome 9222 debug portu bulunamadı.")
        return

    # Instagram sekmesine odaklan
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        url_lower = driver.current_url.lower()
        if "instagram.com" in url_lower:
            break

    print("=" * 60)
    print("🚀 [Instagram Scraper] Chrome'a bağlandı!")
    print(f"✅ Sekme: {driver.title}")
    print("👉 Canlı sohbet taranıyor...")
    print("=" * 60)

    # Instagram'ın iç HTML yapısından [Kullanıcı, Mesaj] ikilisini çözen JS
    js_cekici = """
    let sonuclar = [];
    let yanitlar = Array.from(document.querySelectorAll('*')).filter(
        el => el.children.length === 0 && (el.innerText || '').trim().toLowerCase() === 'yanıtla'
    );

    for (let y of yanitlar) {
        let blok = y.parentElement;
        for (let i = 0; i < 4; i++) {
            if (blok && blok.parentElement) {
                let metin = blok.parentElement.innerText || '';
                let sayi = (metin.match(/Yanıtla/g) || []).length;
                if (sayi > 1) break;
                blok = blok.parentElement;
            }
        }

        if (blok) {
            let hamSatir = (blok.innerText || '')
                .split('\\n')
                .map(s => s.trim())
                .filter(s => s.length > 0 && s.toLowerCase() !== 'yanıtla' && s !== '...' && s !== '…');

            if (hamSatir.length > 0) {
                sonuclar.push(hamSatir[0]);
            }
        }
    }
    return sonuclar;
    """

    while True:
        try:
            ham_satirlar = driver.execute_script(js_cekici)
            if not ham_satirlar:
                ham_satirlar = []

            for satir in ham_satirlar:
                metin = satir.strip()
                if not metin or len(metin) < 2:
                    continue

                eslesme = re.match(r"^([a-zA-Z0-9_\.]+)[\s:]+(.+)$", metin)
                if eslesme:
                    kullanici = eslesme.group(1).strip()
                    mesaj = eslesme.group(2).strip()
                else:
                    parcalar = metin.split(maxsplit=1)
                    if len(parcalar) == 2:
                        kullanici, mesaj = parcalar[0].strip(), parcalar[1].strip()
                    else:
                        continue

                mesaj = re.sub(r"[\s\.\…]+$", "", mesaj).strip()
                if not mesaj:
                    continue

                u_low = kullanici.lower()
                m_low = mesaj.lower()

                if any(ign in u_low for ign in ["halic", "haliç", "hamdullah", "moderatör", "canlı"]):
                    continue
                if m_low in [".", "..", "...", "…", "yanıtla", "katıldı"]:
                    continue

                kimlik = f"{kullanici}:{mesaj}"

                if kimlik not in okunan_yorumlar_set:
                    if len(okunan_yorumlar) >= MAX_YORUM_HAFIZA:
                        eski = okunan_yorumlar.popleft()
                        okunan_yorumlar_set.discard(eski)
                    okunan_yorumlar.append(kimlik)
                    okunan_yorumlar_set.add(kimlik)

                    if callback_func:
                        callback_func("Instagram", kullanici, mesaj)

            time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n🛑 Instagram dinleyici durduruldu.")
            break
        except Exception:
            time.sleep(1.0)


if __name__ == "__main__":
    start_listening(konsol_ve_sunucu_test)