from __future__ import annotations

from core.console import console_print as print


async def run_diagnostics(settings, secret_store) -> int:
    from core.app import ValleRaApp

    print("=== Діагностика ValleRa ===")
    app = ValleRaApp(settings, secret_store, text_only=False)
    await app.speaker.start()
    try:
        summary, report = await app.services["diagnostics"].run()
    finally:
        import asyncio
        await asyncio.to_thread(app.listener.close)
        await app.speaker.close()
        await app.llm.close()

    print(f"OS: {report['platform']}")
    print(f"Python: {report['python']}")
    for check in report["checks"]:
        state = (
            "OK"
            if check["ok"]
            else "FAIL" if check.get("required", True) else "WARN"
        )
        print(f"[{state}] {check['name']}: {check['detail']}")
    print(summary)
    return 0 if report["ok"] else 1
