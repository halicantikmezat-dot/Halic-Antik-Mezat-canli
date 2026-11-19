import azure.cognitiveservices.speech as speechsdk
from config import Config

if not getattr(Config, "AZURE_SPEECH_KEY", None):
    raise RuntimeError("AZURE_SPEECH_KEY tanımlı değil! Lütfen .env dosyasını kontrol edin.")

speech_config = speechsdk.SpeechConfig(
    subscription=Config.AZURE_SPEECH_KEY,
    region=Config.AZURE_SPEECH_REGION,
)
speech_config.speech_synthesis_voice_name = Config.AZURE_SPEECH_VOICE

synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)
sonuc = synthesizer.speak_text_async(
    "Haliç Antik mezatına hoş geldiniz! Canlı sunucu yayında."
).get()

if sonuc.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
    print("✅ Azure TTS testi başarılı!")
elif sonuc.reason == speechsdk.ResultReason.Canceled:
    detay = sonuc.cancellation_details
    print(f"❌ Azure TTS iptal edildi: {detay.reason} | {detay.error_details}")