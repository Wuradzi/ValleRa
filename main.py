from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from config import ConfigError, load_settings
from core.console import console_getpass, console_print
from core.logging_setup import SessionLogging, configure_logging
from core.single_instance import SingleInstance
from diagnostics import run_diagnostics
from services.storage.secret_store import SecretStore


class ConsoleArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            console_print(message, end="")


def parse_args() -> argparse.Namespace:
    parser = ConsoleArgumentParser(description="ValleRa Voice Assistant")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--web-ui", action="store_true", help="Локальний вебінтерфейс")
    parser.add_argument("--web-port", type=int, default=0, help="Порт UI; 0 — вільний порт")
    parser.add_argument("--no-browser", action="store_true", help="Не відкривати UI автоматично")
    parser.add_argument(
        "--profile",
        choices=("balanced", "fast", "raspberry_pi"),
        help="Профіль продуктивності лише для цього запуску",
    )
    return parser.parse_args()


def unlock_vault(settings) -> SecretStore:
    vault = SecretStore(settings.paths.data_dir / "secrets.json")
    if not vault.exists:
        return vault

    for attempt in range(3):
        password = console_getpass("Майстер-пароль ValleRa: ")
        if vault.unlock_with_password(password):
            print("[SECURITY] Сховище секретів розблоковано.")
            return vault
        console_print(f"Неправильний пароль. Спроба {attempt + 1}/3.")
    console_print("Сховище лишається заблокованим.")
    return vault


async def async_main() -> int:
    args = parse_args()
    if not 0 <= args.web_port <= 65535:
        console_print("Порт має бути від 0 до 65535.")
        return 2
    try:
        settings = load_settings(args.profile)
    except ConfigError as exc:
        print(f"[CONFIG] {exc}")
        console_print("Виправте config.json або відновіть його з резервної копії. Подробиці — у журналі сесії.")
        return 2

    configure_logging(settings)
    if args.setup or not settings.paths.config_file.exists():
        from setup_wizard import run_first_start_wizard

        run_first_start_wizard(settings.paths.project_root)
        try:
            settings = load_settings(args.profile)
        except ConfigError as exc:
            print(f"[CONFIG] Налаштування не збережено: {exc}")
            console_print("Налаштування не збережено. Подробиці — у журналі сесії.")
            return 2

    configure_logging(settings)
    instance = SingleInstance(settings.paths.data_dir / "valera.lock")
    if not instance.acquire():
        console_print("ValleRa вже запущена.")
        return 2

    try:
        vault = unlock_vault(settings)
        if args.diagnostics:
            return await run_diagnostics(settings, vault)

        from core.metrics import MetricsCollector
        from core.performance import PerformanceRecorder

        performance = PerformanceRecorder(MetricsCollector(settings.paths.data_dir / "metrics.jsonl"))
        logging.getLogger("session.lifecycle").info("Performance run_id=%s", performance.run_id)
        with performance.span("startup.app_imports"):
            from core.app import ValleRaApp
        with performance.span("startup.app_construct"):
            app = ValleRaApp(settings, vault, text_only=args.text_only, performance=performance)
        try:
            if args.web_ui:
                from core.web_ui import LocalWebUI
                from core.console import console_stream
                from core.logging_setup import register_secret

                app.web_ui = LocalWebUI(app, args.web_port)
                # Opening a web panel is not consent to start the microphone.
                app.microphone_enabled = False
                app._mic_ready.clear()
                if app.listener is not None:
                    app.listener.set_paused(True)
                url = await app.web_ui.start()
                register_secret(app.web_ui.token)
                # Do not persist the local bearer token in session logs.
                print(f"Вебінтерфейс цієї сесії: {url}", file=console_stream(), flush=True)
                if not args.no_browser:
                    import webbrowser

                    try:
                        await asyncio.to_thread(webbrowser.open, url)
                    except Exception:
                        console_print("Відкрийте посилання вебінтерфейсу вручну.")
            await app.run()
        finally:
            if app.web_ui is not None:
                await app.web_ui.close()
        return 0
    finally:
        instance.release()


def main() -> int:
    session = SessionLogging(Path(__file__).resolve().parent / "logs" / "sessions")
    try:
        with session:
            console_print(f"Журнал сесії: {session.path}")
            try:
                exit_code = asyncio.run(async_main())
            except KeyboardInterrupt:
                logging.getLogger("session.lifecycle").info("Shutdown requested: Ctrl+C")
                console_print("\nValleRa завершено.")
                return 0
            logging.getLogger("session.lifecycle").info("Application exit code: %s", exit_code)
            return exit_code
    except KeyboardInterrupt:
        console_print("\nValleRa завершено.")
        return 0
    except Exception:
        # The context logged the traceback; do not dump it into the dialogue.
        if session.path.exists():
            console_print(f"ValleRa зупинено через помилку. Подробиці: {session.path}")
        else:
            console_print("Не вдалося створити журнал сесії. Перевірте доступ до logs/sessions і вільне місце.")
        return 1


if __name__ == "__main__":
    exit_code = main()
    # Do not surface a normal shutdown as SystemExit in IDE debuggers.
    if exit_code:
        raise SystemExit(exit_code)
