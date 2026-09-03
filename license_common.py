# ============================================================
# license_common.py  —  License key ka SHARED code
#
# Ye file DONO jagah use hoti hai:
#   - admin_tool.py  (owner ke paas)  -> key banata hai (private key ke sath)
#   - fb_joiner.py   (har employee)   -> key verify karta hai (public key se)
#
# Koi external library nahi chahiye — sirf Python standard library.
# ============================================================

import base64
import hashlib
import json
import os
import platform
import secrets
import sys
import time
import uuid
from datetime import date, datetime, timedelta


def _base_dir() -> str:
    """
    Files ke liye ek STABLE folder — dev mein script ka folder, aur
    PyInstaller .exe mein exe ka folder (temp extract dir nahi, warna
    license.key har band hone par gum ho jati).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# ── PUBLIC KEY ──────────────────────────────────────────────
# admin_tool.py "Generate Keypair" dabane par ye do lines KHUD update
# karta hai. Jab tak dono 0 hain, license system "not initialised" hai.
# === LICENSE_PUBKEY_BEGIN (auto-managed by admin_tool.py) ===
LICENSE_PUBKEY_N = 14822463346784765426066219834840202607199910216884837969119783755459470610813750785069028905088290960243084932134413632779414990087077745996732322348424993481223442406044896303317767556498509304303591139459130877181102555808506633630609794770577213105740160968524601829135541663749409037165133371422855127053412859072118978467157552725982730475496315004031672846221842819115992871698339935283012763877618593219841519659087485802007410083310104746629144260522321350887689332670317899671542873808881894049168816701329110024269943533825204397098287419242704661695204036805461333019413997255979922422680844686769895424173
LICENSE_PUBKEY_E = 65537
REVOCATION_URL = ""
REPORT_URL = "https://discord.com/api/webhooks/1544446002969182341/0HFuVUBjqdeFeHJIEoyzmbJ4oqkf5Cz96eWCriO8CT3wdLGXYS6xmP1SapAuw_MDoSuN"
UPDATE_URL = ""
# === LICENSE_PUBKEY_END ===

KEY_PREFIX = "FBJ1"
REVO_PREFIX = "REVO1"

# License state file — anti clock-rollback ke liye aakhri dekhi hui date
_STATE_FILE = os.path.join(_base_dir(), ".lic_state")
# Employee ke PC pe verified key yahan save hoti hai (dubara paste na karna pare)
ACTIVE_KEY_FILE = os.path.join(_base_dir(), "license.key")
# Admin ki bheji hui signed revocation list (jo keys suspend hui hain)
REVOCATION_FILE = os.path.join(_base_dir(), "revocation.json")
_REVO_CACHE = os.path.join(_base_dir(), ".revocation_cache")


# ── base64url helpers ──────────────────────────────────────
def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ── RSA (pure python) ──────────────────────────────────────
# Sign  : s = H^d mod n         (H = sha256(payload) as int)
# Verify: H == s^e mod n
_SMALL_PRIMES = [
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67,
    71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149,
    151, 157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229,
]


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = 2 + secrets.randbelow(n - 3)
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int) -> int:
    while True:
        cand = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(cand):
            return cand


def generate_keypair(bits: int = 2048) -> dict:
    """Owner ke liye — one time. {n, e, d} return karta hai."""
    e = 65537
    while True:
        p = _gen_prime(bits // 2)
        q = _gen_prime(bits // 2)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        if phi % e == 0:
            continue
        n = p * q
        try:
            d = pow(e, -1, phi)
        except ValueError:
            continue
        return {"n": n, "e": e, "d": d, "bits": bits}


def _hash_int(payload_b64: str) -> int:
    return int.from_bytes(hashlib.sha256(payload_b64.encode()).digest(), "big")


def _sign(payload_b64: str, priv: dict) -> str:
    s = pow(_hash_int(payload_b64), priv["d"], priv["n"])
    size = (priv["n"].bit_length() + 7) // 8
    return _b64u(s.to_bytes(size, "big"))


def _verify(payload_b64: str, sig_b64: str, n: int, e: int) -> bool:
    if not n:
        return False
    try:
        s = int.from_bytes(_b64u_dec(sig_b64), "big")
        return pow(s, e, n) == _hash_int(payload_b64)
    except Exception:
        return False


# ── Machine ID ─────────────────────────────────────────────
def machine_id() -> str:
    """Is PC ka chhota fingerprint — machine-locked keys ke liye."""
    raw = f"{uuid.getnode()}|{platform.node()}|{platform.machine()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ── Date/time helpers ─────────────────────────────────────
def _parse_dt(s: str, end_of_day: bool = False) -> datetime:
    """
    Accept 'YYYY-MM-DD' or full ISO 'YYYY-MM-DDTHH:MM:SS'.
    Date-only -> 23:59:59 if end_of_day else 00:00:00.
    """
    s = (s or "").strip().replace(" ", "T")
    if len(s) == 10 and "T" not in s:          # date only
        d = date.fromisoformat(s)
        return datetime(d.year, d.month, d.day, 23, 59, 59) if end_of_day \
            else datetime(d.year, d.month, d.day)
    return datetime.fromisoformat(s)


def _fmt_left(seconds: float) -> str:
    """3660 -> '1h 1m', 200000 -> '2 days', -5 -> 'expired'."""
    if seconds <= 0:
        return "expired"
    s = int(seconds)
    if s >= 2 * 86400:
        return f"{s // 86400} days"
    if s >= 86400:
        return "1 day"
    h, rem = divmod(s, 3600)
    m = rem // 60
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{s}s"


# ── Key banao / parho ──────────────────────────────────────
def make_key(priv: dict, employee: str, days: int = 0, hours: float = 0,
             exp: str = "", machine: str = "") -> str:
    """
    Owner side. Give ONE of:
      exp   : 'YYYY-MM-DD' or full ISO 'YYYY-MM-DDTHH:MM:SS'
      hours : valid for N hours from now (supports fractions)
      days  : valid for N days from now
    `machine` empty = runs on any PC; otherwise only that machine-id.
    """
    now = datetime.now().replace(microsecond=0)
    if exp:
        exp_dt = _parse_dt(exp, end_of_day=True)
    elif hours:
        exp_dt = now + timedelta(hours=float(hours))
    elif days:
        # end of the day, N days from today -> a "30 day" key reads "30 days"
        end = date.today() + timedelta(days=int(days))
        exp_dt = datetime(end.year, end.month, end.day, 23, 59, 59)
    else:
        raise ValueError("Provide one of: 'exp', 'hours' or 'days'")

    payload = {
        "v": 1,
        "emp": employee.strip(),
        "kid": "K-" + now.strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper(),
        "iat": now.isoformat(timespec="seconds"),
        "exp": exp_dt.replace(microsecond=0).isoformat(timespec="seconds"),
        "mid": machine.strip(),
    }
    p_b64 = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _sign(p_b64, priv)
    return f"{KEY_PREFIX}.{p_b64}.{sig}"


def _parse(key_str: str) -> dict:
    parts = (key_str or "").strip().split(".")
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        raise ValueError("Key format is not recognised")
    payload = json.loads(_b64u_dec(parts[1]).decode())
    return {"payload_b64": parts[1], "sig": parts[2], "payload": payload}


# ── Revocation (key suspend) ───────────────────────────────
def make_revocation(priv: dict, kids, note: str = "") -> str:
    """Owner side. Signed list of suspended key-IDs. Returns 'REVO1.<p>.<sig>'."""
    payload = {
        "v": 1,
        "revoked": sorted({str(k).strip() for k in kids if str(k).strip()}),
        "generated": datetime.now().replace(microsecond=0).isoformat(),
        "note": note,
    }
    p_b64 = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{REVO_PREFIX}.{p_b64}.{_sign(p_b64, priv)}"


def _parse_revocation(text: str):
    """Signature verify karo — theek ho to payload dict, warna None."""
    parts = (text or "").strip().split(".")
    if len(parts) != 3 or parts[0] != REVO_PREFIX:
        return None
    if not _verify(parts[1], parts[2], LICENSE_PUBKEY_N, LICENSE_PUBKEY_E):
        return None
    try:
        return json.loads(_b64u_dec(parts[1]).decode())
    except Exception:
        return None


def _fetch_url(url: str, timeout: int = 5) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "fb-joiner-lic/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(262_144).decode("utf-8", "replace")


def revoked_kids(use_url: bool = True) -> set:
    """
    Suspend ki gayi key-IDs ka set. Do source:
      1. local revocation.json  (admin ne bheji / shared folder)
      2. REVOCATION_URL          (agar set ho) + net fail par cached copy
    Sirf SIGNED (genuine) list count hoti hai.
    """
    kids = set()
    try:
        d = _parse_revocation(open(REVOCATION_FILE, encoding="utf-8").read())
        if d:
            kids |= set(d.get("revoked", []))
    except Exception:
        pass

    if use_url and REVOCATION_URL:
        d = None
        try:
            text = _fetch_url(REVOCATION_URL)
            d = _parse_revocation(text)
            if d is not None:
                try:
                    with open(_REVO_CACHE, "w", encoding="utf-8") as f:
                        f.write(text)
                except Exception:
                    pass
        except Exception:
            d = None
        if d is None:  # net down / bad response -> last good copy
            try:
                d = _parse_revocation(open(_REVO_CACHE, encoding="utf-8").read())
            except Exception:
                d = None
        if d:
            kids |= set(d.get("revoked", []))
    return kids


# ── Anti clock-rollback state ──────────────────────────────
def _load_state() -> dict:
    try:
        raw = _b64u_dec(open(_STATE_FILE).read().strip())
        return json.loads(raw.decode())
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        data = _b64u(json.dumps(st, separators=(",", ":")).encode())
        with open(_STATE_FILE, "w") as f:
            f.write(data)
    except Exception:
        pass


# ── VALIDATE — bot har baar yahi call karta hai ────────────
def validate_key(key_str: str, enforce_machine: bool = True,
                 check_url: bool = True) -> dict:
    """
    return dict:
      ok        : True/False
      error     : reason (ok=False par)
      employee  : naam
      kid, iat, exp, mid
      days_left : aaj se expiry tak din (int, -ve = expired)
    """
    out = {"ok": False, "error": "", "employee": "", "kid": "", "iat": "",
           "exp": "", "mid": "", "days_left": 0, "secs_left": 0, "time_left": ""}

    if not LICENSE_PUBKEY_N:
        out["error"] = "License system not initialised (admin must run 'Generate Keypair')"
        return out

    try:
        parsed = _parse(key_str)
    except Exception as e:
        out["error"] = str(e)
        return out

    pl = parsed["payload"]
    out.update(employee=pl.get("emp", ""), kid=pl.get("kid", ""),
               iat=pl.get("iat", ""), exp=pl.get("exp", ""), mid=pl.get("mid", ""))

    if not _verify(parsed["payload_b64"], parsed["sig"], LICENSE_PUBKEY_N, LICENSE_PUBKEY_E):
        out["error"] = "Key is not genuine (signature check failed)"
        return out

    try:
        exp_dt = _parse_dt(pl["exp"], end_of_day=True)
        iat_dt = _parse_dt(pl["iat"], end_of_day=False)
    except Exception:
        out["error"] = "Key has invalid dates"
        return out

    now = datetime.now()

    # Clock rollback check — have we ever seen a later time than now?
    st = _load_state()
    last_seen = st.get("last_seen", "")
    if last_seen:
        try:
            if now < _parse_dt(last_seen) - timedelta(hours=2):
                out["error"] = ("System clock appears to have been set back "
                                f"(last seen: {last_seen}). Set the correct date/time.")
                return out
        except Exception:
            pass

    if now < iat_dt:
        out["error"] = f"Key is not valid yet (starts {pl['iat']})"
        return out

    secs = (exp_dt - now).total_seconds()
    out["secs_left"] = int(secs)
    out["days_left"] = int(secs // 86400)
    out["time_left"] = _fmt_left(secs)
    if secs <= 0:
        out["error"] = f"License expired ({pl['exp']})"
        return out

    if enforce_machine and pl.get("mid"):
        if pl["mid"] != machine_id():
            out["error"] = "This key was issued for a different PC"
            return out

    # Suspended by the administrator? (signed revocation list)
    try:
        if pl.get("kid") in revoked_kids(use_url=check_url):
            out["error"] = "License suspended by the administrator"
            return out
    except Exception:
        pass

    # All good — update state (latest wall-clock time we have trusted)
    now_iso = now.replace(microsecond=0).isoformat(timespec="seconds")
    if not last_seen or now_iso > last_seen:
        st["last_seen"] = now_iso
        st["kid"] = pl.get("kid", "")
        _save_state(st)

    out["ok"] = True
    return out


# ── Employee ke PC pe active key save/load ─────────────────
def save_active_key(key_str: str) -> None:
    with open(ACTIVE_KEY_FILE, "w") as f:
        f.write(key_str.strip())


def load_active_key() -> str:
    try:
        return open(ACTIVE_KEY_FILE).read().strip()
    except Exception:
        return ""


def clear_active_key() -> None:
    try:
        os.remove(ACTIVE_KEY_FILE)
    except OSError:
        pass
