# core/listen.py
import speech_recognition as sr
import config
import faster_whisper
import os

class Listener:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        
        # Load faster-whisper model (base for better accuracy)
        print("🎧 Завантажую Whisper model...")
        self.whisper_model = faster_whisper.WhisperModel("base", compute_type="int8")
        print("✅ Whisper ready.")
        
        # === НАЛАШТУВАННЯ "ТЕРПІННЯ" ===
        self.recognizer.pause_threshold = 1.5
        self.recognizer.non_speaking_duration = 0.5

        # Динамічне налаштування під шум кімнати
        with self.microphone as source:
            print("🎧 Калібрую мікрофон під шум кімнати... (Помовчіть 1 сек)")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            print("✅ Мікрофон налаштовано.")

    def listen(self):
        with self.microphone as source:
            try:
                # phrase_time_limit=None - не обмежуємо довжину фрази
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=None)
                
                # Save audio to temporary file for Whisper
                with open("temp_audio.wav", "wb") as f:
                    f.write(audio.get_wav_data())
                
                # Transcribe with faster-whisper
                segments, info = self.whisper_model.transcribe(
                    "temp_audio.wav", 
                    language="uk",
                    beam_size=5
                )
                
                # Clean up
                if os.path.exists("temp_audio.wav"):
                    os.remove("temp_audio.wav")
                
                # Get best result
                for segment in segments:
                    text = segment.text.strip()
                    if text:
                        print(f"👂 Whisper: {text}")
                        return text.lower()
                
                return None
                
            except sr.UnknownValueError:
                return None
            except sr.RequestError as e:
                print(f"🔴 Speech recognition error: {e}")
                return None
            except sr.WaitTimeoutError:
                return None