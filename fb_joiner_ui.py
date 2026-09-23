#!/usr/bin/env python3
# ============================================================
# fb_joiner_ui.py  —  NAYA web-style dashboard (design jaisa)
#
# Purana tkinter UI (fb_joiner.py) jaisa hai waisa rehta hai — backup.
# Ye file usi backend ko reuse karti hai (import fb_joiner) aur ek
# modern web UI deti hai:
#   - ek chhota local server (Python stdlib) UI + JSON endpoints serve karta hai
#   - Playwright ka jo Chromium pehle se install hai, usi ko "app window"
#     bana kar UI us mein khulta hai (na browser tab, na address bar)
#   - koi nayi library install nahi
#
# Chalao:  py fb_joiner_ui.py         (profile 1)
#          py fb_joiner_ui.py 3       (profile 3)
# ============================================================

import glob
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import fb_joiner as B          # backend — import karne se tkinter launch NAHI hota
import license_common as lic

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "ui_page.html")

# ── shared state (drain thread <-> HTTP handlers) ──────────
_LK = threading.Lock()
STATE = {
    "running": False, "busy": False,
    "today": 0, "skipped": 0, "joined_run": 0, "failed": 0,
    "area": "", "search": "", "run_start": None,
}
_ROWS = []          # {id,time,status,msg,tag}
_ROW_ID = [0]
_SETTINGS_SENT = [False]


def _classify(text):
    t = (text or "").strip(); low = t.lower()
    if ("\u2705" in t or "\U0001f389" in t or "joined:" in low or t.startswith("\u2611")):
        return "ok", "SUCCESS"
    if any(k in low for k in ("account block", "account checkpoint", "crash",
                              "could not", "failed", "error")) or "\u26d4" in t or "\U0001f6ab" in t or "\u274c" in t:
        return "fail", "FAILED"
    if ("\u23ed" in t or "\u2696" in t or "\U0001f4a4" in t or "\U0001f341" in t
            or any(k in low for k in ("skip", "blocked keyword", "quota",
                                      "already member", "wrong state", "low "))):
        return "skip", "SKIPPED"
    return "info", ""


def _clean_msg(t):
    for e in ("\u2705", "\U0001f389", "\u23ed\ufe0f", "\u23ed", "\u2696\ufe0f",
              "\U0001f4a4", "\U0001f341", "\u26d4", "\U0001f6ab", "\u274c",
              "\u2611\ufe0f", "\u2611", "\U0001f50d", "\U0001f4cd", "\U0001f5fa\ufe0f",
              "\U0001f5fa", "\u26a0\ufe0f", "\u2139\ufe0f", "\U0001f501", "\U0001f6e1",
              "\U0001f4ca", "\U0001f3af", "\U0001f465", "\u2699", "\u26a1", "\U0001f4d8",
              "\U0001f504", "\u23f3", "\U0001f550"):
        t = t.replace(e, "")
    return t.strip(" \u2014-:\u00b7\t")


def _add_row(text):
    tag, status = _classify(text)
    msg = _clean_msg(text)
    if not msg:
        return
    with _LK:
        _ROW_ID[0] += 1
        _ROWS.append({"id": _ROW_ID[0],
                      "time": datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
                      "status": status, "msg": msg[:220], "tag": tag})
        if tag == "fail":
            STATE["failed"] += 1
        if len(_ROWS) > 400:
            del _ROWS[:len(_ROWS) - 400]


def _drain_loop():
    """Backend ke ui_queue se messages nikaal kar STATE update karo."""
    while True:
        try:
            msg = B.ui_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        except Exception:
            time.sleep(0.3); continue
        t = msg.get("type")
        try:
            if t == "log":
                _add_row(msg.get("text", ""))
            elif t == "area":
                with _LK: STATE["area"] = msg.get("text", "")
            elif t == "target":
                with _LK: STATE["search"] = "\U0001f50d " + msg.get("text", "")
            elif t == "joined":
                with _LK:
                    STATE["today"] = msg.get("count", STATE["today"])
                    STATE["joined_run"] += 1
            elif t == "skipped":
                with _LK: STATE["skipped"] += 1
            elif t in ("login_done", "logout_done", "preflight_done", "autopost_done"):
                with _LK: STATE["busy"] = False
            elif t == "stopped":
                with _LK:
                    STATE["running"] = False; STATE["run_start"] = None
                    STATE["search"] = ""
        except Exception:
            pass


