import os, sys, platform, subprocess
def install():
    if platform.system() == "Linux":
        os.system("sudo apt update && sudo apt install -y python3-venv portaudio19-dev python3-tk scrot xsel xclip xdotool")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    print("✅ Успішно! Запускай python main.py")
if __name__ == "__main__": install()