import os
import urllib.request

# Hugging Face doğrulanmış doğrudan indirme bağlantıları
MODEL_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx"
JSON_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx.json"

# Alternatif resmi HuggingFace ham depo linki (Garanti yol):
RESMI_MODEL_URL = "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-tr-fahrettin-medium.tar.gz"

HEDEF_MODEL = "tr_erkek.onnx"
HEDEF_JSON = "tr_erkek.onnx.json"

# Kullanıcı aracısı (User-Agent) ekleyerek 403/404 engellerini aşan güvenli indirme
def dosya_indir(url, hedef):
    print(f"⏳ İndiriliyor: {hedef}...")
    headers = {'User-Agent': 'Mozilla/5.0'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as response, open(hedef, 'wb') as out_file:
        out_file.write(response.read())
    print(f"✅ {hedef} başarıyla indirildi.")

# 1. ONNX Dosyası Kontrol ve İndirme
if not os.path.exists(HEDEF_MODEL):
    try:
        dosya_indir("https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx", HEDEF_MODEL)
    except Exception as e:
        print(f"⚠️ İlk link başarısız ({e}), alternatif deneniyor...")
        dosya_indir("https://huggingface.co/datasets/rhasspy/piper-checkpoints/resolve/main/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx", HEDEF_MODEL)
else:
    print("✅ Model zaten mevcut.")

# 2. JSON Dosyası Kontrol ve İndirme
if not os.path.exists(HEDEF_JSON):
    try:
        dosya_indir("https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx.json", HEDEF_JSON)
    except Exception as e:
        print(f"⚠️ İlk link başarısız ({e}), alternatif deneniyor...")
        dosya_indir("https://huggingface.co/datasets/rhasspy/piper-checkpoints/resolve/main/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx.json", HEDEF_JSON)
else:
    print("✅ Konfigürasyon zaten mevcut.")