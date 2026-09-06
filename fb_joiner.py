#!/usr/bin/env python3
"""
FB Group Auto Joiner — Playwright + Tkinter UI
"""

import tkinter as tk
import os
import subprocess
import sys
from tkinter import ttk, scrolledtext, filedialog, messagebox
import threading
import asyncio
import queue
import csv
import json
import random
import re
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
import pgeocode
import numpy as np
from geopy.geocoders import Nominatim
from geopy.distance import geodesic

from areas import AREAS

# ── License + Usage tracking ────────────────────────────────
# license_common.py = key verify karta hai (public key isme embedded).
# activity.py       = usage/usage_<employee>.json likhta hai (owner ke
#                     dashboard ke liye).
import license_common as lic
from activity import ActivityLog

# Har 2 min pe heartbeat + license re-check
LICENSE_RECHECK_SEC = 120

# ── Multi-Account Support ────────────────────────────────────────
# Har account apna alag browser profile (apna login) aur apni alag
# log/joined/total files use karta hai — taake ek dusre se clash na ho.
# Chalane ka tarika: `py fb_joiner.py 2` (account 2), `py fb_joiner.py 3` ...
# Kuch na do to account 1 (default, purani files ke sath) chalta hai.
INSTANCE = sys.argv[1] if len(sys.argv) > 1 else "1"
SUFFIX   = "" if INSTANCE == "1" else f"_{INSTANCE}"

# Human-facing version (UI mein dikhta hai). Andar ka auto-update abhi bhi
# .update_ver ke monotonic integer (41, 42, …) se chalta hai — usse chhedo
# mat, warna downgrade-protection toot jayegi.
APP_VERSION = "4.1"

# ── App folder ───────────────────────────────────────────────
# Sab files (browser profile, logs, screenshots, area cache) is folder
# ke andar rehti hain — dev mein script ka folder, packaged .exe mein
# exe ka folder. Isse bot kisi bhi PC pe portable rehta hai.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)   # relative log/csv files bhi yahin banein

# ── Config ────────────────────────────────────────────────────
# Credentials ki zaroorat NAHI — Chrome profile use hoga
PW_PROFILE_DIR = os.path.join(APP_DIR, f"pw_profile{SUFFIX}")

ALL_AREAS_LABEL = "🌎 ALL AREAS (loop through all 65 areas)"
AREA_CACHE_FILE = os.path.join(APP_DIR, "areas_cache.json")

# Account 1 ke liye purana default page rakha hai (backward compatible).
# Baaki accounts (2, 3, ...) mein khali rakhte hain — har account ka apna
# page naam UI mein zaroor type karna hoga.
DEFAULT_PAGE_NAME = "Edwin Junior" if INSTANCE == "1" else ""

# Page ka direct link (URL) — yeh ho toh switch isi se hota hai (name-based
# dropdown switching se zyada reliable). UI mein bhi change kar sakte ho.
DEFAULT_PAGE_LINK = ""

# In keywords wale groups KABHI join nahi karne — buy/sell/garage-sale
# type groups mein service business ka koi faida nahi hota
BLOCKED_GROUP_KEYWORDS = [
    "buy", "sell", "sale", "selling", "marketplace", "flea market",
    "swap", "trade", "free stuff", "classified", "auction",
    "rummage", "thrift", "consignment", "deals",
]

# Muzammil ki di hui "don't-join" list — keyword form. Har naye bot folder
# mein ye pehle se load hoti hai (bot_settings.json / block_keywords.txt),
# aur UI ke box mein editable hai. Group ke naam + page title + URL slug
# par substring match hota hai (case-insensitive).
DEFAULT_DONT_JOIN = [
    # buy / sell / marketplace / free
    "buy and sell", "buy sell", "buy/sell", "b/s/t", "bst", "marketplace",
    "free items", "free item", "free stuff", "free gifts", "free gift",
    "swap", "trade", "trading post", "selling items", "for sale",
    "garage sale", "yard sale", "estate sale", "rummage sale", "resale",
    "re-sale", "classified", "classifieds", "buy nothing", "flea market",
    "auction", "thrift", "consignment", "bargain",
    # jobs / business promo / leads / contractors
    "jobs", "job openings", "now hiring", "hiring", "employment", "careers",
    "gig work", "business promotion", "promote your business",
    "business networking", "network marketing", "advertise your business",
    "advertising", "self promotion", "self-promotion", "shameless plug",
    "customer leads", "lead generation", "sales leads", "referral group",
    "contractor", "contractors", "subcontractor", "sub-contractor",
    # lost & found pets
    "lost and found", "lost & found", "lost pet", "found pet", "missing pet",
    "lost dog", "lost cat", "finding pets", "pet finder", "rehome",
    # sports (any)
    "sports", "soccer", "football", "basketball", "baseball", "softball",
    "hockey", "tennis", "golf", "volleyball", "cricket", "rugby", "lacrosse",
    "wrestling", "boxing", "mma", "ufc", "cycling", "running club",
    "marathon", "crossfit", "pickleball", "bowling league",
    "fantasy football", "little league", "youth sports",
    # gaming
    "gaming", "gamers", "video game", "video games", "videogame", "xbox",
    "playstation", "nintendo", "fortnite", "minecraft", "call of duty",
    "roblox", "esports", "e-sports", "twitch", "pokemon",
    # lgbtq
    "lgbt", "lgbtq", "lgbtqia", "queer", "lesbian", "transgender",
    "non-binary", "nonbinary", "pride community", "pride month",
    "gay men", "gay community",
    # non-english (Spanish / Portuguese-Brazilian / other) — English only
    "en espanol", "en español", "espanol", "español", "solo espanol",
    "solo español", "se habla espanol", "hispanohablantes", "comunidad hispana",
    "grupo hispano", "grupo de", "grupo latino", "latinos", "latinas",
    "hispano", "hispanos", "hispana", "para hispanos", "amigos latinos",
    "portugues", "português", "em portugues", "em português", "so portugues",
    "só português", "falamos portugues", "comunidade brasileira",
    "brasileiros", "brasileiras", "amigos brasileiros", "grupo brasileiro",
    "vietnamese community", "chinese community", "grupo chino",
    # construction / housing / real estate
    "construction", "house rent", "for rent", "houses for rent",
    "apartments for rent", "rental", "rentals", "roommate", "roommates",
    "sublet", "real estate", "realtor", "realty", "homes for sale",
    "property for sale", "house for sale", "mls listings", "landlord",
    # medical
    "medicine", "medical", "pharmacy", "pharmaceutical", "doctors",
    "dentist", "dental", "clinic", "nurses", "healthcare workers",
    # vehicles
    "cars for sale", "car for sale", "used cars", "auto sales", "car sales",
    "vehicles for sale", "motorcycles for sale", "auto trader",
    # goods
    "shoes", "sneakers", "footwear", "clothing", "clothes", "apparel",
    "fashion resale", "wardrobe", "furniture", "perfume", "fragrance",
    "accessories", "jewelry for sale", "paintings", "drawings",
    "art for sale", "artists market", "arts and crafts sale",
    # food / drink / venues
    "coffee lovers", "coffee shop", "bakery", "baked goods", "home bakers",
    "food lovers", "foodies", "restaurant deals", "alcohol", "wine lovers",
    "craft beer", "bars and clubs", "nightlife", "brewery",
    # misc
    "barber", "barbershop", "library", "book club", "cat lovers", "kittens",
]

# Canada ke groups skip karne ke liye — group header mein yeh alfaz hon
# toh non-USA samjho
CANADA_MARKERS = [
    "canada", "canadian", "ontario", "british columbia", "alberta",
    "manitoba", "saskatchewan", "quebec", "nova scotia",
    "new brunswick", "newfoundland", "prince edward island",
]

# English-only policy — koi bhi group jiska naam English lage lekin content
# Spanish/Portuguese (Brazilian) mein ho, usse bhi pakadne ke liye. Ye
# alfaz English mein normally nahi aate (accented / bilkul distinctive),
# isliye 1-2 match false-positive risk kam rakhte hain, 2+ pe hi skip.
NON_ENGLISH_MARKERS = [
    "¿", "¡", "años", "gracias", "bienvenidos", "bienvenidas", "está",
    "cómo estás", "qué tal", "hola a todos", "muchas gracias",
    "buenos días", "buenas tardes", "buenas noches", "únete al grupo",
    "somos un grupo", "grupo para", "se habla español",
    "não", "então", "você", "vocês", "obrigado", "obrigada",
    "bem-vindo", "bem-vindos", "olá pessoal", "tudo bem",
    "somos uma comunidade", "grupo para todos os",
]


def detect_non_english(text: str) -> str:
    """Header text mein Spanish/Portuguese ke 2+ distinctive alfaz milen
    to us group ko non-English maano (naam English ho tab bhi)."""
    t = (text or "").lower()
    hits = sum(1 for m in NON_ENGLISH_MARKERS if m in t)
    return "non-English content" if hits >= 2 else ""

JOIN_ANSWERS = [
    "I'm a local resident looking to connect with my community and stay updated on local events and services.",
    "I live nearby and love being part of local community groups. Looking forward to connecting with neighbors!",
    "Community member here — excited to join and contribute to this local group!",
    "I'm from the local area and interested in staying connected with my community.",
    "Local resident just trying to stay connected with my neighborhood. Love finding community groups like this!",
]
BOT_ANSWERS = [
    "No, I'm a real person! I'm a local community member looking to connect with neighbors.",
    "Absolutely not! I'm a genuine local resident who enjoys being part of community groups.",
    "Nope, definitely human! Just a local who loves staying connected with my community.",
]
BOT_KEYWORDS = ["bot","human","real person","not a bot","spam","automated","robot","verify"]

# Sawaal ke hisaab se jawab — pehla matching rule jeet-ta hai.
# Pehle har sawaal pe ek hi "local resident" wala jawab chipka diya
# jata tha ("Are you a business owner?" -> "I'm a local resident" 🤦),
# ab sawaal ka text parh ke munasib jawab milta hai.
QA_RULES = [
    # NOTE (Muzammil ka hukum): business (car detailing / duct cleaning) ka
    # zikar KABHI nahi karna — kisi bhi jawab mein
    (["business owner", "own a business", "are you a business", "business name",
      "company name", "what business", "do you have a business", "business page",
      "promoting any", "promoting a business", "promote a business", "promote your",
      "advertising a business", "type of business", "represent a business"],
     ["No, I'm not here to promote anything — just a local resident looking to be part of the community.",
      "No, nothing to promote. I just want to stay connected with the local community."]),
    (["not advertise", "no selling", "not sell", "no spam", "not spam", "not promote",
      "will you please not", "promise not", "no soliciting", "not post anything for sale"],
     ["Yes, absolutely — I won't post any ads or spam. I'm just here to be part of the community.",
      "Of course, I agree. No selling or advertising from me — just here to connect with the community."]),
    (["agree to", "rules", "guidelines", "follow the", "terms"],
     ["Yes, I agree to the group rules.",
      "Yes, I have read the rules and agree to follow them."]),
    (["do you live", "live in", "are you local", "are you from", "where do you live",
      "where are you from", "your city", "what city", "what town", "zip code",
      "which area", "what area", "part of town"],
     ["Yes, I live in the local area.",
      "I'm based right here in the local area."]),
    (["how did you hear", "how did you find", "who invited", "referred", "who told you"],
     ["I found this group while searching for local community groups.",
      "I came across this group while looking for local groups in the area."]),
    (["why do you want", "why are you joining", "why would you like", "reason for joining",
      "what brings you", "purpose", "object of", "reason for request", "object of request",
      "why join", "why this group"],
     ["I want to stay updated on local events and connect with people in the community.",
      "I'd like to stay in touch with what's happening locally and be part of the community."]),
]

# Abhi kaunse city mein search ho rahi hai — "what city do you live in?"
# jaise sawaalon ke jawab mein yehi naam jata hai (search_and_join set karta hai)
CURRENT_CITY = ""

# ── Gemini (AI answers to join questions) — KEY ROTATION ────
# Bot ke paas kai Gemini API keys ho sakti hain. Jab ek key ka free-tier
# rate-limit (HTTP 429 / RESOURCE_EXHAUSTED) lag jaye, bot us key ko thodi
# der ke liye "cooldown" mein daal ke agli key pe switch kar deta hai —
# taake AI answers din bhar chalte rahen. Saari keys thak jayein to us
# sawaal ka jawab built-in template se chala jata hai (joining nahi rukti) —
# LEKIN agar Gemini AI ON kiya gaya hai (keys diye gaye hain) aur EK round
# mein SAARI keys fail/rate-limited ho jayein, to policy ye hai ke "fake"
# template answers pe chupke se chalte rehne ke bajaye bot FORAN rok do +
# Discord alert bhejo (neeche _gemini_sync dekho).
GEMINI_MODEL = "gemini-flash-lite-latest"   # verified working; auto-switches if deprecated
# Agar GEMINI_MODEL "high demand" (503) de ya deprecated (404) nikle, bot
# isi list se agla model try karta hai (SAME api key — sirf model badalta
# hai, keys nahi). Sab live-verified working models hain.
GEMINI_MODEL_FALLBACKS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
]
GEMINI_KEYS = []          # run_playwright() config se set hoti hai
GEMINI_ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/"
                   "models/{model}:generateContent")
SETTINGS_FILE = os.path.join(APP_DIR, "bot_settings.json")

_gk_idx = 0               # abhi kaunsi key use ho rahi hai
_gk_cooldown = {}         # key -> monotonic time tak woh key skip karni hai
_GK_COOLDOWN_SEC = 90     # 429 ke baad key kitni der aaram kare

_ACT = None               # current session ka ActivityLog — module-level
                          # functions (jaise _gemini_sync) se bhi Discord
                          # alert bhejne ke liye
_GEMINI_DEAD_REASON = None  # set hota hai jab saari Gemini keys fail ho jayein


