#!/usr/bin/env python3
"""
Isomer pre-start hook.

Ensures /data/uploads exists before gunicorn imports app:app. The actual
server is started by gunicorn via the image CMD; app.py no longer calls
app.run() because Flask's dev server isn't a production WSGI server.
"""

import os

if __name__ == "__main__":
    os.makedirs("/data/uploads", exist_ok=True)
