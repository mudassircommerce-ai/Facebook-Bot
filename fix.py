#!/usr/bin/env python3
# ============================================================
# fix.py  —  bot stuck ho to ye chalao (FIX.bat double-click)
#
# Karta hai (SIRF is folder ke bot ke liye — baaki bots / aapka
# personal Chrome ko haath nahi lagता):
#   1. Is folder ke atke/zombie Chrome windows band
#   2. pw_profile* ke lock files (SingletonLock/Cookie/Socket) hataao
#   3. adhoori update ki *.new files hataao
#
# Standalone — fb_joiner.py toota hua ho tab bhi chalta hai.
# ============================================================
import glob
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def _profiles():
    return [d for d in glob.glob(os.path.join(HERE, "pw_profile*")) if os.path.isdir(d)]


def kill_chrome():
    """SIRF is folder ke pw_profile* se bandhe chrome.exe maar do."""
    if os.name != "nt":
        return 0
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception:
        return 0
    if not out:
        return 0
    try:
        data = json.loads(out)
    except Exception:
        return 0
    if isinstance(data, dict):
        data = [data]
    hl = HERE.lower()
    killed = 0
    for p in data:
        cmd = ((p or {}).get("CommandLine") or "").lower()
        # is folder ka bot-chrome: user-data-dir is folder ke andar pw_profile
        if "--user-data-dir=" in cmd and "pw_profile" in cmd and hl in cmd:
            pid = (p or {}).get("ProcessId")
            if pid:
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                                   capture_output=True, text=True, timeout=10)
                    killed += 1
                except Exception:
                    pass
    return killed


def clear_locks():
    n = 0
    for d in _profiles():
        for lk in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                os.remove(os.path.join(d, lk))
                n += 1
            except Exception:
                pass
    return n


def clear_new():
    n = 0
    for f in glob.glob(os.path.join(HERE, "*.new")):
        try:
            os.remove(f)
            n += 1
        except Exception:
            pass
    return n


def main():
    print("FIX — clearing stuck Chrome + profile locks for THIS bot...\n")
    k = kill_chrome()
    l = clear_locks()
    nf = clear_new()
    print(f"  closed  {k} stuck Chrome window(s)")
    print(f"  cleared {l} profile lock file(s)")
    print(f"  removed {nf} leftover update file(s)")
    print("\nDONE.  Ab START.bat dobara chalao.")


if __name__ == "__main__":
    main()