def load_settings() -> dict:
    try:
        return json.load(open(SETTINGS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_settings(d: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except Exception:
        pass


def _split_keys(text: str) -> list:
    """Newline / comma / space se alag karo, order + uniqueness rakho."""
    out, seen = [], set()
    for part in re.split(r"[\s,]+", (text or "").strip()):
        p = part.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def resolve_block_keywords() -> list:
    """
    "Don't-join" keywords for pre-filling the box:
      saved bot_settings.json  >  block_keywords.txt (bot folder)  >  DEFAULT_DONT_JOIN
    """
    s = load_settings().get("block_keywords", [])
    if isinstance(s, str):
        s = [x.strip() for x in s.splitlines()]
    s = [x.strip().lower() for x in s if x and x.strip()]
    if s:
        return s
    try:
        p = os.path.join(APP_DIR, "block_keywords.txt")
        if os.path.exists(p):
            f = [ln.strip().lower() for ln in open(p, encoding="utf-8")
                 if ln.strip() and not ln.strip().startswith("#")]
            if f:
                return f
    except Exception:
        pass
    return list(DEFAULT_DONT_JOIN)


def resolve_gemini_keys(ui_val: str = "") -> list:
    """UI box > env GEMINI_API_KEY(S) > gemini_keys.txt / gemini_key.txt."""
    keys = _split_keys(ui_val)
    if keys:
        return keys
    for env in ("GEMINI_API_KEYS", "GEMINI_API_KEY"):
        keys = _split_keys(os.environ.get(env, ""))
        if keys:
            return keys
    for fn in ("gemini_keys.txt", "gemini_key.txt"):
        try:
            p = os.path.join(APP_DIR, fn)
            if os.path.exists(p):
                keys = _split_keys(open(p, encoding="utf-8").read())
                if keys:
                    return keys
        except Exception:
            pass
    return []


def _gemini_once(api_key, question, city):
    """
    Ek key se ek call. Return: text | 'RATELIMIT' | 'ERROR' | None.

    Same key ke saath, agar GEMINI_MODEL "high demand" (503), rate-limited
    (429/403) ya deprecated (404) nikle, to isi call ke andar hi
    GEMINI_MODEL_FALLBACKS list se agla model try karta hai — SAME API key,
    bas model badalta hai (AQ./AIza keys sab Gemini models pe kaam karti
    hain). Gemini free-tier ka rate-limit per-MODEL hota hai, per-key nahi —
    isliye ek model par 429 aane ka matlab ye nahi ke yehi key doosre model
    par bhi rate-limited hogi. Jo model kaam kar jaye wahi GEMINI_MODEL ban
    jata hai taake agli har call seedhi usi se shuru ho.

    'RATELIMIT' sirf tab return hota hai jab is key ke saath SAARE
    (chaaron) fallback models rate-limited nikle — tabhi is key ko
    cooldown mein daala jata hai (_gk_one_pass mein).
    """
    where = _city_pretty() or city or "the local area"
    rules = (
        "You are a real local resident in the USA"
        + (f" living in {where}" if where else "")
        + ". You are answering a Facebook group's membership screening question. "
        "Reply in the first person, natural and friendly, 1-2 short sentences, "
        "no greeting and no sign-off. NEVER mention any business, brand, company, "
        "product, service, advertising, promotion, marketing or selling. If asked "
        "whether you run or represent a business or want to promote something, say "
        "no - you are just a local resident. If asked whether you are a bot or a "
        "real person, say you are a real person. If asked which city/area you live "
        f"in, say you live in {where}. "
        "LANGUAGE: Always answer in ENGLISH ONLY, no matter what language the "
        "question is written in. Do not use Spanish, Portuguese, or any other "
        "language, even if the question itself is in that language - reply in "
        "English regardless. "
        "Output ONLY the answer text, nothing else."
    )
    body = json.dumps({
        "system_instruction": {"parts": [{"text": rules}]},
        "contents": [{"parts": [{"text": f"Question: {question}\nAnswer:"}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 120},
    }).encode("utf-8")
    global GEMINI_MODEL
    # Har key (AIza... ya AQ...) x-goog-api-key header se — Bearer 401 deta hai
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

    candidates = [GEMINI_MODEL] + [m for m in GEMINI_MODEL_FALLBACKS if m != GEMINI_MODEL]
    all_ratelimited = True   # sab candidates try karne ke baad bhi True rahe
                             # to matlab: is key ka HAR model par quota khatam
    for model in candidates:
        url = GEMINI_ENDPOINT.format(model=model)
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
            txt = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if model != GEMINI_MODEL:
                send_ui("log", text=f"   ↪ Gemini model {GEMINI_MODEL} busy/unavailable "
                                    f"→ switched to {model}")
                GEMINI_MODEL = model
            return txt.strip('"').strip() or None
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                continue    # is model par rate-limit — agla model try karo
                            # (per-key quota per-model hoti hai, blanket nahi)
            all_ratelimited = False
            if e.code in (503, 404):
                # 503 high-demand / 404 deprecated -> isi key se agla model try karo
                if e.code == 404:
                    try:
                        msg = e.read().decode("utf-8", "replace")
                    except Exception:
                        msg = ""
                    m = re.search(r"use\s+models/([a-zA-Z0-9._-]+)", msg)
                    if m and m.group(1) not in candidates:
                        candidates.append(m.group(1))
                continue
            return "ERROR"       # 400/500/etc — is key se na sahi, agli key try karo
        except Exception:
            all_ratelimited = False
            continue              # timeout/network — agla model bhi try kar lo

    # Yahan pahunche matlab is key ke saath koi bhi model kaam nahi kiya.
    return "RATELIMIT" if all_ratelimited else "ERROR"


def _gk_one_pass(question, city, keys, n):
    """Ek round — saari keys pe ek-ek baar try karo (jo cooldown mein nahi
    hain). Jawab mile to wapas karo, warna None."""
    global _gk_idx
    now = time.monotonic()
    i = _gk_idx % n
    for tried in range(n):
        k = keys[i]
        if _gk_cooldown.get(k, 0) <= now:
            res = _gemini_once(k, question, city)
            if res == "RATELIMIT":
                _gk_cooldown[k] = now + _GK_COOLDOWN_SEC
                send_ui("log", text=f"   🔁 Gemini key #{i+1} rate-limited → next key")
            elif res == "ERROR":
                send_ui("log", text=f"   ↻ Gemini key #{i+1} error → next key")
            elif res:
                _gk_idx = i           # is key pe tik jao jab tak chale
                return res
        i = (i + 1) % n
    return None


def _gemini_sync(question, city):
    """
    Keys ke pool par rotate karke jawab lao. HAR sawaal ke liye Gemini
    hi try hota hai.

    Agar AI answers ON hain (keys diye gaye) aur is round mein SAARI keys
    fail/rate-limited nikle — matlab AI answers filhaal bilkul available
    nahi — to chupke se template pe fallback nahi karte (owner ki policy:
    sab jawab AI se hi dene hain). Iske bajaye: bot ROK do + Discord alert
    bhejo, taake owner naya key de sake ya thodi der baad khud restart kare.

    LEKIN foran hi mar ke baith jane se pehle EK chhoti retry (~6 sec baad)
    deta hai — kyunki "saari keys ek saath fail" kabhi Google ke apne
    temporary "high demand" (503) ya network blip ki wajah se bhi ho sakta
    hai (asal keys bilkul theek hote hue bhi), na ke keys genuinely dead
    hone ki wajah se. Agar keys sach mein rate-limited (429) hain to woh
    to cooldown mein hi rahengi aur retry bhi turant fail hoga — is case
    mein rukna abhi bhi turant (~6 sec) hi hota hai.
    """
    global _GEMINI_DEAD_REASON
    keys = GEMINI_KEYS
    n = len(keys)
    if n == 0:
        return None

    ans = _gk_one_pass(question, city, keys, n)
    if ans:
        return ans

    send_ui("log", text="   ⏳ All keys failed this round — retrying once "
                        "before giving up (may be a temporary blip)...")
    time.sleep(6)
    ans = _gk_one_pass(question, city, keys, n)
    if ans:
        return ans

    # Retry ke baad bhi saari keys fail — ab genuinely maano ke AI se koi
    # jawab nahi mil sakta. Sirf pehli baar hi alert + stop (baar-baar
    # Discord spam na ho).
    if not stop_event.is_set():
        send_ui("log", text=f"⛔ All {n} Gemini API key(s) failing/rate-limited "
                             f"— stopping bot (AI answers required, no fallback).")
        _GEMINI_DEAD_REASON = "gemini_keys_failed"
        _dmsg = (f"⚠️ GEMINI API KEYS DOWN — saari {n} key(s) rate-limited/error "
                 f"ho gayi hain. Bot ROK diya gaya hai. Naye/extra Gemini keys "
                 f"chahiye ya thodi der baad dobara START karo.")
        _sent = False
        if _ACT:
            try:
                _ACT.alert(_dmsg)
                _sent = True
            except Exception:
                pass
        if not _sent:      # _ACT na ho to bhi Discord pe reason zaroor jaye
            try:
                import activity
                activity.send_alert("bot", _dmsg)
            except Exception:
                pass
        stop_event.set()
    return None


async def gemini_answer(question):
    if not GEMINI_KEYS:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _gemini_sync, question, CURRENT_CITY)


def _gemini_pick_sync(question, options):
    """Multiple-choice: Gemini se poochho kaunsa option — 0-based index return."""
    numbered = "\n".join(f"{i+1}. {o}" for i, o in enumerate(options))
    q = ("A Facebook group's membership form has a multiple-choice question. "
         "You are a genuine local resident (never a business, never promoting, "
         "a real person not a bot).\n\n"
         f"Question / context:\n{question}\n\nOptions:\n{numbered}\n\n"
         "Reply with ONLY the number of the single best option for you. Number:")
    ans = _gemini_sync(q, CURRENT_CITY)
    if not ans:
        return None
    m = re.search(r"\d+", ans)
    if not m:
        return None
    idx = int(m.group()) - 1
    return idx if 0 <= idx < len(options) else None


async def gemini_pick_index(question, options):
    if not GEMINI_KEYS or not options:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _gemini_pick_sync, question, list(options))

def _city_pretty() -> str:
    """'Acworth GA' -> 'Acworth, GA' (aakhri 2-letter state code ho toh comma)"""
    c = CURRENT_CITY.strip()
    parts = c.rsplit(" ", 1)
    if len(parts) == 2 and len(parts[1]) == 2 and parts[1].isupper():
        return f"{parts[0]}, {parts[1]}"
    return c

def pick_answer(question_text: str) -> str:
    """Sawaal ke text se munasib jawab chuno; kuch match na ho toh generic"""
    q = (question_text or "").lower()
    # "KAUNSA city/ilaqa?" — yeh yes/no sawaal nahi, naam batana hota hai
    if CURRENT_CITY and any(k in q for k in [
            "what city", "which city", "what town", "which town",
            "what area", "which area", "where do you live",
            "where are you from", "your city", "zip code", "what part of"]):
        return pick([f"I live in {_city_pretty()}.",
                     f"I'm in {_city_pretty()} — right here in the local area."])
    for keywords, answers in QA_RULES:
        if any(k in q for k in keywords):
            return pick(answers)
    return pick(JOIN_ANSWERS)

# Multiple-choice checkbox question mein in keywords wala option priority se
# select hota hai (local resident persona ke sath match karne ke liye)
CHECKBOX_PREFERENCE_KEYWORDS = ["yes, i live", "full time", "yes, i", "i live", "yes"]

SEARCH_TEMPLATES = []  # ab use nahi hota

LOG_FILE         = f"groups_log{SUFFIX}.csv"
JOINED_FILE      = f"joined_groups{SUFFIX}.txt"
TOTAL_FILE       = f"total_count{SUFFIX}.txt"
TOTAL_SKIP_FILE  = f"total_skipped_count{SUFFIX}.txt"

# ── Globals ───────────────────────────────────────────────────
ui_queue   = queue.Queue()   # Playwright → UI
stop_event = threading.Event()

# ── Utilities ─────────────────────────────────────────────────

def rand_delay(lo, hi):
    return random.uniform(lo, hi)

def pick(lst):
    return random.choice(lst)

def load_joined():
    if not Path(JOINED_FILE).exists():
        return set()
    return set(open(JOINED_FILE).read().splitlines())

def save_joined(url):
    with open(JOINED_FILE, "a") as f:
        f.write(url + "\n")

def load_total():
    if not Path(TOTAL_FILE).exists():
        return 0
    try:
        return int(open(TOTAL_FILE).read().strip())
    except:
        return 0

def save_total(n):
    open(TOTAL_FILE, "w").write(str(n))

def load_total_skipped():
    if not Path(TOTAL_SKIP_FILE).exists():
        return 0
    try:
        return int(open(TOTAL_SKIP_FILE).read().strip())
    except:
        return 0

def save_total_skipped(n):
    open(TOTAL_SKIP_FILE, "w").write(str(n))

_pgeo = pgeocode.Nominatim('us')
_geo  = Nominatim(user_agent="fb_group_joiner_v1", timeout=10)

def load_area_cache() -> dict:
    """areas_cache.json load karo (build_area_cache.py se pehle se generate hoti hai)."""
    if not Path(AREA_CACHE_FILE).exists():
        return {}
    try:
        return json.loads(Path(AREA_CACHE_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}

_AREA_CACHE = load_area_cache()

def get_nearby_cities(city_state: str, radius_miles: int = 50) -> list:
    """
    city_state (e.g. 'Raleigh NC') ke 50-mile radius mein saari unique
    cities return karo, entered city included.
    Sirf tab chalta hai jab area cache mein nahi mili (custom typed city) —
    warna yeh live geocode + full-database scan bohot slow hai.
    """
    try:
        loc = _geo.geocode(city_state + ", USA")
        if not loc:
            return [city_state]
        center = (loc.latitude, loc.longitude)

        # pgeocode se poora US zip code database load karo
        data = _pgeo._data
        if data is None or data.empty:
            return [city_state]

        data = data.dropna(subset=["latitude", "longitude", "place_name", "state_code"])
        cities = set()
        for _, row in data.iterrows():
            dist = geodesic(center, (row["latitude"], row["longitude"])).miles
            if dist <= radius_miles:
                cities.add(f"{row['place_name']} {row['state_code']}")

        result = sorted(cities) if cities else [city_state]
        return result
    except Exception as e:
        return [city_state]

def _area_state_code(area: str) -> str:
    """'Charlotte North Carolina' -> 'NC' ; 'Waco Texas' -> 'TX' ;
       'Washington DC' -> 'DC' ; 'New York' -> 'NY' ; 'Raleigh NC' -> 'NC'."""
    a = (area or "").strip()
    low = a.lower()
    if low.endswith(" dc") or low == "washington dc":
        return "DC"
    parts = a.split()
    if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isupper():
        return parts[-1]
    for code, name in STATE_NAMES.items():
        if low.endswith(name.lower()):
            return code
    return {"new york": "NY", "new jersey": "NJ"}.get(low, "")


def _target_state(target: str) -> str:
    """'Belton TX' -> 'TX' ; 'Bell County TX' -> 'TX' ; kuch na mile toh ''."""
    p = (target or "").split()
    return p[-1] if p and len(p[-1]) == 2 and p[-1].isupper() else ""


def get_targets_for_area(area: str, same_state_only: bool = True,
                          include_counties: bool = True) -> list:
    """
    Ek area (e.g. 'Waco Texas') ke liye search targets (cities + counties)
    return karo. Pehle cache check karo (fast), warna live calculate karo.
    same_state_only=True -> sirf usi state ki cities/counties (border par
    50-mile radius dusre state mein ghus jata tha — ab nahi).
    include_counties=False -> sirf cities, county-level targets skip
    (employee ki apni choice — UI checkbox se).
    """
    cached = _AREA_CACHE.get(area)
    if cached:
        targets = list(cached.get("cities", []))
        if include_counties:
            targets += list(cached.get("counties", []))
    else:
        targets = get_nearby_cities(area, 50)

    if same_state_only:
        st = _area_state_code(area)
        if st:
            in_state = [t for t in targets if _target_state(t) == st]
            # Chhote areas (jaise DC) mein same-state filter ke baad bohot
            # kam targets bachte hain — us surat mein filter chhoro
            if len(in_state) >= 8:
                targets = in_state
    return targets

_LAST_CSV_STATUS = ""

def log_csv(area, name, url, status, members="?", privacy="?"):
    global _LAST_CSV_STATUS
    _LAST_CSV_STATUS = status
    exists = Path(LOG_FILE).exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["Date","Time","Area","Group","URL","Members","Privacy","Status"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d"),
            datetime.now().strftime("%H:%M:%S"),
            area, name, url, members, privacy, status
        ])

def send_ui(msg_type, **kwargs):
    ui_queue.put({"type": msg_type, **kwargs})
    # Log lines file mein bhi save karo — debugging ke liye (UI band ho
    # jaye toh bhi history mile)
    if msg_type == "log":
        try:
            with open(f"ui_log{SUFFIX}.txt", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%H:%M:%S')} {kwargs.get('text','')}\n")
        except:
            pass

# ── Playwright Helpers ────────────────────────────────────────

async def sleep(sec):
    await asyncio.sleep(sec)


async def _sleep_interruptible(secs, chunk=5):
    """Lambi wait (break / off-hours) — stop_event set hote hi turant wapas."""
    end = time.time() + max(0.0, secs)
    while time.time() < end and not stop_event.is_set():
        await asyncio.sleep(min(chunk, max(0.2, end - time.time())))


def _parse_hhmm(s, default_min):
    try:
        h, m = str(s).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return default_min


def _fmt_hhmm(mins):
    return f"{(mins // 60) % 24:02d}:{mins % 60:02d}"


class HumanPacer:
    """
    Din ke joins ko phaila deta hai (bina-ruke burst nahi), working-hours ke
    bahar ruk jata hai, aur beech mein 'natural break' leta hai — pattern
    insani lage aur account safe rahe.
    """

    def __init__(self, config):
        self.on        = bool(config.get("pace_enabled", True))
        self.limit     = int(config.get("daily_limit", 250) or 250)
        self.win_start = _parse_hhmm(config.get("work_start", "09:00"), 9 * 60)
        self.win_end   = _parse_hhmm(config.get("work_end", "21:00"), 21 * 60)
        self.n_session = 0
        self.since_brk = 0
        self.brk_at    = random.randint(18, 32)

    def _now_min(self):
        t = datetime.now()
        return t.hour * 60 + t.minute

    def _in_window(self):
        if self.win_start == self.win_end:
            return True
        n = self._now_min()
        if self.win_start < self.win_end:
            return self.win_start <= n < self.win_end
        return n >= self.win_start or n < self.win_end       # overnight

    def _secs_left_in_window(self):
        if self.win_start == self.win_end:
            return 12 * 3600
        n = self._now_min()
        if self.win_start < self.win_end:
            return max(60, (self.win_end - n) * 60)
        if n >= self.win_start:
            return max(60, (24 * 60 - n + self.win_end) * 60)
        return max(60, (self.win_end - n) * 60)

    async def wait_for_window(self):
        if not self.on:
            return
        told = False
        while self.on and not self._in_window() and not stop_event.is_set():
            if not told:
                send_ui("log", text=f"🕗 Working hours ({_fmt_hhmm(self.win_start)}"
                                    f"–{_fmt_hhmm(self.win_end)}) ke bahar — "
                                    f"window khulne ka intezaar…")
                told = True
            await _sleep_interruptible(180)
        if told and not stop_event.is_set():
            send_ui("log", text="🕘 Working hours shuru — joining resume.")

    async def pace(self, joined_today):
        """Ek successful join ke BAAD call karo."""
        if not self.on:
            await sleep(rand_delay(4, 9))
            return
        self.n_session += 1
        self.since_brk += 1

        if self.since_brk >= self.brk_at:
            mins = random.randint(5, 15)
            send_ui("log", text=f"☕ Natural break — {mins} min")
            await _sleep_interruptible(mins * 60)
            self.since_brk = 0
            self.brk_at    = random.randint(20, 38)
            if stop_event.is_set():
                return

        remaining = max(1, self.limit - joined_today)
        gap = self._secs_left_in_window() / remaining
        gap = max(25.0, min(300.0, gap)) * random.uniform(0.7, 1.35)
        if self.n_session <= 15:                    # dheema start
            gap *= random.uniform(1.6, 2.4)
        send_ui("log", text=f"   ⏳ {gap:.0f}s wait (paced)")
        await _sleep_interruptible(gap)


async def human_type(el, text):
    """React-compatible typing"""
    await el.focus()
    await sleep(0.3)
    try:
        # contenteditable divs
        await el.evaluate("""
            (el, txt) => {
                el.innerHTML = '';
                el.focus();
                document.execCommand('insertText', false, txt);
            }
        """, text)
    except:
        await el.fill(text)
    await sleep(0.3)

async def _is_checkbox_checked(chk):
    try:
        aria = await chk.get_attribute("aria-checked")
    except:
        aria = None
    if aria is not None:
        return aria == "true"
    try:
        return await chk.is_checked()
    except:
        return False

async def _cb_checked(page, i) -> bool:
    return await _is_checkbox_checked(page.locator(f'[data-fbjoin-idx="{i}"]').first)

async def _click_cb(page, i) -> bool:
    """Tagged clickable (label/wrapper) pe click, phir tagged box ka state
    verify. Kai tareeqe — FB ke custom/hidden checkbox ke liye."""
    clk = page.locator(f'[data-fbjoin-click="{i}"]').first
    box = page.locator(f'[data-fbjoin-idx="{i}"]').first
    for _ in range(3):
        for target in (clk, box):
            try:
                await target.click(timeout=2000, force=True)
            except Exception:
                try:
                    await target.evaluate("el => el.click()")
                except Exception:
                    pass
            await sleep(rand_delay(0.3, 0.6))
            if await _is_checkbox_checked(box):
                return True
    return await _is_checkbox_checked(box)

async def tick_checkboxes(page):
    """
    Join-dialog ke checkbox/radio options handle karo. FB inputs ko CSS se
    chhupata hai — is liye hum HIDDEN inputs bhi pakadte hain aur unke
    dikhne wale <label>/wrapper pe click karte hain.
    """
    ticked = 0

    groups = await page.evaluate("""
        () => {
            // Sabse upar wala VISIBLE dialog (aakhri in DOM) — warna document
            const dlgs = [...document.querySelectorAll('div[role="dialog"]')]
                          .filter(d => d.offsetParent !== null);
            const scope = dlgs.length ? dlgs[dlgs.length - 1] : document;
            const isBox = 'input[type="checkbox"], input[type="radio"], '
                        + '[role="checkbox"], [role="radio"], [aria-checked]';
            const vis = el => el.offsetParent !== null
                              || (el.getClientRects && el.getClientRects().length > 0);
            // real <input> hidden bhi ho to rakho; role/aria elements sirf visible
            let raw = [...scope.querySelectorAll(isBox)].filter(el =>
                el.tagName === 'INPUT' ? true : vis(el));
            // nested duplicates hata do (chhupa input + upar custom wrapper)
            const boxes = raw.filter(el => !raw.some(o => o !== el && o.contains(el)));

            function labelText(el) {
                let lab = el.closest('label');
                let t = (lab ? lab.innerText : '') || '';
                if (!t.trim()) {
                    let p = el.parentElement;
                    for (let d = 0; p && d < 3; d++, p = p.parentElement) {
                        if ((p.innerText || '').trim()) { t = p.innerText; break; }
                    }
                }
                if (!t.trim()) t = el.getAttribute('aria-label') || '';
                return t.trim().slice(0, 160);
            }
            function clickTarget(el) {
                // hidden <input> -> uska label ya visible parent
                if (el.tagName === 'INPUT' && !vis(el)) {
                    let lab = el.closest('label');
                    if (lab && vis(lab)) return lab;
                    let p = el.parentElement;
                    for (let d = 0; p && d < 4; d++, p = p.parentElement)
                        if (vis(p)) return p;
                }
                return el;
            }

            boxes.forEach((el, i) => {
                el.setAttribute('data-fbjoin-idx', String(i));
                clickTarget(el).setAttribute('data-fbjoin-click', String(i));
            });

            function countBoxesIn(el) { return el.querySelectorAll(isBox).length; }
            const wrapperOf = boxes.map(el => {
                let cur = el.parentElement, depth = 0;
                while (cur && depth < 10) {
                    if (countBoxesIn(cur) >= 2) return cur;
                    cur = cur.parentElement; depth++;
                }
                return null;
            });

            const groups = [];
            const seen = new Map();
            boxes.forEach((el, i) => {
                const w = wrapperOf[i];
                if (w === null) {
                    groups.push({idx: [i], q: labelText(el), labels: [labelText(el)]});
                } else {
                    if (!seen.has(w)) {
                        const g = {idx: [], q: (w.innerText || '').trim().slice(0, 500), labels: []};
                        seen.set(w, g); groups.push(g);
                    }
                    seen.get(w).idx.push(i);
                    seen.get(w).labels.push(labelText(el));
                }
            });
            return groups;
        }
    """)

    _NEG = ("no", "nope", "disagree", "i don't", "i do not", "i won't",
            "decline", "not agree", "false")

    def _is_neg(t):
        t = (t or "").strip().lower()
        return t in ("no", "nope") or any(t.startswith(n) for n in _NEG)

    def _is_aff(t):
        t = (t or "").strip().lower()
        return (t in ("yes", "yeah", "yep", "i agree", "agree", "i do", "true")
                or any(w in t for w in ("yes", "agree", "i do", "promise",
                                        "accept", "i will", "confirm", "i am",
                                        "full time", "i live")))

    # ── groups parse karo (idx + label JS se aata hai) ──
    parsed = []
    for group in groups:
        idxs = group.get("idx", []) if isinstance(group, dict) else group
        labels = group.get("labels", []) if isinstance(group, dict) else []
        qtext = group.get("q", "") if isinstance(group, dict) else ""
        items = []
        already = False
        for n, i in enumerate(idxs):
            lbl = labels[n] if n < len(labels) else ""
            if await _cb_checked(page, i):
                already = True
            items.append((i, lbl))
        if items:
            parsed.append({"items": items, "q": qtext, "already": already})

    # ── FB kabhi Yes/No ko alag-alag "standalone" bana deta hai — ek aff
    #    + ek neg lone option -> ek group ──
    lone_aff = [g for g in parsed if len(g["items"]) == 1
                and _is_aff(g["items"][0][1]) and not _is_neg(g["items"][0][1])]
    lone_neg = [g for g in parsed if len(g["items"]) == 1
                and _is_neg(g["items"][0][1])]
    if lone_aff and lone_neg:
        merged = [g["items"][0] for g in lone_aff + lone_neg]
        parsed = [g for g in parsed if g not in lone_aff and g not in lone_neg]
        parsed.append({"items": merged, "q": "Yes / No", "already": False})

    for g in parsed:
        items, qtext, already = g["items"], g["q"], g["already"]
        if already:
            continue

        if len(items) == 1:
            i, lbl = items[0]
            gi = await gemini_pick_index(
                qtext or lbl or "Should you tick this box to join the group?",
                ["Yes — tick it (I agree / I do / I promise)",
                 "No — leave it unticked"])
            if gi == 1:
                send_ui("log", text=f"   🤖 AI: leave unticked — {(qtext or lbl)[:45]}")
                continue
            if gi is None and _is_neg(lbl):
                send_ui("log", text=f"   ⏭️ Negative option, skip: {lbl[:40]}")
                continue
            if await _click_cb(page, i):
                ticked += 1
                send_ui("log", text=f"   {'🤖 AI ' if gi == 0 else ''}☑️ Ticked: "
                                    f"{(lbl or 'agree')[:40]}")
            else:
                send_ui("log", text=f"   ⚠️ Could not tick: {(lbl or 'box')[:40]}")
            await sleep(rand_delay(0.4, 0.8))
            continue

        opts = [lbl or f"option {n+1}" for n, (i, lbl) in enumerate(items)]
        chosen = None
        gi = await gemini_pick_index(qtext or "Which option applies to you?", opts)
        if gi is not None and not _is_neg(opts[gi]):
            chosen = items[gi]
            send_ui("log", text=f"   🤖 AI picked: {opts[gi][:50]}")
        if chosen is None:
            chosen = next((it for it in items
                           if _is_aff(it[1]) and not _is_neg(it[1])), None)
        if chosen is None:
            chosen = next((it for it in items if not _is_neg(it[1])), items[0])
        if await _click_cb(page, chosen[0]):
            ticked += 1
            send_ui("log", text=f"   ☑️ Ticked: {(chosen[1] or 'option')[:40]}")
        else:
            send_ui("log", text="   ⚠️ Checkbox click didn't register")
        await sleep(rand_delay(0.4, 0.8))

    # ── Safety: options the par kuch tick nahi hua -> koi affirmative box ──
    if parsed and ticked == 0:
        for g in parsed:
            for i, lbl in g["items"]:
                if not _is_neg(lbl) and not await _cb_checked(page, i):
                    if await _click_cb(page, i):
                        ticked += 1
                        send_ui("log", text=f"   ☑️ (safety) Ticked: {(lbl or 'option')[:40]}")
                        break
            if ticked:
                break

    return ticked

async def handle_questions(page):
    await sleep(1.5)

    # SIRF join-questions dialog ke andar kaam karo. Pehle poore page ke
    # text boxes uthate the — jis se script group ke COMMENT BOX mein
    # jawab type kar deti thi (posts ke neeche comments ho rahe the!).
    # Dialog nahi hai = koi sawaal nahi = kuch mat karo.
    # Dialog kabhi kabhi dair se render hota hai — ~6 sec tak intezar karo.
    # Aur VISIBLE dialog dhundo (.first kabhi DOM mein pade hue chhupe
    # dialog ko pakar leta tha jo is_visible fail karta tha).
    dlg = None
    for _ in range(4):
        try:
            for cand in await page.locator('div[role="dialog"]').all():
                if await cand.is_visible():
                    dlg = cand
                    break
        except:
            pass
        if dlg:
            break
        await sleep(1.5)
    if dlg is None:
        # Yeh normal bhi ho sakta hai (bohat se groups sawaal nahi poochte),
        # lekin screenshot rakhte hain taake confirm kar saken
        send_ui("log", text="   ℹ️ No question dialog appeared (joined without questions?)")
        try:
            await ss(page, "no_dialog")
        except:
            pass
        return False

    # Dialog aksar SKELETON (khali grey placeholders) ke sath khulta hai
    # aur asal sawaal baad mein load hote hain — screenshot se confirm hua.
    # Inputs/checkboxes render hone tak ~8 sec intezar karo, warna hum
    # khali dialog parh ke "question text not found" pe pahunch jate hain.
    for _ in range(8):
        try:
            n = await dlg.locator(
                'textarea, [role="textbox"], div[contenteditable="true"], '
                'input[type="checkbox"], [role="checkbox"]'
            ).count()
            if n > 0:
                await sleep(0.8)  # thoda aur — text bhi paint ho jaye
                break
        except:
            pass
        await sleep(1)

    ticked = await tick_checkboxes(page)
    if ticked:
        send_ui("log", text=f"☑️  Ticked {ticked} checkbox(es)")

    inputs = (
        await dlg.locator('textarea').all() +
        await dlg.locator('[role="textbox"]').all() +
        await dlg.locator('div[contenteditable="true"]').all()
    )
    if not inputs and not ticked:
        # Dialog toh hai lekin na koi text-question mila na checkbox —
        # screenshot se pata chalega yeh kaunsa dialog tha
        send_ui("log", text="   ⚠️ Dialog found but no question inputs — screenshot saved")
        try:
            await ss(page, "dialog_no_inputs")
        except:
            pass
    # Fallback ke liye: dialog ke poore text mein se "?" wali lines —
    # i-wan input ka sawaal aksar i-wan "?" line hoti hai (order same hai)
    try:
        dlg_text = await dlg.inner_text(timeout=2000)
    except:
        dlg_text = ""
    q_lines = [l.strip() for l in dlg_text.split("\n")
               if "?" in l and len(l.strip()) > 5]

    answered = 0
    q_idx = 0
    for inp in inputs:
        if not await inp.is_visible():
            continue
        # Sawaal ka text dhundo — input se upar climb karo aur pehla aisa
        # ancestor lo jisme input ke placeholder ("Write an answer...")
        # ke ilawa asli text ho. Pehle ancestor::div[3..6] ka inner_text
        # lete the, jo aksar sirf placeholder hi hota tha — isliye rules
        # kabhi match nahi hote the aur har sawaal pe generic jawab jata tha.
        ctx = ""
        try:
            ctx = await inp.evaluate("""
                (el) => {
                    const junk = /^(write an answer|your answer|answer here|type your answer|required|optional|you must answer|answer all|answer the question|only .* can see|admins? (and|&) moderators|\\d+\\s*\\/\\s*\\d+)/i;
                    const ownText = ((el.value || el.innerText || '') + '').trim();
                    let cur = el.parentElement, depth = 0;
                    while (cur && depth < 15) {
                        const lines = (cur.innerText || '').split('\\n')
                            .map(s => s.trim())
                            .filter(s => s.length > 2)
                            .filter(s => !junk.test(s))
                            .filter(s => !ownText || s !== ownText);
                        if (lines.length) {
                            // "?" wali line asli sawaal hone ka sabse bara ishara hai
                            const q = lines.find(s => s.includes('?'));
                            return q || lines[0];
                        }
                        cur = cur.parentElement;
                        depth++;
                    }
                    return '';
                }
            """)
            ctx = (ctx or "").strip().lower()
        except:
            ctx = ""
        # Fallback: DOM-climb se kuch na mila (ya jo mila usme "?" nahi —
        # aksar woh dialog ki hidayat hoti hai, sawaal nahi) toh dialog-text
        # ki "?" lines order ke hisaab se use karo (pehla input = pehla sawaal)
        if q_idx < len(q_lines) and (not ctx or "?" not in ctx):
            ctx = q_lines[q_idx].lower()
        q_idx += 1
        # Phir bhi khali? Poora dialog-text + screenshot save karo taake
        # agli baar extraction isi data se theek ki ja sake
        if not ctx:
            try:
                with open(f"debug_dialog_text{SUFFIX}.txt", "a", encoding="utf-8") as df:
                    df.write(f"\n--- {datetime.now()} ---\n{dlg_text}\n")
                await ss(page, "question_text_not_found")
            except:
                pass
        # HAR sawaal ka jawab AI se — q_for_ai hamesha kuch na kuch hota hai
        q_for_ai = (ctx
                    or (q_lines[q_idx - 1] if 0 <= q_idx - 1 < len(q_lines) else "")
                    or (dlg_text or "").strip()[:400]
                    or "Answer this Facebook group's membership question briefly, "
                       "in the first person, as a friendly local resident.")
        is_bot = any(k in (ctx or q_for_ai.lower()) for k in BOT_KEYWORDS)
        answer = await gemini_answer(q_for_ai) if GEMINI_KEYS else None
        if answer:
            send_ui("log", text=f"   🤖 AI answer ({GEMINI_MODEL})")
        elif is_bot:
            answer = pick(BOT_ANSWERS)
            send_ui("log", text="   🤖 Bot-check — human-style template (AI unavailable)")
        else:
            answer = pick_answer(ctx)
            send_ui("log", text="   💬 template answer (AI unavailable)")
        # Log mein dikhao kaunsa sawaal mila (pehle 60 chars) — taake
        # ghalat jawab jaye toh pata chale kyun
        q_preview = ctx.split("\n")[0][:60] if ctx else "(question text not found)"
        send_ui("log", text=f"   ❓ Q: {q_preview}")
        send_ui("log", text=f"   💬 A: {answer[:60]}...")
        await human_type(inp, answer)
        answered += 1
        await sleep(rand_delay(0.8, 1.5))

    if answered:
        send_ui("log", text=f"📝 Answered {answered} question(s)")

    # ── Koi bhi text field khali NA chhoro (saare jawab lazmi hain) ──
    for inp in inputs:
        try:
            if not await inp.is_visible():
                continue
        except Exception:
            continue
        cur = ""
        try:
            cur = ((await inp.input_value()) or "").strip()
        except Exception:
            try:
                cur = ((await inp.inner_text()) or "").strip()
            except Exception:
                cur = ""
        if not cur:
            fill = None
            if GEMINI_KEYS:
                fill = await gemini_answer(
                    (dlg_text or "").strip()[:400]
                    or "Answer this Facebook group's membership question as a "
                       "friendly local resident, one short sentence.")
            fill = fill or pick(JOIN_ANSWERS)
            try:
                await human_type(inp, fill)
                answered += 1
                send_ui("log", text="   ✍️ Filled a blank answer (mandatory)")
            except Exception:
                pass

    # ── HAR checkbox/radio ka jawab AI se — tick_checkboxes AI-driven hai
    #    (standalone box: AI se "tick karun ya nahi"; multi-option: AI se
    #    kaunsa). Loop karo taake dair se render hue boxes bhi pakre jayein
    #    aur koi unticked non-negative box na bache. ──
    async def _unchecked_left():
        return await page.evaluate(r"""
            () => {
              const dlgs = [...document.querySelectorAll('div[role=dialog]')]
                            .filter(d => d.offsetParent !== null);
              const dlg = dlgs.length ? dlgs[dlgs.length - 1] : document;
              const SEL = 'input[type=checkbox], input[type=radio], '
                        + '[role=checkbox], [role=radio], [aria-checked]';
              const isOn = el => el.getAttribute('aria-checked') === 'true'
                                 || el.checked === true;
              const labOf = el => {
                let t = (el.closest('label') ? el.closest('label').innerText : '')
                      || el.getAttribute('aria-label')
                      || (el.parentElement ? el.parentElement.innerText : '') || '';
                return t.trim().toLowerCase().slice(0, 90);
              };
              const isNeg = l => /(^|\W)(no|nope|disagree|i do not|i don't|i won't|decline|false)(\W|$)/.test(l);
              let boxes = [...dlg.querySelectorAll(SEL)]
                .filter(el => el.tagName === 'INPUT' || el.offsetParent !== null
                              || (el.getClientRects && el.getClientRects().length));
              boxes = boxes.filter(el => !boxes.some(o => o !== el && o.contains(el)));
              const wrapOf = el => {
                let cur = el.parentElement, d = 0;
                while (cur && d < 10) {
                  if (cur.querySelectorAll(SEL).length >= 2) return cur;
                  cur = cur.parentElement; d++;
                }
                return null;
              };
              const groups = new Map(); const lone = [];
              boxes.forEach(el => {
                const w = wrapOf(el);
                if (w) { if(!groups.has(w)) groups.set(w, []); groups.get(w).push(el); }
                else lone.push(el);
              });
              let left = 0;
              [...groups.values()].forEach(els => { if (!els.some(isOn)) left++; });
              lone.forEach(el => { if (!isOn(el) && !isNeg(labOf(el))) left++; });
              return left;
            }
        """)

    for _round in range(6):
        try:
            n = await tick_checkboxes(page)      # AI-driven
        except Exception:
            n = 0
        ticked += n
        try:
            left = await _unchecked_left()
        except Exception:
            left = 0
        if not left:
            break
        # AI/label click nahi laga -> is round mein raw click (label ya self)
        if _round >= 1:
            try:
                forced = await page.evaluate(r"""
                    () => {
                      const dlgs = [...document.querySelectorAll('div[role=dialog]')]
                                    .filter(d => d.offsetParent !== null);
                      const dlg = dlgs.length ? dlgs[dlgs.length-1] : document;
                      const SEL='input[type=checkbox],input[type=radio],[role=checkbox],[role=radio],[aria-checked]';
                      const isOn=el=>el.getAttribute('aria-checked')==='true'||el.checked===true;
                      const labOf=el=>((el.closest('label')?el.closest('label').innerText:'')
                        ||el.getAttribute('aria-label')||(el.parentElement?el.parentElement.innerText:'')||'')
                        .trim().toLowerCase().slice(0,90);
                      const isNeg=l=>/(^|\W)(no|nope|disagree|i do not|i don't|i won't|decline)(\W|$)/.test(l);
                      let n=0;
                      let boxes=[...dlg.querySelectorAll(SEL)];
                      boxes=boxes.filter(el=>!boxes.some(o=>o!==el&&o.contains(el)));
                      // group by wrapper (2+ boxes) — group mein koi on nahi to pehla non-neg
                      const wrapOf=el=>{let c=el.parentElement,d=0;while(c&&d<10){if(c.querySelectorAll(SEL).length>=2)return c;c=c.parentElement;d++;}return null;};
                      const G=new Map(),L=[];
                      boxes.forEach(el=>{const w=wrapOf(el);if(w){if(!G.has(w))G.set(w,[]);G.get(w).push(el);}else L.push(el);});
                      for(const els of G.values()){
                        if(els.some(isOn))continue;
                        const p=els.find(e=>!isNeg(labOf(e)))||els[0];
                        const t=p.closest('label')||p; try{t.click();n++;}catch(e){}
                      }
                      for(const el of L){
                        if(isOn(el)||isNeg(labOf(el)))continue;
                        const t=el.closest('label')||el; try{t.click();n++;}catch(e){}
                      }
                      return n;
                    }
                """)
                if forced:
                    ticked += forced
                    send_ui("log", text=f"   ☑️ forced {forced} option(s)")
            except Exception:
                pass
        await sleep(rand_delay(0.5, 0.9))

    # ── Last-resort (rare): Submit ABHI bhi disabled -> keyword affirmative
    #    sweep taake join na atke (AI se sab try ho chuka). ──
    async def _submit_is_disabled():
        for sel in ['[aria-label="Submit"]', '[aria-label="Send"]',
                    '[aria-label="Send Request"]', 'button[type="submit"]',
                    'div[role="button"]:has-text("Submit")',
                    'div[role="button"]:has-text("Send Request")']:
            try:
                b = dlg.locator(sel).first
                if await b.count() and await b.is_visible(timeout=500):
                    ad = await b.get_attribute("aria-disabled")
                    try:
                        dis = await b.is_disabled()
                    except Exception:
                        dis = False
                    return ad == "true" or dis
            except Exception:
                pass
        return False

    if await _submit_is_disabled():
        did = await page.evaluate(r"""
            () => {
              const dlgs = [...document.querySelectorAll('div[role=dialog]')]
                            .filter(d => d.offsetParent !== null);
              const dlg = dlgs.length ? dlgs[dlgs.length - 1] : document;
              const SEL = 'input[type=checkbox], input[type=radio], '
                        + '[role=checkbox], [role=radio], [aria-checked]';
              const isOn = el => el.getAttribute('aria-checked') === 'true' || el.checked === true;
              const labOf = el => ((el.closest('label') ? el.closest('label').innerText : '')
                        || el.getAttribute('aria-label')
                        || (el.parentElement ? el.parentElement.innerText : '') || '')
                        .trim().toLowerCase().slice(0, 90);
              const isNeg = l => /(^|\W)(no|nope|disagree|i do not|i don't|i won't|decline)(\W|$)/.test(l);
              let n = 0;
              [...dlg.querySelectorAll(SEL)].forEach(el => {
                if (isOn(el) || isNeg(labOf(el))) return;
                const t = el.closest('label') || el;
                try { t.click(); n++; } catch (e) {}
              });
              return n;
            }
        """)
        if did:
            ticked += did
            send_ui("log", text=f"   ☑️ (last-resort) ticked {did} box(es) to enable Submit")

    # ── Submit — aur confirm karo ke dialog band hua ──
    async def _submit_once():
        # 1) known selectors
        for sel in ['[aria-label="Submit"]', '[aria-label="Send"]',
                    '[aria-label="Send Request"]', 'button[type="submit"]',
                    'div[role="button"]:has-text("Submit")',
                    'div[role="button"]:has-text("Send Request")',
                    'div[role="button"]:has-text("Done")']:
            try:
                btn = dlg.locator(sel).first
                if await btn.count() and await btn.is_visible(timeout=800):
                    await btn.click(force=True)
                    await sleep(1.8)
                    return True
            except:
                pass
        # 2) koi bhi dialog-button jis pe Submit/Send/Join/Done/Request likha ho
        try:
            btns = await dlg.locator('div[role="button"], button').all()
            for b in btns:
                try:
                    txt = ((await b.inner_text(timeout=400)) or "").strip().lower()
                except Exception:
                    txt = ""
                if txt in ("submit", "send", "send request", "done", "join",
                           "request to join", "continue"):
                    await b.click(force=True)
                    await sleep(1.8)
                    return True
        except Exception:
            pass
        return False

    ok = await _submit_once()
    # dialog abhi bhi khula? matlab kuch adhoora reh gaya — ek retry
    try:
        still_open = await dlg.is_visible(timeout=800)
    except:
        still_open = False
    if still_open:
        send_ui("log", text="   ↻ Form abhi tak khula — dobara bhar ke submit")
        try:
            await tick_checkboxes(page)
        except Exception:
            pass
        for inp in inputs:
            try:
                if await inp.is_visible():
                    v = (await inp.inner_text()).strip()
                    if not v:
                        await human_type(inp, pick(JOIN_ANSWERS))
            except Exception:
                pass
        await _submit_once()
        try:
            still_open = await dlg.is_visible(timeout=800)
        except:
            still_open = False
        if still_open:
            send_ui("log", text="   ⚠️ Submit button disabled/form incomplete — screenshot saved")
            try:
                await ss(page, "form_incomplete")
            except:
                pass

    return ticked > 0 or answered > 0 or ok

async def get_group_info(page):
    html  = await page.content()
    text  = await page.inner_text("body")
    members = 0
    for pat in [r'([\d,]+\.?\d*[KkMm]?)\s*[Mm]embers?', r'"memberCount":([\d]+)']:
        m = re.search(pat, html)
        if m:
            # _parse_count khali/ajeeb string pe crash nahi karta, 0 deta hai
            members = _parse_count(m.group(1))
            if members:
                break
    privacy     = "Private" if "Private group" in text else "Public" if "Public group" in text else "Unknown"
    already     = any(x in text for x in ["Leave group","Joined","Member ·","You're a member"])
    page_blocked = "doesn't allow Pages to join" in text or "does not allow Pages to join" in text
    # Members post nahi kar sakte (admin-only / announcement group) — pre-join
    # best-effort detection, Facebook ke alfaz badalte rehte hain
    tl = text.lower()
    post_disabled = any(m in tl for m in [
        "only admins can post", "only admins and moderators can post",
        "only moderators can post", "only admins and mods can post",
        "admins have turned off posting", "posting has been turned off",
        "posting is turned off", "members can't post", "members cannot post",
        "posting turned off for members",
    ])
    # Canada check — sirf page ka upar wala hissa (header/about) dekho,
    # poora body nahi (kisi post mein "Canada" ka zikar false-positive
    # na ban jaye)
    header_txt = text[:600].lower()
    is_canada  = any(m in header_txt for m in CANADA_MARKERS)
    non_english = detect_non_english(text[:1500])
    return members, privacy, already, page_blocked, is_canada, post_disabled, non_english


# Account-level block / checkpoint markers (page body text, lowercase)
ACCOUNT_BLOCK_MARKERS = [
    "confirm your identity", "we need to confirm", "help us confirm",
    "temporarily locked", "temporarily blocked", "temporarily restricted",
    "your account has been temporarily", "you're restricted from",
    "your account is restricted", "account has been disabled",
    "you can't use facebook", "you cannot use facebook",
    "this feature isn't available right now", "this feature isn’t available right now",
    "you can't use this feature right now", "you cannot use this feature",
    "you're doing that too much", "you’re doing that too much",
    "we limit how often", "action blocked",
    "unusual activity", "suspicious activity", "security check",
    "solve this puzzle", "enter the code we", "enter security code",
    "please try again later",
]
# Sirf join karne ki limit — pending requests bharay hue
PENDING_LIMIT_MARKERS = [
    "you've reached the limit", "you have reached the limit",
    "reached the limit for join", "requested to join too many",
    "too many groups", "too many pending",
    "wait for a decision on some", "cancel some of your",
    "can't join any more groups", "cannot join any more groups",
    "limit for the number of groups",
]


async def check_account_block(page, body_text: str = None) -> str:
    """Account checkpoint/block detect. Reason string ya '' return."""
    try:
        if page.url and "/checkpoint/" in page.url:
            return "checkpoint page"
        t = (body_text if body_text is not None
             else await page.inner_text("body")).lower()
    except Exception:
        return ""
    for m in ACCOUNT_BLOCK_MARKERS:
        if m in t:
            return m
    # login page pe redirect = session mar gayi
    if ("log in" in t or "log into facebook" in t) and 'name="pass"' in \
            (await page.content()).lower():
        return "logged out (session expired)"
    return ""


def check_pending_limit(body_text: str) -> bool:
    t = (body_text or "").lower()
    return any(m in t for m in PENDING_LIMIT_MARKERS)


def _parse_count(s: str) -> int:
    """'1.2K' -> 1200, '3M' -> 3000000, '234' -> 234, '1,050' -> 1050"""
    s = s.replace(",", "").strip()
    m = re.match(r'([\d.]+)\s*([KkMm])?', s)
    if not m:
        return 0
    try:
        val = float(m.group(1))
    except ValueError:
        return 0
    suf = (m.group(2) or "").lower()
    if suf == "k":
        val *= 1_000
    elif suf == "m":
        val *= 1_000_000
    return int(val)

async def check_group_activity(page) -> bool:
    """
    Public group ke recent posts check karo:
    - Kam se kam 2 posts mein 5+ likes ya 2+ comments honi chahiye
    - "1.2K" / "3M" jaise Facebook shorthand numbers bhi parse hote hain
    Naapne ka tareeqa: DOM se (aria-label + text), kyunke FB likes screen
    pe sirf bare number dikhata hai aur text-regex unhe miss kar deta tha.
    Faisla insaani andaz mein:
    - 2+ posts pe koi bhi engagement (3+ reactions ya 1+ comment) = zinda group
    - YA ek bhi post strongly active ho (10+ reactions / 5+ comments)
    - Counts parhe hi na ja saken = group ki ghalti nahi, allow
    """
    try:
        # Scroll karo taake posts + counts hydrate ho jayein
        await page.keyboard.press("End")
        await sleep(1.2)
        await page.keyboard.press("End")
        await sleep(1.0)

        stats = await page.evaluate("""
            () => {
                const parseCount = (s) => {
                    if (!s) return 0;
                    const m = String(s).replace(/,/g, '').match(/([\\d.]+)\\s*([KkMm])?/);
                    if (!m) return 0;
                    let v = parseFloat(m[1]) || 0;
                    const suf = (m[2] || '').toLowerCase();
                    if (suf === 'k') v *= 1000;
                    if (suf === 'm') v *= 1000000;
                    return Math.round(v);
                };
                const posts = [...document.querySelectorAll('[role="article"]')]
                    .filter(a => !a.parentElement.closest('[role="article"]'))
                    .slice(0, 10);
                const out = [];
                for (const post of posts) {
                    let reactions = 0, comments = 0, sawCounter = false;
                    for (const el of post.querySelectorAll('[aria-label]')) {
                        const al = el.getAttribute('aria-label') || '';
                        const m = al.match(/([\\d.,]+\\s*[KkMm]?)\\s*(?:people|person|reaction|others?)/i)
                               || (/reacted|reaction/i.test(al) && al.match(/([\\d.,]+\\s*[KkMm]?)/));
                        if (m) { reactions = Math.max(reactions, parseCount(m[1])); sawCounter = true; }
                    }
                    const txt = post.innerText || '';
                    let m = txt.match(/all reactions:?\\s*([\\d.,]+\\s*[KkMm]?)/i);
                    if (m) { reactions = Math.max(reactions, parseCount(m[1])); sawCounter = true; }
                    m = txt.match(/([\\d.,]+\\s*[KkMm]?)\\s*comments?/i);
                    if (m) { comments = parseCount(m[1]); sawCounter = true; }
                    out.push({ reactions, comments, sawCounter });
                }
                return out;
            }
        """)

        if len(stats) < 2:
            return True  # Posts load nahi hue — judge nahi kar sakte

        counted = [p for p in stats if p["sawCounter"]]
        if not counted:
            return True  # Counts parh hi nahi sake — allow

        active = sum(1 for p in counted if p["reactions"] >= 3 or p["comments"] >= 1)
        strong = any(p["reactions"] >= 10 or p["comments"] >= 5 for p in counted)
        ok = active >= 2 or strong
        send_ui("log", text=f"   📊 Engagement: {active}/{len(counted)} active posts"
                            f"{' (strong post mila)' if strong and active < 2 else ''}"
                            f" → {'OK' if ok else 'LOW'}")
        return ok
    except:
        return True   # Error pe group skip mat karo

async def click_join(page):
    # Method 1: aria-label
    for sel in [
        '[aria-label="Join group"]',
        '[aria-label="Join Group"]',
        '[aria-label="Join this group"]',
        'a[aria-label="Join group"]',
        'a[aria-label="Join Group"]',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                return True
        except:
            pass

    # Method 2: any visible button/link jisme "join" text ho
    for el in await page.locator('[role="button"], button, a').all():
        try:
            if not await el.is_visible(timeout=300):
                continue
            txt = (await el.inner_text(timeout=400)).strip().lower()
            if txt in ("join group", "join", "join this group", "join group ·"):
                await el.click()
                return True
        except:
            pass

    # Method 3: JavaScript se dhundo — "Join" wala koi bhi clickable element
    try:
        clicked = await page.evaluate("""
            () => {
                let els = [...document.querySelectorAll('[role="button"], button, a')];
                for (let el of els) {
                    let t = (el.innerText || '').trim().toLowerCase();
                    if (t === 'join group' || t === 'join') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        if clicked:
            return True
    except:
        pass

    return False

SS_DIR = os.path.join(APP_DIR, f"debug_ss{SUFFIX}")
Path(SS_DIR).mkdir(exist_ok=True)

async def ss(page, name):
    try:
        await page.screenshot(path=f"{SS_DIR}\\{name}.png", full_page=False)
    except:
        pass

async def dismiss_popups(page):
    """
    Facebook kabhi kabhi interstitial popups dikhata hai (jaise 'You're in
    sleep mode') jo bacha ke rakha button-click ko intercept kar lete hain.
    Yeh function un popups ko band karta hai taake age ka automation
    (page switch, join, etc.) block na ho.
    """
    closed_any = False
    checks = [
        ('text="You\'re in sleep mode"', 'div[role="button"]:has-text("OK")'),
        ('text="Turn on notifications"', '[aria-label="Not Now"]'),
        ('text="Welcome to your new Page!"', 'text="Use Page"'),
        ('text="Welcome back"', 'text="OK"'),
    ]
    for marker_sel, close_sel in checks:
        try:
            marker = page.locator(marker_sel).first
            if await marker.is_visible(timeout=800):
                btn = page.locator(close_sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await sleep(0.5)
                    closed_any = True
        except:
            pass
    # Generic dialog close button (X) — agar koi aur unexpected dialog khula ho
    try:
        close_x = page.locator('[aria-label="Close"]').first
        if await close_x.is_visible(timeout=500):
            await close_x.click()
            await sleep(0.3)
            closed_any = True
    except:
        pass
    if closed_any:
        send_ui("log", text="   💤 Closed a popup")
    return closed_any

async def _verify_switched(page, page_name):
    """
    Confirm karo ke switch actually ho gaya — "Manage Page" heading (Page
    mode ka pakka sign), composer placeholder, ya top-left active profile
    name mein se koi bhi page_name match kare to switch ho chuka hai.
    """
    try:
        manage_page = page.locator('text="Manage Page"').first
        if await manage_page.is_visible(timeout=1500):
            name_near = page.locator(f'text="{page_name}"').first
            if await name_near.is_visible(timeout=1000):
                return True
    except:
        pass
    try:
        composer = page.locator(f'[aria-label*="{page_name}"], [placeholder*="{page_name}"]').first
        if await composer.is_visible(timeout=1500):
            return True
    except:
        pass
    try:
        # Left sidebar sabse upar wala item hamesha active profile hota hai
        top_item = page.locator(f'text="{page_name}"').first
        if await top_item.is_visible(timeout=1500):
            box = await top_item.bounding_box()
            if box and box["y"] < 150:
                return True
    except:
        pass
    return False

async def switch_via_link(page, page_link, page_name=""):
    """
    Page ke direct link se switch karo — sab se reliable tareeqa:
    1. Page ka URL kholo
    2. Agar "Manage Page" pehle se dikh raha hai -> already switched
    3. Warna "Switch Now" / "Switch" button dhundo aur dabao
    4. Verify karo ke "Manage Page" aa gaya
    """
    try:
        send_ui("log", text=f"   🔗 Switching via page link: {page_link}")
        await page.goto(page_link, wait_until="domcontentloaded", timeout=20000)
        await sleep(rand_delay(2, 3))
        await dismiss_popups(page)
        await ss(page, "01_link_opened")

        # Already page mode mein hain?
        try:
            if await page.locator('text="Manage Page"').first.is_visible(timeout=2000):
                send_ui("log", text="   ✅ Already in page mode")
                return True
        except:
            pass

        # Switch button dhundo (Facebook alag alag labels use karta hai)
        clicked_switch = False
        for sw_sel in ['[aria-label="Switch Now"]', 'text="Switch Now"',
                       '[aria-label="Switch to Page"]', 'text="Switch to Page"',
                       'div[aria-label="Switch"][role="button"]',
                       '[aria-label*="Switch into"]',
                       '[role="button"]:has-text("Switch Now")',
                       '[role="button"]:has-text("Switch to")',
                       '[role="button"]:has-text("Switch")']:
            try:
                sw = page.locator(sw_sel).first
                if await sw.is_visible(timeout=1500):
                    await sw.click()
                    clicked_switch = True
                    send_ui("log", text=f"   Clicked switch button ({sw_sel})")
                    await sleep(rand_delay(2, 3.5))
                    # confirm dialog?
                    for c in ['[role="dialog"] [aria-label="Switch"]',
                              '[role="dialog"] div[role="button"]:has-text("Switch")',
                              '[role="dialog"] [aria-label="Continue"]']:
                        try:
                            cb = page.locator(c).first
                            if await cb.is_visible(timeout=1200):
                                await cb.click()
                                await sleep(rand_delay(2, 3))
                        except:
                            pass
                    break
            except:
                pass

        await dismiss_popups(page)
        # Verify — SPA dheere load hota hai, 20 sec tak retry
        for _ in range(10):
            for t in ['text="Manage Page"', 'text="Professional dashboard"',
                      '[aria-label="Switch back to profile"]']:
                try:
                    if await page.locator(t).first.is_visible(timeout=1000):
                        send_ui("log", text="   ✅ Switched via link (verified)")
                        return True
                except Exception:
                    pass
            if page_name and await _verify_switched(page, page_name):
                send_ui("log", text="   ✅ Switched via link (verified)")
                return True
            # "Switch Now" ab dikh nahi raha + page URL par hain = shayad switch ho gaya
            try:
                still_switch = await page.locator(
                    '[role="button"]:has-text("Switch Now")').first.is_visible(timeout=800)
            except Exception:
                still_switch = False
            if clicked_switch and not still_switch:
                send_ui("log", text="   ✅ Switch button gone — treating as switched")
                return True
            await sleep(2)

        # diagnose: screen par kya hai
        try:
            btns = await page.evaluate("""() => [...document.querySelectorAll(
                '[role=button],button')].map(b=>(b.getAttribute('aria-label')||b.innerText||'')
                .trim()).filter(x=>x && x.length<40).slice(0,25)""")
            send_ui("log", text=f"   screen buttons: {btns}")
        except:
            pass
        await ss(page, "02_link_switch_fail")
        send_ui("log", text="   ⚠️  Link opened but switch could not be verified")
        return False
    except Exception as e:
        send_ui("log", text=f"   link switch error: {str(e)[:80]}")
        return False

async def switch_to_page(page, page_name):
    """Facebook profile se Page pe switch karo"""
    try:
        await dismiss_popups(page)
        await ss(page, "01_before_switch")
        # Method 1: Top-right corner mein account/profile menu dhundo
        # Facebook different aria-labels use karta hai — JS se dhundo
        menu_clicked = False
        top_btns = await page.evaluate("""
            () => {
                let btns = [];
                document.querySelectorAll('[role="button"], button, a').forEach(el => {
                    let rect = el.getBoundingClientRect();
                    if (rect.top < 70 && rect.right > window.innerWidth - 200 && rect.width > 0) {
                        btns.push({
                            label: el.getAttribute('aria-label') || '',
                            tag: el.tagName,
                            x: Math.round(rect.x + rect.width/2),
                            y: Math.round(rect.y + rect.height/2),
                        });
                    }
                });
                return btns;
            }
        """)
        send_ui("log", text=f"   Top-right buttons: {[b['label'] for b in top_btns if b['label']]}")

        # Known account menu labels
        acct_labels = ["Account", "Your profile", "Account controls and privacy",
                       "Account menu", "Profile"]
        for btn_info in top_btns:
            if any(lbl.lower() in btn_info['label'].lower() for lbl in acct_labels):
                await page.mouse.click(btn_info['x'], btn_info['y'])
                menu_clicked = True
                await sleep(1.5)
                break

        # If label match nahi hua — rightmost top button click karo
        if not menu_clicked and top_btns:
            rightmost = max(top_btns, key=lambda b: b['x'])
            await page.mouse.click(rightmost['x'], rightmost['y'])
            await sleep(1.5)
            menu_clicked = True

        if menu_clicked:
            await sleep(0.5)
            await ss(page, "02_after_menu_click")
            # Page naam dhundo — lekin SIRF dropdown ke andar (top-right,
            # narrow region), warna sidebar shortcuts mein wahi naam ka
            # koi aur link galti se match ho jata hai
            match = await page.evaluate("""
                (name) => {
                    const els = [...document.querySelectorAll('span, div')];
                    for (const el of els) {
                        const txt = (el.innerText || '').trim();
                        if (txt !== name) continue;
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) continue;
                        // Dropdown hamesha top-right corner ke neeche khulta hai
                        if (rect.top > 50 && rect.top < 650 && rect.left > window.innerWidth * 0.5) {
                            return {x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2)};
                        }
                    }
                    return null;
                }
            """, page_name)
            if match:
                await page.mouse.click(match["x"], match["y"])
                await sleep(1.5)
                await ss(page, "03_after_profile_click")
                # Facebook kabhi confirmation dialog dikhata hai
                # ("Switch to X?" -> Switch/Continue button) — lekin SIRF
                # dialog ke andar click karo, warna page ke kisi aur element
                # pe ghalat click ho jata hai
                try:
                    dlg = page.locator('[role="dialog"]').first
                    if await dlg.is_visible(timeout=1500):
                        for confirm_sel in ['div[aria-label="Switch"]', 'text="Switch"',
                                             'button:has-text("Switch")', '[aria-label="Continue"]',
                                             'text="Continue"']:
                            try:
                                cbtn = dlg.locator(confirm_sel).first
                                if await cbtn.is_visible(timeout=1000):
                                    await cbtn.click()
                                    await sleep(rand_delay(2, 3))
                                    break
                            except:
                                pass
                except:
                    pass
                await sleep(1)
                await dismiss_popups(page)
                # Facebook SPA dheere load hota hai — verify ko 16 second
                # tak retry karo (har 2 sec), ek hi baar check karne se
                # kaamyab switch bhi "fail" lag raha tha
                for _ in range(8):
                    if await _verify_switched(page, page_name):
                        send_ui("log", text=f"   ✅ Selected '{page_name}' from menu (verified)")
                        return True
                    await sleep(2)
                await ss(page, "04_verify_fail")
                send_ui("log", text=f"   ⚠️  Clicked '{page_name}' but switch not verified")

            # "See all profiles" link dhundo
            for see_sel in ['text="See all profiles"', 'text="See all"', '[href*="profiles"]']:
                try:
                    see_all = page.locator(see_sel).first
                    if await see_all.is_visible(timeout=1500):
                        await see_all.click()
                        await sleep(1.5)
                        match2 = await page.evaluate("""
                            (name) => {
                                const els = [...document.querySelectorAll('span, div')];
                                for (const el of els) {
                                    const txt = (el.innerText || '').trim();
                                    if (txt !== name) continue;
                                    const rect = el.getBoundingClientRect();
                                    if (rect.width > 0 && rect.height > 0) {
                                        return {x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2)};
                                    }
                                }
                                return null;
                            }
                        """, page_name)
                        if match2:
                            await page.mouse.click(match2["x"], match2["y"])
                            await sleep(rand_delay(2, 3))
                            send_ui("log", text=f"   ✅ Selected '{page_name}' via 'See all profiles'")
                            return True
                        break
                except:
                    pass

        # Method 2: facebook.com/pages — "Your Pages" se switch karo
        await page.goto("https://www.facebook.com/pages/?category=your_pages&ref=bookmarks",
                        wait_until="domcontentloaded", timeout=15000)
        await sleep(2)
        await dismiss_popups(page)
        page_link = page.locator(f'a:has-text("{page_name}")').first
        if await page_link.is_visible(timeout=3000):
            href = await page_link.get_attribute("href")
            if href:
                await page.goto(href, wait_until="domcontentloaded", timeout=15000)
                await sleep(2)
                # "Switch to Page" button
                for sw_sel in ['[aria-label="Switch Now"]', 'text="Switch Now"',
                               'text="Switch to Page"', '[aria-label="Switch to Page"]']:
                    try:
                        sw = page.locator(sw_sel).first
                        if await sw.is_visible(timeout=2000):
                            await sw.click()
                            await sleep(rand_delay(2, 3))
                            return True
                    except:
                        pass

        # Method 3: Direct URL
        page_slug = page_name.lower().replace(" ", "")
        await page.goto(f"https://www.facebook.com/{page_slug}", wait_until="domcontentloaded")
        await sleep(2)
        await dismiss_popups(page)
        for sw_sel in ['[aria-label="Switch Now"]', 'text="Switch Now"', 'text="Switch to Page"']:
            try:
                sw = page.locator(sw_sel).first
                if await sw.is_visible(timeout=2000):
                    await sw.click()
                    await sleep(rand_delay(2, 3))
                    return True
            except:
                pass

        return False
    except Exception as e:
        send_ui("log", text=f"   switch error: {e}")
        return False

async def select_page(page, page_name):
    if not page_name:
        return
    await sleep(1.2)
    try:
        opt = page.locator(f'text="{page_name}"').first
        if await opt.is_visible(timeout=2000):
            await opt.click()
            await sleep(0.8)
            confirm = page.locator('[aria-label="Confirm"]').first
            if await confirm.is_visible(timeout=1500):
                await confirm.click()
    except:
        pass

# ── Apply Facebook Filters in sidebar ────────────────────────

def city_only(city_state: str) -> str:
    """'Raleigh NC' → 'Raleigh'  |  'Charlotte, NC' → 'Charlotte'"""
    parts = city_state.replace(",", " ").split()
    if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isupper():
        return " ".join(parts[:-1])
    return city_state.strip()

def state_only(city_state: str) -> str:
    """'Omaha NE' → 'NE'  |  'Raleigh, NC' → 'NC'"""
    parts = city_state.replace(",", " ").split()
    if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isupper():
        return parts[-1]
    return ""

# State abbreviation → full name
STATE_NAMES = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California",
    "CO":"Colorado","CT":"Connecticut","DE":"Delaware","FL":"Florida","GA":"Georgia",
    "HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa",
    "KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland",
    "MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi",
    "MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada","NH":"New Hampshire",
    "NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina",
    "ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania",
    "RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee",
    "TX":"Texas","UT":"Utah","VT":"Vermont","VA":"Virginia","WA":"Washington",
    "WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming","DC":"District of Columbia",
}

async def apply_fb_filters(page, city):
    """
    Facebook search page pe left sidebar filters apply karo:
    - Location: sirf city naam type karo, suggestion select karo
    - Public groups toggle hamesha OFF (sirf private join karein)
    """
    await sleep(rand_delay(1, 1.5))

    city_name  = city_only(city)   # "Raleigh NC" → "Raleigh"
    state_abbr = state_only(city)  # "Raleigh NC" → "NC"
    state_full = STATE_NAMES.get(state_abbr, "")  # "NC" → "North Carolina"

    try:
        loc_input = None
        for sel in [
            'div[aria-label*="Location"]',
            'input[placeholder*="ity"]',
            'input[placeholder*="ocation"]',
            '[aria-label*="Location"] input',
        ]:
            el = page.locator(sel).first
            try:
                if await el.is_visible(timeout=1500):
                    loc_input = el
                    break
            except:
                pass

        if loc_input:
            await loc_input.click()
            await sleep(0.5)
            # Pehle field clear karo
            await loc_input.press("Control+a")
            await loc_input.press("Delete")
            await sleep(0.3)
            # Sirf city naam type karo (autocomplete trigger hogi)
            await loc_input.type(city_name, delay=80)
            await sleep(rand_delay(1.5, 2.5))

            # Suggestion dropdown ka intezaar karo
            suggestion_sels = [
                '[role="option"]',
                '[role="listbox"] li',
                'ul[role="listbox"] [role="option"]',
            ]
            chosen = False
            for s_sel in suggestion_sels:
                opts = await page.locator(s_sel).all()
                if not opts:
                    continue
                # Sirf USA wala suggestion select karo — state abbreviation
                # ya full name match hona zaroori hai. Kabhi bhi blindly
                # pehla option select nahi karna (warna France/kisi aur
                # country ka wrong location lag jata hai)
                for opt in opts:
                    try:
                        opt_text = (await opt.inner_text(timeout=600)).strip()
                        is_usa = (
                            (state_abbr and state_abbr in opt_text) or
                            (state_full and state_full.lower() in opt_text.lower()) or
                            "United States" in opt_text or
                            ", US" in opt_text
                        )
                        if is_usa:
                            await opt.click()
                            await sleep(1)
                            send_ui("log", text=f"📍 Location: {opt_text.strip()}")
                            chosen = True
                            break
                    except:
                        pass
                if chosen:
                    break

            if not chosen:
                # Koi USA suggestion nahi mila — dropdown band karo.
                # Bina USA filter ke search karna = Canada/doosre mulkon
                # ke groups aa jate hain, isliye yeh city hi skip hogi.
                try:
                    await loc_input.press("Escape")
                except:
                    pass
                await loc_input.press("Control+a")
                await loc_input.press("Delete")
                send_ui("log", text=f"   ⚠️  No USA suggestion for '{city_name}'")
            return chosen
        return False
    except Exception as e:
        send_ui("log", text=f"   location filter error: {e}")
        return False

    # Public + Private dono — koi toggle nahi badlna

# ── Main Join Logic ───────────────────────────────────────────

async def join_one_group(page, url, name, area, config):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await sleep(rand_delay(1, 2))
        await dismiss_popups(page)

        # ── Account block / checkpoint? -> foran STOP + alert ──
        try:
            body_txt = await page.inner_text("body")
        except Exception:
            body_txt = ""
        blk = await check_account_block(page, body_txt)
        if blk:
            send_ui("log", text=f"🚫 ACCOUNT BLOCK: '{blk}' — bot rok raha hai")
            _a = config.get("_activity")
            if _a:
                try:
                    _a.alert(f"ACCOUNT CHECKPOINT / BLOCK ({blk}) — bot stopped. "
                             f"Is account ko kuch din araam do.")
                except Exception:
                    pass
            try:
                await ss(page, "account_block")
            except Exception:
                pass
            config["_end_reason"] = "account_blocked"
            stop_event.set()
            return "blocked"

        members, privacy, already, page_blocked, is_canada, post_disabled, non_english = \
            await get_group_info(page)

        if page_blocked:
            send_ui("log", text=f"⛔ Pages not allowed: {name}")
            log_csv(area, name, url, "page_not_allowed", members, privacy)
            return "skipped"

        # Members post nahi kar sakte -> is business ke liye bekaar
        if config.get("skip_no_post", True) and post_disabled:
            send_ui("log", text=f"🚫 Members can't post here, skip: {name}")
            log_csv(area, name, url, "posting_disabled", members, privacy)
            return "skipped"

        # Buy/sell type + employee ke apne "don't-join" keywords
        try:
            title = (await page.title()).lower()
        except:
            title = ""
        check_text = f"{name.lower()} {title}"
        all_blocked = BLOCKED_GROUP_KEYWORDS + list(config.get("custom_blocked", []))
        bad_kw = next((kw for kw in all_blocked if kw and kw in check_text), None)
        if bad_kw:
            send_ui("log", text=f"⏭️  Blocked keyword ('{bad_kw}'), skip: {name}")
            log_csv(area, name, url, "blocked_keyword", members, privacy)
            return "skipped"

        if is_canada:
            send_ui("log", text=f"🍁 Canada group, skip: {name}")
            log_csv(area, name, url, "non_usa", members, privacy)
            return "skipped"

        if non_english:
            send_ui("log", text=f"🌐 Non-English group ({non_english}), skip: {name}")
            log_csv(area, name, url, "non_english", members, privacy)
            return "skipped"

        if already:
            send_ui("log", text=f"⏭️  Already member: {name}")
            log_csv(area, name, url, "already_member", members, privacy)
            return "skipped"

        if members > 0 and members < config["min_members"]:
            send_ui("log", text=f"⏭️  Skip ({members} members < {config['min_members']}): {name}")
            log_csv(area, name, url, "low_members", members, privacy)
            return "skipped"

        # ── Public / Private ratio ──────────────────────────
        # Employee set karta hai kitne % public join karne hain (baaqi private).
        # Jo type target se aage nikal jaye usko skip karo jab tak doosra
        # catch up na kare — session bhar mein ratio balance ho jata hai.
        pub_pct = config.get("public_pct", 30)
        jp = config.get("_jp", 0)
        jv = config.get("_jpriv", 0)
        tot = jp + jv
        if privacy == "Public":
            if pub_pct <= 0:
                send_ui("log", text=f"⚖️  100% private set — skip public: {name}")
                log_csv(area, name, url, "ratio_public", members, privacy)
                return "skipped"
            if tot >= 4 and (jp + 1) / (tot + 1) > pub_pct / 100.0 + 0.05:
                send_ui("log", text=f"⚖️  Public quota reached ({jp}/{tot}), skip: {name}")
                log_csv(area, name, url, "ratio_public", members, privacy)
                return "skipped"
        elif privacy == "Private":
            if pub_pct >= 100:
                send_ui("log", text=f"⚖️  100% public set — skip private: {name}")
                log_csv(area, name, url, "ratio_private", members, privacy)
                return "skipped"
            if tot >= 4 and (jv + 1) / (tot + 1) > (100 - pub_pct) / 100.0 + 0.05:
                send_ui("log", text=f"⚖️  Private quota reached ({jv}/{tot}), skip: {name}")
                log_csv(area, name, url, "ratio_private", members, privacy)
                return "skipped"

        # Public group mein engagement check karo — neeche scroll kar ke
        # dekhte hain ke posts pe reactions/comments aa rahe hain ya nahi.
        # (K/M numbers ab sahi parse hote hain, is liye bade groups pe
        # bhi chalta hai)
        if privacy == "Public":
            active = await check_group_activity(page)
            if not active:
                send_ui("log", text=f"⏭️  Skip (low activity/admin-only): {name}")
                log_csv(area, name, url, "low_activity", members, privacy)
                return "skipped"

        clicked = await click_join(page)
        if not clicked:
            send_ui("log", text=f"⏭️  Join button not found, skip: {name}")
            log_csv(area, name, url, "no_button", members, privacy)
            return "skipped"

        await sleep(rand_delay(1.5, 2.5))

        # ── Join dabane ke baad: pending-request limit ya account block? ──
        try:
            after_txt = await page.inner_text("body")
        except Exception:
            after_txt = ""
        if check_pending_limit(after_txt):
            send_ui("log", text="⏸️  Join-request limit reached (pending groups full) — bot rok raha hai")
            _a = config.get("_activity")
            if _a:
                try:
                    _a.alert("JOIN-REQUEST LIMIT reached — bahut se pending requests hain. "
                             "Bot stopped. Kuch requests approve/cancel hone do, phir chalao.")
                except Exception:
                    pass
            try:
                await ss(page, "pending_limit")
            except Exception:
                pass
            config["_end_reason"] = "pending_limit"
            stop_event.set()
            return "blocked"
        blk2 = await check_account_block(page, after_txt)
        if blk2:
            send_ui("log", text=f"🚫 ACCOUNT BLOCK after join: '{blk2}' — bot rok raha hai")
            _a = config.get("_activity")
            if _a:
                try:
                    _a.alert(f"ACCOUNT BLOCK ({blk2}) after a join — bot stopped.")
                except Exception:
                    pass
            config["_end_reason"] = "account_blocked"
            stop_event.set()
            return "blocked"

        await select_page(page, config.get("page_name", ""))
        await handle_questions(page)

        if privacy == "Public":
            config["_jp"] = config.get("_jp", 0) + 1
        elif privacy == "Private":
            config["_jpriv"] = config.get("_jpriv", 0) + 1
        _jp, _jv = config.get("_jp", 0), config.get("_jpriv", 0)
        _tt = _jp + _jv
        _mix = f" | mix {round(100*_jp/_tt)}% pub / {round(100*_jv/_tt)}% priv" if _tt else ""
        send_ui("log", text=f"✅ Joined: {name} ({members} members | {privacy}){_mix}")
        log_csv(area, name, url, "joined", members, privacy)
        save_joined(url)
        return "joined"

    except Exception as e:
        import traceback
        with open(f"error_log{SUFFIX}.txt", "a", encoding="utf-8") as ef:
            ef.write(f"\n--- {datetime.now()} | {name} ---\n{e}\n{traceback.format_exc()}\n")
        send_ui("log", text=f"⏭️  Error: {str(e)[:80]}")
        log_csv(area, name, url, "error")
        return "skipped"

async def search_and_join(page, city, already_joined, config, joined_today=0, pacer=None):
    global CURRENT_CITY
    CURRENT_CITY = city
    joined  = 0
    skipped = 0
    limit   = config["daily_limit"]

    if stop_event.is_set() or joined_today >= limit:
        return joined, skipped

    send_ui("target", text=city)

    # Pura target (city + state, ya county + state) se search — sirf city
    # naam se search karna galat results deta tha
    query = city
    url   = f"https://www.facebook.com/search/groups/?q={query.replace(' ','%20')}"
    send_ui("log", text=f"🔍 Searching: '{query}'")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await sleep(rand_delay(1.5, 2.5))
    except:
        return joined, skipped

    # Location filter sirf city search ke liye lagao — county search mein
    # nahi (county naam is filter ke sath match nahi karta, galat results dete)
    if "County" not in city:
        filter_ok = await apply_fb_filters(page, city)
        if not filter_ok:
            # USA location filter nahi laga — bina filter ke Canada/wrong
            # country ke groups aate hain, isliye yeh city chhor do
            send_ui("log", text=f"   ⏭️  Skipping '{city}' — USA location filter could not be applied")
            return joined, skipped
    else:
        send_ui("log", text=f"   ℹ️  County search — skipping location filter")

    # Scroll to load more groups
    for _ in range(4):
        await page.keyboard.press("End")
        await sleep(rand_delay(0.7, 1.2))

    # Group links + har link ke search-card ka text bhi utha lo — member
    # count wahan pehle se likha hota hai ("12K members" waghera), toh
    # chhote groups ko kholne ki zaroorat hi nahi (10-15 sec/group bachta hai)
    cards = await page.evaluate("""
        () => {
            const out = {};
            document.querySelectorAll('a[href*="/groups/"]').forEach(a => {
                let href = a.href.split('?')[0].replace(/\\/+$/, '');
                if (!/\\/groups\\/[a-zA-Z0-9._-]+$/.test(href)) return;
                // Anchor se upar chadho jab tak 'member' likha text na mile
                let cur = a, depth = 0, txt = '';
                while (cur && depth < 8) {
                    const t = (cur.innerText || '');
                    if (t.toLowerCase().includes('member') && t.length < 500) { txt = t; break; }
                    cur = cur.parentElement; depth++;
                }
                if (!(href in out) || txt.length > (out[href] || '').length) out[href] = txt;
            });
            return out;
        }
    """)

    urls = []
    pre_skipped = 0
    for clean, card_txt in cards.items():
        if clean in already_joined:
            continue
        slug = clean.split("/groups/")[-1]
        name = slug.strip("/").replace("-", " ").title()
        # Blocked keyword slug mein? Visit kiye baghair hi chhor do
        slug_l = slug.replace("-", "").replace("_", "").replace(".", "").lower()
        _blk = BLOCKED_GROUP_KEYWORDS + list(config.get("custom_blocked", []))
        if any(kw and kw.replace(" ", "") in slug_l for kw in _blk):
            continue
        # Card pe member count likha hai? Chhota group wahin skip karo
        m = re.search(r'([\d.,]+\s*[KkMm]?)\s*members', card_txt or "", re.I)
        card_members = _parse_count(m.group(1)) if m else 0
        if 0 < card_members < config["min_members"]:
            card_privacy = "Private" if "Private" in (card_txt or "") else "Public" if "Public" in (card_txt or "") else "?"
            log_csv(city, name, clean, "low_members", card_members, card_privacy)
            skipped += 1
            pre_skipped += 1
            send_ui("skipped")
            _pa = config.get("_activity")
            if _pa:
                try:
                    _pa.record_skip("low_members")
                except Exception:
                    pass
            continue
        urls.append(clean)

    if pre_skipped:
        send_ui("log", text=f"   ⚡ {pre_skipped} small groups skipped from search results (not opened)")
    send_ui("log", text=f"   📋 {len(urls)} groups found")

    for group_url in list(urls):
        if stop_event.is_set() or joined_today + joined >= limit:
            break

        # Working-hours ke bahar ho to yahin ruk jao (window khulne tak)
        if pacer is not None:
            await pacer.wait_for_window()
            if stop_event.is_set():
                break

        name = group_url.split("/groups/")[-1].strip("/").replace("-", " ").title()
        status = await join_one_group(page, group_url, name, city, config)

        _act = config.get("_activity")
        if status == "blocked":
            # account block / pending-limit -> join_one_group ne stop_event set kiya
            break
        if status == "joined":
            joined += 1
            already_joined.add(group_url)
            send_ui("joined", count=joined_today + joined)
            if _act:
                try:
                    _act.record_join()
                except Exception:
                    pass

        elif status == "skipped":
            skipped += 1
            send_ui("skipped")
            if _act:
                try:
                    _act.record_skip(_LAST_CSV_STATUS)
                except Exception:
                    pass

        # Delay strategy: Facebook sirf JOIN action ko sensitive samajhta
        # hai — group ka page dekhna aam browsing hai. Isliye join ke baad
        # poora delay, skip ke baad chhota sa.
        if status == "joined":
            if pacer is not None:
                # Human pacing: din bhar phaila ke + breaks (account safe)
                await pacer.pace(joined_today + joined)
            else:
                wait = rand_delay(config["delay_min"], config["delay_max"])
                send_ui("log", text=f"   ⏳ {wait:.0f}s wait...")
                await sleep(wait)
        else:
            await sleep(rand_delay(1.5, 3.5))

    return joined, skipped

# ── Stealth browser launch ──────────────────────────────────
# Facebook plain Playwright/Chromium ko "automation" ke taur pe detect kar
# leta hai (navigator.webdriver=true, missing plugins, "Chrome is being
# controlled..." infobar wagera) — isi ki wajah se login ke baad kabhi
# kabhi checkpoint/captcha aa ke wapas login page pe bhej deta hai. Ye
# helper wahi kami door karta hai — har browser (login/join/logout) isi se
# khulta hai taake sab jagah ek jaisi (aur behtar) fingerprint mile.
_STEALTH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Init script har page/frame par chalta hai FB ka apna JS chalne se PEHLE.
# Ye woh saare "ye ek automated / server browser hai" wale signals chhupata
# hai jinki wajah se FB login ke baad captcha/checkpoint deta hai:
#   - navigator.webdriver
#   - plugins / mimeTypes khaali hona
#   - window.chrome object missing
#   - WebGL vendor/renderer "SwiftShader" / "Google Inc." (VPS bina GPU ka
#     tell-tale sign — asli Intel GPU jaisa bana dete hain)
#   - hardwareConcurrency / deviceMemory bahut kam (VPS = 1-2 core) —
#     asli PC jaise 8 kar dete hain
#   - permissions.query mismatch
_STEALTH_INIT_JS = """
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
  } catch (e) {}
  try {
    const mk = (name, filename, desc) => {
      const p = { name, filename, description: desc, length: 1 };
      p[0] = { type: 'application/pdf', suffixes: 'pdf', description: desc };
      return p;
    };
    const arr = [
      mk('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      mk('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      mk('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      mk('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      mk('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format'),
    ];
    Object.defineProperty(navigator, 'plugins', { get: () => arr });
    Object.defineProperty(navigator, 'mimeTypes', {
      get: () => [{ type: 'application/pdf', suffixes: 'pdf', description: '' }]
    });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
  } catch (e) {}
  try {
    if (!window.chrome) { window.chrome = {}; }
    if (!window.chrome.runtime) { window.chrome.runtime = {}; }
    if (!window.chrome.app) { window.chrome.app = { isInstalled: false }; }
  } catch (e) {}
  try {
    const origQuery = window.navigator.permissions &&
                      window.navigator.permissions.query;
    if (origQuery) {
      window.navigator.permissions.query = (params) => (
        params && params.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : origQuery(params)
      );
    }
  } catch (e) {}
  // WebGL vendor/renderer -> asli Intel GPU jaisa (VPS software-renderer chhupao)
  try {
    const spoof = (proto) => {
      const gp = proto.getParameter;
      proto.getParameter = function (p) {
        if (p === 37445) return 'Intel Inc.';                 // UNMASKED_VENDOR_WEBGL
        if (p === 37446) return 'Intel Iris OpenGL Engine';   // UNMASKED_RENDERER_WEBGL
        return gp.apply(this, [p]);
      };
    };
    if (window.WebGLRenderingContext)  spoof(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) spoof(WebGL2RenderingContext.prototype);
  } catch (e) {}
})();
"""


async def _launch_ctx(p, headless: bool, viewport=None, extra_args=None):
    """Persistent-context browser — automation/VPS fingerprint chhupa ke."""
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-notifications",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=IsolateOrigins,site-per-process",
    ]
    if extra_args:
        args += extra_args
    kwargs = dict(
        user_data_dir=PW_PROFILE_DIR,
        headless=headless,
        args=args,
        ignore_default_args=["--enable-automation"],
        user_agent=_STEALTH_UA,
        locale="en-US",
        timezone_id="America/Chicago",   # US timezone — JS clock US location se match kare
        color_scheme="light",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    if viewport is not None:
        kwargs["viewport"] = viewport
    ctx = await p.chromium.launch_persistent_context(**kwargs)
    try:
        await ctx.add_init_script(_STEALTH_INIT_JS)
    except Exception:
        pass
    # persistent-context ka pehla (about:blank) page init-script add karne se
    # PEHLE ban chuka hota hai — us par stealth patch nahi lagta. Isliye ek
    # fresh page kholo (usi par init-script chalega) aur purana band kar do,
    # taake callers ka ctx.pages[0] patched page ho.
    try:
        old_pages = list(ctx.pages)
        await ctx.new_page()
        for pg in old_pages:
            try:
                await pg.close()
            except Exception:
                pass
    except Exception:
        pass
    return ctx


# ── Playwright Main ───────────────────────────────────────────

async def playwright_main(config):
    already_joined = load_joined()
    total         = load_total()
    total_skipped = load_total_skipped()
    # joined_today PERSISTENT hai — agar aaj is profile ne pehle hi X group
    # join kiye hain (restart / crash / PC reboot se pehle), to wahi se aage
    # badhte hain, 0 se nahi. Daily limit bhi isi ke hisaab se lagta hai.
    _act0 = config.get("_activity")
    joined_today  = _act0.joined_today() if _act0 else 0
    _session_start = joined_today          # is run ka apna count nikalne ke liye
    skipped_today = 0
    if joined_today:
        send_ui("log", text=f"↻ Aaj ab tak {joined_today} group join ho chuke — "
                            f"wahin se continue (limit {config.get('daily_limit', 250)}).")
        send_ui("joined", count=joined_today)

    async with async_playwright() as p:
        send_ui("log", text="🌐 Launching browser...")
        ctx = await _launch_ctx(p, headless=False, viewport={"width": 1366, "height": 768},
                               extra_args=["--start-maximized"])

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        send_ui("log", text="📘 Opening Facebook...")
        await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=20000)
        await sleep(2)
        await dismiss_popups(page)

        await sleep(3)

        # Logged in check — home feed ya nav bar dikh raha hai?
        # Facebook logged-in hone par [role="navigation"] dikhta hai
        nav_visible  = await page.locator('[role="navigation"]').count() > 0
        feed_visible = await page.locator('[role="feed"]').count() > 0
        is_logged_in = nav_visible or feed_visible

        send_ui("log", text=f"Login status: nav={nav_visible} feed={feed_visible}")

        if not is_logged_in:
            send_ui("log", text="⚠️  Please log in — enter Facebook ID and password in the browser!")
            send_ui("log", text="⏳ Script will continue automatically after login (3 min)...")
            try:
                await page.wait_for_selector('[role="navigation"]', timeout=180000)
            except:
                send_ui("log", text="❌ Timeout — press START again")
                await ctx.close()
                send_ui("stopped")
                return
            await sleep(3)
            send_ui("log", text="✅ Logged in! Session saved — next time it's automatic.")
        else:
            send_ui("log", text="✅ Already logged in to Facebook!")

        # Switch to page
        page_name = config.get("page_name") or DEFAULT_PAGE_NAME
        page_link = (config.get("page_link") or DEFAULT_PAGE_LINK).strip()
        if not page_name and not page_link:
            send_ui("log", text="❌ Page Link is empty — never joining from personal profile. Stopping.")
            await ctx.close()
            send_ui("stopped")
            return

        send_ui("log", text=f"🔄 Switching to page '{page_name or page_link}'...")
        switched = False
        if page_link:
            switched = await switch_via_link(page, page_link, page_name)
        if not switched and page_name:
            if page_link:
                send_ui("log", text="   Link switch failed — trying by name...")
            switched = await switch_to_page(page, page_name)
        if switched:
            send_ui("log", text=f"✅ Switched to page '{page_name or page_link}'!")
        else:
            send_ui("log", text="❌ Could not switch to the page — never joining from personal profile. Stopping.")
            send_ui("log", text="   Check: (1) Page Link is correct (2) This account is an admin of the page")
            config["_end_reason"] = "setup_failed"
            await ctx.close()
            send_ui("stopped")
            return

        # ── License watchdog — har 2 min: heartbeat + expiry check ──
        act = config.get("_activity")
        _integrity_ctr = {"n": 0}

        async def _license_watchdog():
            while not stop_event.is_set():
                await sleep(LICENSE_RECHECK_SEC)
                if stop_event.is_set():
                    return
                if act:
                    try:
                        act.heartbeat()
                    except Exception:
                        pass

                # Har ~10 cycle (~20 min): SIRF file-tamper check (koi version
                # update mid-session NAHI — warna chal rahi joining bina error
                # ke silently ruk jaati thi). Sirf tab rukta hai jab koi file
                # sach mein tamper hui ho — aur tab clear Discord alert bhi
                # jata hai. Blocking network call hai — thread mein.
                _integrity_ctr["n"] += 1
                if _integrity_ctr["n"] % 10 == 0:
                    try:
                        import updater

                        def _integrity_alert(msg):
                            if act:
                                try:
                                    act.alert(msg)
                                except Exception:
                                    pass

                        tampered = await asyncio.to_thread(
                            updater.check_integrity_only,
                            getattr(lic, "UPDATE_URL", ""), APP_DIR,
                            print, _integrity_alert)
                        if tampered:
                            send_ui("log", text=f"🚨 File tampering detected ({', '.join(tampered)}) "
                                                 f"— bot is stopping. Restart to reload verified files.")
                            config["_end_reason"] = "file_tamper"
                            stop_event.set()
                            return
                    except Exception:
                        pass
                # account block periodically bhi check karo (current page)
                try:
                    blk = await check_account_block(page)
                except Exception:
                    blk = ""
                if blk:
                    send_ui("log", text=f"🚫 ACCOUNT BLOCK (watchdog): '{blk}' — stopping")
                    if act:
                        try:
                            act.alert(f"ACCOUNT CHECKPOINT / BLOCK ({blk}) — bot stopped.")
                        except Exception:
                            pass
                    config["_end_reason"] = "account_blocked"
                    stop_event.set()
                    return
                chk = lic.validate_key(config.get("license_key", ""))
                if not chk["ok"]:
                    send_ui("log", text=f"⛔ License: {chk['error']}")
                    send_ui("log", text="   Bot is stopping — activate a new key and press START again.")
                    config["_end_reason"] = "license_expired"
                    stop_event.set()
                    return

                # Owner ka remote ON/OFF switch — chalte hue bhi rok sakta hai
                try:
                    import updater as _upd
                    _sok, _smsg = _upd.service_allows(
                        getattr(lic, "UPDATE_URL", ""), config.get("employee", ""))
                except Exception:
                    _sok, _smsg = True, ""
                if not _sok:
                    send_ui("log", text=f"⏸️  {_smsg} — bot ruk raha hai.")
                    if act:
                        try:
                            act.alert(f"Bot admin-paused ({_smsg}) — stopped.")
                        except Exception:
                            pass
                    config["_end_reason"] = "service_paused"
                    stop_event.set()
                    return

        wd_task = asyncio.create_task(_license_watchdog())

        pacer = HumanPacer(config)
        if pacer.on:
            send_ui("log", text=f"🚶 Human pacing ON — joins din bhar phaile "
                                f"({_fmt_hhmm(pacer.win_start)}–{_fmt_hhmm(pacer.win_end)}), "
                                f"beech mein breaks. (Account safety)")
        else:
            send_ui("log", text="⚡ Human pacing OFF — fixed delays "
                                f"({config.get('delay_min')}–{config.get('delay_max')}s).")

        selection = config["city"]
        limit     = config["daily_limit"]

        # "ALL AREAS" select ho toh saari 65 areas loop karo, warna sirf ek
        if selection == ALL_AREAS_LABEL:
            areas_to_run = AREAS.copy()
            random.shuffle(areas_to_run)
        else:
            areas_to_run = [selection]

        total_areas = len(areas_to_run)
        send_ui("log", text=f"📍 {total_areas} area(s) to process")

        for area_idx, area in enumerate(areas_to_run, 1):
            if stop_event.is_set() or joined_today >= limit:
                break

            send_ui("log", text=f"\n🏙️  Area {area_idx}/{total_areas}: {area}")
            send_ui("area", text=f"Area {area_idx}/{total_areas}: {area}")

            # Cache se cities + counties nikalo (fast). Custom typed city ho
            # toh cache mein nahi hogi — live calculate hoga (thoda slow).
            loop = asyncio.get_event_loop()
            _same_state = config.get("same_state_only", True)
            _inc_counties = config.get("include_counties", True)
            targets = await loop.run_in_executor(
                None, get_targets_for_area, area, _same_state, _inc_counties)
            random.shuffle(targets)
            _st = _area_state_code(area)
            send_ui("log", text=f"   🗺️  {len(targets)} targets"
                    + (f" — {_st} only (50-mi radius)" if _same_state and _st
                       else "")
                    + (" (cities + counties)" if _inc_counties else " (cities only, no counties)"))

            area_joined_start = joined_today
            for target in targets:
                if stop_event.is_set() or joined_today >= limit:
                    break
                config["_current_city"] = target
                n_joined, n_skipped = await search_and_join(
                    page, target, already_joined, config, joined_today, pacer)
                joined_today  += n_joined
                skipped_today += n_skipped
                total         += n_joined
                total_skipped += n_skipped
                save_total(total)
                save_total_skipped(total_skipped)
                send_ui("total", count=total)
                send_ui("total_skipped", count=total_skipped)

            area_joined = joined_today - area_joined_start
            send_ui("log", text=f"   ✅ Area '{area}' complete: joined {area_joined} groups")

        try:
            wd_task.cancel()
            try:
                await wd_task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception:
            pass
        if _GEMINI_DEAD_REASON:
            config["_end_reason"] = _GEMINI_DEAD_REASON
        elif config.get("_end_reason") not in ("license_expired", "account_blocked",
                                                "pending_limit", "setup_failed",
                                                "file_tamper", "service_paused"):
            config["_end_reason"] = "user_stop" if stop_event.is_set() else "completed"

        _this_run = joined_today - _session_start
        send_ui("log", text=f"\n🎉 Done! This run: joined {_this_run} groups, skipped {skipped_today}. "
                            f"Aaj total: {joined_today}/{config.get('daily_limit', 250)}.")
        send_ui("log", text="✅ Session complete!")
        await ctx.close()
        send_ui("stopped")

async def _login_browser_main():
    """Sirf browser kholo Facebook pe — user login karega, joining kuch
    nahi. User window band karega -> session save -> ho gaya."""
    async with async_playwright() as p:
        send_ui("log", text="🌐 Browser khul raha hai — Facebook pe login karo…")
        ctx = await _launch_ctx(p, headless=False, viewport={"width": 1366, "height": 768},
                               extra_args=["--start-maximized"])
        closed = {"v": False}
        ctx.on("close", lambda *a: closed.__setitem__("v", True))
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.goto("https://www.facebook.com", wait_until="domcontentloaded",
                            timeout=20000)
        except Exception:
            pass
        send_ui("log", text="   👉 Login karo, phir browser window BAND kar do (ya STOP dabao).")
        # user ke band karne / stop_event tak intezar
        while not closed["v"] and not stop_event.is_set():
            await sleep(1)
            try:
                if not ctx.pages:      # saari windows band
                    break
            except Exception:
                break
        logged_in = False
        try:
            if ctx.pages:
                try:
                    logged_in = await ctx.pages[0].locator(
                        '[role="navigation"]').count() > 0
                except Exception:
                    logged_in = False
            await ctx.close()
        except Exception:
            pass
        send_ui("log", text=("✅ Login session save ho gaya — ab START dabao."
                             if logged_in else
                             "ℹ️ Browser band. Agar login nahi hua to dobara "
                             "'Open browser' dabao."))
    send_ui("login_done")


def run_login_browser():
    try:
        asyncio.run(_login_browser_main())
    except Exception as e:
        send_ui("log", text=f"login browser error: {str(e)[:80]}")
        send_ui("login_done")


async def _logout_main():
    """Facebook session (cookies + local storage) clear karo — account
    suspend/checkpoint hone par employee khud yahan se logout kar sake,
    dobara login karne ke liye. Browser dikhta nahi (background mein)."""
    send_ui("log", text="🚪 Facebook se logout ho raha hai…")
    try:
        async with async_playwright() as p:
            ctx = await _launch_ctx(p, headless=True)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                await page.goto("https://www.facebook.com/", timeout=30000,
                                wait_until="domcontentloaded")
                await page.evaluate(
                    "() => { try { localStorage.clear(); sessionStorage.clear(); "
                    "} catch(e) {} }")
            except Exception:
                pass
            await ctx.clear_cookies()
            logged_out = True
            try:
                await page.goto("https://www.facebook.com/", timeout=30000,
                                wait_until="domcontentloaded")
                await sleep(2)
                html = await page.content()
                logged_out = ("login" in page.url.lower()
                             or 'name="email"' in html or 'name="pass"' in html)
            except Exception:
                pass
            await ctx.close()
        send_ui("log", text=("✅ Logout ho gaya — session clear. Dobara login "
                             "karne ke liye 'Open browser & log in' dabao."
                             if logged_out else
                             "⚠️ Logout ki koshish hui lekin verify nahi ho saka "
                             "— 'Open browser' se check kar lo."))
    except Exception as e:
        send_ui("log", text=f"logout error: {str(e)[:80]}")
    send_ui("logout_done")


def run_logout_browser():
    try:
        asyncio.run(_logout_main())
    except Exception as e:
        send_ui("log", text=f"logout error: {str(e)[:80]}")
        send_ui("logout_done")


def run_playwright(config):
    # ── License gate — the bot does not run without a valid key ──
    info = lic.validate_key(config.get("license_key", ""))
    if not info["ok"]:
        send_ui("log", text=f"⛔ License invalid: {info['error']}")
        send_ui("log", text="   Activate a valid key, then press START.")
        send_ui("stopped")
        return

    # ── Owner ka remote ON/OFF switch (admin bina PC chhue rok/chalu kar sake) ──
    try:
        import updater as _upd
        _sok, _smsg = _upd.service_allows(getattr(lic, "UPDATE_URL", ""),
                                          config.get("employee", ""))
    except Exception:
        _sok, _smsg = True, ""
    if not _sok:
        send_ui("log", text=f"⏸️  {_smsg}")
        send_ui("log", text="   Administrator ne is bot ko rok rakha hai — "
                            "baad mein START karo (ya admin se poochho).")
        send_ui("stopped")
        return

    # ── Gemini AI answers (optional, key rotation) ──
    global GEMINI_KEYS, _gk_idx, _GEMINI_DEAD_REASON
    GEMINI_KEYS = list(config.get("gemini_keys", []) or [])
    _gk_idx = 0
    _gk_cooldown.clear()
    _GEMINI_DEAD_REASON = None   # purani run ka stale flag na reh jaye
    send_ui("log", text=(f"🤖 AI answers ON ({GEMINI_MODEL}) — {len(GEMINI_KEYS)} key(s) in rotation")
            if GEMINI_KEYS else "💬 AI answers OFF — using built-in template answers")

    # ── Public/Private target + filters (session-wide counters) ──
    config["_jp"] = 0
    config["_jpriv"] = 0
    _pp = config.get("public_pct", 30)
    send_ui("log", text=f"🎯 Target mix: {_pp}% public / {100 - _pp}% private"
            + ("  ·  skip no-post groups" if config.get("skip_no_post", True) else ""))
    if config.get("custom_blocked"):
        send_ui("log", text="   ⛔ Extra blocked keywords: "
                + ", ".join(config["custom_blocked"][:12]))

    # ── Usage log shuru (+ live report agar REPORT_URL set hai) ──
    act = ActivityLog(config.get("employee", "unknown"), config.get("key_id", ""),
                      config.get("license_exp", ""), INSTANCE)
    global _ACT
    _ACT = act
    try:
        act.set_machine(lic.machine_id())
    except Exception:
        pass
    try:
        act.set_page_link(config.get("page_link", ""))
    except Exception:
        pass
    _rurl = (getattr(lic, "REPORT_URL", "") or "").strip()
    if _rurl:
        _kind = ("Discord" if "discord.com/api/webhooks" in _rurl
                 else "Telegram" if "api.telegram.org/bot" in _rurl else "webhook")
        send_ui("log", text=f"📡 Live tracking ON — reporting to {_kind}")
    else:
        send_ui("log", text="📡 Live tracking OFF (no REPORT_URL in license_common.py)")
    act.start_session()
    config["_activity"] = act
    config["_end_reason"] = "completed"

    # ── Auto-resume on crash ──────────────────────────────────
    # Bot / browser beech mein crash ho jaye (ya browser window band ho
    # jaye) to khud restart hota hai aur AAJ ke joins wahin se continue
    # karta hai (joined_today persistent hai). User ne STOP dabaya ho ya
    # koi terminal reason ho (license / block / gemini / tamper) to restart
    # NAHI hota. Max 6 koshish, badhta hua wait.
    MAX_RESTARTS = 6
    _terminal = ("license_expired", "account_blocked", "pending_limit",
                 "setup_failed", "file_tamper", "gemini_keys_failed",
                 "service_paused")
    restarts = 0
    while True:
        try:
            asyncio.run(playwright_main(config))
            act.end_session(config.get("_end_reason", "completed"))
            break                                   # normal / terminal finish
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            try:
                with open(f"error_log{SUFFIX}.txt", "a", encoding="utf-8") as ef:
                    ef.write(f"\n--- {datetime.now()} | crash (run_playwright) ---\n{tb}\n")
            except Exception:
                pass
            try:
                act.end_session("error")
            except Exception:
                pass

            # User ne STOP dabaya / terminal reason -> restart nahi
            if stop_event.is_set() or config.get("_end_reason") in _terminal:
                send_ui("log", text=f"❌ Error: {str(e)[:120]}")
                send_ui("log", text=f"   (Details: error_log{SUFFIX}.txt)")
                break

            restarts += 1
            if restarts > MAX_RESTARTS:
                send_ui("log", text=f"❌ {MAX_RESTARTS} baar crash hua — ab ruk raha hoon. "
                                    f"error_log{SUFFIX}.txt dekho / bot dobara START karo.")
                try:
                    _a = config.get("_activity")
                    if _a:
                        _a.alert(f"Bot {MAX_RESTARTS} baar crash hua aur restart fail — "
                                 f"is profile ko manually START karna hoga.")
                except Exception:
                    pass
                break

            wait = min(60, 10 * restarts)
            send_ui("log", text=f"⚠️ Bot crash hua — {wait}s baad KHUD restart ho raha hai "
                                f"(koshish {restarts}/{MAX_RESTARTS}). Aaj ke joins safe hain, "
                                f"wahin se continue hoga.")
            # Chromium ke stale lock hata do (unclean exit ke baad relaunch
            # rok sakte hain)
            for _lk in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                try:
                    os.remove(os.path.join(PW_PROFILE_DIR, _lk))
                except Exception:
                    pass
            time.sleep(wait)
            if stop_event.is_set():          # wait ke doran user ne STOP dabaya
                break
            config["_end_reason"] = "completed"
            _GEMINI_DEAD_REASON = None     # already declared global at top of fn
            try:
                act.start_session()
            except Exception:
                pass
            continue

    send_ui("stopped")

# ── Tkinter UI ────────────────────────────────────────────────

BG        = "#0f1117"   # window background (dark)
CARD_BG   = "#181b23"   # cards
BORDER    = "#262b38"   # borders
INPUT_BG  = "#20242f"   # entry fields
LOG_BG    = "#0b0d12"   # console
TXT       = "#e8eaf0"   # primary text
TXT_MUTED = "#8b90a0"   # secondary text
FB_BLUE   = "#3b82f6"   # accent
FB_BLUE_D = "#2563eb"
GREEN     = "#22c55e"
GREEN_BG  = "#14231a"
ORANGE    = "#f59e0b"
ORANGE_BG = "#26200f"
RED       = "#ef4444"

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"FB Group Joiner  v{APP_VERSION}  —  Account {INSTANCE}   ·   build {_build_no()}")
        # 2-column layout. Left settings scroll karte hain aur START button
        # left column ke neeche PINNED hai — isliye chhoti screen par bhi
        # START hamesha nazar aata hai.
        h = min(660, max(480, root.winfo_screenheight() - 90))
        self.root.geometry(f"860x{h}")
        self.root.minsize(720, 440)
        self.root.configure(bg=BG)

        self.joined_today   = 0
        self.skipped_today  = 0
        self.running        = False
        self._login_open    = False
        self._logout_open   = False
        self.lic_info       = {"ok": False, "error": "No license", "employee": ""}

        self._style()
        self._build()
        self._refresh_gemini_status()
        self._refresh_mix_lbl()
        self._refresh_license_ui()             # fast, local-only check
        self._license_gate()                   # <-- ask for a key BEFORE anything else
        if self._alive():
            self._poll()
            # Networked re-check (revocation URL) shortly after the window opens,
            # then every 2 min while idle — so a remote "Suspend" takes hold
            # without freezing the UI on startup.
            self.root.after(1500, self._bg_license_recheck)

    # ── License UI ───────────────────────────────────────────
    def _alive(self):
        try:
            return bool(self.root.winfo_exists())
        except tk.TclError:
            return False

    def _key_from_file(self, path):
        """Read a key string from a .fbjkey (JSON {"key": ...}) or plain-text file."""
        raw = open(path, encoding="utf-8").read().strip()
        try:
            d = json.loads(raw)
            return (d.get("key") or "").strip() if isinstance(d, dict) else ""
        except Exception:
            return raw  # plain key string

    # ── Targeting UI ────────────────────────────────────────
    def _public_pct(self):
        try:
            return max(0, min(100, int(self.public_pct_var.get())))
        except Exception:
            return 30

    def _refresh_mix_lbl(self):
        p = self._public_pct()
        self.mix_lbl.config(text=f"→ {p}% public / {100 - p}% private")

    def _block_keywords(self):
        try:
            txt = self.block_box.get("1.0", "end")
        except Exception:
            txt = ""
        out, seen = [], set()
        for ln in txt.splitlines():
            k = ln.strip().lower()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    # ── Gemini API keys UI (rotation) ───────────────────────
    def _gemini_box_text(self):
        try:
            return self.gemini_box.get("1.0", "end")
        except Exception:
            return ""

    def _refresh_gemini_status(self):
        keys = resolve_gemini_keys(self._gemini_box_text())
        if keys:
            self.gemini_status.config(
                text=f"AI answers: ON  ·  {len(keys)} key(s) in rotation  ({GEMINI_MODEL})",
                fg=GREEN)
        else:
            self.gemini_status.config(
                text="AI answers: OFF — using built-in template answers", fg=TXT_MUTED)

    def _persist_gemini(self):
        s = load_settings()
        s["gemini_keys"] = resolve_gemini_keys(self._gemini_box_text())
        save_settings(s)

    def _persist_simple(self):
        """Page link + area + pacing settings turant save — har profile
        (account) ka apna alag, taake dobara khulne par bhare rahein aur ek
        doosre ko overwrite na karein."""
        try:
            s = load_settings()
            s[f"page_link{SUFFIX}"] = self.page_link_var.get().strip()
            s[f"city{SUFFIX}"] = self.city_var.get().strip()
            if hasattr(self, "pace_var"):
                s[f"pace_enabled{SUFFIX}"] = bool(self.pace_var.get())
                s[f"work_start{SUFFIX}"] = self.work_start_var.get().strip()
                s[f"work_end{SUFFIX}"] = self.work_end_var.get().strip()
            save_settings(s)
        except Exception:
            pass

    def _test_gemini(self):
        keys = resolve_gemini_keys(self._gemini_box_text())
        if not keys:
            self.gemini_status.config(text="No keys to test.", fg=ORANGE)
            return
        self.gemini_status.config(text=f"Testing {len(keys)} key(s)…", fg=TXT_MUTED)
        self._persist_gemini()

        def work():
            global GEMINI_KEYS, _gk_idx
            old = GEMINI_KEYS
            ok = 0
            sample = ""
            for k in keys:
                GEMINI_KEYS = [k]
                _gk_idx = 0
                _gk_cooldown.clear()
                try:
                    ans = _gemini_sync("Do you live in the area?", "Dallas TX")
                except Exception:
                    ans = None
                if ans:
                    ok += 1
                    sample = sample or ans
            GEMINI_KEYS = old
            msg = (f"{ok}/{len(keys)} keys OK — e.g. \"{sample[:40]}\"" if ok
                   else "All keys failed — check keys / internet")
            col = GREEN if ok else RED
            self.root.after(0, lambda: self.gemini_status.config(text=msg, fg=col))

        threading.Thread(target=work, daemon=True).start()

    def _bg_license_recheck(self):
        """Off-thread validate WITH the revocation URL; re-arm every 2 min."""
        if not self._alive():
            return
        if self.running:
            self.root.after(120000, self._bg_license_recheck)
            return

        def work():
            try:
                info = lic.validate_key(lic.load_active_key(), check_url=True)
            except Exception:
                info = None
            if self._alive():
                self.root.after(0, lambda: self._after_bg_recheck(info))

        threading.Thread(target=work, daemon=True).start()

    def _after_bg_recheck(self, info):
        if info is not None and not self.running:
            self._refresh_license_ui(info)
        if self._alive():
            self.root.after(120000, self._bg_license_recheck)

    def _refresh_license_ui(self, info=None):
        if info is None:
            info = lic.validate_key(lic.load_active_key(), check_url=False)
        self.lic_info = info
        if info["ok"]:
            self.lic_var.set(
                f"Licensed to:  {info['employee']}      "
                f"Expires:  {info['exp']}   ({info.get('time_left','')} left)")
            self.lic_lbl.config(fg=GREEN)
            self.lic_row.pack_forget()
            if not self.running:
                self.btn.config(state="normal")
        else:
            self.lic_var.set(f"Not activated  —  {info.get('error') or 'no license key'}")
            self.lic_lbl.config(fg=ORANGE)
            self.lic_row.pack(fill="x", padx=16, pady=(0, 8))
            self.btn.config(state="disabled")

    def _try_activate(self, key, parent=None):
        """Validate + save a key. Returns (ok, message)."""
        key = (key or "").strip()
        if not key:
            return False, "Paste a license key first."
        info = lic.validate_key(key)
        if not info["ok"]:
            return False, info["error"] or "Invalid key."
        lic.save_active_key(key)
        self._refresh_license_ui()
        return True, (f"Activated for {info['employee']} — "
                      f"expires {info['exp']} ({info.get('time_left','')} left).")

    def _activate_key(self):
        ok, msg = self._try_activate(self.key_entry_var.get())
        if ok:
            self.key_entry_var.set("")
            messagebox.showinfo("Activated", msg)
        else:
            messagebox.showerror("Key rejected", msg)

    def _load_transfer(self):
        path = filedialog.askopenfilename(
            title="Select the transfer_<name>.fbjkey file",
            filetypes=[("FB Joiner key", "*.fbjkey"), ("All files", "*.*")])
        if not path:
            return
        try:
            key = self._key_from_file(path)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if not key:
            messagebox.showerror("Error", "No key found in that file.")
            return
        self.key_entry_var.set(key)
        self._activate_key()

    def _show_machine_id(self):
        mid = lic.machine_id()
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(mid)
        except Exception:
            pass
        messagebox.showinfo(
            "Machine ID",
            f"This PC's Machine ID:\n\n{mid}\n\n"
            "Copied to clipboard. Send it to your administrator if you need a "
            "PC-locked key.")

    # ── Startup license gate — modal, blocks the app until activated ──
    def _license_gate(self):
        if self.lic_info.get("ok"):
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Activation Required")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        W, H = 540, 310
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - W) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - H) // 3
        dlg.geometry(f"{W}x{H}+{max(x, 0)}+{max(y, 0)}")

        tk.Label(dlg, text="License key required", bg=BG, fg=TXT,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=26, pady=(24, 4))
        tk.Label(dlg, text="This copy is not activated. Enter the license key provided\n"
                           "by your administrator to continue.",
                 bg=BG, fg=TXT_MUTED, font=("Segoe UI", 9),
                 justify="left").pack(anchor="w", padx=26)

        gv = tk.StringVar()
        ent = tk.Entry(dlg, textvariable=gv, font=("Consolas", 9), bg=INPUT_BG, fg=TXT,
                       insertbackground=TXT, relief="flat", highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=FB_BLUE)
        ent.pack(fill="x", padx=26, pady=(18, 6), ipady=5)
        ent.focus_set()

        gmsg = tk.Label(dlg, text="", bg=BG, fg=ORANGE, font=("Segoe UI", 9),
                        anchor="w", justify="left", wraplength=W - 52)
        gmsg.pack(fill="x", padx=26)

        def g_activate(_evt=None):
            ok, msg = self._try_activate(gv.get(), parent=dlg)
            if ok:
                dlg.grab_release()
                dlg.destroy()
            else:
                gmsg.config(text=msg, fg=ORANGE)

        def g_load():
            path = filedialog.askopenfilename(
                parent=dlg, title="Select the transfer_<name>.fbjkey file",
                filetypes=[("FB Joiner key", "*.fbjkey"), ("All files", "*.*")])
            if not path:
                return
            try:
                gv.set(self._key_from_file(path))
            except Exception as e:
                gmsg.config(text=str(e), fg=ORANGE)
                return
            g_activate()

        def g_mid():
            mid = lic.machine_id()
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(mid)
            except Exception:
                pass
            gmsg.config(text=f"Machine ID copied: {mid}", fg=FB_BLUE)

        br = tk.Frame(dlg, bg=BG)
        br.pack(fill="x", padx=26, pady=(18, 0))
        tk.Button(br, text="Activate", bg=GREEN, fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold"), cursor="hand2", padx=20, pady=6,
                  command=g_activate).pack(side="left")
        tk.Button(br, text="Load .fbjkey", bg=INPUT_BG, fg=TXT, relief="flat",
                  font=("Segoe UI", 10), cursor="hand2", padx=14, pady=6,
                  command=g_load).pack(side="left", padx=(8, 0))
        tk.Button(br, text="Machine ID", bg=INPUT_BG, fg=TXT, relief="flat",
                  font=("Segoe UI", 10), cursor="hand2", padx=14, pady=6,
                  command=g_mid).pack(side="left", padx=(8, 0))
        tk.Button(br, text="Quit", bg=INPUT_BG, fg=TXT_MUTED, relief="flat",
                  font=("Segoe UI", 10), cursor="hand2", padx=14, pady=6,
                  command=lambda: self.root.destroy()).pack(side="right")

        ent.bind("<Return>", g_activate)
        dlg.protocol("WM_DELETE_WINDOW",
                     lambda: self.root.destroy() if not self.lic_info.get("ok")
                     else dlg.destroy())
        self.root.wait_window(dlg)

    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dark.TCombobox",
                        fieldbackground=INPUT_BG, background=INPUT_BG,
                        foreground=TXT, arrowcolor=TXT_MUTED,
                        bordercolor=BORDER, lightcolor=INPUT_BG, darkcolor=INPUT_BG,
                        selectbackground=INPUT_BG, selectforeground=TXT)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", INPUT_BG)],
                  foreground=[("readonly", TXT)])
        style.configure("FB.Horizontal.TProgressbar",
                        troughcolor=INPUT_BG, bordercolor=BORDER,
                        background=GREEN, lightcolor=GREEN, darkcolor=GREEN,
                        thickness=8)
        self.root.option_add("*TCombobox*Listbox.background", INPUT_BG)
        self.root.option_add("*TCombobox*Listbox.foreground", TXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", FB_BLUE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "white")

    def _card(self, parent, **pack_opts):
        """Dark card container"""
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="x", **pack_opts)
        card = tk.Frame(outer, bg=CARD_BG, padx=14, pady=12,
                         highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x")
        return card

    def _section_title(self, parent, text):
        tk.Label(parent, text=text, bg=CARD_BG, fg=TXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 8))

    def _entry(self, parent, var, **pack_opts):
        e = tk.Entry(parent, textvariable=var, font=("Segoe UI", 11),
                     bg=INPUT_BG, fg=TXT, insertbackground=TXT,
                     relief="flat", highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=FB_BLUE)
        e.pack(fill="x", ipady=4, **pack_opts)
        return e

    def _build(self):
        # ── Header ───────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=CARD_BG)
        hdr.pack(fill="x")
        row = tk.Frame(hdr, bg=CARD_BG)
        row.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(row, text="FB Group Joiner", bg=CARD_BG, fg=TXT,
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(row, text=f"v{APP_VERSION}", bg=CARD_BG, fg=TXT_MUTED,
                 font=("Segoe UI", 10)).pack(side="left", padx=(8, 0))
        tk.Label(row, text=f"  ACCOUNT {INSTANCE}  ", bg=FB_BLUE, fg="white",
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(10, 0), pady=4)
        self.status_var = tk.StringVar(value="●  Idle — press START to begin")
        tk.Label(hdr, textvariable=self.status_var, bg=CARD_BG, fg=TXT_MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=16, pady=(0, 4))

        # ── License bar ──────────────────────────────────────
        self.lic_var = tk.StringVar(value="")
        self.lic_lbl = tk.Label(hdr, textvariable=self.lic_var, bg=CARD_BG,
                                fg=TXT_MUTED, font=("Segoe UI", 9, "bold"),
                                anchor="w")
        self.lic_lbl.pack(fill="x", padx=16, pady=(0, 4))

        self.lic_row = tk.Frame(hdr, bg=CARD_BG)
        self.key_entry_var = tk.StringVar()
        tk.Entry(self.lic_row, textvariable=self.key_entry_var, font=("Consolas", 9),
                 bg=INPUT_BG, fg=TXT, insertbackground=TXT, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=FB_BLUE).pack(side="left", fill="x", expand=True, ipady=3)
        tk.Button(self.lic_row, text="Activate", bg=FB_BLUE, fg="white", relief="flat",
                  font=("Segoe UI", 9, "bold"), cursor="hand2", padx=12,
                  command=self._activate_key).pack(side="left", padx=(6, 0))
        tk.Button(self.lic_row, text="Load .fbjkey", bg=INPUT_BG, fg=TXT, relief="flat",
                  font=("Segoe UI", 9), cursor="hand2", padx=10,
                  command=self._load_transfer).pack(side="left", padx=(6, 0))
        tk.Button(self.lic_row, text="Machine ID", bg=INPUT_BG, fg=TXT, relief="flat",
                  font=("Segoe UI", 9), cursor="hand2", padx=10,
                  command=self._show_machine_id).pack(side="left", padx=(6, 0))
        # pack/unpack _refresh_license_ui se hota hai

        tk.Frame(hdr, bg=CARD_BG, height=8).pack(fill="x")
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        # ── Main area: 2 columns ─────────────────────────────
        # Left = Settings + START  |  Right = Stats + Activity + Log
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=12, pady=12)
        main.columnconfigure(0, weight=0, minsize=310)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = tk.Frame(main, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = tk.Frame(main, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")

        # ══ LEFT COLUMN — START (pinned) + scrollable Settings ═══
        self.btn = tk.Button(left, text="▶   START",
                             bg=GREEN, fg="white",
                             font=("Segoe UI", 13, "bold"),
                             relief="flat", cursor="hand2",
                             activebackground="#16a34a", activeforeground="white",
                             command=self._toggle, pady=12)
        self.btn.pack(side="bottom", fill="x", pady=(8, 0))

        # Account row: login (browser kholo) + logout (session clear —
        # account suspend/checkpoint ho to employee khud yahan se karega)
        acct_row = tk.Frame(left, bg=BG)
        acct_row.pack(side="bottom", fill="x", pady=(8, 0))
        self.logout_btn = tk.Button(acct_row, text="🚪  Log out",
                                    bg=INPUT_BG, fg=TXT_MUTED, font=("Segoe UI", 9),
                                    relief="flat", cursor="hand2",
                                    activebackground=BORDER, activeforeground=TXT,
                                    command=self._do_logout, pady=6)
        self.logout_btn.pack(side="left", padx=(0, 6))
        self.login_btn = tk.Button(acct_row, text="🔓  Open browser & log in to Facebook",
                                   bg=INPUT_BG, fg=TXT, font=("Segoe UI", 9),
                                   relief="flat", cursor="hand2",
                                   activebackground=BORDER, activeforeground=TXT,
                                   command=self._open_login, pady=6)
        self.login_btn.pack(side="left", fill="x", expand=True)

        _sc = tk.Frame(left, bg=BG)
        _sc.pack(side="top", fill="both", expand=True)
        _canvas = tk.Canvas(_sc, bg=BG, highlightthickness=0)
        _vsb = tk.Scrollbar(_sc, orient="vertical", command=_canvas.yview)
        _canvas.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side="right", fill="y")
        _canvas.pack(side="left", fill="both", expand=True)
        card = tk.Frame(_canvas, bg=CARD_BG, padx=14, pady=12,
                        highlightbackground=BORDER, highlightthickness=1)
        _cw = _canvas.create_window((0, 0), window=card, anchor="nw")
        card.bind("<Configure>",
                  lambda e: _canvas.configure(scrollregion=_canvas.bbox("all")))
        _canvas.bind("<Configure>",
                     lambda e: _canvas.itemconfig(_cw, width=e.width))

        def _wheel(e):
            _canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        for _w in (_canvas, card):
            _w.bind("<Enter>", lambda e: _canvas.bind_all("<MouseWheel>", _wheel))
            _w.bind("<Leave>", lambda e: _canvas.unbind_all("<MouseWheel>"))

        self._section_title(card, "SETTINGS")

        self._label(card, "Area")
        _s0 = load_settings()

        self.city_var = tk.StringVar(
            value=_s0.get(f"city{SUFFIX}") or _s0.get("city") or ALL_AREAS_LABEL)
        area_values = [ALL_AREAS_LABEL] + AREAS
        area_combo = ttk.Combobox(card, textvariable=self.city_var,
                                   values=area_values, font=("Segoe UI", 10),
                                   state="normal", style="Dark.TCombobox")
        area_combo.pack(fill="x", pady=(2, 10), ipady=3)

        self._label(card, "Page Link (your Facebook page URL)")
        self.page_link_var = tk.StringVar(
            value=_s0.get(f"page_link{SUFFIX}") or _s0.get("page_link") or DEFAULT_PAGE_LINK)
        self._entry(card, self.page_link_var, pady=(2, 10))
        # Page link + area badalte hi turant save — baar-baar paste na karna pade
        self.page_link_var.trace_add("write", lambda *a: self._persist_simple())
        self.city_var.trace_add("write", lambda *a: self._persist_simple())

        row1 = tk.Frame(card, bg=CARD_BG); row1.pack(fill="x")
        col1 = tk.Frame(row1, bg=CARD_BG); col1.pack(side="left", expand=True, fill="x", padx=(0, 5))
        col2 = tk.Frame(row1, bg=CARD_BG); col2.pack(side="left", expand=True, fill="x")

        self._label(col1, "Min Members")
        self.min_members_var = tk.IntVar(value=1000)
        self._entry(col1, self.min_members_var, pady=(2, 10))

        self._label(col2, "Daily Limit")
        self.daily_limit_var = tk.IntVar(value=250)
        self._entry(col2, self.daily_limit_var, pady=(2, 10))

        row2 = tk.Frame(card, bg=CARD_BG); row2.pack(fill="x")
        col3 = tk.Frame(row2, bg=CARD_BG); col3.pack(side="left", expand=True, fill="x", padx=(0, 5))
        col4 = tk.Frame(row2, bg=CARD_BG); col4.pack(side="left", expand=True, fill="x")

        self._label(col3, "Delay Min (sec)")
        self.delay_min_var = tk.IntVar(value=5)
        self._entry(col3, self.delay_min_var, pady=(2, 4))

        self._label(col4, "Delay Max (sec)")
        self.delay_max_var = tk.IntVar(value=12)
        self._entry(col4, self.delay_max_var, pady=(2, 4))

        # ── Human pacing (account safety) ──
        _pace_def = _s0.get(f"pace_enabled{SUFFIX}")
        if _pace_def is None:
            _pace_def = _s0.get("pace_enabled", True)
        self.pace_var = tk.BooleanVar(value=bool(_pace_def))
        tk.Checkbutton(card,
                       text="Human pacing — spread joins across the day + take breaks (recommended)",
                       variable=self.pace_var, bg=CARD_BG, fg=TXT_MUTED,
                       selectcolor=INPUT_BG, activebackground=CARD_BG,
                       activeforeground=TXT, font=("Segoe UI", 8),
                       highlightthickness=0, bd=0,
                       command=self._persist_simple).pack(anchor="w", pady=(8, 2))
        wrow = tk.Frame(card, bg=CARD_BG); wrow.pack(fill="x")
        wc1 = tk.Frame(wrow, bg=CARD_BG); wc1.pack(side="left", expand=True, fill="x", padx=(0, 5))
        wc2 = tk.Frame(wrow, bg=CARD_BG); wc2.pack(side="left", expand=True, fill="x")
        self._label(wc1, "Work start (HH:MM)")
        self.work_start_var = tk.StringVar(
            value=_s0.get(f"work_start{SUFFIX}") or _s0.get("work_start") or "09:00")
        self._entry(wc1, self.work_start_var, pady=(2, 4))
        self._label(wc2, "Work end (HH:MM)")
        self.work_end_var = tk.StringVar(
            value=_s0.get(f"work_end{SUFFIX}") or _s0.get("work_end") or "21:00")
        self._entry(wc2, self.work_end_var, pady=(2, 4))
        self.work_start_var.trace_add("write", lambda *a: self._persist_simple())
        self.work_end_var.trace_add("write", lambda *a: self._persist_simple())
        tk.Label(card, text="Pacing ON = 'Delay Min/Max' ignore; joins din bhar "
                            "phaila ke honge. Same HH:MM dono = 24h.",
                 bg=CARD_BG, fg=TXT_MUTED, font=("Segoe UI", 7),
                 wraplength=380, justify="left").pack(anchor="w", pady=(0, 2))

        # ── Public / Private target mix ──
        row3 = tk.Frame(card, bg=CARD_BG); row3.pack(fill="x", pady=(8, 0))
        self._label(row3, "Public %  (rest = Private)")
        pcell = tk.Frame(row3, bg=CARD_BG); pcell.pack(fill="x")
        self.public_pct_var = tk.IntVar(value=int(_s0.get("public_pct", 30)))
        e = tk.Entry(pcell, textvariable=self.public_pct_var, font=("Segoe UI", 11),
                     bg=INPUT_BG, fg=TXT, insertbackground=TXT, relief="flat", width=6,
                     highlightthickness=1, highlightbackground=BORDER, highlightcolor=FB_BLUE)
        e.pack(side="left", ipady=3)
        self.mix_lbl = tk.Label(pcell, text="", bg=CARD_BG, fg=TXT_MUTED,
                                font=("Segoe UI", 8))
        self.mix_lbl.pack(side="left", padx=(8, 0))
        e.bind("<KeyRelease>", lambda ev: self._refresh_mix_lbl())

        self.skip_nopost_var = tk.BooleanVar(value=bool(_s0.get("skip_no_post", True)))
        tk.Checkbutton(card, text="Skip groups where members can't post (admin-only)",
                       variable=self.skip_nopost_var, bg=CARD_BG, fg=TXT_MUTED,
                       selectcolor=INPUT_BG, activebackground=CARD_BG,
                       activeforeground=TXT, font=("Segoe UI", 8),
                       highlightthickness=0, bd=0).pack(anchor="w", pady=(6, 2))

        self.same_state_var = tk.BooleanVar(value=bool(_s0.get("same_state_only", True)))
        tk.Checkbutton(card, text="Stay inside the target state only (50-mi radius, then next area)",
                       variable=self.same_state_var, bg=CARD_BG, fg=TXT_MUTED,
                       selectcolor=INPUT_BG, activebackground=CARD_BG,
                       activeforeground=TXT, font=("Segoe UI", 8),
                       highlightthickness=0, bd=0).pack(anchor="w", pady=(0, 2))

        self.include_counties_var = tk.BooleanVar(value=bool(_s0.get("include_counties", True)))
        tk.Checkbutton(card, text="Also join county-level groups (untick = cities only, no counties)",
                       variable=self.include_counties_var, bg=CARD_BG, fg=TXT_MUTED,
                       selectcolor=INPUT_BG, activebackground=CARD_BG,
                       activeforeground=TXT, font=("Segoe UI", 8),
                       highlightthickness=0, bd=0).pack(anchor="w", pady=(0, 2))

        self._label(card, "Don't-join keywords  —  one per line (added to buy/sell filter)")
        bkrow = tk.Frame(card, bg=CARD_BG)
        bkrow.pack(fill="x", pady=(2, 4))
        self.block_box = tk.Text(bkrow, height=3, font=("Consolas", 8), bg=INPUT_BG,
                                 fg=TXT, insertbackground=TXT, relief="flat", wrap="word",
                                 highlightthickness=1, highlightbackground=BORDER,
                                 highlightcolor=FB_BLUE)
        self.block_box.pack(side="left", fill="both", expand=True)
        bkscroll = tk.Scrollbar(bkrow, command=self.block_box.yview)
        bkscroll.pack(side="left", fill="y")
        self.block_box.config(yscrollcommand=bkscroll.set)
        try:
            self.block_box.insert("1.0", "\n".join(resolve_block_keywords()))
        except Exception:
            pass
        tk.Button(card, text="Reset to default list", bg=INPUT_BG, fg=TXT_MUTED,
                  relief="flat", font=("Segoe UI", 7), cursor="hand2", padx=6,
                  command=lambda: (self.block_box.delete("1.0", "end"),
                                   self.block_box.insert("1.0",
                                       "\n".join(DEFAULT_DONT_JOIN)))
                  ).pack(anchor="w", pady=(0, 2))

        tk.Label(card, text="Always on:  USA only  ·  no buy/sell  ·  engagement check",
                 bg=CARD_BG, fg=TXT_MUTED, font=("Segoe UI", 8),
                 justify="left").pack(anchor="w", pady=(6, 4))

        # ── Gemini API keys — AI answers (auto-loaded from gemini_keys.txt) ──
        self._label(card, "Gemini API Keys  —  one per line  ·  auto-loads gemini_keys.txt")
        grow = tk.Frame(card, bg=CARD_BG)
        grow.pack(fill="x", pady=(2, 2))
        self.gemini_box = tk.Text(grow, height=3, font=("Consolas", 8), bg=INPUT_BG,
                                  fg=TXT, insertbackground=TXT, relief="flat", wrap="none",
                                  highlightthickness=1, highlightbackground=BORDER,
                                  highlightcolor=FB_BLUE)
        self.gemini_box.pack(side="left", fill="x", expand=True)
        try:
            # settings > env > gemini_keys.txt in the bot folder — all auto
            _prev = resolve_gemini_keys("")
            if _prev:
                self.gemini_box.insert("1.0", "\n".join(_prev))
        except Exception:
            pass
        tk.Button(grow, text="Test", bg=INPUT_BG, fg=TXT, relief="flat",
                  font=("Segoe UI", 8), cursor="hand2", padx=8,
                  command=self._test_gemini).pack(side="left", padx=(4, 0))
        self.gemini_status = tk.Label(card, text="", bg=CARD_BG, fg=TXT_MUTED,
                                      font=("Segoe UI", 8), anchor="w")
        self.gemini_status.pack(anchor="w")
        self.gemini_box.bind("<KeyRelease>", lambda e: self._refresh_gemini_status())

        # (START button is pinned at the bottom of the left column — created above)

        # ══ RIGHT COLUMN — Stats / Progress / Activity / Log ═
        stats = tk.Frame(right, bg=BG)
        stats.pack(fill="x")
        stat_items = [
            (0, GREEN,  GREEN_BG,  "JOINED THIS SESSION",  "joined_lbl"),
            (1, ORANGE, ORANGE_BG, "SKIPPED THIS SESSION", "skipped_lbl"),
        ]
        for col, color, bg_color, label, attr in stat_items:
            f = tk.Frame(stats, bg=bg_color, padx=12, pady=8,
                         highlightbackground=BORDER, highlightthickness=1)
            f.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else 0)
            stats.columnconfigure(col, weight=1)
            v = self._tvar(attr)
            v.set("0")
            tk.Label(f, textvariable=v, font=("Segoe UI", 22, "bold"),
                     fg=color, bg=bg_color).pack()
            tk.Label(f, text=label, font=("Segoe UI", 8, "bold"),
                     fg=TXT_MUTED, bg=bg_color).pack()

        pb_frame = tk.Frame(right, bg=BG)
        pb_frame.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(pb_frame, maximum=250, mode="determinate",
                                         style="FB.Horizontal.TProgressbar")
        self.progress.pack(fill="x")
        self.progress_lbl_var = tk.StringVar(value="0 / 250 joined today")
        tk.Label(pb_frame, textvariable=self.progress_lbl_var,
                 bg=BG, font=("Segoe UI", 9), fg=TXT_MUTED).pack(pady=(3, 0))

        now_card = tk.Frame(right, bg=CARD_BG, padx=14, pady=10,
                            highlightbackground=BORDER, highlightthickness=1)
        now_card.pack(fill="x", pady=(10, 0))
        self._section_title(now_card, "CURRENT ACTIVITY")
        self.now_area_var = tk.StringVar(value="Area: —")
        self.now_target_var = tk.StringVar(value="Search: —")
        tk.Label(now_card, textvariable=self.now_area_var, bg=CARD_BG,
                 fg=TXT, font=("Segoe UI", 10, "bold"), anchor="w",
                 wraplength=400, justify="left").pack(fill="x")
        tk.Label(now_card, textvariable=self.now_target_var, bg=CARD_BG,
                 fg=TXT_MUTED, font=("Segoe UI", 9), anchor="w",
                 wraplength=400, justify="left").pack(fill="x", pady=(2, 0))

        log_frame = tk.Frame(right, bg=BG)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        log_hdr = tk.Frame(log_frame, bg=BG)
        log_hdr.pack(fill="x")
        tk.Label(log_hdr, text="ACTIVITY LOG", bg=BG, fg=TXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Button(log_hdr, text="Clear", font=("Segoe UI", 8),
                  relief="flat", bg=INPUT_BG, fg=TXT_MUTED,
                  activebackground=BORDER, activeforeground=TXT,
                  cursor="hand2", command=self._clear_log,
                  padx=10).pack(side="right")
        self.log_box = scrolledtext.ScrolledText(
            log_frame, height=6, font=("Consolas", 9),
            bg=LOG_BG, fg="#c9ceda", state="disabled",
            relief="flat", bd=0, insertbackground=TXT,
            highlightthickness=1, highlightbackground=BORDER)
        self.log_box.pack(fill="both", expand=True, pady=(4, 0))

    def _label(self, parent, text):
        tk.Label(parent, text=text, bg=CARD_BG,
                 font=("Segoe UI", 9, "bold"),
                 fg=TXT_MUTED).pack(anchor="w")

    def _tvar(self, name):
        v = tk.StringVar()
        setattr(self, f"{name}_var", v)
        return v

    def _open_login(self):
        if self.running or getattr(self, "_logout_open", False):
            return
        if getattr(self, "_login_open", False):
            # dobara dabaya -> login browser band karo
            stop_event.set()
            self.login_btn.config(text="🌐  Closing…")
            return
        self._login_open = True
        stop_event.clear()
        self.login_btn.config(text="🌐  Browser open — log in, then click here / close it")
        self.btn.config(state="disabled")
        self.logout_btn.config(state="disabled")
        self.status_var.set("●  Login browser open…")
        threading.Thread(target=run_login_browser, daemon=True).start()

    def _do_logout(self):
        if self.running or getattr(self, "_login_open", False) or \
                getattr(self, "_logout_open", False):
            return
        if not messagebox.askyesno(
                "Log out of Facebook",
                "Ye is PC/profile ki Facebook login session clear kar dega.\n\n"
                "Sirf tab karo jab account suspend/checkpoint ho gaya ho aur "
                "dobara (ya kisi doosre account se) login karna ho.\n\n"
                "Continue?"):
            return
        self._logout_open = True
        self.logout_btn.config(text="🚪  Logging out…", state="disabled")
        self.login_btn.config(state="disabled")
        self.btn.config(state="disabled")
        self.status_var.set("●  Logging out of Facebook…")
        threading.Thread(target=run_logout_browser, daemon=True).start()

    def _toggle(self):
        if self.running:
            stop_event.set()
            self.btn.config(text="▶   START", bg=GREEN)
            self.status_var.set("●  Stopping...")
            self.running = False
        else:
            if getattr(self, "_login_open", False):
                messagebox.showinfo("Login browser open",
                                    "Pehle login browser band karo, phir START.")
                return
            if getattr(self, "_logout_open", False):
                messagebox.showinfo("Logging out",
                                    "Logout khatam hone ka intezaar karo, phir START.")
                return
            # ── License check — no START without a valid key ──
            info = lic.validate_key(lic.load_active_key())
            if not info["ok"]:
                self._refresh_license_ui()
                self.status_var.set(f"●  {info.get('error') or 'License required'}")
                messagebox.showerror(
                    "License required",
                    info.get("error") or "Activate a valid license key first.")
                self._license_gate()
                return
            self.lic_info = info

            city = self.city_var.get().strip()
            if not city:
                self.city_var.set("⚠ Enter an area!")
                return
            stop_event.clear()
            self.joined_today  = 0
            self.skipped_today = 0
            self._update_stats()
            self.progress["maximum"] = self.daily_limit_var.get()
            self.progress["value"]   = 0
            self.progress_lbl_var.set(f"0 / {self.daily_limit_var.get()} joined today")
            self.now_area_var.set("Area: starting...")
            self.now_target_var.set("Search: —")
            self.running = True
            self.btn.config(text="⏹   STOP", bg=RED)
            self.login_btn.config(state="disabled")
            self.logout_btn.config(state="disabled")
            self.status_var.set("●  Running...")
            config = {
                "city":        city,
                "page_name":   DEFAULT_PAGE_NAME,
                "page_link":   self.page_link_var.get().strip(),
                "min_members": self.min_members_var.get(),
                "daily_limit": self.daily_limit_var.get(),
                "delay_min":   self.delay_min_var.get(),
                "delay_max":   self.delay_max_var.get(),
                "employee":    self.lic_info.get("employee", "") or "unknown",
                "license_key": lic.load_active_key(),
                "license_exp": self.lic_info.get("exp", ""),
                "key_id":      self.lic_info.get("kid", ""),
                "gemini_keys": resolve_gemini_keys(self._gemini_box_text()),
                "public_pct":  self._public_pct(),
                "skip_no_post": bool(self.skip_nopost_var.get()),
                "same_state_only": bool(self.same_state_var.get()),
                "include_counties": bool(self.include_counties_var.get()),
                "custom_blocked": self._block_keywords(),
                "pace_enabled": bool(self.pace_var.get()),
                "work_start":  self.work_start_var.get().strip() or "09:00",
                "work_end":    self.work_end_var.get().strip() or "21:00",
            }
            self._persist_gemini()          # remember keys for next time
            s = load_settings()
            s[f"page_link{SUFFIX}"] = self.page_link_var.get().strip()
            s[f"city{SUFFIX}"] = self.city_var.get().strip()
            s[f"pace_enabled{SUFFIX}"] = bool(self.pace_var.get())
            s[f"work_start{SUFFIX}"] = self.work_start_var.get().strip() or "09:00"
            s[f"work_end{SUFFIX}"] = self.work_end_var.get().strip() or "21:00"
            s["public_pct"] = self._public_pct()
            s["skip_no_post"] = bool(self.skip_nopost_var.get())
            s["same_state_only"] = bool(self.same_state_var.get())
            s["include_counties"] = bool(self.include_counties_var.get())
            s["block_keywords"] = self._block_keywords()
            save_settings(s)
            self._refresh_gemini_status()
            threading.Thread(target=run_playwright, args=(config,), daemon=True).start()

    def _log(self, text):
        self.log_box.config(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    def _update_stats(self):
        self.joined_lbl_var.set(str(self.joined_today))
        self.skipped_lbl_var.set(str(self.skipped_today))

    def _poll(self):
        try:
            while True:
                msg = ui_queue.get_nowait()
                t = msg["type"]
                if t == "log":
                    self._log(msg["text"])
                elif t == "area":
                    self.now_area_var.set(f"📍 {msg['text']}")
                elif t == "target":
                    self.now_target_var.set(f"🔍 Searching: {msg['text']}")
                elif t == "joined":
                    self.joined_today = msg["count"]
                    self._update_stats()
                    self.progress["value"] = self.joined_today
                    self.progress_lbl_var.set(f"{self.joined_today} / {self.daily_limit_var.get()} joined today")
                elif t == "skipped":
                    self.skipped_today += 1
                    self._update_stats()
                elif t in ("total", "total_skipped"):
                    pass  # All-time counters UI se hata diye — files mein ab bhi save hote hain
                elif t == "login_done":
                    self._login_open = False
                    self.login_btn.config(
                        text="🔓  Open browser & log in to Facebook", state="normal")
                    self.logout_btn.config(state="normal")
                    self.status_var.set("●  Login done — press START")
                    self._refresh_license_ui()
                elif t == "logout_done":
                    self._logout_open = False
                    self.logout_btn.config(text="🚪  Log out", state="normal")
                    self.login_btn.config(state="normal")
                    self.status_var.set("●  Logged out — press 'Open browser & log in' to sign in again")
                    self._refresh_license_ui()
                elif t == "stopped":
                    self.running = False
                    self._login_open = False
                    self.btn.config(text="▶   START", bg=GREEN)
                    self.login_btn.config(
                        text="🔓  Open browser & log in to Facebook", state="normal")
                    self.logout_btn.config(state="normal")
                    self.status_var.set("●  Idle — press START to begin")
                    self.now_target_var.set("Search: —")
                    self._log(f"📊 Today: {self.joined_today} joined | {self.skipped_today} skipped this run")
                    self._refresh_license_ui()  # expire hui to START dobara lock
        except queue.Empty:
            pass
        self.root.after(400, self._poll)


def _build_no() -> str:
    try:
        return open(os.path.join(APP_DIR, ".update_ver"), encoding="utf-8").read().strip()
    except Exception:
        return "?"


def _self_update():
    """Startup par check karo — naye files mile to lene ke baad bot ko
    naye code ke saath restart karo. Frozen .exe par skip (chalti exe
    replace nahi hoti)."""
    if getattr(sys, "frozen", False) or "--no-update" in sys.argv:
        print(f"[build] FB Group Joiner v{APP_VERSION} (build {_build_no()}) - auto-update off")
        return
    try:
        import updater

        # Tamper-alert ke liye employee ka naam pata karo (agar activated
        # hai) — ActivityLog abhi nahi bani hoti, isliye standalone
        # activity.send_alert() use karte hain.
        try:
            _emp = (lic.validate_key(lic.load_active_key(), check_url=False)
                    .get("employee") or "unknown")
        except Exception:
            _emp = "unknown"

        def _tamper_alert(msg: str) -> None:
            try:
                import activity
                # sync=True: process abhi thodi der mein restart/exit hone
                # wala hai — background thread poori bhejne se pehle hi
                # process mar sakta hai, isliye yahan wait karke bhejo.
                activity.send_alert(_emp, msg, sync=True)
            except Exception:
                pass

        if updater.check_and_apply(getattr(lic, "UPDATE_URL", ""), APP_DIR,
                                    alert=_tamper_alert):
            # os.execl Windows par tootta hai jab python.exe ke path mein
            # space ho (jaise "C:\Program Files\..."). subprocess.Popen
            # argv ko list ke roop mein leta hai — koi shell-quoting issue nahi.
            import subprocess
            subprocess.Popen([sys.executable] + sys.argv, cwd=APP_DIR,
                             close_fds=True)
            sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass
    print(f"[build] FB Group Joiner v{APP_VERSION} (build {_build_no()})")


if __name__ == "__main__":
    _self_update()
    root = tk.Tk()
    app  = App(root)
    # `py fb_joiner.py 3 --autostart` = UI khulte hi khud START ho jaye
    if "--autostart" in sys.argv:
        root.after(1500, app._toggle)
    root.mainloop()
