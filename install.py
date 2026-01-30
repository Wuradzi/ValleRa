import os
import sys
import platform
import subprocess

def install_system_deps_linux():
    print("🐧 Виявлено Linux Mint/Ubuntu.")
    print("📦 Встановлюю системні драйвери (потрібен пароль sudo)...")
    
    packages = [
        "python3-venv",
        "portaudio19-dev", # Для мікрофона
        "python3-tk",      # Для інтерфейсу помилок
        "scrot",           # Для скріншотів
        "xsel",            # Для буфера обміну (pyperclip)
        "xclip"            # Альтернатива для буфера
    ]
    
    cmd = f"sudo apt update && sudo apt install -y {' '.join(packages)}"
    os.system(cmd)

def install_python_deps():
    print("🐍 Встановлюю Python-бібліотеки з requirements.txt...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

def main():
    system = platform.system()
    
    if system == "Linux":
        install_system_deps_linux()
        install_python_deps()
        print("\n✅ Успішно! Запускай: ./main.py")
        
    elif system == "Windows":
        print("🪟 Виявлено Windows.")
        install_python_deps()
        print("\n✅ Успішно! Запускай: python main.py")
        
    else:
        print(f"❌ Невідома система: {system}. Встановлюй вручну.")

if __name__ == "__main__":
    main()