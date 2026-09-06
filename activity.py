# ============================================================
# activity.py  —  Har employee ke bot ka usage record
#
# Ye file `usage/usage_<employee>.json` banati/update karti hai (bot ke
# apne folder mein, offline backup / history ke liye — admin_tool.py ka
# Dashboard tab isse padh sakta hai agar folder copy kiya jaye).
#
# LIVE REPORTING (koi file copy nahi karni): agar license_common.py mein
# REPORT_URL set hai (admin ek baar sab bots ke liye deploy se pehle
# set karta hai), to har start/join/skip/heartbeat/stop par bot khud
# ek chhota status update us URL (Google Apps Script Web App) ko bhej
# deta hai — background thread mein, kabhi bhi automation ko block
# nahi karta, net na ho to bhi joining chalti rehti hai.
#
# Koi external library nahi.
# ============================================================

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


_DIR = os.path.join(_base_dir(), "usage")


def _safe_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_\- ]+", "", (name or "unknown")).strip().replace(" ", "_")
    return s or "unknown"


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _report_url() -> str:
    try:
        import license_common as lic
        return (getattr(lic, "REPORT_URL", "") or "").strip()
    except Exception:
        return ""


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def _send_chat_text(text: str, sync: bool = False) -> None:
    """Ek raw text Discord/Telegram pe bhejo — ActivityLog instance ke
    BINA (module-level). Startup tamper-check jaise cases ke liye, jahan
    abhi employee ka ActivityLog bana hi nahi hota.

    sync=True: background thread ke bajaye isi thread mein bhejo aur
    wapas aane tak wait karo (timeout tak) — jab caller alert ke turant
    baad process restart/exit karne wala ho, taake message poora jaane
    se pehle hi process na mar jaye."""
    url = _report_url()
    if not url:
        return
    low = url.lower()
    is_discord = "discord.com/api/webhooks" in low or "discordapp.com/api/webhooks" in low
    is_tg = "api.telegram.org/bot" in low
    if is_discord:
        target = url
        body = json.dumps({"content": text[:1900]}).encode("utf-8")
    elif is_tg:
        sep = "&" if "?" in url else "?"
        target = f"{url}{sep}text={urllib.parse.quote(text)}&parse_mode=Markdown"
        body = None
    else:
        return

    def work():
        try:
            hdrs = {"User-Agent": _UA}
            if body is None:
                req = urllib.request.Request(target, headers=hdrs)
            else:
                hdrs["Content-Type"] = "application/json"
                req = urllib.request.Request(target, data=body, headers=hdrs)
            urllib.request.urlopen(req, timeout=8).read()
        except Exception:
            pass

    if sync:
        work()
    else:
        threading.Thread(target=work, daemon=True).start()


def send_alert(employee: str, text: str, sync: bool = False) -> None:
    """Module-level alert — ActivityLog ke bina bhi (startup/updater
    tamper-detection ke liye)."""
    _send_chat_text(f"🚨  **{employee or 'unknown'}**\n{text}", sync=sync)