# ── license / settings helpers ─────────────────────────────
_RENEW_T = [0.0]     # auto-renew (network) ki aakhri koshish ka waqt

def _lic_info():
    # Local validate SASTA hai (network nahi) — har poll par theek.
    try:
        info = lic.validate_key(lic.load_active_key(), check_url=False)
    except Exception:
        info = {"ok": False, "error": "no key"}
    # Auto-renew GitHub se keys.json laata hai (network) — isay har 0.7s
    # poll par NAHI, sirf har 5 min chalao, aur tabhi jab key expire ho
    # rahi ho. (Bug tha: har poll par network hit ho raha tha.)
    now = time.time()
    if now - _RENEW_T[0] > 300:
        _RENEW_T[0] = now
        try:
            if not info.get("ok") or int(info.get("days_left", 99) or 0) <= B.LICENSE_WARN_DAYS:
                if B.auto_renew_license(info.get("employee", "")):
                    info = lic.validate_key(lic.load_active_key(), check_url=False)
        except Exception:
            pass
    return info


def _current_settings():
    s = B.load_settings()
    suf = B.SUFFIX
    mode = (s.get(f"business_mode{suf}") or s.get("business_mode") or "car").lower()
    return {
        "business_mode": mode,
        "areas": [B.ALL_AREAS_LABEL] + list(B.areas_for(mode)),
        "city": s.get(f"city{suf}") or s.get("city") or B.ALL_AREAS_LABEL,
        "page_link": s.get(f"page_link{suf}") or s.get("page_link") or "",
        "min_members_public": int(s.get("min_members_public", B.DEFAULT_MIN_MEMBERS) or 0),
        "min_members_private": int(s.get("min_members_private", B.DEFAULT_MIN_MEMBERS) or 0),
        "delay_min": int(s.get("delay_min", 30) or 30),
        "delay_max": int(s.get("delay_max", 60) or 60),
        "public_pct": int(s.get("public_pct", 30) or 0),
        "skip_no_post": bool(s.get("skip_no_post", True)),
        "same_state_only": bool(s.get("same_state_only", True)),
        "include_counties": bool(s.get("include_counties", True)),
        "safe_mode": bool(s.get("safe_mode", False)),
        "search_keywords": s.get("search_keywords") or B.default_search_keyword_lines(),
        "block_keywords": s.get("block_keywords") or B.resolve_block_keywords(),
        "daily_limit": B.DAILY_LIMIT,
    }


def _build_config(d, info):
    kw_lines = [x for x in (d.get("search_keywords", "") or "").splitlines() if x.strip()]
    blk = [x.strip() for x in (d.get("block_keywords", "") or "").splitlines() if x.strip()]
    pub = max(0, min(100, int(d.get("public_pct", 30) or 0)))
    mode = (d.get("business_mode") or "car").lower()
    safe = bool(d.get("safe_mode"))
    city = d.get("city") or B.ALL_AREAS_LABEL
    if safe:
        mode = "duct"; pub = 0; city = B.ALL_AREAS_LABEL
    cfg = {
        "city": city, "page_name": B.DEFAULT_PAGE_NAME,
        "page_link": (d.get("page_link") or "").strip(),
        "daily_limit": B.DAILY_LIMIT,
        "delay_min": int(d.get("delay_min", 30) or 30),
        "delay_max": int(d.get("delay_max", 60) or 60),
        "employee": info.get("employee", "") or "unknown",
        "license_key": lic.load_active_key(),
        "license_exp": info.get("exp", ""), "key_id": info.get("kid", ""),
        "gemini_keys": B.resolve_gemini_keys(""),
        "public_pct": pub,
        "min_members": int(d.get("min_members_public", B.DEFAULT_MIN_MEMBERS) or 0),
        "min_members_public": int(d.get("min_members_public", B.DEFAULT_MIN_MEMBERS) or 0),
        "min_members_private": int(d.get("min_members_private", B.DEFAULT_MIN_MEMBERS) or 0),
        "skip_no_post": bool(d.get("skip_no_post", True)),
        "same_state_only": bool(d.get("same_state_only", True)),
        "include_counties": bool(d.get("include_counties", True)),
        "custom_blocked": blk,
        "search_keywords": kw_lines,
        "_search_templates": B.resolve_search_keywords("\n".join(kw_lines)),
        "business_mode": mode, "safe_mode": safe,
    }
    return cfg


