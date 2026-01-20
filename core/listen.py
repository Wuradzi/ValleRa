# core/listen.py
import speech_recognition as sr
import config

class Listener:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        
        # === НАЛАШТУВАННЯ "ТЕРПІННЯ" ===
        
        # 1. Скільки секунд тиші треба почути, щоб зрозуміти, що фраза закінчилась.
        # Було 0.8 -> Ставимо 1.5 або 2.0 (тепер можна робити паузи)
        self.recognizer.pause_threshold = 1.5 
        
        # 2. Мінімальна довжина тиші, яку ми вважаємо "тишею" (щоб не плутати з шумом)
        self.recognizer.non_speaking_duration = 0.5

        # 3. Динамічне налаштування під шум кімнати (тільки один раз при старті)
        with self.microphone as source:
            print("🎧 Калібрую мікрофон під шум кімнати... (Помовчіть 1 сек)")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            print("✅ Мікрофон налаштовано.")

    def listen(self):
        with self.microphone as source:
            try:
                # phrase_time_limit=None означає, що ми не обмежуємо довжину фрази
                # (раніше могло стояти 5 секунд)
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=None)
                
                try:
                    text = self.recognizer.recognize_google(audio, language="uk-UA")
                    return text.lower()
                except sr.UnknownValueError:
                    return None
                except sr.RequestError:
                    print("🔴 Немає зв'язку з Google Speech Recognition")
                    return None
                    
            except sr.WaitTimeoutError:
                return None