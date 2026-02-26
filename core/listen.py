import speech_recognition as sr
class Listener:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.recognizer.pause_threshold = 1.5
        self.recognizer.non_speaking_duration = 0.5
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)

    def listen(self):
        with self.microphone as source:
            try:
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=None)
                try:
                    return self.recognizer.recognize_google(audio, language="uk-UA").lower()
                except: return None
            except sr.WaitTimeoutError: return None