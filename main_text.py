from __future__ import annotations

import asyncio
import sys

from main import async_main

if "--text-only" not in sys.argv:
    sys.argv.append("--text-only")

raise SystemExit(asyncio.run(async_main()))