class ActivityLog:
    """
    Bot ke andar ek instance banta hai. Thread-safe (UI thread se bhi
    call ho sakta hai, playwright thread se bhi).
    """

    def __init__(self, employee: str, key_id: str = "", license_exp: str = "",
                 instance: str = "1"):
        self.employee = employee or "unknown"
        self.key_id = key_id
        self.license_exp = license_exp
        self.instance = str(instance)
        self.page_link = ""
        self._lock = threading.Lock()
        os.makedirs(_DIR, exist_ok=True)
        # Har profile (instance) ki alag file — warna 2 processes ek hi
        # file par likh kar ek-doosre ka data mita dete.
        suffix = "" if self.instance == "1" else f"_{self.instance}"
        self.path = os.path.join(
            _DIR, f"usage_{_safe_name(self.employee)}{suffix}.json")
        self._data = self._load()
        self._session = None  # abhi wali khuli session ka reference

    # ── disk ────────────────────────────────────────────────
    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}
        d.setdefault("employee", self.employee)
        d["instance"] = self.instance
        d.setdefault("key_id", self.key_id)
        d.setdefault("machine_id", "")
        d.setdefault("first_seen", _now())
        d.setdefault("last_heartbeat", _now())
        d.setdefault("license_exp", self.license_exp)
        d.setdefault("daily", {})       # {"2026-08-31": 42}
        d.setdefault("daily_skips", {}) # {"2026-08-31": {"low_members": 12, ...}}
        d.setdefault("sessions", [])    # [{start, stop, joined, skipped, reason, instance}]
        d.setdefault("totals", {"joined": 0, "skipped": 0})
        return d

    def _save(self) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ── live push (background, non-blocking, best-effort) ──
    def _running(self) -> bool:
        return any(s.get("stop") is None for s in self._data.get("sessions", []))

    def _summary(self) -> dict:
        today = date.today().isoformat()
        return {
            "employee": self.employee,
            "instance": self.instance,
            "key_id": self.key_id,
            "machine_id": self._data.get("machine_id", ""),
            "page_link": self.page_link,
            "license_exp": self.license_exp,
            "running": self._running(),
            "joined_today": self._data.get("daily", {}).get(today, 0),
            "joined_total": self._data.get("totals", {}).get("joined", 0),
            "skipped_total": self._data.get("totals", {}).get("skipped", 0),
            "last_update": _now(),
        }

    def _status_line(self) -> str:
        s = self._summary()
        icon = "🟢 RUNNING" if s["running"] else "⚪ stopped"
        return (f"**{s['employee']}** (profile {s['instance']})  {icon}\n"
                f"page: {s['page_link'] or '—'}\n"
                f"joined today: {s['joined_today']}   ·   total: {s['joined_total']}   ·   "
                f"skipped: {s['skipped_total']}\n"
                f"license expires: {s['license_exp'] or '—'}   ·   {s['last_update']}")

    def _push(self, event: str = "heartbeat") -> None:
        """
        event: start | stop | join | skip | heartbeat
        URL kis tarah ki hai uske hisaab se bhejta hai:
          - Discord webhook  -> ek line ka message (start/stop pe + har 15 min)
          - Telegram bot API -> ek line ka message (start/stop pe + har 15 min)
          - koi aur URL       -> poora JSON (Google Sheet / custom) har event pe
        """
        url = _report_url()
        if not url:
            return
        low = url.lower()
        is_discord = "discord.com/api/webhooks" in low or "discordapp.com/api/webhooks" in low
        is_tg = "api.telegram.org/bot" in low

        if is_discord or is_tg:
            # chat channel — spam se bacho: sirf start/stop + har ~15 min
            if event not in ("start", "stop"):
                if time.time() - getattr(self, "_last_chat", 0) < 900:
                    return
            self._last_chat = time.time()
            head = {"start": "▶️  STARTED\n", "stop": "⏹️  STOPPED\n"}.get(event, "")
            text = head + self._status_line()
            if is_discord:
                target, body = url, json.dumps({"content": text[:1900]}).encode("utf-8")
            else:
                sep = "&" if "?" in url else "?"
                target = f"{url}{sep}text={urllib.parse.quote(text)}&parse_mode=Markdown"
                body = None
        else:
            target = url
            body = json.dumps(self._summary()).encode("utf-8")

        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")

        def work():
            try:
                hdrs = {"User-Agent": ua}
                if body is None:
                    req = urllib.request.Request(target, headers=hdrs)
                else:
                    hdrs["Content-Type"] = "application/json"
                    req = urllib.request.Request(target, data=body, headers=hdrs)
                urllib.request.urlopen(req, timeout=8).read()
            except Exception:
                pass  # report fail ho jaye to bhi joining nahi rukti

        threading.Thread(target=work, daemon=True).start()

    # ── public API ──────────────────────────────────────────
    def set_machine(self, mid: str) -> None:
        with self._lock:
            self._data["machine_id"] = mid
            self._save()

    def set_page_link(self, link: str) -> None:
        with self._lock:
            self.page_link = link or ""
        self._push("heartbeat")

    def joined_today(self) -> int:
        """Aaj ke din ab tak kitne group join ho chuke (persistent — bot
        restart/crash/PC-reboot ke baad bhi yehi count aage badhta hai)."""
        with self._lock:
            return int(self._data.get("daily", {}).get(date.today().isoformat(), 0))

    def start_session(self) -> None:
        with self._lock:
            self._session = {
                "start": _now(), "stop": None,
                "joined": 0, "skipped": 0,
                "reason": None, "instance": self.instance,
            }
            self._data["sessions"].append(self._session)
            self._data["last_heartbeat"] = _now()
            # purani sessions trim — sirf aakhri 300 rakho
            if len(self._data["sessions"]) > 300:
                self._data["sessions"] = self._data["sessions"][-300:]
            self._save()
        self._push("start")

    def record_join(self) -> None:
        with self._lock:
            today = date.today().isoformat()
            self._data["daily"][today] = self._data["daily"].get(today, 0) + 1
            self._data["totals"]["joined"] = self._data["totals"].get("joined", 0) + 1
            if self._session is not None:
                self._session["joined"] += 1
            self._data["last_heartbeat"] = _now()
            self._save()
        self._push("join")

    def record_skip(self, reason: str = "") -> None:
        with self._lock:
            self._data["totals"]["skipped"] = self._data["totals"].get("skipped", 0) + 1
            if self._session is not None:
                self._session["skipped"] += 1
            if reason:
                today = date.today().isoformat()
                ds = self._data.setdefault("daily_skips", {}).setdefault(today, {})
                ds[reason] = ds.get(reason, 0) + 1
            self._data["last_heartbeat"] = _now()
            self._save()
        # skip pe har baar push nahi — sirf save; heartbeat/join se update ho jayega

    def heartbeat(self) -> None:
        with self._lock:
            self._data["last_heartbeat"] = _now()
            self._save()
        self._push("heartbeat")

    def alert(self, text: str) -> None:
        """Foran (throttle bypass) Discord/Telegram pe ek alert bhejo —
        checkpoint / pending-limit / crash jaise cases ke liye."""
        self._last_chat = 0.0
        self._push_text("🚨  " + text + "\n" + self._status_line())

    def daily_summary_text(self) -> str:
        today = date.today().isoformat()
        j = self._data.get("daily", {}).get(today, 0)
        sk = self._data.get("daily_skips", {}).get(today, {})
        sk_total = sum(sk.values())
        parts = ", ".join(f"{k} {v}" for k, v in
                          sorted(sk.items(), key=lambda x: -x[1])) or "—"
        return (f"📊  **{self.employee}** (profile {self.instance}) — {today}\n"
                f"Joined today: {j}   ·   Skipped: {sk_total}\n"
                f"Breakdown: {parts}\n"
                f"All-time joined: {self._data.get('totals', {}).get('joined', 0)}   ·   "
                f"license expires {self.license_exp or '—'}")

    def send_daily_summary(self) -> None:
        self._last_chat = 0.0
        self._push_text(self.daily_summary_text())

    def _push_text(self, text: str) -> None:
        """Ek raw message Discord/Telegram pe (throttle nahi). JSON endpoint
        par sirf normal _summary jata hai (text nahi)."""
        _send_chat_text(text)

    def end_session(self, reason: str = "app_closed") -> None:
        with self._lock:
            if self._session is not None and self._session.get("stop") is None:
                self._session["stop"] = _now()
                self._session["reason"] = reason
            self._data["last_heartbeat"] = _now()
            self._save()
            self._session = None
        self._push("stop")
        # din ka summary (jab employee bot band kare)
        try:
            self.send_daily_summary()
        except Exception:
            pass
