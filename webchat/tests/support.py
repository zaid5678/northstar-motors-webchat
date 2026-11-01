"""Starts the unmodified servers. See `FAST_NAME` in run.py for why the lookup is skipped."""

from __future__ import annotations

import sys

FAST_NAME = "import runpy, socket; socket.getfqdn = lambda name='': name or 'localhost'; "
PLATFORM_COMMAND = [
    sys.executable,
    "-c",
    FAST_NAME + "runpy.run_module('src.server', run_name='__main__', alter_sys=True)",
]
WEBSITE_COMMAND = [sys.executable, "-c", FAST_NAME + "runpy.run_path('server.py', run_name='__main__')"]
START_LIMIT_SECONDS = 60  # Generous, because shared build machines can be very slow.
REQUEST_LIMIT_SECONDS = 30
