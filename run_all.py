#!/usr/bin/env python3
"""
Smart Parking System Launcher
This script starts all components of the Smart Parking System
"""

import subprocess
import sys
import os
import webbrowser
import time
from glob import glob

# Helper to use the correct python executable
python_exec = sys.executable


def find_script_candidates(prefix):
    """Return a list of candidate script filenames for a given prefix.

    We look for files like 'prefix-*.py' first, then plain 'prefix.py'.
    """
    candidates = []
    # prefer machine-specific variants like 'prefix-*LAPTOP*.py'
    pattern_pref = f"{prefix}-*LAPTOP*.py"
    candidates.extend(sorted(glob(pattern_pref)))
    # then any variant like 'prefix-*.py'
    pattern = f"{prefix}-*.py"
    candidates.extend(x for x in sorted(glob(pattern)) if x not in candidates)
    # fallback to plain file
    if os.path.exists(f"{prefix}.py"):
        candidates.append(f"{prefix}.py")
    return candidates


def start_process(script_path, name):
    print(f"▶ Starting {name}: {script_path}")
    # Ensure logs directory exists
    os.makedirs('logs', exist_ok=True)
    log_path = os.path.join('logs', f"{name}.log")
    f = open(log_path, 'a', encoding='utf-8')
    # Start process with combined stdout/stderr redirected to a log file for debugging
    return subprocess.Popen([python_exec, script_path], stdout=f, stderr=subprocess.STDOUT, bufsize=1)


def main():
    print("🚀 Starting Smart Parking System runner (auto-detect scripts)...")

    # Decide which nesm/app scripts to run by searching for variants in repo
    nesm_candidates = find_script_candidates('nesm')
    app_candidates = find_script_candidates('app')

    if not nesm_candidates and not app_candidates:
        print("❌ No nesm/app scripts found (looked for 'nesm*.py' and 'app*.py'). Exiting.")
        return

    processes = []

    try:
        # Start nesm if available
        if nesm_candidates:
            nesm_script = nesm_candidates[0]
            p_nesm = start_process(nesm_script, 'nesm')
            processes.append(('nesm', p_nesm))
            time.sleep(1)

        # Start app if available
        if app_candidates:
            app_script = app_candidates[0]
            p_app = start_process(app_script, 'app')
            processes.append(('app', p_app))
            # give Flask a moment
            time.sleep(2)

        # Open UI pages (best-effort)
        print("🌍 Opening web interface (http://localhost:5000/) and /qr")
        try:
            webbrowser.open('http://localhost:5000/')
            webbrowser.open('http://localhost:5000/qr')
        except Exception:
            pass

        print("\n✅ Smart Parking System processes started. Press Ctrl+C to stop.")

        # Monitor processes: exit if any critical process dies
        while True:
            for name, proc in list(processes):
                if proc.poll() is not None:
                    print(f"⚠️  Process '{name}' exited with code {proc.returncode}")
                    # Shut down remaining
                    raise KeyboardInterrupt
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n🛑 Shutting down processes...")
        for name, proc in processes:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        print("✅ All processes terminated")


if __name__ == '__main__':
    main()