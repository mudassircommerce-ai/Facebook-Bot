# ============================================================
# updater.py  —  Bot ka self-updater
#
# Bot startup par UPDATE_URL (license_common.py mein set) se
# manifest.json fetch karta hai. Naya version mile to sirf woh
# files download karta hai jo badli hain, replace karta hai, aur
# bot khud ko restart kar leta hai.
#
# Manifest RSA-signed hota hai (license private key se) — bot use
# embedded public key se verify karta hai, warna update reject.
#
# Sirf CODE files touch hoti hain — pw_profile / license.key /
# bot_settings.json / usage / gemini_keys.txt / transfer_*.fbjkey
# ko kabhi haath nahi lagta.
#
# Koi external library nahi.
# ============================================================

import hashlib
import json
import os
import urllib.request

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Updater in files ko hi replace kar sakta hai (data/creds kabhi nahi)
UPDATABLE = {
    "fb_joiner.py", "license_common.py", "activity.py", "updater.py",
    "areas.py", "areas_cache.json", "logout_account.py",
    "block_keywords.txt", "EMPLOYEE_SETUP.txt", "READ_ME_FIRST.txt",
    "README.txt",
    "START.bat", "START_2.bat", "START_3.bat", "START_4.bat", "START_5.bat",
}


def _sha(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check_and_apply(update_url: str, base_dir: str, log=print) -> bool:
    """
    Return True agar files update hui (caller ko chahiye ke bot restart kare).
    Net na ho / URL galat ho / manifest signature fail -> chup-chaap False.
    """
    update_url = (update_url or "").strip().rstrip("/")
    if not update_url:
        return False

    try:
        man = json.loads(_get(update_url + "/manifest.json", timeout=12).decode("utf-8"))
    except Exception:
        return False   # no net / no manifest — normal chalo

    ver = str(man.get("version", ""))
    verfile = os.path.join(base_dir, ".update_ver")
    try:
        local_ver = open(verfile, encoding="utf-8").read().strip()
    except Exception:
        local_ver = ""
    if ver and ver == local_ver:
        return False   # is version par pehle se hain

    files = man.get("files", {})   # {name: sha256}

    # signature verify — files-map ko canonical JSON bana ke check
    try:
        import license_common as lic
        body = json.dumps(files, separators=(",", ":"), sort_keys=True)
        ok = lic._verify(lic._b64u(body.encode()), man.get("sig", ""),
                         lic.LICENSE_PUBKEY_N, lic.LICENSE_PUBKEY_E)
        if not ok:
            log("   [update] manifest signature invalid - ignoring")
            return False
    except Exception:
        return False

    to_get = [(n, s) for n, s in files.items()
              if n in UPDATABLE and _sha(os.path.join(base_dir, n)) != s]

    if not to_get:
        try:
            open(verfile, "w", encoding="utf-8").write(ver)   # loop se bacho
        except Exception:
            pass
        return False

    log(f"   [update] v{ver}: downloading {len(to_get)} file(s)...")
    staged = []
    try:
        for name, want in to_get:
            data = _get(update_url + "/" + name, timeout=40)
            if hashlib.sha256(data).hexdigest() != want:
                raise ValueError(f"{name} hash mismatch")
            tmp = os.path.join(base_dir, name + ".new")
            with open(tmp, "wb") as f:
                f.write(data)
            staged.append((tmp, os.path.join(base_dir, name)))
    except Exception as e:
        for tmp, _ in staged:
            try:
                os.remove(tmp)
            except OSError:
                pass
        log(f"   [update] failed ({str(e)[:60]}) - keeping current version")
        return False

    for tmp, dst in staged:
        try:
            os.replace(tmp, dst)
        except Exception:
            pass
    try:
        open(verfile, "w", encoding="utf-8").write(ver)
    except Exception:
        pass
    log(f"   [update] applied {len(staged)} file(s) -> v{ver}. Restarting...")
    return True