def _persist(d):
    suf = B.SUFFIX
    try:
        B.update_settings({
            f"page_link{suf}": (d.get("page_link") or "").strip(),
            f"city{suf}": d.get("city") or B.ALL_AREAS_LABEL,
            f"business_mode{suf}": (d.get("business_mode") or "car").lower(),
            "public_pct": max(0, min(100, int(d.get("public_pct", 30) or 0))),
            "min_members_public": int(d.get("min_members_public", B.DEFAULT_MIN_MEMBERS) or 0),
            "min_members_private": int(d.get("min_members_private", B.DEFAULT_MIN_MEMBERS) or 0),
            "delay_min": int(d.get("delay_min", 30) or 30),
            "delay_max": int(d.get("delay_max", 60) or 60),
            "skip_no_post": bool(d.get("skip_no_post", True)),
            "same_state_only": bool(d.get("same_state_only", True)),
            "include_counties": bool(d.get("include_counties", True)),
            "safe_mode": bool(d.get("safe_mode")),
            "block_keywords": [x.strip() for x in (d.get("block_keywords") or "").splitlines() if x.strip()],
            "search_keywords": [x for x in (d.get("search_keywords") or "").splitlines() if x.strip()],
        })
    except Exception:
        pass


# ── HTTP server ────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass

    def _json(self, obj):
        self._send(200, json.dumps(obj))

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/" or u.path == "/index.html":
            try:
                with open(PAGE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, "UI page missing: " + str(e), "text/plain")
        elif u.path == "/api/state":
            self._json(self._state(int((q.get("since") or ["0"])[0])))
        elif u.path == "/api/areas":
            mode = (q.get("mode") or ["car"])[0].lower()
            self._json({"areas": [B.ALL_AREAS_LABEL] + list(B.areas_for(mode)),
                        "city": B.ALL_AREAS_LABEL})
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path); d = self._body()
        if u.path == "/api/start":
            self._json(self._start(d))
        elif u.path == "/api/stop":
            B.user_stop_event.set(); B.stop_event.set()
            with _LK: STATE["running"] = False
            self._json({"ok": True})
        elif u.path == "/api/login":
            self._json(self._simple(B.run_login_browser, "Opening browser — log in, then close it."))
        elif u.path == "/api/logout":
            self._json(self._simple(B.run_logout_browser, "Logging out of Facebook…"))
        elif u.path == "/api/preflight":
            info = _lic_info()
            if not info.get("ok"):
                self._json({"msg": "Activate a license first."}); return
            cfg = _build_config(d, info)
            self._json(self._simple(lambda: B.run_preflight(cfg), "Running pre-flight check…"))
        elif u.path == "/api/autopost":
            info = _lic_info()
            if not (info.get("ok") and info.get("admin")):
                self._json({"msg": "🔒 Auto-post is admin-only."}); return
            self._json({"msg": "Auto-post: use the desktop app for now."})
        elif u.path == "/api/activate":
            self._json(self._activate(d))
        elif u.path == "/api/save":
            _persist(d); self._json({"ok": True})
        else:
            self._send(404, "not found", "text/plain")

    # ---- actions ----
    def _start(self, d):
        info = _lic_info()
        if not info.get("ok"):
            return {"error": info.get("error") or "License required"}
        if (d.get("business_mode") or "").lower() == "garage" and not info.get("admin"):
            return {"error": "\U0001f512 Garage mode is admin-only."}
        if not (d.get("page_link") or "").strip():
            return {"error": "Enter the Facebook Page link first."}
        if not d.get("safe_mode") and not (d.get("city") or "").strip():
            return {"error": "Pick an area first."}
        _persist(d)
        cfg = _build_config(d, info)
        B.stop_event.clear(); B.user_stop_event.clear()
        try:
            B._GEMINI_DOWN.clear()
        except Exception:
            pass
        with _LK:
            STATE.update(running=True, run_start=time.time(),
                         joined_run=0, skipped=0, failed=0, search="", area="")
        threading.Thread(target=B.run_playwright, args=(cfg,), daemon=True).start()
        return {"ok": True}

    def _simple(self, fn, msg):
        with _LK: STATE["busy"] = True
        threading.Thread(target=fn, daemon=True).start()
        return {"msg": msg}

    def _activate(self, d):
        key = (d.get("key") or "").strip()
        if not key:
            return {"ok": False, "error": "Paste a key first."}
        info = lic.validate_key(key)
        if not info.get("ok"):
            return {"ok": False, "error": info.get("error") or "Invalid key."}
        lic.save_active_key(key)
        _SETTINGS_SENT[0] = False
        return {"ok": True, "employee": info.get("employee", "")}

    def _state(self, since):
        info = _lic_info()
        with _LK:
            lim = B.DAILY_LIMIT
            pct = min(100, round(STATE["today"] / lim * 100)) if lim else 0
            rows = [r for r in _ROWS if r["id"] > since][-120:]
            rs = STATE["run_start"]
            meta = ""
            if rs and STATE["running"]:
                sec = int(time.time() - rs); h, m, s2 = sec // 3600, (sec % 3600) // 60, sec % 60
                meta = f"Runtime: {h:02d}:{m:02d}:{s2:02d}   ·   Today's total: {STATE['today']} / {lim}"
            else:
                meta = f"Today's total: {STATE['today']} / {lim}"
            out = {
                "running": STATE["running"], "busy": STATE["busy"],
                "account": B.INSTANCE, "version": B.APP_VERSION,
                "stats": {"today": STATE["today"], "limit": lim, "pct": pct,
                          "skipped": STATE["skipped"], "joined_run": STATE["joined_run"],
                          "failed": STATE["failed"]},
                "area": (("📍 " + STATE["area"]) if STATE["area"] else ""),
                "search": STATE["search"], "footmeta": meta,
                "rows": rows,
                "lic": {"ok": bool(info.get("ok")), "employee": info.get("employee", ""),
                        "exp": info.get("exp", ""), "days_left": int(info.get("days_left", 0) or 0),
                        "admin": bool(info.get("admin")), "mid": (lic.machine_id() or "")[:12],
                        "error": info.get("error", "")},
            }
        if not _SETTINGS_SENT[0]:
            out["settings"] = _current_settings()
            _SETTINGS_SENT[0] = True
        return out


