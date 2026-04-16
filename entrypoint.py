#!/usr/bin/env python3
"""
Isomer Entrypoint — runs two Flask servers:
  Port 27001: Main application (dashboard, companies, controls, evidence, reports)
  Port 27000: Settings/admin interface (user management, system config)
"""

import subprocess
import sys
import os
import signal
import time

procs = []

def shutdown(signum, frame):
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

if __name__ == "__main__":
    # Ensure data directory exists
    os.makedirs("/data/uploads", exist_ok=True)

    print("=" * 60)
    print("  ISOMER — Compliance Tracking Platform (Alpha)")
    print("=" * 60)
    print(f"  Main application : http://0.0.0.0:27001")
    print(f"  Settings/Admin   : http://0.0.0.0:27000")
    print("=" * 60)
    print(f"  Default login    : admin / admin")
    print("=" * 60)
    print()

    # Start main app on 27001
    p1 = subprocess.Popen([sys.executable, "app.py", "27001"])
    procs.append(p1)

    # Start settings instance on 27000
    p2 = subprocess.Popen([sys.executable, "app.py", "27000"])
    procs.append(p2)

    # Wait for either to exit
    try:
        while True:
            for p in procs:
                ret = p.poll()
                if ret is not None:
                    print(f"Process {p.pid} exited with code {ret}")
                    shutdown(None, None)
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)
