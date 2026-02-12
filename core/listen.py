# core/listen.py
import speech_recognition as sr
import config

class Listener:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        
        # Pause threshold - how many seconds of silence to end a phrase
        self.recognizer.pause_threshold = 1.5
        self.recognizer.non_speaking_duration = 0.5

        # Calibrate microphone for ambient noise
        with self.microphone as source:
            print("🎧 Калібрую мікрофон під шум кімнати... (Помовчіть 1 сек)")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            print("✅ Мікрофон налаштовано.")

    def listen(self):
        with self.microphone as source:
            try:
                # Listen with timeout
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=None)
                
                try:
                    # Use Google Speech Recognition (works with Ukrainian)
                    text = self.recognizer.recognize_google(audio, language="uk-UA")
                    print(f"👂 Google STT: {text}")
                    return text.lower()
                except sr.UnknownValueError:
                    return None
                except sr.RequestError as e:
                    print(f"🔴 Google Speech Error: {e}")
                    return None
                    
            except sr.WaitTimeoutError:
                return None
