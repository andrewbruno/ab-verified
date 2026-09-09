"""Vercel entry point.

`vercel.json` rewrites every path to this module. The ASGI application is
built once per cold start and reused for the life of the instance.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import create_app  # noqa: E402

app = create_app()
