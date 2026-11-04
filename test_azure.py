from config import Config
import azure.cognitiveservices.speech as speechsdk

speech_config = speechsdk.SpeechConfig(subscription=Config.AZURE_SPEECH_KEY, region=Config.AZURE_SPEECH_REGION)
speech_config.speech_synthesis_voice_name = Config.AZURE_SPEECH_VOICE

synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)
synthesizer.speak_text_async("Haliç Antik mezatına hoş geldiniz! Ahmet Usta yayında.").get()