#!/usr/bin/env python3
"""
Isomer Entrypoint — runs a single Flask server on port 27001.

Both the user dashboard and the admin portal are served by the same process.
The admin portal is exposed via /admin and is gated by the existing
@role_required("admin") decorators on settings routes.
"""

import os

if __name__ == "__main__":
    os.makedirs("/data/uploads", exist_ok=True)

    print("=" * 60)
    print("  ISOMER — Compliance Tracking Platform (Alpha)")
    print("=" * 60)
    print("  App   : http://0.0.0.0:27001/")
    print("  Admin : http://0.0.0.0:27001/admin")
    print("=" * 60)
    print("  Default login: admin / admin")
    print("=" * 60)

    from app import app
    app.run(host="0.0.0.0", port=27001, debug=False)