def _launch_window(url):
    """Playwright ka installed Chromium 'app mode' mein — native window jaisa."""
    exes = glob.glob(os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "ms-playwright", "chromium-*", "chrome-win*", "chrome.exe"))
    prof = os.path.join(HERE, f".ui_win{B.SUFFIX}")
    args = ["--app=" + url, "--user-data-dir=" + prof,
            "--window-size=1330,880", "--no-first-run", "--no-default-browser-check"]
    if exes:
        return subprocess.Popen([exes[0]] + args)
    # fallback: system Chrome / default browser
    for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(c):
            return subprocess.Popen([c] + args)
    import webbrowser
    webbrowser.open(url)
    return None


def _self_update():
    """GitHub se naya version aaya ho to laga lo, phir process dobara chalao.
    (Purane tkinter app ki tarah — web UI mein bhi zaroori, warna employees
    ko aage updates milna band ho jate.)"""
    if getattr(sys, "frozen", False) or "--no-update" in sys.argv:
        return
    try:
        import updater
        if updater.check_and_apply(getattr(lic, "UPDATE_URL", ""), HERE):
            # files badal gayin — ye process purane code par hai, dobara chalo
            subprocess.Popen([sys.executable] + sys.argv, cwd=HERE, close_fds=True)
            sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass


def main():
    _self_update()
    threading.Thread(target=_drain_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"[ui] NexFour dashboard  ->  {url}")
    win = _launch_window(url)
    try:
        if win:
            win.wait()          # window band -> app exit
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        B.stop_event.set()


if __name__ == "__main__":
    main()
