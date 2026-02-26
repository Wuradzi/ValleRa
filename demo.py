#!/usr/bin/env python3
"""
ValleRa - Демонстрація функціоналу
Run: python3 demo.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skills import (
    get_time, get_date, calculator, timer, 
    add_note, show_notes, clear_notes,
    remember_data, recall_data,
    system_status, get_help
)

def print_header(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def demo():
    print("\n🎬 ДЕМОНСТРАЦІЯ ВАЛЕРИ\n")
    
    # 1. Час та дата
    print_header("1️⃣  ЧАС ТА ДАТА")
    print(f"  Час: {get_time()}")
    print(f"  Дата: {get_date()}")
    
    # 2. Математика
    print_header("2️⃣  МАТЕМАТИКА")
    test_calcs = [
        "порахуй 2+2",
        "скільки буде 10*5",
        "порахуй 100/4"
    ]
    for calc in test_calcs:
        result = calculator(calc)
        print(f"  '{calc}' → {result}")
    
    # 3. Таймер
    print_header("3️⃣  ТАЙМЕР")
    timer_result = timer("таймер 1 секунда")
    print(f"  {timer_result}")
    
    # 4. Пам'ять
    print_header("4️⃣  ПАМ'ЯТЬ")
    remember_data("мій улюблений колір: зелений")
    remember_data("мій улюблений фільм: Інтерстеллар")
    print(f"  {recall_data('мій')}")
    
    # 5. Нотатки
    print_header("5️⃣  НОТАТКИ")
    print(f"  {add_note('додай нотатку: купити хліб')}")
    print(f"  {add_note('додай нотатку: позвонити мамі')}")
    print(f"\n  {show_notes()}")
    
    # 6. Статус системи
    print_header("6️⃣  СТАТУС СИСТЕМИ")
    print(f"  {system_status()}")
    
    # 7. Справка
    print_header("7️⃣  СПРАВКА")
    help_text = get_help()
    for line in help_text.split("\n")[:15]:
        print(f"  {line}")
    print("  ...")
    
    # Очистка
    print_header("✅ ДЕМОНСТРАЦІЯ ЗАВЕРШЕНА")
    print("\n  Чистимо нотатки...")
    clear_notes()
    print("  ✅ Все готово! Запусти main_text.py для повного функціоналу")
    print()

if __name__ == "__main__":
    try:
        demo()
    except Exception as e:
        print(f"\n❌ Помилка: {e}")
        import traceback
        traceback.print_exc()
