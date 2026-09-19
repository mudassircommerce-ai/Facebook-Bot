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
import html as html_mod
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

from areas import AREAS, CAR_AREAS, DUCT_AREAS, areas_for

# ── License + Usage tracking ────────────────────────────────
# license_common.py = key verify karta hai (public key isme embedded).
# activity.py       = usage/usage_<employee>.json likhta hai (owner ke
#                     dashboard ke liye).
import license_common as lic
import activity as activity_mod
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
BRAND       = "NexfourSolution"

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

ALL_AREAS_LABEL = "🌎 ALL AREAS (loop through every area in this list)"
AREA_CACHE_FILE = os.path.join(APP_DIR, "areas_cache.json")

# ── Hang / stall guard ──────────────────────────────────────
# Kabhi-kabhi bot ek group/page par bina exception ke "atak" jata tha
# (Facebook ka koi call jawab hi nahi deta) — na crash, na stop, bas ruka
# rehta. Watchdog har cycle dekhta hai ke aakhri "progress" ko kitna time
# hua. STALL_RESTART_SEC se zyada ho gaya to browser context band kar deta
# hai -> main coroutine exception phenkta hai -> run_playwright FRESH browser
# se khud restart kar leta hai (aaj ke joins safe). Manual restart ki
# zaroorat nahi.
STALL_RESTART_SEC   = 360      # 6 min bina kisi progress ke = atka hua
STALL_MAX_RESTARTS  = 40       # itni baar tak stall-recovery, phir hi haar
_LAST_ACTIVITY      = time.monotonic()

def _bump_activity():
    """Joining loop ne abhi kuch kiya — stall-watchdog ka timer reset."""
    global _LAST_ACTIVITY
    _LAST_ACTIVITY = time.monotonic()

# Account 1 ke liye purana default page rakha hai (backward compatible).
# Baaki accounts (2, 3, ...) mein khali rakhte hain — har account ka apna
# page naam UI mein zaroor type karna hoga.
DEFAULT_PAGE_NAME = "Edwin Junior" if INSTANCE == "1" else ""

# Page ka direct link (URL) — yeh ho toh switch isi se hota hai (name-based
# dropdown switching se zyada reliable). UI mein bhi change kar sakte ho.
DEFAULT_PAGE_LINK = ""

# Keyword match ke liye word-boundary lookup — plain "kw in text" substring
# ka bug tha: chhote keywords doosre lafzon ke andar chhup ke false-positive
# ban jate the ("cat" -> "vaCATion", "pic" -> "toPIC", "arts" -> "stARTS",
# "cars" -> "nasCARS"). \b se ab sirf ASAL alag lafz/phrase match hota hai.
_KW_HIT_CACHE = {}

def _kw_hit(kw: str, text: str) -> bool:
    k = (kw or "").strip().lower()
    if not k:
        return False
    pat = _KW_HIT_CACHE.get(k)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(k) + r"\b")
        _KW_HIT_CACHE[k] = pat
    return pat.search(text or "") is not None

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
    # Muzammil ki naye list (2026-09-09) — broad categories bhi, ab
    # word-boundary matching ki wajah se safe hain (kw kisi lambe lafz ke
    # andar chhup ke false-positive nahi banta — "cars" ab "NASCAR" ya
    # "cat" "vacation" ke andar match nahi karega)
    "buy & sell", "swap/trade", "jobs & hiring", "business promotion groups",
    "customer lead groups", "contractor groups", "pets", "lgbtq+",
    "cars", "cars & coffee", "cars and coffee",
    "cat", "coffee", "food", "food and drinks", "arts", "bars",
    "gardener", "gardeners", "gardening", "starting job",
    "rocks", "pic", "pics", "history", "shop", "supermarket", "offers",
    "ice fishing", "desi community", "la grange",
    "home school", "homeschool", "school",
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

# Non-Latin scripts — Arabic/Urdu, Hebrew, CJK, Cyrillic, Devanagari, Thai,
# Greek. Ye scripts English mein KABHI nahi aate, isliye 100% reliable hain —
# thodi si bhi maujoodgi (min_hits) confirm karti hai group English nahi hai
# (Spanish/Portuguese ke accented-word check se pehle chalte hain, jyada
# solid signal).
_SCRIPT_CHECKS = [
    ("Arabic script",     re.compile(u'[؀-ۿݐ-ݿ]'), 6),
    ("Hebrew script",     re.compile(u'[֐-׿]'),              6),
    ("CJK script",        re.compile(u'[一-鿿぀-ヿ가-힯]'), 6),
    ("Cyrillic script",   re.compile(u'[Ѐ-ӿ]'),              8),
    ("Devanagari script", re.compile(u'[ऀ-ॿ]'),              6),
    ("Thai script",       re.compile(u'[฀-๿]'),              6),
    ("Greek script",      re.compile(u'[Ͱ-Ͽ]'),              6),
]


def detect_non_english(text: str, short: bool = False) -> str:
    """Group English nahi hai to reason string, warna khaali string.
    1) Non-Latin script (Arabic/Hebrew/CJK/Cyrillic/Devanagari/Thai/Greek)
       thodi si bhi mile -> foran non-English (in scripts mein English
       kabhi nahi likha jata, false-positive risk zero).
    2) Warna Spanish/Portuguese ke 2+ distinctive alfaz milen to bhi
       non-English (naam English ho tab bhi).

    short=True group ke NAAM ke liye — threshold kam ho jata hai. Poore
    body text (2500 chars) ke liye 6 hits theek hain, lekin naam sirf
    chand lafzon ka hota hai: "凤凰城社区" ke 4 characters 6 wale
    threshold se neeche reh jate the aur group join ho jata tha."""
    t = text or ""
    for label, rx, min_hits in _SCRIPT_CHECKS:
        if len(rx.findall(t)) >= (2 if short else min_hits):
            return label
    tl = t.lower()
    hits = sum(1 for m in NON_ENGLISH_MARKERS if m in tl)
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
_GEMINI_DEAD_REASON = None    # (legacy — ab set nahi hota; bot Gemini fail par
                             #  rukta NAHI, template answers pe chalta hai)
_GK_ALL_DOWN_UNTIL = 0.0     # saari keys down mile to itne der (monotonic) tak
_GEMINI_DOWN = threading.Event()   # saari keys fail -> bot ruk kar 5 min baad restart
user_stop_event = threading.Event()  # SIRF UI ka STOP button — gemini/auto stop se alag

GEMINI_RETRY_SEC = 300             # 5 minute

                             #  poora retry-pass skip karo, seedha template


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
    """Gemini API keys nikaalo — # comment lines skip, sirf asli key-shape
    tokens (AQ. / AIza / lambi alphanumeric) rakho. Order + uniqueness."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for part in re.split(r"[\s,]+", line):
            p = part.strip()
            if not p or p in seen:
                continue
            if p.startswith(("AQ.", "AIza")) or (len(p) >= 20 and re.fullmatch(r"[A-Za-z0-9._\-]+", p)):
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


def _kw_to_template(line: str) -> str:
    """Employee ka keyword -> search template.
    "{area}" khud likha ho to waisa hi use hota hai, warna area ke aage
    lag jata hai:  "car detailing" -> "{area} car detailing".
    """
    k = (line or "").strip()
    if not k:
        return ""
    return k if "{area}" in k else "{area} " + k


def default_search_keyword_lines() -> list:
    """Default do-join keywords (raw lines):  keywords.txt > code ki list.
    UI box isi se pre-fill hota hai, taake har employee ko ye pehle se
    mil jayein aur unhe kuch type na karna pare."""
    try:
        fp = os.path.join(APP_DIR, "keywords.txt")
        if os.path.exists(fp):
            out = [ln.strip() for ln in open(fp, encoding="utf-8")
                   if ln.strip() and not ln.strip().startswith("#")]
            if out:
                return out
    except Exception:
        pass
    return list(DEFAULT_SEARCH_KEYWORDS)


def resolve_search_keywords(ui_val: str = "") -> list:
    """Search wordings:  UI box  >  keywords.txt  >  built-in SEARCH_TEMPLATES.

    Employee ke apne keywords SAB SE PEHLE try hote hain, built-in list
    uske baad — taake unke diye hue keywords pehle chalein aur coverage
    bhi kam na ho. (Pehle apne keywords dene ka koi raasta hi nahi tha:
    saari queries 101 hardcoded wordings se banti thin.)
    """
    raw = ui_val or ""
    if not raw.strip():
        raw = "\n".join(default_search_keyword_lines())
    user, seen = [], set()
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        t = _kw_to_template(ln)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            user.append(t)
    if not user:
        return list(SEARCH_TEMPLATES)
    return user + [t for t in SEARCH_TEMPLATES if t.lower() not in seen]


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
    global _GK_ALL_DOWN_UNTIL
    keys = GEMINI_KEYS
    n = len(keys)
    if n == 0:
        return None

    # Abhi-abhi saari keys down mili thi -> 10 min tak poora retry-pass mat
    # karo, seedha None (template) — warna har sawaal par 6s+ zaya hote hain.
    if time.monotonic() < _GK_ALL_DOWN_UNTIL:
        return None

    ans = _gk_one_pass(question, city, keys, n)
    if ans:
        return ans

    time.sleep(4)
    ans = _gk_one_pass(question, city, keys, n)
    if ans:
        return ans

    # Retry ke baad bhi saari keys fail. Bot ko ROKTA NAHI — sirf is sawaal
    # ka jawab built-in template se dega aur chalta rahega. Ek Discord note
    # (spam nahi), phir 10 min tak Gemini try hi nahi karega.
    # Retry ke baad bhi saari keys fail -> bot ROK do. 5 min baad khud
    # restart hoga aur keys dobara check karega. (Pehle bot template
    # answers par chalta rehta tha, jis se AI-only sawaalon wale groups
    # ke jawab kharab jate the.)
    send_ui("log", text=f"⛔ All {n} Gemini key(s) failed — stopping the bot. "
                        f"It will restart by itself in {GEMINI_RETRY_SEC // 60} min "
                        f"and re-check the keys.")
    _dmsg = (f"⛔ All {n} Gemini keys failed — bot STOPPED. Auto-restart in "
             f"{GEMINI_RETRY_SEC // 60} min with a fresh key check.")
    try:
        (_ACT.alert(_dmsg) if _ACT else __import__("activity").send_alert("bot", _dmsg))
    except Exception:
        pass
    _GEMINI_DOWN.set()
    stop_event.set()
    _GK_ALL_DOWN_UNTIL = time.monotonic() + 600
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

# Ek area sirf uske bare naam se search karne par FB ke pehle-page results
# hi milte hain — "har group" (galli-galli ke chhote groups tak) chahiye to
# alag alag query wording se alag results aate hain. Pass 1 = bare area
# naam (jaisa pehle tha), Pass 2+ = neeche wale templates ek-ek karke — isi
# wajah se "0 new joins -> nothing_left" ab bohot der se lagta hai, area
# genuinely khatam hone tak har wording try ho chuki hoti hai.
# Muzammil ki di hui "do-join" list — yehi har bot ki DEFAULT search
# wordings hain. Priority: UI box > keywords.txt > ye list >
# built-in SEARCH_TEMPLATES. Code mein isliye rakhi hain ke
# keywords.txt delete/kharab ho jaye to bhi kaam karti rahein.
DEFAULT_SEARCH_KEYWORDS = [
    'Community',
    'Happening',
    'Mom',
    'Local',
    'Parents',
    'Neighbors',
    'Neighborhood',
    'Nannies',
    'Discussion',
    'Network',
    "What's up",
    'Networking',
    'If you know',
    'Talk',
    'Talking',
    'Chatter',
    'Zip codes',
    'Uncensored',
    'I love',
    'Life in {area}',
    'Local news',
    'County',
    "What's happening",
    'Everything',
    'Talk 2.0',
    'Information',
    'Good news',
    'Live/work/play',
    'Citizens',
    'Surrounding areas',
    'You might be',
    'Friends',
    'Official',
    'The voice of {area}',
    'Remember me',
    'Remember when',
    'The buzz',
    'Events',
    'Informer',
    'Event and news',
    'Town',
    'Conversation',
    'Happens',
    "What's hot",
    'Community Forum',
    '411 information original group',
    'Stay informed',
    'Unfiltered',
    'The {area} times',
    'Being nosey',
    '{area} life',
    "Only mom's",
    'Ask',
    'Beach',
    'Beaches',
    '411 originally info group',
    'Humble community',
    'Updates/update',
    'Folks',
    'Events and social groups',
    'unleashed',
    'Connect',
    'Area share',
    'Discussion board',
    '{area} helping',
    'Welcome to {area}',
    'No restrictions',
    'Pinboard 4.0',
    'Resident but better',
    'Bulletin board',
    'Living in {area}',
    'Real town talk',
    'Subdivision',
    'Ranch',
    'Open forum',
    'Tri-town',
    'Recommended',
    'The original',
    'Creek',
    '411',
    'Helping hand',
    'Straight talk',
    'Unite 2',
    'Unite',
    'Together',
    'Uniquely',
    'Unpaused',
    'Past/ present / future',
]

# ── Backend filters (UI par option nahi — Muzammil ki tay-shuda policy) ──
# Group join karne ki kam se kam shart. Ye jaan boojh kar UI se bahar
# rakhe gaye hain taake employee inhe badal na sake.
DEFAULT_MIN_MEMBERS = 1000  # UI ka default (employee badal sakta hai)
BIG_MIN_MEMBERS = 5000      # is se upar warning dikhao
LICENSE_WARN_DAYS = 2       # itne din bachne par renewal reminder
REQUIRE_ACTIVITY = True     # join se pehle recent activity check
ENGLISH_ONLY = True         # sirf English group

# Ek area par lagataar itni wordings se 0 naya group mile -> area khatam
# samjho aur agle area par jao.
AREA_EMPTY_WORDINGS = 2

SEARCH_TEMPLATES = [
    "{area} neighborhood",
    "{area} residents",
    "{area} community",
    "{area} community group",
    "{area} moms",
    "{area} families",
    "{area} homeowners",
    "{area} local news",
    "{area} updates",
    "{area} events",
    "{area} locals",
    "{area} network",
    "{area} connect",
    "{area} residents group",
    "{area} town",
    "{area} chat",
    "{area} discussion",
    "{area} bulletin board",
    "{area} watch",
    "{area} forum",
    "{area} hub",
    "{area} talk",
    "{area} residents association",
    "{area} homeowners association",
    "{area} folks",
    "{area} people",
    "{area} living",
    "{area} area group",
    "{area} info",
    "{area} scene",
    # Muzammil ki 2026-09-10 wali list (94 phrases, deduped/normalized) —
    # SECOND-LAYER wordings. Normal joining upar wale templates se hi
    # chalti hai; ye area genuinely khatam hone ke baad (pass 32+) hi
    # istemal hoti hain, taake bilkul aakhri tak koi group na chhute.
    "{area} happening",
    "{area} mom",
    "{area} local",
    "{area} parents",
    "{area} neighbors",
    "{area} nannies",
    "{area} what's up",
    "{area} networking",
    "{area} if you know",
    "{area} talking",
    "{area} chatter",
    "{area} zip codes",
    "{area} uncensored",
    "I love {area}",
    "Life in {area}",
    "{area} county",
    "{area} what's happening",
    "{area} everything",
    "{area} talk 2.0",
    "{area} information",
    "{area} good news",
    "{area} live work play",
    "{area} citizens",
    "{area} surrounding areas",
    "{area} friends",
    "{area} official",
    "The Voice of {area}",
    "{area} remember me",
    "{area} remember when",
    "{area} the buzz",
    "{area} informer",
    "{area} events and news",
    "{area} conversation",
    "{area} happens",
    "{area} what's hot",
    "{area} community forum",
    "{area} 411",
    "{area} stay informed",
    "{area} unfiltered",
    "The {area} Times",
    "{area} being nosey",
    "{area} city life",
    "{area} moms only",
    "Ask {area}",
    "{area} beach",
    "{area} beaches",
    "{area} humble community",
    "{area} events and social",
    "{area} unleashed",
    "{area} area share",
    "{area} discussion board",
    "{area} helping",
    "Welcome to {area}",
    "{area} no restrictions",
    "{area} pinboard",
    "Living in {area}",
    "{area} real town talk",
    "{area} subdivision",
    "{area} ranch",
    "{area} open forum",
    "{area} tri-town",
    "{area} recommended",
    "{area} the original",
    "{area} creek",
    "{area} helping hand",
    "{area} straight talk",
    "{area} unite",
    "{area} together",
    "{area} uniquely",
    "{area} unpaused",
    "{area} past present future",
]

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

def load_area_cache(fname: str = None) -> dict:
    """Pre-built radius cache load karo (cities/counties per area)."""
    p = Path(fname) if fname else Path(AREA_CACHE_FILE)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

# CAR areas -> 40-mile cache ;  DUCT areas -> 60-mile cache (alag file)
_AREA_CACHE      = load_area_cache()
_AREA_CACHE_DUCT = load_area_cache(os.path.join(APP_DIR, "areas_cache_duct.json"))

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
                          include_counties: bool = True, mode: str = "car") -> list:
    """
    Ek area (e.g. 'Dallas TX') ke liye search targets (cities + counties)
    return karo. Pehle pre-built cache dekho (fast), warna live calculate.
    mode="duct" -> 60-mile cache (areas_cache_duct.json).
    mode="car"  -> 40-mile cache (areas_cache.json).
    """
    _duct = (mode or "car").lower() == "duct"
    _c = _AREA_CACHE_DUCT if _duct else _AREA_CACHE
    # DUCT mode ka apna 60-mile cache hai. Pehle yahan car (40-mile) cache
    # par fallback tha — duct area agar duct cache mein na ho to chupke se
    # 40-mile radius chal jata tha. Ab galat radius use nahi hota; cache
    # miss par neeche live calculate ho jata hai.
    cached = _c.get(area)
    if cached:
        targets = list(cached.get("cities", []))
        if include_counties:
            targets += list(cached.get("counties", []))
    else:
        targets = get_nearby_cities(area, 60 if (mode or "").lower() == "duct" else 40)

    if same_state_only:
        st = _area_state_code(area)
        if st:
            in_state = [t for t in targets if _target_state(t) == st]
            # Chhote areas (jaise Washington DC) mein same-state filter ke
            # baad bohot kam targets bachte hain — wahan filter chhor dete
            # hain, kyunki radius ke andar ke parosi state (VA/MD) waqai
            # local hain. Lekin caller ko sach bata do (pehle UI "DC only"
            # likh deta tha jabke 244 targets mein VA/MD sab shamil the).
            if len(in_state) >= 8:
                targets = in_state
    return targets


def states_in_targets(targets: list) -> list:
    """Targets list mein jo states aaye hain unki sorted list (log ke liye)."""
    return sorted({s for s in (_target_state(t) for t in targets) if s})

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

def log_error(what: str, exc: Exception) -> None:
    """Chhoti si error error_log par likh do (traceback ke sath)."""
    import traceback
    try:
        with open(f"error_log{SUFFIX}.txt", "a", encoding="utf-8") as ef:
            ef.write(f"\n--- {datetime.now()} | {what} ---\n{exc}\n"
                     f"{traceback.format_exc()}\n")
    except Exception:
        pass


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
        send_ui("log", text="   ↻ Form still open — refilling and submitting again")
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

def _extract_group_name(html: str, text: str) -> str:
    """Group ka ASLI naam page se nikalo.

    Pehle naam sirf URL slug se banta tha — lekin 76% groups ka slug
    numeric ID hota hai ("939950666418470"), to "don't-join" keyword check
    ke paas match karne ko kuch hota hi nahi tha aur aise group join ho
    jate the. og:title / <title> / <h1> se asli naam mil jata hai.
    """
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]{2,160})"', html or "")
    if not m:
        m = re.search(r'<meta[^>]+content="([^"]{2,160})"[^>]+property="og:title"', html or "")
    if m:
        n = html_mod.unescape(m.group(1)).strip()
        if n and n.lower() not in ("facebook", "log in to facebook"):
            return n

    m = re.search(r"<title[^>]*>(.{2,200}?)</title>", html or "", re.S)
    if m:
        n = html_mod.unescape(m.group(1)).strip()
        n = re.sub(r"\s*[|\-–]\s*Facebook\s*$", "", n, flags=re.I).strip()
        if n and n.lower() != "facebook":
            return n

    for ln in (text or "").splitlines():
        ln = ln.strip()
        if 2 < len(ln) < 120 and ln.lower() not in ("facebook", "menu"):
            return ln
    return ""


async def get_group_info(page):
    html  = await page.content()
    text  = await page.inner_text("body")
    gname = _extract_group_name(html, text)
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
    non_english = detect_non_english(text[:2500])
    return members, privacy, already, page_blocked, is_canada, post_disabled, non_english, gname


# Account-level block / checkpoint markers (page body text, lowercase)
# NOTE: sirf woh phrases jo ASAL account-block/checkpoint pe hi aate hain.
# "please try again later", "security check", "this feature isn't available"
# jaise generic phrases JAAN-BOOJH ke nikaal diye — wo Facebook ke aam
# temporary errors mein bhi aate hain (khaas kar VPS pe), aur bot ko bina
# wajah rok dete the.
ACCOUNT_BLOCK_MARKERS = [
    "confirm your identity", "we need to confirm your identity",
    "help us confirm your identity",
    "your account has been temporarily locked",
    "your account has been temporarily restricted",
    "your account has been disabled",
    "we've temporarily blocked", "we have temporarily blocked",
    "you're temporarily blocked", "you are temporarily blocked",
    "you're restricted from joining", "you can't use facebook right now",
    "you cannot use facebook right now",
    "you're doing that too much", "you’re doing that too much",
    "you can't use this feature right now because",
    "we've restricted certain features", "we have restricted certain features",
    "we detected unusual activity on your account",
    "we noticed suspicious activity on your account",
    "please solve this puzzle", "enter the security code we sent",
]
# Ye markers SIRF POSTING se mutalliq hain — joining par inka koi
# matlab nahi. Pehle ye ACCOUNT_BLOCK_MARKERS mein the: nateeja ye
# ke har join ke baad group page par ye lafz mil jate the aur bot
# 2155 baar "possible block" ka 15-second re-check chalata raha —
# aur ek baar bhi koi asal block nahi nikla. Ab sirf auto-post ke
# waqt check hote hain.
POST_LIMIT_MARKERS = [
    "we limit how often you can post",
    "you're posting too fast", "you are posting too fast",
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


async def check_account_block(page, body_text: str = None,
                              include_post_limit: bool = False) -> str:
    """Account checkpoint/block detect. Reason string ya '' return.

    include_post_limit=True sirf AUTO-POST ke liye — wahan "we limit how
    often you can post" waqai rukne ki wajah hai. Joining ke waqt ye lafz
    group ke apne rules/notice mein bhi mil jata hai, isliye default OFF.
    """
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
    if include_post_limit:
        for m in POST_LIMIT_MARKERS:
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


async def confirm_account_block(page, first_marker: str,
                                include_post_limit: bool = False) -> str:
    """
    check_account_block ne kuch pakda — lekin bot ko rokne se PEHLE confirm
    karo ke ye asal ACCOUNT block hai, kisi ek group ka notice nahi.

    Pehle ye page.reload() karta tha — aur wahi sab se bari kharabi thi:
    asli restriction ek dialog/toast hoti hai jo reload karte hi GHAYAB ho
    jati hai. Isi liye logs mein 2155 "possible block" signals ke bawajood
    bot EK BAAR bhi nahi ruka.

    Ab do qadam hain:
      1. Usi page par dobara dekho (reload ke baghair) — dialog wahin hai?
      2. Phir NEUTRAL page (facebook.com) par ja kar dekho — asal account
         restriction HAR page par dikhti hai; ek group ka notice nahi.
    """
    try:
        if page.url and "/checkpoint/" in page.url:
            return first_marker            # checkpoint URL = pakka block

        # 1) usi page par — saboot mita mat do
        await sleep(5)
        again = await check_account_block(page, include_post_limit=include_post_limit)
        if not again:
            return ""

        # 2) neutral page par tasdeeq
        try:
            await page.goto("https://www.facebook.com/",
                            wait_until="domcontentloaded", timeout=20000)
            await sleep(3)
        except Exception:
            return again                    # navigate na ho saka -> shak barqarar
        confirmed = await check_account_block(page, include_post_limit=include_post_limit)
        if confirmed and "/checkpoint/" in (page.url or ""):
            return confirmed
        return confirmed or ""
    except Exception:
        return ""                           # shak ho to bot mat roko


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

# Facebook group ke "About" mein activity likhi hoti hai:
# "12 posts today" / "10+ posts a day" / "3 posts a month" wagera.
# Ye PRIVATE groups par bhi nazar aati hai (post nahi dikhte, ye dikhta hai)
# — isliye dono type ke liye yahi pehla check hai.
_ACT_RX = [
    (re.compile(r"([\d.,]+)\s*\+?\s*posts?\s+(?:a\s+)?(?:day|today)", re.I), 30),
    (re.compile(r"([\d.,]+)\s*\+?\s*posts?\s+(?:a\s+|per\s+)?week", re.I), 4),
    (re.compile(r"([\d.,]+)\s*\+?\s*posts?\s+(?:a\s+|per\s+)?month", re.I), 1),
]
MIN_POSTS_PER_MONTH = 4          # ~hafte mein ek post se kam = mara hua group


def activity_from_text(text: str):
    """(mila?, posts-per-month) — About se activity padho.
    Kuch na mile to (False, 0)."""
    t = text or ""
    best = None
    for rx, mult in _ACT_RX:
        m = rx.search(t)
        if m:
            n = _parse_count(m.group(1))
            v = n * mult
            if best is None or v > best:
                best = v
    return (True, best) if best is not None else (False, 0)


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

# Full state name → abbreviation (reverse lookup)
STATE_ABBR = {v.lower(): k for k, v in STATE_NAMES.items()}


def _state_matches(text: str, abbr: str, full: str) -> bool:
    """Kya `text` (FB ka location suggestion) target state ka hai?

    Full naam ("Arizona") ya abbreviation ko ALAG lafz ki tarah dhoondo.
    Plain `abbr in text` galat tha — "DE" to "Dedham" ke andar bhi mil
    jata hai, aur "United States" ko accept karna to kisi bhi state ko
    pass kar deta tha.
    """
    if not text:
        return False
    t = text.lower()
    if full and re.search(r"\b" + re.escape(full.lower()) + r"\b", t):
        return True
    if abbr and re.search(r"\b" + re.escape(abbr) + r"\b", text):
        return True
    return False


def _other_state_in(text: str, abbr: str) -> str:
    """Agar text mein target ke ilawa kisi DOOSRE US state ka poora naam
    saaf likha ho to wahi naam wapas do, warna "". Sirf full naam dekhte
    hain — 2-letter abbreviations normal alfaz se takra jate hain."""
    t = (text or "").lower()
    mine = (STATE_NAMES.get(abbr, "") or "").lower()
    for name in STATE_NAMES.values():
        low = name.lower()
        if low == mine:
            continue
        if re.search(r"\b" + re.escape(low) + r"\b", t):
            return name
    return ""

async def apply_fb_filters(page, city):
    """
    Facebook search page pe left sidebar filters apply karo:
    - Location: sirf city naam type karo, suggestion select karo
    - Public groups toggle hamesha OFF (sirf private join karein)
    """
    await sleep(rand_delay(1, 1.5))

    city_name  = city_only(city)   # "Raleigh NC" → "Raleigh"
    # "Raleigh NC" → "NC". Agar area sirf state ka naam ho ("Rhode Island",
    # "New Jersey") to state_only khali deta hai — tab _area_state_code se lo.
    state_abbr = state_only(city) or _area_state_code(city)
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
            # Suggestion TABHI select karo jab woh USA ke SAHI state ka ho.
            #
            # BUG (fixed): pehle "United States" / ", US" bhi accept ho jata
            # tha — yaani 'Arlington AZ' search karne par FB ka pehla
            # suggestion "Arlington, Texas" chun liya jata tha aur bot poori
            # us city ke Texas wale groups join karta rehta tha. Isi wajah se
            # bot "apni marzi se doosre state" mein join kar raha tha.
            # Ab state ka match LAAZMI hai; na mile to city skip ho jati hai.
            chosen = False
            for s_sel in suggestion_sels:
                opts = await page.locator(s_sel).all()
                if not opts:
                    continue

                cands = []   # (score, opt, opt_text)
                for opt in opts:
                    try:
                        opt_text = (await opt.inner_text(timeout=600)).strip()
                    except Exception:
                        continue
                    if not opt_text:
                        continue
                    if not _state_matches(opt_text, state_abbr, state_full):
                        continue
                    # Sahi state mil gaya — ab city naam bhi match kare to behtar
                    first_part = opt_text.split(",")[0].strip().lower()
                    score = 2 if first_part == city_name.strip().lower() else \
                            1 if city_name.strip().lower() in first_part else 0
                    cands.append((score, opt, opt_text))

                if cands:
                    cands.sort(key=lambda c: c[0], reverse=True)
                    _score, opt, opt_text = cands[0]
                    try:
                        await opt.click()
                        await sleep(1)
                        send_ui("log", text=f"📍 Location: {opt_text}")
                        chosen = True
                    except Exception:
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
                send_ui("log", text=f"   ⚠️  No '{state_abbr or 'USA'}' suggestion "
                                    f"for '{city_name}' — doosre state ka "
                                    f"location nahi lagayenge")
            return chosen
        return False
    except Exception as e:
        send_ui("log", text=f"   location filter error: {e}")
        return False

    # Public + Private dono — koi toggle nahi badlna

# ── Main Join Logic ───────────────────────────────────────────

def _min_members_for(config, privacy: str) -> int:
    """Per-privacy minimum members. Old configs only had 'min_members' —
    that stays the fallback for both. Unknown privacy -> take the SMALLER
    of the two so nothing is skipped by mistake."""
    _legacy = config.get("min_members", DEFAULT_MIN_MEMBERS)
    pub = int(config.get("min_members_public", _legacy) or 0)
    pri = int(config.get("min_members_private", _legacy) or 0)
    if privacy == "Public":
        return pub
    if privacy == "Private":
        return pri
    return min(pub, pri)


def _quota_block(config, privacy: str):
    """Public/Private ratio quota — should this group be skipped for now?
    Returns (message, csv_status), else (None, None).

    The employee sets what % of joins should be public (rest private).
    Whichever type runs ahead of target is skipped until the other one
    catches up, so the mix stays near the target across the session.
    """
    pub_pct = config.get("public_pct", 30)
    jp = config.get("_jp", 0)
    jv = config.get("_jpriv", 0)
    tot = jp + jv
    if privacy == "Public":
        if pub_pct <= 0:
            return "100% private set — skipping public", "ratio_public"
        if tot >= 4 and (jp + 1) / (tot + 1) > pub_pct / 100.0 + 0.05:
            return f"Public quota reached ({jp}/{tot})", "ratio_public"
    elif privacy == "Private":
        if pub_pct >= 100:
            return "100% public set — skipping private", "ratio_private"
        if tot >= 4 and (jv + 1) / (tot + 1) > (100 - pub_pct) / 100.0 + 0.05:
            return f"Private quota reached ({jv}/{tot})", "ratio_private"
    return None, None


async def _goto_retry(page, url, timeout: int = 20000, tries: int = 2):
    """page.goto ko ek baar dobara koshish karo.

    Error logs mein sabse zyada yehi fail tha (1000+ baar): 15s ka single
    attempt: FB ka ek slow response = poora group zaya. Ab pehla attempt
    fail ho to thoda ruk kar dobara (thoda lamba timeout) try karte hain.
    Aakhri koshish bhi fail ho to exception upar chala jata hai (caller
    ka purana behaviour waisa hi rehta hai).
    """
    last = None
    for i in range(max(1, tries)):
        try:
            return await page.goto(url, wait_until="domcontentloaded",
                                   timeout=timeout + i * 10000)
        except Exception as e:
            last = e
            if i + 1 < tries:
                await sleep(rand_delay(1.5, 3))
    raise last


async def alert_ss(page, config, reason: str, detail: str = "") -> None:
    """Bot ruk-ne / limit / restriction par: SCREENSHOT lo aur wajah ke
    sath Discord par bhejo.

    Employee ko sirf "bot ruk gaya" dikhta tha aur admin ko pata hi nahi
    chalta tha ke kyun. Ab har aise waqt ki tasveer + wajah server par
    pohonch jati hai. Screenshot na ban sake to alert phir bhi jata hai
    (sirf text)."""
    shot = ""
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", reason)[:40] or "event"
        shot = os.path.join(SS_DIR, "alert_" + safe + "_" + stamp + ".png")
        os.makedirs(SS_DIR, exist_ok=True)
        await page.screenshot(path=shot, full_page=False)
    except Exception:
        shot = ""

    who = config.get("employee", "") or "unknown"
    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    txt = ("**" + reason + "**" + "\n" + (detail or "") + "\n" +
           "profile: " + str(SUFFIX or "1") + "  |  today: " +
           str(config.get("_today_total", "?")) + "/" +
           str(config.get("daily_limit", "?")) + "\n" + "page: " + url[:200])

    act = config.get("_activity")
    try:
        if shot and os.path.exists(shot):
            if act:
                act.alert_file(txt, shot)
            else:
                activity_mod.send_alert_file(who, txt, shot, sync=True)
        else:
            if act:
                act.alert(txt)
            else:
                activity_mod.send_alert(who, txt, sync=True)
    except Exception:
        pass


async def join_one_group(page, url, name, area, config, already_joined=None):
    try:
        _bump_activity()
        await _goto_retry(page, url, timeout=20000, tries=2)
        await sleep(rand_delay(1, 2))
        await dismiss_popups(page)
        _bump_activity()

        # ── Account block / checkpoint? -> foran STOP + alert ──
        # SABSE PEHLE ye check hona zaroori hai — kisi aur check se pehle.
        # (Bug tha: "already member" ka fast-exit isse PEHLE chalta tha, aur
        # checkpoint/restriction page par kahin bhi "Joined" jaisa generic
        # lafz mil jaye to wo turant "already member, skip" maan ke agle
        # group pe chala jata tha — matlab restriction lagne ke baad bhi bot
        # kabhi rukta hi nahi tha, cheeza-cheeza groups pe chalta rehta tha.)
        try:
            body_txt = await page.inner_text("body")
        except Exception:
            body_txt = ""

        blk = await check_account_block(page, body_txt)
        if blk:
            send_ui("log", text=f"⚠️ Possible block signal ('{blk}') — re-checking in 15s…")
            blk = await confirm_account_block(page, blk)
        if blk:
            send_ui("log", text=f"🚫 ACCOUNT BLOCK confirmed: '{blk}' — stopping the bot")
            await alert_ss(page, config, f"ACCOUNT CHECKPOINT / BLOCK ({blk})",
                           "Bot stopped. Give this account a few days of rest.")
            config["_end_reason"] = "account_blocked"
            stop_event.set()
            return "blocked"

        # ── FAST already-member exit ─────────────────────────
        # Ye group pehle se joined hai (manually ya kisi purani run mein)
        # lekin humari joined_groups.txt mein kabhi save nahi hua tha —
        # isliye bot HAR run mein isse dobara khol ke waqt zaya karta tha.
        # body_txt yahan pehle se fetch ho chuka hai (upar), isliye poora
        # get_group_info() (page.content() sameet, bhaari) chalaye baghair
        # hi turant nikal jao — aur is baar hamesha ke liye save kar do.
        if any(m in body_txt for m in
               ("Leave group", "Joined", "Member ·", "You're a member")):
            send_ui("log", text=f"⏭️  Already member: {name}")
            log_csv(area, name, url, "already_member", 0, "?")
            save_joined(url)
            if already_joined is not None:
                already_joined.add(url)
            return "skipped"

        members, privacy, already, page_blocked, is_canada, post_disabled, non_english, gname = \
            await get_group_info(page)

        # Page se asli naam mil gaya to usi ko aage use karo — slug wala
        # naam (aksar sirf numeric ID) na log mein kaam ka tha, na
        # don't-join keyword check mein.
        if gname:
            name = gname

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
        bad_kw = next((kw for kw in all_blocked if kw and _kw_hit(kw, check_text)), None)
        if bad_kw:
            send_ui("log", text=f"⏭️  Blocked keyword ('{bad_kw}'), skip: {name}")
            log_csv(area, name, url, "blocked_keyword", members, privacy)
            return "skipped"

        if is_canada:
            send_ui("log", text=f"🍁 Canada group, skip: {name}")
            log_csv(area, name, url, "non_usa", members, privacy)
            return "skipped"

        # ── Doosre state ka group? -> skip ──────────────────
        # Location filter ab sahi state lagata hai, lekin FB kabhi kabhi
        # phir bhi parosi/door ke groups dikha deta hai. Ye aakhri jaal
        # hai: agar group ke naam/header mein kisi DOOSRE state ka poora
        # naam saaf likha ho AUR humare apne state ka koi zikr na ho, to
        # join mat karo. (Employee ke complaint: "apni marzi se doosre
        # state mein join kar raha tha".)
        if config.get("same_state_only", True):
            _tgt_st = state_only(config.get("_current_city", "") or area) or \
                      _area_state_code(config.get("_current_city", "") or area)
            if _tgt_st:
                _hdr = f"{name} {body_txt[:600]}"
                _hdr_l = _hdr.lower()
                # "Washington DC" ko WA (Washington state) mat samajh lena
                if "washington dc" in _hdr_l or "washington, dc" in _hdr_l \
                        or "district of columbia" in _hdr_l:
                    _hdr = re.sub(r"washington(?=[\s,]*d\.?\s?c\.?)", "",
                                  _hdr, flags=re.I)
                _mine = _state_matches(_hdr, _tgt_st, STATE_NAMES.get(_tgt_st, ""))
                _other = "" if _mine else _other_state_in(_hdr, _tgt_st)
                if _other:
                    send_ui("log", text=f"🗺️  Wrong state ({_other}), need {_tgt_st} — skip: {name}")
                    log_csv(area, name, url, "wrong_state", members, privacy)
                    return "skipped"

        # English-only (backend policy). Body ke sath ab group ka ASLI
        # naam bhi check hota hai — kai groups ka content thoda English
        # hota hai lekin naam poora doosri zabaan mein.
        if ENGLISH_ONLY:
            non_english = non_english or detect_non_english(name, short=True)
        if ENGLISH_ONLY and non_english:
            send_ui("log", text=f"🌐 Non-English group ({non_english}), skip: {name}")
            log_csv(area, name, url, "non_english", members, privacy)
            return "skipped"

        if already:
            send_ui("log", text=f"⏭️  Already member: {name}")
            log_csv(area, name, url, "already_member", members, privacy)
            save_joined(url)
            if already_joined is not None:
                already_joined.add(url)
            return "skipped"

        _min_req = _min_members_for(config, privacy)
        if members > 0 and members < _min_req:
            send_ui("log", text=f"⏭️  Skip ({members} {privacy.lower()} members < {_min_req}): {name}")
            log_csv(area, name, url, "low_members", members, privacy)
            return "skipped"

        # ────────────── Sirf private / sirf public ──────────────
        _qmsg, _qcsv = _quota_block(config, privacy)
        if _qmsg:
            send_ui("log", text=f"⚖️  {_qmsg}, skip: {name}")
            log_csv(area, name, url, _qcsv, members, privacy)
            return "skipped"

        # ── Recent activity (backend policy) ─────────────
        # Pehle About ka activity number dekho — ye PRIVATE groups par bhi
        # milta hai. Na mile to Public groups ke posts DOM se naapo
        # (private ke posts join se pehle dikhte hi nahi).
        if REQUIRE_ACTIVITY:
            _found, _ppm = activity_from_text(body_txt)
            if _found and _ppm < MIN_POSTS_PER_MONTH:
                send_ui("log", text=f"💤 Skip (only ~{_ppm} posts/month): {name}")
                log_csv(area, name, url, "low_activity", members, privacy)
                return "skipped"
            if not _found and privacy == "Public":
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
            send_ui("log", text="⏸️  Join-request limit reached (too many pending groups) — stopping the bot")
            await alert_ss(page, config, "JOIN-REQUEST LIMIT reached",
                           "Too many pending requests. Bot stopped. Let some "
                           "requests get approved/cancelled, then start again.")
            config["_end_reason"] = "pending_limit"
            stop_event.set()
            return "blocked"
        blk2 = await check_account_block(page, after_txt)
        if blk2:
            send_ui("log", text=f"⚠️ Possible block signal after join ('{blk2}') — re-checking…")
            blk2 = await confirm_account_block(page, blk2)
        if blk2:
            send_ui("log", text=f"🚫 ACCOUNT BLOCK after join confirmed: '{blk2}' — stopping the bot")
            await alert_ss(page, config, f"ACCOUNT BLOCK ({blk2}) after a join",
                           "Bot stopped right after a join attempt.")
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
        try:
            with open(f"error_log{SUFFIX}.txt", "a", encoding="utf-8") as ef:
                ef.write(f"\n--- {datetime.now()} | {name} ---\n{e}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        # Browser / page hi mar gaya (crash, OOM, band ho gaya) -> is error ko
        # NIGAL kar "skipped" mat batao. Warna dead browser ke saath 100s
        # groups chup-chaap "skip" hote rehte hain aur "Done, joined 0" aata
        # hai. Ise upar bhejo -> run_playwright fresh browser se auto-restart karega.
        _es = (str(e) + " " + type(e).__name__).lower()
        if any(k in _es for k in ("target closed", "targetclosed", "browser has been closed",
                                  "page has been closed", "connection closed",
                                  "browser.newcontext", "crashed", "playwright._impl",
                                  "has been closed", "websocket", "econnreset",
                                  "session closed")):
            send_ui("log", text=f"🔁 Browser lost ({str(e)[:60]}) — will relaunch and continue.")
            raise
        send_ui("log", text=f"⏭️  Error: {str(e)[:80]}")
        log_csv(area, name, url, "error")
        return "skipped"

async def search_and_join(page, city, already_joined, config, joined_today=0, query_variant=0):
    global CURRENT_CITY
    CURRENT_CITY = city
    _bump_activity()
    joined  = 0
    skipped = 0
    limit   = config["daily_limit"]

    if stop_event.is_set() or joined_today >= limit:
        return joined, skipped

    send_ui("target", text=city)

    # Pura target (city + state, ya county + state) se search — sirf city
    # naam se search karna galat results deta tha
    # query_variant=0 -> bare area naam (pehle jaisa). 1+ -> SEARCH_TEMPLATES
    # se alag wording — FB ka search result-set query ke hisaab se badalta
    # hai, isliye alag wording = naye groups discover hote hain (khaas kar
    # chhote/hyperlocal groups jo bare naam se top results mein nahi aate).
    _tmpls = config.get("_search_templates") or SEARCH_TEMPLATES
    if query_variant and _tmpls:
        tmpl  = _tmpls[(query_variant - 1) % len(_tmpls)]
        query = tmpl.format(area=city)
    else:
        query = city
    url   = f"https://www.facebook.com/search/groups/?q={query.replace(' ','%20')}"
    send_ui("log", text=f"🔍 Searching: '{query}'")

    try:
        await _goto_retry(page, url, timeout=20000, tries=2)
        await sleep(rand_delay(1.5, 2.5))
    except Exception as e:
        # Pehle yahan chup-chaap return ho jata tha — poora target bina
        # kisi log ke gayab. Ab kam se kam pata to chale.
        send_ui("log", text=f"   ⚠️  Search page didn't load ('{query}') — skip")
        log_error(f"search goto failed: {query}", e)
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

    # Scroll to load ALL groups for this query — "ek ek gali" ka group bhi
    # miss na ho. Pehle sirf 4 fixed scrolls the (bas pehla batch), ab jab
    # tak FB naye results laata rahe scroll karte raho; 2 baar lagataar
    # koi naya result na aaye to samjho genuinely khatam ho gaya. Max 20
    # rounds — safety cap, infinite-scroll trap mein hamesha ke liye
    # atakne se bachao.
    _prev_n, _stable = -1, 0
    for _ in range(20):
        await page.keyboard.press("End")
        await sleep(rand_delay(0.8, 1.4))
        try:
            _n = await page.evaluate(
                "document.querySelectorAll('a[href*=\"/groups/\"]').length")
        except Exception:
            break
        if _n <= _prev_n:
            _stable += 1
            if _stable >= 2:
                break
        else:
            _stable = 0
        _prev_n = _n

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
        # (separators ko SPACE se replace karte hain, strip nahi — taake
        # word-boundary sahi lage: "vacation-rentals" -> "vacation rentals",
        # "cat" ab "vacation" ke andar false-match nahi karega)
        slug_l = re.sub(r"[-_.]+", " ", slug.lower())
        _blk = BLOCKED_GROUP_KEYWORDS + list(config.get("custom_blocked", []))
        if any(kw and _kw_hit(kw, slug_l) for kw in _blk):
            continue
        card_privacy = "Private" if "Private" in (card_txt or "") else "Public" if "Public" in (card_txt or "") else "?"
        # Card par member count likha hota hai — chhota group yahin chhoro,
        # page kholne ki zaroorat hi nahi (~20 sec per group bachta hai)
        m = re.search(r'([\d.,]+\s*[KkMm]?)\s*members', card_txt or "", re.I)
        card_members = _parse_count(m.group(1)) if m else 0
        if 0 < card_members < _min_members_for(config, card_privacy):
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
        urls.append((clean, card_privacy))

    if pre_skipped:
        send_ui("log", text=f"   ⚡ {pre_skipped} small groups skipped from search results (not opened)")
    send_ui("log", text=f"   📋 {len(urls)} groups found")

    for group_url, card_privacy in list(urls):
        if stop_event.is_set() or joined_today + joined >= limit:
            break

        _bump_activity()                      # har group = progress (hang-guard)
        name = group_url.split("/groups/")[-1].strip("/").replace("-", " ").title()

        # Type (private/public) search-card se hi pata chal jata hai -
        # group kholne ki zaroorat nahi. Pehle har aise skip par bhi poora
        # page load hota tha (~20 sec zaya); ek run mein sainkdon aise skip
        # hote hain, isi liye bot "bohot baad mein" join karta lagta tha.
        if card_privacy in ("Public", "Private"):
            _qmsg, _qcsv = _quota_block(config, card_privacy)
            if _qmsg:
                send_ui("log", text=f"   ⚖️  {_qmsg}, skip (not opened): {name}")
                log_csv(city, name, group_url, _qcsv, 0, card_privacy)
                skipped += 1
                send_ui("skipped")
                _qa = config.get("_activity")
                if _qa:
                    try:
                        _qa.record_skip(_qcsv)
                    except Exception:
                        pass
                continue

        status = await join_one_group(page, group_url, name, city, config, already_joined)

        _act = config.get("_activity")
        if status == "blocked":
            # account block / pending-limit -> join_one_group ne stop_event set kiya
            break
        if status == "joined":
            joined += 1
            already_joined.add(group_url)
            _total_now = joined_today + joined
            send_ui("joined", count=_total_now)
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
        # poora delay (Delay Min/Max), skip ke baad chhota sa.
        if status == "joined":
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
        send_ui("log", text=f"↻ {joined_today} groups already joined today — "
                            f"continuing from here (limit {config.get('daily_limit', 250)}).")
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
                send_ui("log", text="❌ Login timed out — log in to Facebook in the browser, then press START again.")
                config["_end_reason"] = "login_timeout"
                await ctx.close()
                return
            await sleep(3)
            send_ui("log", text="✅ Logged in! Session saved — next time it's automatic.")
        else:
            send_ui("log", text="✅ Already logged in to Facebook!")

        # Switch to page
        page_name = config.get("page_name") or DEFAULT_PAGE_NAME
        page_link = (config.get("page_link") or DEFAULT_PAGE_LINK).strip()
        if not page_name and not page_link:
            send_ui("log", text="❌ Page Link is empty — enter your Facebook Page link, then press START.")
            config["_end_reason"] = "no_page_link"
            await ctx.close()
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
            send_ui("log", text="❌ Could not switch to the Page — the bot never joins from a personal profile.")
            send_ui("log", text="   Check: (1) Page Link is correct  (2) this account is an admin of the Page")
            config["_end_reason"] = "setup_failed"
            await ctx.close()
            return

        # ── License watchdog — har 2 min: heartbeat + expiry check ──
        act = config.get("_activity")
        _integrity_ctr = {"n": 0}

        _bump_activity()   # run shuru — stall timer yahin se

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

                # ── HANG GUARD ──────────────────────────────────
                # Aakhri progress ko STALL_RESTART_SEC se zyada ho gaya?
                # Matlab bot kisi page/call par bina exception ke atka hua
                # hai. Browser context band karo -> main coroutine throw
                # karega -> run_playwright FRESH browser se khud restart.
                _stall = time.monotonic() - _LAST_ACTIVITY
                if _stall >= STALL_RESTART_SEC:
                    _mins = int(_stall // 60)
                    send_ui("log", text=f"⏱️ No progress for ~{_mins} min — the page looks "
                                        f"stuck. Restarting the browser now "
                                        f"(today's joins are safe, no action needed).")
                    if act:
                        try:
                            act.alert(f"Bot stalled ~{_mins} min on a page — auto-restarting "
                                      f"the browser. No action needed unless it repeats. "
                                      f"📞 Contact {BRAND} if it keeps happening.")
                        except Exception:
                            pass
                    config["_stall_restart"] = True
                    try:
                        await page.context.close()
                    except Exception:
                        pass
                    return

                # Har ~10 cycle (~20 min): file-tamper check. Ab bot ko ROKTA
                # NAHI — sirf Discord alert + file ko chup-chaap original se
                # heal kar deta hai (heal disk par ho jata hai, agli restart
                # par apply). Isse chalti joining bina wajah nahi rukti.
                _integrity_ctr["n"] += 1
                if _integrity_ctr["n"] % 10 == 0:
                    try:
                        import updater

                        def _integrity_alert(msg):
                            if act:
                                try:
                                    act.alert(msg + " (file restored to the verified "
                                              "original — will apply on next restart)")
                                except Exception:
                                    pass

                        tampered = await asyncio.to_thread(
                            updater.check_integrity_only,
                            getattr(lic, "UPDATE_URL", ""), APP_DIR,
                            print, _integrity_alert)
                        if tampered:
                            send_ui("log", text=f"🚨 File change detected ({', '.join(tampered)}) "
                                                 f"— alert sent + verified original restored. "
                                                 f"Joining continues.")
                    except Exception:
                        pass
                # account block periodically bhi check karo (current page)
                try:
                    blk = await check_account_block(page)
                    if blk:
                        blk = await confirm_account_block(page, blk)
                except Exception:
                    blk = ""
                if blk:
                    send_ui("log", text=f"🚫 ACCOUNT BLOCK (watchdog, confirmed): '{blk}' — stopping")
                    await alert_ss(page, config,
                                   f"ACCOUNT CHECKPOINT / BLOCK ({blk})",
                                   "Caught by the 2-minute watchdog. Bot stopped.")
                    config["_end_reason"] = "account_blocked"
                    stop_event.set()
                    return
                chk = lic.validate_key(config.get("license_key", ""))
                if not chk["ok"]:
                    send_ui("log", text=f"⛔ License: {chk['error']}")
                    send_ui("log", text="   Bot is stopping — activate a new key and press START again.")
                    _alert_wrong_pc(chk)
                    await alert_ss(page, config, "LICENSE problem",
                                   str(chk.get("error", "")))
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
                    send_ui("log", text=f"⏸️  {_smsg} — stopping the bot.")
                    if act:
                        try:
                            act.alert(f"Bot admin-paused ({_smsg}) — stopped.")
                        except Exception:
                            pass
                    config["_end_reason"] = "service_paused"
                    stop_event.set()
                    return

        wd_task = asyncio.create_task(_license_watchdog())

        send_ui("log", text=f"⚡ Delay between joins: {config.get('delay_min')}–"
                            f"{config.get('delay_max')}s  ·  runs 24/7 until the daily limit.")

        selection = config["city"]
        limit     = config["daily_limit"]
        _mode     = config.get("business_mode", "car")
        _area_list = areas_for(_mode)
        send_ui("log", text=f"🧩 Mode: {_mode.upper()}  ·  {len(_area_list)} areas in this list")

        # Area order: agar ek specific area chuna hai to WOH pehle, phir
        # baaki SAARE areas (taake area khatam hone par bot rukta nahi —
        # khud agle area par chala jata hai). "ALL AREAS" = sab shuffle.
        _others = [a for a in _area_list if a != selection]
        random.shuffle(_others)
        if selection == ALL_AREAS_LABEL:
            areas_to_run = _others
        else:
            areas_to_run = [selection] + _others

        total_areas = len(areas_to_run)
        send_ui("log", text=f"📍 {total_areas} area(s) queued "
                            f"— bot won't stop when one finishes, it moves to the next.")

        # OUTER loop: saare areas khatam ho jayein aur limit bhi na lagi ho
        # to phir se shuru (naye bane groups mil sakte hain). Ek poore pass
        # mein 0 naye join mile -> ab genuinely kuch nahi bacha, tab rukein.
        _round = 0
        while not stop_event.is_set() and joined_today < limit:
            _round += 1
            _round_start = joined_today
            if _round > 1:
                send_ui("log", text=f"\n🔁 Round {_round} — re-scanning all areas for newly created groups…")
                random.shuffle(areas_to_run)

            for area_idx, area in enumerate(areas_to_run, 1):
                if stop_event.is_set() or joined_today >= limit:
                    break

                send_ui("log", text=f"\n🏙️  Area {area_idx}/{total_areas}: {area}")
                send_ui("area", text=f"Area {area_idx}/{total_areas}: {area}")

                loop = asyncio.get_event_loop()
                _same_state = config.get("same_state_only", True)
                _inc_counties = config.get("include_counties", True)
                targets = await loop.run_in_executor(
                    None, get_targets_for_area, area, _same_state, _inc_counties, _mode)
                random.shuffle(targets)
                _st = _area_state_code(area)
                _radius_mi = 60 if (_mode or "").lower() == "duct" else 40
                # Sach likho: agar radius mein parosi states bhi shamil hain
                # to unke naam dikhao, "DC only" jaisa jhoot mat bolo.
                _sts = states_in_targets(targets)
                if _same_state and _st:
                    _scope = (f" — {_st} only ({_radius_mi}-mi radius)"
                              if _sts in ([_st], [])
                              else f" — {_radius_mi}-mi radius around {_st}"
                                   f" (states: {', '.join(_sts)})")
                else:
                    _scope = ""
                send_ui("log", text=f"   🗺️  {len(targets)} targets" + _scope
                        + (" (cities + counties)" if _inc_counties else " (cities only, no counties)"))

                area_joined_start = joined_today

                # Is area par ek ke baad ek WORDING chalao — jab tak naye
                # groups milte rahein. Pehle har area ko sirf EK wording
                # milti thi aur bot foran agle state par chala jata tha
                # (isi liye area "bohot jaldi pura" ho jata tha); baaki
                # 100 wordings agle rounds ke liye reh jati thin, jo aksar
                # aate hi nahi the.
                _variant = _round - 1        # round 1 -> bare area naam
                _empty_streak = 0
                _wordings = 0
                while not stop_event.is_set() and joined_today < limit:
                    _before = joined_today
                    for target in targets:
                        if stop_event.is_set() or joined_today >= limit:
                            break
                        config["_current_city"] = target
                        n_joined, n_skipped = await search_and_join(
                            page, target, already_joined, config, joined_today,
                            query_variant=_variant)
                        joined_today  += n_joined
                        skipped_today += n_skipped
                        total         += n_joined
                        total_skipped += n_skipped
                        save_total(total)
                        save_total_skipped(total_skipped)
                        send_ui("total", count=total)
                        send_ui("total_skipped", count=total_skipped)

                    _wordings += 1
                    _got = joined_today - _before
                    if _got:
                        _empty_streak = 0
                        send_ui("log", text=f"      ↳ wording {_variant}: +{_got} groups")
                    else:
                        _empty_streak += 1
                        if _empty_streak >= AREA_EMPTY_WORDINGS:
                            break
                    _variant += 1
                    if _variant > len(config.get("_search_templates") or SEARCH_TEMPLATES):
                        break
                    random.shuffle(targets)

                area_joined = joined_today - area_joined_start
                send_ui("log", text=f"   ✅ Area '{area}' done: +{area_joined} groups "
                                    f"({_wordings} wording(s) tried)")

            # Poora pass khatam — is pass mein kuch mila?
            if joined_today - _round_start == 0:
                send_ui("log", text="\nℹ️ Full pass over all areas found no new groups "
                                    "to join right now. Nothing left — you can run again later.")
                config["_end_reason"] = "nothing_left"
                break
            if _round >= 110:    # safety — infinite loop se bacho (101 templates + bare naam)
                break

        try:
            wd_task.cancel()
            try:
                await wd_task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception:
            pass
        if _GEMINI_DOWN.is_set():
            config["_end_reason"] = "gemini_down"
        if config.get("_end_reason") not in ("license_expired", "account_blocked",
                                              "pending_limit", "setup_failed",
                                              "file_tamper", "service_paused",
                                              "login_timeout", "no_page_link",
                                              "nothing_left", "gemini_down"):
            config["_end_reason"] = "user_stop" if stop_event.is_set() else "completed"

        _this_run = joined_today - _session_start
        config["_run_joined"]  = _this_run
        config["_today_total"] = joined_today

        # Har GHAIR-MAMOOLI stop par screenshot + wajah Discord bhejo.
        # (Normal finish / employee ka apna STOP par nahi - warna har roz
        # ka aam stop bhi alert ban jata.)
        _r = config.get("_end_reason", "")
        if _r not in ("completed", "user_stop", "nothing_left"):
            _why = {
                "gemini_down":     "All Gemini API keys failed",
                "license_expired": "License expired / invalid",
                "account_blocked": "Facebook checkpoint or account block",
                "pending_limit":   "Join-request limit (too many pending)",
                "service_paused":  "Paused by the administrator",
                "setup_failed":    "Pre-flight setup failed",
                "file_tamper":     "Bot files were modified",
                "login_timeout":   "Facebook login timed out",
                "no_page_link":    "Page link missing or wrong",
                "error":           "Bot crashed",
            }.get(_r, _r)
            try:
                await alert_ss(page, config, "BOT STOPPED - " + _why,
                               "This run: joined " + str(_this_run) +
                               ", skipped " + str(skipped_today) + ".")
            except Exception:
                pass
        send_ui("log", text=f"\n🎉 This run: joined {_this_run} groups, skipped {skipped_today}. "
                            f"Today's total: {joined_today}/{config.get('daily_limit', 250)}.")
        await ctx.close()

async def _login_browser_main():
    """Sirf browser kholo Facebook pe — user login karega, joining kuch
    nahi. User window band karega -> session save -> ho gaya."""
    async with async_playwright() as p:
        send_ui("log", text="🌐 Opening browser — log in to Facebook…")
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
        send_ui("log", text="   👉 Log in, then CLOSE the browser window (or press STOP).")
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
        send_ui("log", text=("✅ Login session saved — you can press START now."
                             if logged_in else
                             "ℹ️ Browser closed. If you didn't log in, click "
                             "'Open browser' again."))
    send_ui("login_done")


def _launch_error_hint(e: Exception) -> str:
    """Browser launch fail hone par employee ko SAAF wajah + hal batao.

    Pehle sirf `str(e)[:80]` dikhta tha — Playwright ka asli message isse
    kahin lamba hota hai, to employee ko bas "launching error" nazar aata
    tha aur wajah kabhi pata nahi chalti thi.
    """
    s = (str(e) + " " + type(e).__name__).lower()
    if "executable doesn't exist" in s or "playwright install" in s \
            or "browsertype.launch" in s and "download" in s:
        return ("Chromium browser is not installed in this folder.\n"
                "FIX: open Command Prompt in this folder and run:\n"
                "       py -m playwright install chromium\n"
                "   (or close the bot and double-click START.bat again)")
    if "processsingleton" in s or "singletonlock" in s \
            or "profile appears to be in use" in s or "already in use" in s:
        return ("This profile is already open in another window.\n"
                "FIX: close every bot + Chrome window for this profile, "
                "then try again.")
    if "permission" in s or "access is denied" in s:
        return ("No write permission for this folder (permission denied).\n"
                "FIX: move the folder to Desktop/Documents (not Program Files "
                "or OneDrive), or right-click START.bat -> "
                "'Run as administrator'.")
    if "no such file or directory" in s or "cannot find the path" in s:
        return ("A file is missing from this folder.\n"
                "FIX: copy the whole folder again — the copy was incomplete.")
    return ""


def run_login_browser():
    try:
        asyncio.run(_login_browser_main())
    except Exception as e:
        # Poori error hamesha error_log mein — support ke liye
        log_error("login browser launch failed", e)
        send_ui("log", text=f"❌ Browser launch fail: {str(e)[:300]}")
        hint = _launch_error_hint(e)
        if hint:
            send_ui("log", text=f"💡 {hint}")
        send_ui("log", text=f"   (poori detail: error_log{SUFFIX}.txt)")
        send_ui("login_done")


async def _logout_main():
    """Facebook session (cookies + local storage) clear karo — account
    suspend/checkpoint hone par employee khud yahan se logout kar sake,
    dobara login karne ke liye. Browser dikhta nahi (background mein)."""
    send_ui("log", text="🚪 Logging out of Facebook…")
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
        send_ui("log", text=("✅ Logged out — session cleared. Click 'Open browser & "
                             "log in' to sign in again."
                             if logged_out else
                             "⚠️ Tried to log out but couldn't verify it — check with "
                             "'Open browser'."))
    except Exception as e:
        send_ui("log", text=f"logout error: {str(e)[:80]}")
    send_ui("logout_done")


def run_logout_browser():
    try:
        asyncio.run(_logout_main())
    except Exception as e:
        send_ui("log", text=f"logout error: {str(e)[:80]}")
        send_ui("logout_done")


# ── Auto-post to joined groups (DEV only) ───────────────────
POSTED_FILE = f"posted_groups{SUFFIX}.txt"

def _load_posted() -> set:
    try:
        return set(x.strip() for x in open(POSTED_FILE, encoding="utf-8") if x.strip())
    except Exception:
        return set()

def _mark_posted(url: str):
    try:
        with open(POSTED_FILE, "a", encoding="utf-8") as f:
            f.write(url + "\n")
    except Exception:
        pass

def _joined_group_urls() -> list:
    """groups_log{SUFFIX}.csv se woh group URLs jinme Status == joined."""
    out, seen = [], set()
    try:
        with open(LOG_FILE, encoding="utf-8", newline="") as f:
            for row in csv.reader(f):
                if len(row) >= 8 and row[7].strip().lower() == "joined":
                    u = row[4].strip()
                    if u and u not in seen:
                        seen.add(u); out.append(u)
    except Exception:
        pass
    return out


async def _dismiss_group_gates(page):
    """Group kholte hi kabhi 'group rules' / 'Next' / 'Got it' / 'I agree'
    wale interstitials aate hain — unko click karke aage niklo."""
    for _ in range(6):
        hit = False
        for sel in ('div[role="dialog"] div[role="button"]:has-text("Next")',
                    'div[role="dialog"] div[role="button"]:has-text("Got it")',
                    'div[role="dialog"] div[role="button"]:has-text("I Agree")',
                    'div[role="dialog"] div[role="button"]:has-text("I agree")',
                    'div[role="dialog"] div[role="button"]:has-text("Continue")',
                    'div[role="dialog"] div[role="button"]:has-text("Done")',
                    'div[role="dialog"] div[role="button"]:has-text("Agree")'):
            try:
                b = page.locator(sel).first
                if await b.count() and await b.is_visible(timeout=600):
                    await b.click()
                    await sleep(rand_delay(0.8, 1.6))
                    hit = True
                    break
            except Exception:
                continue
        if not hit:
            break


async def _find_composer_box(page):
    """Active post-composer ka contenteditable textbox dhoondo — MODAL dialog
    ya INLINE dono layouts. Comment box ko chhod ke. Nahi mila -> None."""
    sels = [
        'div[role="dialog"] div[role="textbox"][contenteditable="true"]',
        'div[role="textbox"][contenteditable="true"][aria-label*="mind" i]',
        'div[role="textbox"][contenteditable="true"][aria-label*="write" i]',
        'div[role="textbox"][contenteditable="true"][aria-label*="discussion" i]',
        'div[role="textbox"][contenteditable="true"][aria-label*="post" i]',
        'div[contenteditable="true"][role="textbox"]',
    ]
    for sel in sels:
        try:
            n = await page.locator(sel).count()
        except Exception:
            n = 0
        for i in range(min(n, 6)):
            b = page.locator(sel).nth(i)
            try:
                if not await b.is_visible():
                    continue
                al = (await b.get_attribute("aria-label") or "").lower()
                ph = (await b.get_attribute("data-placeholder") or "").lower()
                if "comment" in al or "comment" in ph or "reply" in al:
                    continue
                return b
            except Exception:
                continue
    return None


async def _click_post_button(page):
    """Composer ka 'Post' button (dialog ya inline) — visible + enabled."""
    for ps in ('div[role="dialog"] [aria-label="Post"]',
               'div[role="dialog"] div[role="button"]:has-text("Post")',
               '[aria-label="Post"][role="button"]',
               'div[role="button"][aria-label="Post"]'):
        try:
            loc = page.locator(ps)
            for i in range(min(await loc.count(), 4)):
                pb = loc.nth(i)
                if await pb.is_visible():
                    dis = await pb.get_attribute("aria-disabled")
                    if dis == "true":
                        continue
                    await pb.click()
                    return True
        except Exception:
            continue
    # last resort: exact-text button
    try:
        pb = page.get_by_role("button", name="Post", exact=True).last
        if await pb.is_visible():
            await pb.click()
            return True
    except Exception:
        pass
    return False


async def _post_to_group(page, message: str, image_path: str) -> str:
    """Group ke 'Create a post' composer se post karo (comment box se NAHI).
    Return: 'posted' | 'pending' | 'disabled' | 'nocomposer' | 'error'."""
    try:
        await _dismiss_group_gates(page)
        await sleep(rand_delay(1, 2))

        # posting genuinely band? (only admins can post)
        try:
            btxt = (await page.inner_text("body"))[:2500].lower()
            if ("only admins can post" in btxt or "admins have turned off posting" in btxt
                    or "you can't post in this group" in btxt):
                return "disabled"
        except Exception:
            pass

        # 1) composer trigger ("Write something...", "Start a discussion...")
        trig = None
        for sel in ('div[role="button"]:has-text("Write something")',
                    'div[role="button"]:has-text("Start a discussion")',
                    'div[role="button"][aria-label*="Create a" i]',
                    'div[aria-label="Write something..."]',
                    'div[aria-label="Start a discussion..."]',
                    'span:has-text("Write something")'):
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible(timeout=800):
                    trig = loc
                    break
            except Exception:
                continue
        if trig is None:
            return "nocomposer"
        try:
            await trig.click()
        except Exception:
            await trig.click(force=True)
        await sleep(rand_delay(2.5, 4))

        # 2) composer textbox aane ka wait (modal ya inline) — ~10s
        box = None
        for _ in range(20):
            box = await _find_composer_box(page)
            if box is not None:
                break
            await sleep(0.5)
        if box is None:
            # dobara trigger try
            try:
                await trig.click()
                await sleep(2)
                box = await _find_composer_box(page)
            except Exception:
                pass
        if box is None:
            try: await page.keyboard.press("Escape")
            except Exception: pass
            return "nocomposer"

        # 3) type message
        await box.click()
        await sleep(0.5)
        typed = False
        try:
            await box.evaluate("(el, t) => { el.focus(); "
                               "document.execCommand('insertText', false, t); }", message)
            typed = bool((await box.inner_text() or "").strip())
        except Exception:
            typed = False
        if not typed:
            try:
                await box.type(message[:800], delay=5)
            except Exception:
                pass
        await sleep(rand_delay(1.5, 2.5))

        # 4) image attach
        if image_path and os.path.exists(image_path):
            try:
                for pv in ('div[role="dialog"] [aria-label="Photo/video"]',
                           '[aria-label="Photo/video"]',
                           'div[role="button"][aria-label*="Photo" i]',
                           'div[role="button"]:has-text("Photo/video")'):
                    pb = page.locator(pv).first
                    if await pb.count() and await pb.is_visible():
                        await pb.click()
                        break
                await sleep(1.3)
                fi = page.locator('input[type="file"][accept*="image"]').last
                if not await fi.count():
                    fi = page.locator('input[type="file"]').last
                await fi.set_input_files(image_path)
                await sleep(rand_delay(4, 8))          # upload
            except Exception:
                pass

        # 5) Post
        if not await _click_post_button(page):
            try: await page.keyboard.press("Escape")
            except Exception: pass
            return "error"

        await sleep(rand_delay(3, 6))
        try:
            body = (await page.inner_text("body")).lower()
            if ("pending" in body or "will be visible once" in body
                    or "sent for review" in body or "awaiting approval" in body):
                return "pending"
        except Exception:
            pass
        return "posted"
    except Exception:
        try: await page.keyboard.press("Escape")
        except Exception: pass
        return "error"


async def _autopost_main(config):
    msg   = config.get("post_message", "").strip()
    img   = config.get("post_image", "")
    send_ui("log", text="\n📢 Auto-post: launching browser…")
    posted_set = _load_posted()
    urls = [u for u in _joined_group_urls() if u not in posted_set]
    send_ui("log", text=f"   {len(urls)} groups to post in "
                        f"({len(posted_set)} already done, skipped).")
    if not urls:
        send_ui("log", text="   Nothing to post — all joined groups already posted.")
        send_ui("autopost_done"); return

    done = fails = 0
    try:
        async with async_playwright() as p:
            ctx = await _launch_ctx(p, headless=False, viewport={"width": 1366, "height": 768},
                                   extra_args=["--start-maximized"])
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=25000)
            await sleep(3)
            await dismiss_popups(page)
            if await page.locator('[role="navigation"]').count() == 0:
                send_ui("log", text="❌ Not logged in — open the bot, log in, then retry.")
                await ctx.close(); send_ui("autopost_done"); return

            plink = (config.get("page_link") or DEFAULT_PAGE_LINK).strip()
            if plink:
                await switch_via_link(page, plink, config.get("page_name", ""))
            await sleep(2)

            _act = config.get("_activity")
            for i, url in enumerate(urls, 1):
                if stop_event.is_set():
                    break
                gname = url.rstrip("/").split("/")[-1]
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await sleep(rand_delay(2.5, 4))
                    await dismiss_popups(page)
                    await _dismiss_group_gates(page)   # 'group rules' / Next / Got it
                except Exception:
                    send_ui("log", text=f"   ↻ {gname}: load failed, will retry next run")
                    continue

                # Group deleted / privacy badal gayi / link toota — yahan kabhi
                # post nahi hoga. MARK karke turant agle group par jao (warna
                # bot yahin atka rehta tha).
                try:
                    _bt = (await page.inner_text("body"))[:3000].lower()
                except Exception:
                    _bt = ""
                if ("isn't available at the moment" in _bt
                        or "content isn't available" in _bt
                        or "this page isn't available" in _bt
                        or "page isn't available" in _bt
                        or "link you followed may be broken" in _bt
                        or "content not found" in _bt
                        or "this group is no longer available" in _bt
                        or "the page you requested cannot be displayed" in _bt):
                    fails += 1
                    _mark_posted(url)
                    send_ui("log", text=f"   ⏭️ [{i}/{len(urls)}] group unavailable "
                                        f"(deleted/private) — skipped: {gname}")
                    await sleep(rand_delay(4, 9))
                    continue

                # Auto-post mein posting-limit bhi rukne ki wajah hai
                blk = await check_account_block(page, include_post_limit=True)
                if blk:
                    blk = await confirm_account_block(page, blk, include_post_limit=True)
                if blk:
                    send_ui("log", text=f"🚫 ACCOUNT BLOCK ({blk}) — stopping auto-post.")
                    if _act:
                        try: _act.alert(f"ACCOUNT BLOCK during auto-post ({blk}) — stopped.")
                        except Exception: pass
                    config["_end_reason"] = "account_blocked"
                    stop_event.set()
                    break

                res = await _post_to_group(page, msg, img)
                if res in ("posted", "pending"):
                    done += 1
                    _mark_posted(url)
                    send_ui("log", text=f"   ✅ [{i}/{len(urls)}] {res}: {gname}")
                elif res == "disabled":
                    fails += 1
                    _mark_posted(url)     # is group mein members post nahi kar sakte — dobara mat try
                    send_ui("log", text=f"   ⏭️ [{i}/{len(urls)}] posting disabled (admins only): {gname}")
                elif res == "nocomposer":
                    fails += 1            # composer nahi mila — MARK MAT karo, agli run retry
                    send_ui("log", text=f"   ↻ [{i}/{len(urls)}] composer not found: {gname} (retry next run)")
                else:
                    fails += 1
                    send_ui("log", text=f"   ⚠️ [{i}/{len(urls)}] post failed: {gname} (retry next run)")

                # rest cycle + delay
                if done and done % 10 == 0 and not stop_event.is_set():
                    send_ui("log", text="😴 10 posts — resting 5 min…")
                    _e = time.time() + 300
                    while time.time() < _e and not stop_event.is_set():
                        await asyncio.sleep(5)
                await sleep(rand_delay(8, 12))     # ~10 sec between posts

            await ctx.close()
    except Exception as e:
        send_ui("log", text=f"auto-post error: {str(e)[:100]}")

    send_ui("log", text=f"\n■ AUTO-POST DONE — posted {done}, skipped/failed {fails}.")
    try:
        _a = config.get("_activity")
        if _a:
            _a.alert(f"Auto-post run finished — posted {done}, skipped/failed {fails}.")
    except Exception:
        pass
    send_ui("autopost_done")


def run_autopost(config):
    try:
        asyncio.run(_autopost_main(config))
    except Exception as e:
        send_ui("log", text=f"auto-post error: {str(e)[:100]}")
        send_ui("autopost_done")


# ── Pre-flight check ─────────────────────────────────────────
async def _preflight_test_join(page, config) -> tuple:
    """ASAL ek group dhoond ke join try karo — agar account pe koi
    restriction (checkpoint/block/pending-limit) pehle se lagi hai to
    poori session shuru karne se pehle hi pata chal jaye, 100+ groups
    barbaad karne se pehle.
    Return: (status, detail)
      'ok'           -> join ho gaya, koi restriction nahi mili
      'blocked'      -> restriction confirm hui
      'no_candidate' -> test ke liye koi group hi nahi mila
      'error'        -> kuch aur gadbad
    """
    try:
        city = (config.get("city") or "").strip()
        if not city or city == ALL_AREAS_LABEL:
            _al = areas_for(config.get("business_mode", "car"))
            city = random.choice(_al) if _al else "United States"

        query = f"{city} community"
        url = f"https://www.facebook.com/search/groups/?q={query.replace(' ', '%20')}"
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await sleep(rand_delay(1.5, 2.5))
        if "County" not in city:
            try:
                await apply_fb_filters(page, city)
            except Exception:
                pass
        for _ in range(3):
            await page.keyboard.press("End")
            await sleep(rand_delay(0.6, 1.0))

        hrefs = await page.evaluate("""
            () => {
                const out = new Set();
                document.querySelectorAll('a[href*="/groups/"]').forEach(a => {
                    let href = a.href.split('?')[0].replace(/\\/+$/, '');
                    if (/\\/groups\\/[a-zA-Z0-9._-]+$/.test(href)) out.add(href);
                });
                return [...out];
            }
        """)
        already = load_joined()
        candidates = [u for u in hrefs if u not in already]
        random.shuffle(candidates)
        if not candidates:
            return ("no_candidate", f"no un-joined group found near '{city}' to test with")

        test_url = candidates[0]
        name = test_url.split("/groups/")[-1].strip("/").replace("-", " ").title()

        await page.goto(test_url, wait_until="domcontentloaded", timeout=15000)
        await sleep(rand_delay(1, 2))
        await dismiss_popups(page)
        try:
            body_txt = await page.inner_text("body")
        except Exception:
            body_txt = ""

        # Yahan pehle se restriction? -> join try karne ki zaroorat nahi
        blk = await check_account_block(page, body_txt)
        if blk:
            blk = await confirm_account_block(page, blk)
        if blk:
            return ("blocked", f"checkpoint/block already active: {blk}")

        clicked = await click_join(page)
        if not clicked:
            return ("no_candidate", f"'{name}' had no visible Join button — try again")

        await sleep(rand_delay(2, 3.5))
        try:
            after = await page.inner_text("body")
        except Exception:
            after = ""
        if check_pending_limit(after):
            return ("blocked", "join-request limit already reached (too many pending)")
        blk2 = await check_account_block(page, after)
        if blk2:
            blk2 = await confirm_account_block(page, blk2)
        if blk2:
            return ("blocked", f"restricted right after joining: {blk2}")

        # Sab theek — is test-join ko normal join ki tarah save karo
        # (barbaad nahi hota, humesha ke liye track ho jata hai)
        log_csv(city, name, test_url, "joined", 0, "?")
        save_joined(test_url)
        return ("ok", f"test-joined '{name}' — no restriction detected")
    except Exception as e:
        return ("error", str(e)[:80])


async def _preflight_main(config):
    """START se pehle sab kuch verify: license, internet, admin switch,
    Gemini keys, Facebook login, page. Log mein green/red report."""
    send_ui("log", text="\n🔎 Running pre-flight check… (this takes a moment)")
    rows = []   # (label, "ok"/"warn"/"fail", detail)

    # 1. License
    try:
        i = lic.validate_key(lic.load_active_key())
        if i.get("ok"):
            rows.append(("License", "ok", f"valid · {i.get('time_left', '')} left"))
        else:
            rows.append(("License", "fail", i.get("error", "invalid")))
    except Exception as e:
        rows.append(("License", "fail", str(e)[:60]))

    # 2. Internet
    def _net():
        import urllib.request
        for u in ("https://www.facebook.com/robots.txt",
                  "https://raw.githubusercontent.com/"):
            try:
                urllib.request.urlopen(urllib.request.Request(
                    u, headers={"User-Agent": _STEALTH_UA}), timeout=8).read(64)
                return True
            except Exception:
                continue
        return False
    _ok = await asyncio.to_thread(_net)
    rows.append(("Internet", "ok" if _ok else "fail",
                 "connected" if _ok else "no connection — check network"))

    # 3. Admin remote switch
    try:
        import updater as _u
        _sok, _sm = await asyncio.to_thread(
            _u.service_allows, getattr(lic, "UPDATE_URL", ""), config.get("employee", ""))
        rows.append(("Admin switch", "ok" if _sok else "fail",
                     "allowed" if _sok else (_sm or "paused by the administrator")))
    except Exception:
        rows.append(("Admin switch", "warn", "could not check"))

    # 4. Gemini keys
    keys = list(config.get("gemini_keys", []) or [])
    if not keys:
        rows.append(("Gemini keys", "warn", "none set — template answers will be used"))
    else:
        def _gk():
            import urllib.request
            good = 0
            for k in keys:
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        headers={"x-goog-api-key": k}), timeout=12).read(64)
                    good += 1
                except Exception:
                    pass
            return good
        g = await asyncio.to_thread(_gk)
        rows.append(("Gemini keys", "ok" if g == len(keys) else ("warn" if g else "fail"),
                     f"{g}/{len(keys)} valid"))

    # 5. Facebook login + page (headless browser)
    fb_ok, page_ok, blk = False, None, ""
    try:
        async with async_playwright() as p:
            ctx = await _launch_ctx(p, headless=True)
            pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                await pg.goto("https://www.facebook.com/", wait_until="domcontentloaded",
                              timeout=25000)
                await sleep(2)
            except Exception:
                pass
            body = ""
            try:
                body = await pg.inner_text("body")
            except Exception:
                pass
            try:
                blk = await check_account_block(pg, body)
            except Exception:
                blk = ""
            try:
                fb_ok = ("/login" not in (pg.url or "").lower()) and (
                    await pg.locator('[role="navigation"]').count() > 0
                    or await pg.locator('[role="feed"]').count() > 0)
            except Exception:
                fb_ok = False
            plink = (config.get("page_link") or "").strip()
            if fb_ok and plink and not blk:
                try:
                    await pg.goto(plink, wait_until="domcontentloaded", timeout=25000)
                    await sleep(2)
                    page_ok = "/login" not in (pg.url or "").lower()
                except Exception:
                    page_ok = False

            # 6. Live restriction test — ek asal group dhoond ke join karke
            #    dekho FB abhi restrict tou nahi kar raha (Muzammil ka hukum:
            #    poori session shuru karne se pehle hi pata chal jaye).
            tj_status, tj_detail = None, ""
            if fb_ok and not blk:
                send_ui("log", text="   🧪 Testing a real group join (checking for restrictions)…")
                tj_status, tj_detail = await _preflight_test_join(pg, config)

            await ctx.close()
    except Exception as e:
        rows.append(("Browser", "fail", str(e)[:60]))

    if blk and "logged out" not in blk:
        rows.append(("Facebook", "fail", f"checkpoint/block: {blk} — rest this account"))
    else:
        rows.append(("Facebook login", "ok" if fb_ok else "fail",
                     "logged in" if fb_ok else "NOT logged in — click 'Open browser & log in'"))
    if page_ok is not None:
        rows.append(("Page link", "ok" if page_ok else "warn",
                     "opens fine" if page_ok else "won't open — check the URL / admin access"))
    if tj_status == "ok":
        rows.append(("Restriction test", "ok", tj_detail))
    elif tj_status == "blocked":
        rows.append(("Restriction test", "fail", tj_detail))
    elif tj_status == "no_candidate":
        rows.append(("Restriction test", "warn", tj_detail))
    elif tj_status == "error":
        rows.append(("Restriction test", "warn", f"could not test: {tj_detail}"))

    ic = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    for lbl, st, dt in rows:
        send_ui("log", text=f"   {ic[st]} {lbl}: {dt}")
    fails = [r for r in rows if r[1] == "fail"]
    warns = [r for r in rows if r[1] == "warn"]
    if fails:
        send_ui("log", text=f"■ PRE-FLIGHT: {len(fails)} FAIL — fix these, then START. "
                            f"📞 If unsure, Contact {BRAND}.")
    elif warns:
        send_ui("log", text=f"■ PRE-FLIGHT: all OK ({len(warns)} warning) — you can START.")
    else:
        send_ui("log", text="■ PRE-FLIGHT: all GREEN ✅ — go ahead and START.")
    send_ui("preflight_done")


def run_preflight(config):
    try:
        asyncio.run(_preflight_main(config))
    except Exception as e:
        send_ui("log", text=f"pre-flight error: {str(e)[:80]}")
        send_ui("preflight_done")


def run_playwright(config):
    # ── License gate — the bot does not run without a valid key ──
    info = lic.validate_key(config.get("license_key", ""))
    if not info["ok"]:
        send_ui("log", text=f"⛔ License invalid: {info['error']}")
        send_ui("log", text=f"   Activate a new key, then press START.  📞 Contact {BRAND}")
        _alert_wrong_pc(info)
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
        send_ui("log", text=f"   The administrator has paused this bot — try START later.  "
                            f"📞 Contact {BRAND}")
        send_ui("stopped")
        return

    # ── Gemini AI answers (optional, key rotation) ──
    global GEMINI_KEYS, _gk_idx, _GEMINI_DEAD_REASON, _GK_ALL_DOWN_UNTIL
    GEMINI_KEYS = list(config.get("gemini_keys", []) or [])
    _gk_idx = 0
    _gk_cooldown.clear()
    _GEMINI_DEAD_REASON = None   # purani run ka stale flag na reh jaye
    _GK_ALL_DOWN_UNTIL = 0.0
    send_ui("log", text=(f"🤖 AI answers ON ({GEMINI_MODEL}) — {len(GEMINI_KEYS)} key(s) in rotation")
            if GEMINI_KEYS else "💬 AI answers OFF — using built-in template answers")

    config["_jp"] = 0
    config["_jpriv"] = 0
    _pp = config.get("public_pct", 30)
    send_ui("log", text=f"🎯 Target mix: {_pp}% public / {100 - _pp}% private"
            + ("  ·  skip no-post groups" if config.get("skip_no_post", True) else ""))
    _hi = max(int(config.get("min_members_public", DEFAULT_MIN_MEMBERS) or 0),
              int(config.get("min_members_private", DEFAULT_MIN_MEMBERS) or 0))
    send_ui("log", text=f"👥 Min members: {config.get('min_members_public')} public / "
                        f"{config.get('min_members_private')} private")
    if _hi >= BIG_MIN_MEMBERS:
        send_ui("log", text=f"⚠️  Minimum {_hi:,} members is HIGH — such big groups are "
                            f"rare, so joining will be MUCH slower and the daily total "
                            f"will be lower. Lower it to ~{DEFAULT_MIN_MEMBERS:,} for speed.")
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
    # Crash / browser band ho jaye -> khud restart, aaj ke joins wahin se
    # continue (joined_today persistent). STOP dabaya / terminal reason
    # (license / block / gemini / etc.) -> restart NAHI. end_session sirf
    # EK baar (end mein) — warna Discord par baar-baar "stopped" spam hota
    # tha jo "bina wajah band" jaisa lagta tha.
    import traceback
    MAX_RESTARTS = 6
    _terminal = ("license_expired", "account_blocked", "pending_limit",
                 "setup_failed", "file_tamper", "gemini_keys_failed",
                 "service_paused", "login_timeout", "no_page_link",
                 "nothing_left")
    restarts = 0
    _stall_restarts = 0
    _gem_restarts = 0
    GEMINI_MAX_RESTARTS = 24        # ~2 ghante tak har 5 min koshish
    _last_err = ""
    try:
        while True:
            try:
                asyncio.run(playwright_main(config))
                if config.get("_end_reason") == "gemini_down" and not user_stop_event.is_set():
                    _gem_restarts += 1
                    if _gem_restarts > GEMINI_MAX_RESTARTS:
                        config["_end_reason"] = "gemini_keys_failed"
                        break
                    send_ui("log", text=f"⏸️  Gemini keys down — waiting "
                                        f"{GEMINI_RETRY_SEC // 60} min, then restarting "
                                        f"({_gem_restarts}/{GEMINI_MAX_RESTARTS}). "
                                        f"DON'T press START — it continues by itself.")
                    _end_at = time.time() + GEMINI_RETRY_SEC
                    while time.time() < _end_at and not user_stop_event.is_set():
                        time.sleep(3)
                    if user_stop_event.is_set():
                        config["_end_reason"] = "user_stop"
                        break
                    # keys dobara check: cooldown saaf karo aur file/UI se
                    # taza keys uthao (admin ne nayi daal di hon to lag jayen)
                    _GEMINI_DOWN.clear()
                    _gk_cooldown.clear()
                    globals()["_GK_ALL_DOWN_UNTIL"] = 0.0
                    _fresh = resolve_gemini_keys("")   # file/env se taza keys
                    if _fresh:
                        globals()["GEMINI_KEYS"] = _fresh
                    send_ui("log", text=f"🔄 Restarting — re-checking "
                                        f"{len(GEMINI_KEYS)} Gemini key(s)…")
                    stop_event.clear()
                    config["_end_reason"] = "completed"
                    continue
                break                                       # normal / terminal finish
            except SystemExit:
                raise
            except BaseException as e:
                _last_err = f"{type(e).__name__}: {str(e)[:150]}".strip()
                try:
                    with open(f"error_log{SUFFIX}.txt", "a", encoding="utf-8") as ef:
                        ef.write(f"\n--- {datetime.now()} | crash ---\n{traceback.format_exc()}\n")
                except Exception:
                    pass

                if stop_event.is_set():
                    if config.get("_end_reason", "completed") == "completed":
                        config["_end_reason"] = "user_stop"
                    break
                if config.get("_end_reason") in _terminal:
                    break

                # ── Stall-recovery (hang guard ne browser band kiya) ──
                # Iska apna alag budget hai — ye "crash" nahi, jaan-boojh ke
                # kiya gaya restart hai. MAX_RESTARTS se alag rakha hai taake
                # ek din bhar ki susti (slow net) bot ko permanently na roke.
                if config.pop("_stall_restart", False):
                    _stall_restarts += 1
                    if _stall_restarts > STALL_MAX_RESTARTS:
                        config["_end_reason"] = "error"
                        _last_err = "page kept stalling — gave up after many browser restarts"
                        break
                    for _lk in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                        try:
                            os.remove(os.path.join(PW_PROFILE_DIR, _lk))
                        except Exception:
                            pass
                    send_ui("log", text=f"🔄 Browser was stuck — restarting it "
                                        f"(recovery {_stall_restarts}). Today's joins are safe, "
                                        f"joining continues by itself.")
                    time.sleep(8)
                    if stop_event.is_set():
                        config["_end_reason"] = "user_stop"
                        break
                    config["_end_reason"] = "completed"
                    _GEMINI_DEAD_REASON = None
                    continue

                restarts += 1
                if restarts > MAX_RESTARTS:
                    config["_end_reason"] = "error"
                    break

                wait = min(75, 15 * restarts)
                send_ui("log", text=f"⚠️ Bot crashed ({_last_err}) — auto-restarting in "
                                    f"{wait}s (attempt {restarts}/{MAX_RESTARTS}). "
                                    f"DON'T press START — it continues by itself. "
                                    f"Today's joins are safe.")
                try:
                    _a = config.get("_activity")
                    if _a and restarts == 1:
                        _a.alert(f"Bot crashed ({_last_err}) — auto-restarting. "
                                 f"No action needed unless it keeps happening.")
                except Exception:
                    pass
                for _lk in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                    try:
                        os.remove(os.path.join(PW_PROFILE_DIR, _lk))
                    except Exception:
                        pass
                time.sleep(wait)
                if stop_event.is_set():
                    config["_end_reason"] = "user_stop"
                    break
                config["_end_reason"] = "completed"
                _GEMINI_DEAD_REASON = None    # already declared global at top of fn
                continue
    except SystemExit:
        raise
    except BaseException as e:
        config["_end_reason"] = "error"
        _last_err = f"{type(e).__name__}: {str(e)[:150]}".strip()
        try:
            with open(f"error_log{SUFFIX}.txt", "a", encoding="utf-8") as ef:
                ef.write(f"\n--- {datetime.now()} | run_playwright outer crash ---\n"
                         f"{traceback.format_exc()}\n")
        except Exception:
            pass

    # ── end_session ONCE (not per retry) ──
    try:
        act.end_session(config.get("_end_reason", "completed"))
    except Exception:
        pass

    # ── ALWAYS a clear stop reason (UI + Discord) — never "random / silent" ──
    _reason = config.get("_end_reason", "completed")
    _RMAP = {
        "completed":        "✅ Daily target reached — normal stop.",
        "user_stop":        "⏹️ You pressed STOP.",
        "nothing_left":     "✅ Went through every area — no new groups left to join right now. Run again later.",
        "license_expired":  "⛔ License key expired/invalid — activate a new key.",
        "account_blocked":  "🚫 Facebook checkpoint/block on this account — give this ID a few days of rest.",
        "pending_limit":    "⏸️ Facebook applied a join-request limit (too many pending) — get some approved/cancelled.",
        "gemini_keys_failed":"⚠️ All Gemini API keys are down — need new keys, or try again later.",
        "file_tamper":      "🚨 A bot file was changed — verified files will reload on restart.",
        "service_paused":   "⏸️ The administrator has paused the bot.",
        "setup_failed":     "❌ Could not switch to the Page — check the Page Link / admin access.",
        "no_page_link":     "❌ Page Link is empty — enter your Facebook Page link.",
        "login_timeout":    "❌ Login timed out — log in to Facebook in the browser, then START.",
        "error":            f"❌ Bot crashed{(' — ' + _last_err) if _last_err else ''} "
                            f"(see error_log{SUFFIX}.txt). Restarted {restarts} time(s).",
    }
    _normal = ("completed", "user_stop", "nothing_left")
    _run_j = config.get("_run_joined")
    _tot   = config.get("_today_total")
    _lim   = config.get("daily_limit", 250)

    if _reason == "completed":
        if _run_j == 0 and _tot is not None and _tot >= _lim:
            _msg = (f"✅ Today's limit ({_lim}) is already reached — nothing more to do "
                    f"until tomorrow. This is NORMAL — no need to keep restarting.")
        elif _run_j is not None:
            _msg = (f"✅ Finished this run — joined {_run_j}, today's total "
                    f"{_tot}/{_lim}. Normal stop.")
        else:
            _msg = _RMAP["completed"]
    else:
        _msg = _RMAP.get(_reason, f"Stopped (reason: {_reason}).")
    if _reason not in _normal:
        _msg += f"\n   📞  If it doesn't resolve → Contact {BRAND}"

    send_ui("log", text=f"\n■ BOT STOPPED — {_msg}")

    # Discord: har stop par ek saaf line — "bina wajah band" kabhi na lage
    try:
        _a = config.get("_activity")
        if _a:
            if _reason in _normal:
                _a._last_chat = 0.0
                _a._push_text(f"⏹️ BOT STOPPED — {_msg}")
            else:
                _a.alert(f"■ BOT STOPPED — {_RMAP.get(_reason, _reason)}  ·  Contact {BRAND}")
    except Exception:
        pass

    send_ui("stopped")

# ── Tkinter UI ────────────────────────────────────────────────

# ── NexfourSolution brand palette (Electric Blue + dark) ────
BG        = "#0b1220"   # window background — deep navy
CARD_BG   = "#121a2a"   # cards
CARD_HI   = "#1a2438"   # elevated / hover
BORDER    = "#26314a"   # borders
INPUT_BG  = "#182236"   # entry fields
LOG_BG    = "#080d17"   # console
TXT       = "#eef2f9"   # primary text
TXT_MUTED = "#9aa6bd"   # secondary text
TXT_DIM   = "#5c6884"   # faint

# Brand accent — electric blue
BRAND_BLUE   = "#3b82f6"
BRAND_BLUE_D = "#2563eb"   # pressed / hover-dark
BRAND_BLUE_HI= "#60a5fa"   # highlight / light
BRAND_INK    = "#08132b"   # text on top of brand blue
# Back-compat aliases — poore code mein FB_BLUE use hota hai
FB_BLUE   = BRAND_BLUE
FB_BLUE_D = BRAND_BLUE_D

GREEN     = "#26d07c"
GREEN_BG  = "#0f2b22"
GREEN_D   = "#1fae68"
ORANGE    = "#f5a623"
ORANGE_BG = "#2a2110"
RED       = "#ff5c5c"

BRAND_NAME_A = "NEXFOUR"    # wordmark (white)
BRAND_NAME_B = "SOLUTION"   # wordmark (blue)

# ── Typography scale ────────────────────────────────────────
F_H1    = ("Segoe UI Semibold", 15)
F_H2    = ("Segoe UI Semibold", 11)
F_LABEL = ("Segoe UI", 8, "bold")
F_BODY  = ("Segoe UI", 9)
F_SMALL = ("Segoe UI", 8)
F_TINY  = ("Segoe UI", 7)
F_BIG   = ("Segoe UI", 30, "bold")
F_MONO  = ("Consolas", 9)

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"NexfourSolution  —  FB Group Joiner  v{APP_VERSION}   ·   "
                        f"Account {INSTANCE}   ·   build {_build_no()}")
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
        self._preflight_open = False
        self._ap_open       = False
        self._run_start     = None
        self.lic_info       = {"ok": False, "error": "No license", "employee": ""}

        self._style()
        self._build()
        self._refresh_gemini_status()
        self._refresh_mix_lbl()
        self._refresh_big_min_warning()
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

    def _min_pub(self):
        try:
            return max(0, int(self.min_members_public_var.get()))
        except Exception:
            return DEFAULT_MIN_MEMBERS

    def _min_priv(self):
        try:
            return max(0, int(self.min_members_private_var.get()))
        except Exception:
            return DEFAULT_MIN_MEMBERS

    def _refresh_big_min_warning(self):
        """Warn when a very high minimum is set - otherwise the employee
        thinks the bot is slow/broken, when really such big groups are
        rare and take much longer to find."""
        try:
            hi = max(self._min_pub(), self._min_priv())
        except Exception:
            return
        if hi >= BIG_MIN_MEMBERS:
            self.big_min_lbl.config(
                text=(f"⚠  Minimum is set to {hi:,} members. Groups that big "
                      f"are rare - the bot will take MUCH longer to find each "
                      f"join and the daily total will be lower. For speed, keep "
                      f"it around {DEFAULT_MIN_MEMBERS:,}."))
        else:
            self.big_min_lbl.config(text="")

    def _search_keywords(self):
        """UI box se employee ke apne search keywords (raw lines)."""
        try:
            txt = self.kw_box.get("1.0", "end")
        except Exception:
            txt = ""
        out, seen = [], set()
        for ln in txt.splitlines():
            k = ln.strip()
            if k and not k.startswith("#") and k.lower() not in seen:
                seen.add(k.lower())
                out.append(k)
        return out

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
        """Page link + area + business mode turant save — har profile ka apna
        alag, taake dobara khulne par bhare rahein aur ek doosre ko overwrite
        na karein."""
        try:
            s = load_settings()
            s[f"page_link{SUFFIX}"] = self.page_link_var.get().strip()
            s[f"city{SUFFIX}"] = self.city_var.get().strip()
            if hasattr(self, "business_var"):
                s[f"business_mode{SUFFIX}"] = self.business_var.get()
            save_settings(s)
        except Exception:
            pass

    def _on_business_change(self):
        """Car <-> Duct toggle — Area dropdown ko us list se refresh karo,
        selection reset (purana area nayi list mein na ho to)."""
        mode = self.business_var.get()
        lst = areas_for(mode)
        try:
            self.area_combo.config(values=[ALL_AREAS_LABEL] + lst)
        except Exception:
            pass
        if self.city_var.get() not in lst:
            self.city_var.set(ALL_AREAS_LABEL)
        for k, b in getattr(self, "_biz_btns", {}).items():
            b.config(fg=(TXT if k == mode else TXT_MUTED))
        self._persist_simple()

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
            _dl = int(info.get("days_left", 99) or 0)
            self.lic_var.set(
                f"Licensed to:  {info['employee']}      "
                f"Expires:  {info['exp']}   ({info.get('time_left','')} left)"
                + (f"      \u26a0  RENEW SOON \u2014 contact the admin for a new key"
                   if _dl <= LICENSE_WARN_DAYS else ""))
            self.lic_lbl.config(fg=ORANGE if _dl <= LICENSE_WARN_DAYS else GREEN)
            self._license_expiry_reminder(info)
            self.lic_row.pack_forget()
            if not self.running:
                self.btn.config(state="normal")
        else:
            self.lic_var.set(f"Not activated  —  {info.get('error') or 'no license key'}")
            self.lic_lbl.config(fg=ORANGE)
            self.lic_row.pack(fill="x", padx=16, pady=(0, 8))
            self.btn.config(state="disabled")
            _alert_wrong_pc(info)

    def _license_expiry_reminder(self, info) -> None:
        """Show a one-time popup when the license is about to expire, so the
        employee asks the admin for a new key BEFORE the bot stops dead.
        (Two employees lost a full working day this way.)"""
        if getattr(self, "_exp_warned", False):
            return
        try:
            days = int(info.get("days_left", 99) or 0)
        except Exception:
            return
        if days > LICENSE_WARN_DAYS:
            return
        self._exp_warned = True
        when = ("TODAY" if days <= 0 else
                "in 1 day" if days == 1 else f"in {days} days")
        try:
            messagebox.showwarning(
                "License expiring soon",
                f"Your license expires {when}." + "\n"
                f"({info.get('employee','')} \u2014 valid until {info.get('exp','')})" + "\n\n"
                "Please CONTACT THE ADMIN now and get a new key." + "\n"
                "When it runs out the bot stops and you cannot "
                "start it again until a new key is entered." + "\n\n"
                "You can keep working until then.")
        except Exception:
            pass
        send_ui("log", text=f"\u26a0\ufe0f  License expires {when} \u2014 contact the "
                            f"admin for a new key before it runs out.")

    def _try_activate(self, key, parent=None):
        """Validate + save a key. Returns (ok, message)."""
        key = (key or "").strip()
        if not key:
            return False, "Paste a license key first."
        info = lic.validate_key(key)
        if not info["ok"]:
            _alert_wrong_pc(info)
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
                        background=BRAND_BLUE, lightcolor=BRAND_BLUE, darkcolor=BRAND_BLUE,
                        thickness=14)
        self.root.option_add("*TCombobox*Listbox.background", INPUT_BG)
        self.root.option_add("*TCombobox*Listbox.foreground", TXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", FB_BLUE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "white")

    @staticmethod
    def _round_rect(c, x0, y0, x1, y1, r, **kw):
        """Rounded-rectangle on a Canvas (smooth polygon)."""
        r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return c.create_polygon(pts, smooth=True, **kw)

    def _brand_mark(self, parent, size=36, bg=None):
        """NexfourSolution logo mark — 2x2 rounded-tile grid ('four'),
        top-right tile bright (the 'next'). Drawn crisp on a Canvas."""
        bg = bg or CARD_BG
        c = tk.Canvas(parent, width=size, height=size, bg=bg,
                      highlightthickness=0, bd=0)
        g  = max(2, round(size * 0.13))        # gap between tiles
        t  = (size - g) / 2                    # tile side
        rd = max(2, round(t * 0.28))           # corner radius
        tiles = {
            (0, 0): "#1e3a8a",                 # top-left  — deep
            (1, 0): BRAND_BLUE_HI,             # top-right — bright ("next")
            (0, 1): BRAND_BLUE_D,              # bottom-left
            (1, 1): "#1e40af",                 # bottom-right
        }
        for (cx, cy), col in tiles.items():
            x0 = cx * (t + g)
            y0 = cy * (t + g)
            self._round_rect(c, x0, y0, x0 + t, y0 + t, rd, fill=col, outline="")
        return c

    def _hover(self, widget, normal, hot, attr="bg"):
        """Hover colour swap for a flat button (skips while disabled).
        `normal` / `hot` may be a colour string or a 0-arg callable."""
        def _val(v):
            return v() if callable(v) else v
        def _on(_e):
            try:
                if str(widget["state"]) != "disabled":
                    widget[attr] = _val(hot)
            except Exception:
                pass
        def _off(_e):
            try:
                widget[attr] = _val(normal)
            except Exception:
                pass
        widget.bind("<Enter>", _on)
        widget.bind("<Leave>", _off)

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

    def _grouphdr(self, parent, text, first=False):
        """Settings ke andar chhota section header + divider — visual grouping."""
        if not first:
            tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(16, 0))
        r = tk.Frame(parent, bg=CARD_BG)
        r.pack(fill="x", pady=(12 if not first else 0, 8))
        tk.Frame(r, bg=BRAND_BLUE, width=3, height=14).pack(side="left", padx=(0, 8))
        tk.Label(r, text=text, bg=CARD_BG, fg=BRAND_BLUE_HI,
                 font=("Segoe UI Semibold", 9)).pack(side="left")

    def _check(self, parent, text, var):
        """Consistent-styled checkbox."""
        tk.Checkbutton(parent, text=text, variable=var, bg=CARD_BG, fg=TXT_MUTED,
                       selectcolor=INPUT_BG, activebackground=CARD_BG,
                       activeforeground=TXT, font=F_SMALL, anchor="w",
                       highlightthickness=0, bd=0, padx=0,
                       wraplength=360, justify="left").pack(anchor="w", pady=(3, 0))

    def _note(self, parent, text):
        tk.Label(parent, text=text, bg=CARD_BG, fg=TXT_DIM, font=F_TINY,
                 wraplength=360, justify="left").pack(anchor="w", pady=(4, 0))

    def _entry(self, parent, var, **pack_opts):
        e = tk.Entry(parent, textvariable=var, font=("Segoe UI", 11),
                     bg=INPUT_BG, fg=TXT, insertbackground=TXT,
                     relief="flat", highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=FB_BLUE)
        e.pack(fill="x", ipady=4, **pack_opts)
        return e

    def _build(self):
        # ── Header  (NexfourSolution brand bar) ──────────────
        hdr = tk.Frame(self.root, bg=CARD_BG)
        hdr.pack(fill="x")
        row = tk.Frame(hdr, bg=CARD_BG)
        row.pack(fill="x", padx=18, pady=(14, 6))

        # logo mark
        self._brand_mark(row, size=34, bg=CARD_BG).pack(side="left", pady=1)

        titlebox = tk.Frame(row, bg=CARD_BG)
        titlebox.pack(side="left", padx=(11, 0))
        wm = tk.Frame(titlebox, bg=CARD_BG)
        wm.pack(anchor="w")
        tk.Label(wm, text=BRAND_NAME_A, bg=CARD_BG, fg=TXT,
                 font=("Segoe UI Black", 13)).pack(side="left")
        tk.Label(wm, text=BRAND_NAME_B, bg=CARD_BG, fg=BRAND_BLUE_HI,
                 font=("Segoe UI Black", 13)).pack(side="left", padx=(3, 0))
        tk.Label(titlebox,
                 text=f"FB Group Joiner   ·   v{APP_VERSION}   ·   build {_build_no()}",
                 bg=CARD_BG, fg=TXT_DIM, font=F_TINY).pack(anchor="w", pady=(1, 0))

        tk.Label(row, text=f"ACCOUNT {INSTANCE}", bg=INPUT_BG, fg=BRAND_BLUE_HI,
                 font=("Segoe UI Semibold", 8), padx=10, pady=4).pack(side="right")

        # brand accent strip
        tk.Frame(hdr, bg=BRAND_BLUE, height=2).pack(fill="x")

        self.status_var = tk.StringVar(value="●  Idle — press START to begin")
        self.status_lbl = tk.Label(hdr, textvariable=self.status_var, bg=CARD_BG,
                                   fg=TXT_MUTED, font=("Segoe UI Semibold", 9), anchor="w")
        self.status_lbl.pack(fill="x", padx=18, pady=(0, 6))

        def _recolor_status(*_a):
            t = self.status_var.get().lower()
            if any(k in t for k in ("running", "checking", "open")):
                c = GREEN
            elif any(k in t for k in ("error", "invalid", "block", "expired",
                                      "fail", "paused", "stopping")):
                c = RED
            else:
                c = TXT_MUTED
            try:
                self.status_lbl.config(fg=c)
            except Exception:
                pass
        self.status_var.trace_add("write", _recolor_status)

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

        # ══ LEFT COLUMN — primary START (pinned) + secondary buttons ═══
        self.btn = tk.Button(left, text="▶   START",
                             bg=BRAND_BLUE, fg="white",
                             font=("Segoe UI Semibold", 14),
                             relief="flat", cursor="hand2", bd=0,
                             activebackground=BRAND_BLUE_D, activeforeground="white",
                             command=self._toggle, pady=13)
        self.btn.pack(side="bottom", fill="x", pady=(10, 0))
        self._hover(self.btn,
                    lambda: RED if self.running else BRAND_BLUE,
                    lambda: "#ff7a7a" if self.running else BRAND_BLUE_HI)

        # Secondary: pre-flight check (outlined)
        preflight_row = tk.Frame(left, bg=BG)
        preflight_row.pack(side="bottom", fill="x", pady=(6, 0))
        self.preflight_btn = tk.Button(
            preflight_row, text="🔎   Pre-flight check",
            bg=CARD_BG, fg=FB_BLUE, font=("Segoe UI Semibold", 9),
            relief="flat", cursor="hand2", bd=0,
            highlightthickness=1, highlightbackground=FB_BLUE_D, highlightcolor=FB_BLUE_D,
            activebackground=CARD_HI, activeforeground=FB_BLUE,
            command=self._do_preflight, pady=7)
        self.preflight_btn.pack(fill="x")
        self._hover(self.preflight_btn, CARD_BG, CARD_HI)

        # Auto-post on Page (not built yet)
        autopost_row = tk.Frame(left, bg=BG)
        autopost_row.pack(side="bottom", fill="x", pady=(6, 0))
        self.autopost_btn = tk.Button(
            autopost_row, text="📢   Auto-post on Page",
            bg=CARD_BG, fg=TXT_MUTED, font=("Segoe UI Semibold", 9),
            relief="flat", cursor="hand2", bd=0,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER,
            activebackground=CARD_HI, activeforeground=TXT,
            command=self._auto_post_soon, pady=7)
        self.autopost_btn.pack(fill="x")
        self._hover(self.autopost_btn, CARD_BG, CARD_HI)

        # Tertiary: login / logout (ghost)
        acct_row = tk.Frame(left, bg=BG)
        acct_row.pack(side="bottom", fill="x", pady=(6, 0))
        self.logout_btn = tk.Button(acct_row, text="🚪  Log out",
                                    bg=BG, fg=TXT_DIM, font=F_SMALL,
                                    relief="flat", cursor="hand2", bd=0,
                                    activebackground=CARD_BG, activeforeground=TXT_MUTED,
                                    command=self._do_logout, pady=5)
        self.logout_btn.pack(side="left", padx=(0, 6))
        self.login_btn = tk.Button(acct_row, text="🔓  Log in to Facebook",
                                   bg=BG, fg=TXT_MUTED, font=F_SMALL,
                                   relief="flat", cursor="hand2", bd=0,
                                   activebackground=CARD_BG, activeforeground=TXT,
                                   command=self._open_login, pady=5)
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

        _s0 = load_settings()

        self._grouphdr(card, "🎯  TARGET", first=True)

        # Business toggle: Car / Duct (two equal halves, mutually exclusive)
        self.business_var = tk.StringVar(
            value=(_s0.get(f"business_mode{SUFFIX}") or _s0.get("business_mode") or "car").lower())
        self._label(card, "Business  ·  picks the area list")
        brow = tk.Frame(card, bg=CARD_BG); brow.pack(fill="x", pady=(0, 2))
        self._biz_btns = {}
        for _key, _txt in (("car", "🚗  Car"), ("duct", "💨  Duct")):
            b = tk.Radiobutton(
                brow, text=_txt, value=_key, variable=self.business_var,
                indicatoron=False, font=("Segoe UI Semibold", 9),
                bg=INPUT_BG, fg=TXT_MUTED, selectcolor=FB_BLUE_D,
                activebackground=BORDER, activeforeground="white",
                relief="flat", bd=0, cursor="hand2", pady=7,
                command=self._on_business_change)
            b.pack(side="left", fill="x", expand=True, padx=(0, 6) if _key == "car" else 0)
            self._biz_btns[_key] = b
        for _k, _b in self._biz_btns.items():
            _b.config(fg=("white" if _k == self.business_var.get() else TXT_MUTED))

        self._label(card, "Area")
        self.city_var = tk.StringVar(
            value=_s0.get(f"city{SUFFIX}") or _s0.get("city") or ALL_AREAS_LABEL)
        self.area_combo = ttk.Combobox(card, textvariable=self.city_var,
                                   values=[ALL_AREAS_LABEL] + areas_for(self.business_var.get()),
                                   font=F_BODY, state="normal", style="Dark.TCombobox")
        self.area_combo.pack(fill="x", pady=(0, 2), ipady=4)

        self._label(card, "Facebook Page link")
        self.page_link_var = tk.StringVar(
            value=_s0.get(f"page_link{SUFFIX}") or _s0.get("page_link") or DEFAULT_PAGE_LINK)
        self._entry(card, self.page_link_var, pady=(0, 2))
        self.page_link_var.trace_add("write", lambda *a: self._persist_simple())
        self.city_var.trace_add("write", lambda *a: self._persist_simple())

        # ── LIMITS & SPEED ──────────────────────────────────
        self._grouphdr(card, "⏱  LIMITS & SPEED")
        row1 = tk.Frame(card, bg=CARD_BG); row1.pack(fill="x")
        col1 = tk.Frame(row1, bg=CARD_BG); col1.pack(side="left", expand=True, fill="x", padx=(0, 6))
        col2 = tk.Frame(row1, bg=CARD_BG); col2.pack(side="left", expand=True, fill="x")
        self._label(col1, "Min Public members")
        self.min_members_public_var = tk.IntVar(
            value=int(_s0.get("min_members_public", DEFAULT_MIN_MEMBERS)))
        self._entry(col1, self.min_members_public_var, pady=(0, 2))
        self._label(col2, "Min Private members")
        self.min_members_private_var = tk.IntVar(
            value=int(_s0.get("min_members_private", DEFAULT_MIN_MEMBERS)))
        self._entry(col2, self.min_members_private_var, pady=(0, 2))
        self.big_min_lbl = tk.Label(card, text="", bg=CARD_BG, fg=ORANGE,
                                    font=F_SMALL, justify="left", wraplength=340)
        self.big_min_lbl.pack(fill="x", anchor="w")
        for _v in (self.min_members_public_var, self.min_members_private_var):
            _v.trace_add("write", lambda *a: self._refresh_big_min_warning())

        self._label(card, "Daily limit")
        self.daily_limit_var = tk.IntVar(value=250)
        self._entry(card, self.daily_limit_var, pady=(0, 2))

        row2 = tk.Frame(card, bg=CARD_BG); row2.pack(fill="x")
        col3 = tk.Frame(row2, bg=CARD_BG); col3.pack(side="left", expand=True, fill="x", padx=(0, 6))
        col4 = tk.Frame(row2, bg=CARD_BG); col4.pack(side="left", expand=True, fill="x")
        self._label(col3, "Delay min (sec)")
        self.delay_min_var = tk.IntVar(value=5)
        self._entry(col3, self.delay_min_var, pady=(0, 2))
        self._label(col4, "Delay max (sec)")
        self.delay_max_var = tk.IntVar(value=12)
        self._entry(col4, self.delay_max_var, pady=(0, 2))

        self._label(card, "Public %  ·  rest = private")
        pcell = tk.Frame(card, bg=CARD_BG); pcell.pack(fill="x")
        self.public_pct_var = tk.IntVar(value=int(_s0.get("public_pct", 30)))
        e = tk.Entry(pcell, textvariable=self.public_pct_var, font=F_BODY,
                     bg=INPUT_BG, fg=TXT, insertbackground=TXT, relief="flat", width=6,
                     highlightthickness=1, highlightbackground=BORDER, highlightcolor=FB_BLUE)
        e.pack(side="left", ipady=4)
        self.mix_lbl = tk.Label(pcell, text="", bg=CARD_BG, fg=TXT_MUTED, font=F_SMALL)
        self.mix_lbl.pack(side="left", padx=(10, 0))
        e.bind("<KeyRelease>", lambda ev: self._refresh_mix_lbl())

        self._note(card, "Runs 24/7 non-stop · stops at the daily limit.")

        # ── SAFETY FILTERS ──────────────────────────────────
        self._grouphdr(card, "🛡  SAFETY FILTERS")
        self.skip_nopost_var = tk.BooleanVar(value=bool(_s0.get("skip_no_post", True)))
        self._check(card, "Skip admin-only groups (members can't post)", self.skip_nopost_var)
        self.same_state_var = tk.BooleanVar(value=bool(_s0.get("same_state_only", True)))
        self._check(card, "Stay inside the target state only", self.same_state_var)
        self.include_counties_var = tk.BooleanVar(value=bool(_s0.get("include_counties", True)))
        self._check(card, "Also join county-level groups", self.include_counties_var)
        self._note(card, "Always on:  USA only · no buy/sell · engagement check")

        self._label(card, "Search keywords  ·  one per line  ·  auto-loads keywords.txt")
        skrow = tk.Frame(card, bg=CARD_BG)
        skrow.pack(fill="x", pady=(0, 2))
        self.kw_box = tk.Text(skrow, height=3, font=F_MONO, bg=INPUT_BG,
                              fg=TXT, insertbackground=TXT, relief="flat", wrap="word",
                              highlightthickness=1, highlightbackground=BORDER,
                              highlightcolor=FB_BLUE)
        self.kw_box.pack(side="left", fill="both", expand=True)
        kwscroll = tk.Scrollbar(skrow, command=self.kw_box.yview)
        kwscroll.pack(side="left", fill="y")
        self.kw_box.config(yscrollcommand=kwscroll.set)
        try:
            self.kw_box.insert("1.0", "\n".join(
                _s0.get("search_keywords") or default_search_keyword_lines()))
        except Exception:
            pass
        self._note(card, "Leave empty = built-in wordings (community, moms, "
                         "residents…). Your own keywords run FIRST, built-in ones "
                         "after. The area is added automatically: "
                         "\"car detailing\" → \"Phoenix AZ car detailing\".")

        self._label(card, "Don't-join keywords  ·  one per line")
        bkrow = tk.Frame(card, bg=CARD_BG)
        bkrow.pack(fill="x", pady=(0, 2))
        self.block_box = tk.Text(bkrow, height=3, font=F_MONO, bg=INPUT_BG,
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
        tk.Button(card, text="Reset to defaults", bg=INPUT_BG, fg=TXT_MUTED,
                  relief="flat", font=F_TINY, cursor="hand2", padx=8,
                  command=lambda: (self.block_box.delete("1.0", "end"),
                                   self.block_box.insert("1.0",
                                       "\n".join(DEFAULT_DONT_JOIN)))
                  ).pack(anchor="w", pady=(4, 0))

        # ── AI ANSWERS ──────────────────────────────────────
        self._grouphdr(card, "🤖  AI ANSWERS  (optional)")
        self._label(card, "Gemini API keys  ·  one per line  ·  auto-loads gemini_keys.txt")
        grow = tk.Frame(card, bg=CARD_BG)
        grow.pack(fill="x", pady=(0, 2))
        self.gemini_box = tk.Text(grow, height=3, font=F_MONO, bg=INPUT_BG,
                                  fg=TXT, insertbackground=TXT, relief="flat", wrap="none",
                                  highlightthickness=1, highlightbackground=BORDER,
                                  highlightcolor=FB_BLUE)
        self.gemini_box.pack(side="left", fill="x", expand=True)
        try:
            _prev = resolve_gemini_keys("")
            if _prev:
                self.gemini_box.insert("1.0", "\n".join(_prev))
        except Exception:
            pass
        tk.Button(grow, text="Test", bg=INPUT_BG, fg=TXT, relief="flat",
                  font=F_SMALL, cursor="hand2", padx=10,
                  command=self._test_gemini).pack(side="left", padx=(6, 0))
        self.gemini_status = tk.Label(card, text="", bg=CARD_BG, fg=TXT_MUTED,
                                      font=F_SMALL, anchor="w")
        self.gemini_status.pack(anchor="w", pady=(4, 0))
        self.gemini_box.bind("<KeyRelease>", lambda e: self._refresh_gemini_status())

        # (START button is pinned at the bottom of the left column — created above)

        # ══ RIGHT COLUMN — TODAY summary card + Activity + Log ═
        today_wrap = tk.Frame(right, bg=CARD_BG,
                              highlightbackground=BORDER, highlightthickness=1)
        today_wrap.pack(fill="x")
        tk.Frame(today_wrap, bg=BRAND_BLUE, height=2).pack(fill="x")   # top hairline
        today = tk.Frame(today_wrap, bg=CARD_BG, padx=18, pady=16)
        today.pack(fill="x")

        trow = tk.Frame(today, bg=CARD_BG); trow.pack(fill="x")
        _tl = tk.Frame(trow, bg=CARD_BG); _tl.pack(side="left")
        tk.Frame(_tl, bg=BRAND_BLUE, width=3, height=12).pack(side="left", padx=(0, 7))
        tk.Label(_tl, text="TODAY", bg=CARD_BG, fg=TXT_MUTED,
                 font=F_LABEL).pack(side="left")
        self.runtime_var = tk.StringVar(value="")
        tk.Label(trow, textvariable=self.runtime_var, bg=CARD_BG, fg=TXT_DIM,
                 font=F_SMALL).pack(side="right")

        numrow = tk.Frame(today, bg=CARD_BG); numrow.pack(fill="x", pady=(8, 6))
        self.joined_lbl_var = tk.StringVar(value="0")
        tk.Label(numrow, textvariable=self.joined_lbl_var, bg=CARD_BG, fg=GREEN,
                 font=F_BIG).pack(side="left")
        self.progress_lbl_var = tk.StringVar(value="/ 250   ·   0%  joined")
        tk.Label(numrow, textvariable=self.progress_lbl_var, bg=CARD_BG, fg=TXT_MUTED,
                 font=F_BODY).pack(side="left", anchor="s", pady=(0, 8), padx=(8, 0))

        self.progress = ttk.Progressbar(today, maximum=250, mode="determinate",
                                        style="FB.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(2, 10))

        srow = tk.Frame(today, bg=CARD_BG); srow.pack(fill="x")
        self.skipped_lbl_var = tk.StringVar(value="0")
        tk.Label(srow, text="⏭  skipped this run: ", bg=CARD_BG, fg=TXT_DIM,
                 font=F_SMALL).pack(side="left")
        tk.Label(srow, textvariable=self.skipped_lbl_var, bg=CARD_BG, fg=ORANGE,
                 font=("Segoe UI Semibold", 9)).pack(side="left")

        # Current activity
        now_card = tk.Frame(right, bg=CARD_BG, padx=18, pady=12,
                            highlightbackground=BORDER, highlightthickness=1)
        now_card.pack(fill="x", pady=(10, 0))
        _nh = tk.Frame(now_card, bg=CARD_BG); _nh.pack(anchor="w", pady=(0, 6))
        tk.Frame(_nh, bg=BRAND_BLUE, width=3, height=12).pack(side="left", padx=(0, 7))
        tk.Label(_nh, text="CURRENT ACTIVITY", bg=CARD_BG, fg=TXT_MUTED,
                 font=F_LABEL).pack(side="left")
        self.now_area_var = tk.StringVar(value="Area: —")
        self.now_target_var = tk.StringVar(value="Search: —")
        tk.Label(now_card, textvariable=self.now_area_var, bg=CARD_BG,
                 fg=TXT, font=("Segoe UI Semibold", 10), anchor="w",
                 wraplength=400, justify="left").pack(fill="x")
        tk.Label(now_card, textvariable=self.now_target_var, bg=CARD_BG,
                 fg=TXT_MUTED, font=F_BODY, anchor="w",
                 wraplength=400, justify="left").pack(fill="x", pady=(2, 0))

        log_frame = tk.Frame(right, bg=BG)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        log_hdr = tk.Frame(log_frame, bg=BG)
        log_hdr.pack(fill="x")
        _lh = tk.Frame(log_hdr, bg=BG); _lh.pack(side="left")
        tk.Frame(_lh, bg=BRAND_BLUE, width=3, height=12).pack(side="left", padx=(0, 7))
        tk.Label(_lh, text="ACTIVITY LOG", bg=BG, fg=TXT_MUTED,
                 font=F_LABEL).pack(side="left")
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
        self.log_box.tag_config("ok",   foreground=GREEN)
        self.log_box.tag_config("err",  foreground=RED)
        self.log_box.tag_config("warn", foreground=ORANGE)
        self.log_box.tag_config("head", foreground=BRAND_BLUE_HI, font=("Consolas", 9, "bold"))
        self.log_box.tag_config("dim",  foreground=TXT_DIM)

        # ── Footer ───────────────────────────────────────────
        tk.Frame(self.root, bg=BRAND_BLUE, height=2).pack(fill="x")
        foot = tk.Frame(self.root, bg=CARD_BG)
        foot.pack(fill="x")
        fl = tk.Frame(foot, bg=CARD_BG)
        fl.pack(side="left", padx=14, pady=5)
        tk.Label(fl, text="●", bg=CARD_BG, fg=BRAND_BLUE,
                 font=("Segoe UI", 7)).pack(side="left", padx=(0, 5))
        tk.Label(fl, text=f"© {datetime.now():%Y} NexfourSolution", bg=CARD_BG,
                 fg=TXT_MUTED, font=F_TINY).pack(side="left")
        tk.Label(foot, text=f"FB Group Joiner  v{APP_VERSION}   ·   build {_build_no()}",
                 bg=CARD_BG, fg=TXT_DIM, font=F_TINY).pack(side="right", padx=14, pady=5)

    def _label(self, parent, text):
        tk.Label(parent, text=text, bg=CARD_BG,
                 font=F_LABEL, fg=TXT_MUTED).pack(anchor="w", pady=(10, 3))

    def _tvar(self, name):
        v = tk.StringVar()
        setattr(self, f"{name}_var", v)
        return v

    def _is_admin(self) -> bool:
        """Auto-post sirf ADMIN license se chalta hai. License RSA-signed hai,
        isliye employee na naam badal sakta hai na admin flag laga sakta hai.
        (Pehle yeh check autopost_dev.txt file par tha — koi bhi employee wo
        file khud bana kar feature unlock kar leta tha.)"""
        info = self.lic_info if self.lic_info.get("ok") else None
        if info is None:
            try:
                info = lic.validate_key(lic.load_active_key(), check_url=False)
            except Exception:
                return False
        return bool(info.get("ok") and info.get("admin"))

    def _auto_post_soon(self):
        if not self._is_admin():
            messagebox.showinfo(
                "Auto-post on Page",
                "🔒  Only for developer\n\n"
                "Auto-posting is an admin-only feature — it runs only "
                "with an admin license.\n\nIf you need it, please "
                "contact the admin.")
            return
        self._open_autopost()

    def _open_autopost(self):
        # Doosri safety layer — dialog kabhi bhi non-admin ke liye na khule.
        if not self._is_admin():
            return
        if self.running or getattr(self, "_login_open", False) or \
                getattr(self, "_logout_open", False) or \
                getattr(self, "_preflight_open", False) or \
                getattr(self, "_ap_open", False):
            return
        dlg = tk.Toplevel(self.root); dlg.title("Auto-post to joined groups (DEV)")
        dlg.configure(bg=BG); dlg.transient(self.root); dlg.grab_set()
        dlg.geometry("560x420")
        tk.Label(dlg, text="Auto-post to ALL joined groups", bg=BG, fg=TXT,
                 font=F_H2).pack(anchor="w", padx=20, pady=(18, 2))
        _joined = len(_joined_group_urls()); _pd = len(_load_posted())
        tk.Label(dlg, text=f"{_joined} joined · {_pd} already posted · "
                           f"{max(0, _joined - _pd)} to go",
                 bg=BG, fg=TXT_MUTED, font=F_SMALL).pack(anchor="w", padx=20)

        tk.Label(dlg, text="Post text", bg=BG, fg=TXT_MUTED,
                 font=F_LABEL).pack(anchor="w", padx=20, pady=(14, 2))
        txt = scrolledtext.ScrolledText(dlg, height=7, font=F_BODY, bg=INPUT_BG, fg=TXT,
                                        insertbackground=TXT, relief="flat",
                                        highlightthickness=1, highlightbackground=BORDER)
        txt.pack(fill="both", expand=True, padx=20)
        try:
            _pm = os.path.join(APP_DIR, "post_message.txt")
            if os.path.exists(_pm):
                txt.insert("1.0", open(_pm, encoding="utf-8").read())
        except Exception:
            pass

        self._ap_image = ""
        imgrow = tk.Frame(dlg, bg=BG); imgrow.pack(fill="x", padx=20, pady=(10, 0))
        img_lbl = tk.Label(imgrow, text="No image chosen", bg=BG, fg=TXT_DIM, font=F_SMALL)
        def pick_img():
            p = filedialog.askopenfilename(parent=dlg, title="Choose image",
                    filetypes=[("Images", "*.jpg *.jpeg *.png *.webp"), ("All", "*.*")])
            if p:
                self._ap_image = p
                img_lbl.config(text=os.path.basename(p), fg=TXT)
        tk.Button(imgrow, text="Choose image", bg=INPUT_BG, fg=TXT, relief="flat",
                  font=F_SMALL, cursor="hand2", padx=10, command=pick_img).pack(side="left")
        img_lbl.pack(side="left", padx=(10, 0))

        brow = tk.Frame(dlg, bg=BG); brow.pack(fill="x", padx=20, pady=16)
        def start():
            m = txt.get("1.0", "end").strip()
            if not m:
                messagebox.showwarning("Post text", "Enter the post text first.", parent=dlg)
                return
            try:
                open(os.path.join(APP_DIR, "post_message.txt"), "w",
                     encoding="utf-8").write(m)
            except Exception:
                pass
            self._ap_open = True
            self._ap_msg = m
            dlg.destroy()
            self.btn.config(state="disabled")
            self.preflight_btn.config(state="disabled")
            self.login_btn.config(state="disabled")
            self.logout_btn.config(state="disabled")
            self.autopost_btn.config(text="📢  Posting…", state="disabled")
            self.status_var.set("●  Auto-posting to joined groups…")
            cfg = {
                "post_message": m,
                "post_image":   self._ap_image,
                "page_link":    self.page_link_var.get().strip(),
                "page_name":    DEFAULT_PAGE_NAME,
                "employee":     self.lic_info.get("employee", "") or "unknown",
                "license_key":  lic.load_active_key(),
            }
            stop_event.clear()
            threading.Thread(target=run_autopost, args=(cfg,), daemon=True).start()
        tk.Button(brow, text="▶  Start posting", bg=GREEN, fg="#08130c", relief="flat",
                  font=("Segoe UI Semibold", 10), cursor="hand2", padx=16, pady=6,
                  command=start).pack(side="left")
        tk.Button(brow, text="Cancel", bg=INPUT_BG, fg=TXT_MUTED, relief="flat",
                  font=F_SMALL, cursor="hand2", padx=14, pady=6,
                  command=dlg.destroy).pack(side="right")

    def _do_preflight(self):
        if self.running or getattr(self, "_login_open", False) or \
                getattr(self, "_logout_open", False) or \
                getattr(self, "_preflight_open", False) or \
                getattr(self, "_ap_open", False):
            return
        self._preflight_open = True
        self.preflight_btn.config(text="🔎   Checking…", state="disabled")
        self.btn.config(state="disabled")
        self.login_btn.config(state="disabled")
        self.logout_btn.config(state="disabled")
        self.status_var.set("●  Running pre-flight check…")
        cfg = {
            "license_key": lic.load_active_key(),
            "employee":    self.lic_info.get("employee", "") or "unknown",
            "page_link":   self.page_link_var.get().strip(),
            "gemini_keys": resolve_gemini_keys(self._gemini_box_text()),
        }
        threading.Thread(target=run_preflight, args=(cfg,), daemon=True).start()

    def _open_login(self):
        if self.running or getattr(self, "_logout_open", False) or \
                getattr(self, "_preflight_open", False) or \
                getattr(self, "_ap_open", False):
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
        self.preflight_btn.config(state="disabled")
        self.status_var.set("●  Login browser open…")
        threading.Thread(target=run_login_browser, daemon=True).start()

    def _do_logout(self):
        if self.running or getattr(self, "_login_open", False) or \
                getattr(self, "_logout_open", False) or \
                getattr(self, "_preflight_open", False) or \
                getattr(self, "_ap_open", False):
            return
        if not messagebox.askyesno(
                "Log out of Facebook",
                "This clears the Facebook login session for this PC/profile.\n\n"
                "Only do this if the account got suspended/checkpointed and you "
                "need to log in again (or with a different account).\n\n"
                "Continue?"):
            return
        self._logout_open = True
        self.logout_btn.config(text="🚪  Logging out…", state="disabled")
        self.login_btn.config(state="disabled")
        self.preflight_btn.config(state="disabled")
        self.btn.config(state="disabled")
        self.status_var.set("●  Logging out of Facebook…")
        threading.Thread(target=run_logout_browser, daemon=True).start()

    def _toggle(self):
        if self.running:
            user_stop_event.set()
            stop_event.set()
            self.btn.config(text="▶   START", bg=BRAND_BLUE)
            self.status_var.set("●  Stopping...")
            self.running = False
        else:
            if getattr(self, "_login_open", False):
                messagebox.showinfo("Login browser open",
                                    "Close the login browser first, then START.")
                return
            if getattr(self, "_logout_open", False):
                messagebox.showinfo("Logging out",
                                    "Wait for the logout to finish, then START.")
                return
            if getattr(self, "_preflight_open", False):
                messagebox.showinfo("Pre-flight check",
                                    "Let the pre-flight check finish, then START.")
                return
            if getattr(self, "_ap_open", False):
                messagebox.showinfo("Auto-post",
                                    "Let the auto-post run finish, then START.")
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
            user_stop_event.clear()
            _GEMINI_DOWN.clear()
            self.joined_today  = 0
            self.skipped_today = 0
            self._run_start = time.time()
            self._update_stats()
            self.progress["maximum"] = self.daily_limit_var.get()
            self.progress["value"]   = 0
            self.progress_lbl_var.set(self._prog_text())
            self.now_area_var.set("Area: starting…")
            self.now_target_var.set("Search: —")
            self.running = True
            self.btn.config(text="⏹   STOP", bg=RED)
            self.login_btn.config(state="disabled")
            self.logout_btn.config(state="disabled")
            self.preflight_btn.config(state="disabled")
            self.status_var.set("●  Running...")
            config = {
                "city":        city,
                "page_name":   DEFAULT_PAGE_NAME,
                "page_link":   self.page_link_var.get().strip(),
                "daily_limit": self.daily_limit_var.get(),
                "delay_min":   self.delay_min_var.get(),
                "delay_max":   self.delay_max_var.get(),
                "employee":    self.lic_info.get("employee", "") or "unknown",
                "license_key": lic.load_active_key(),
                "license_exp": self.lic_info.get("exp", ""),
                "key_id":      self.lic_info.get("kid", ""),
                "gemini_keys": resolve_gemini_keys(self._gemini_box_text()),
                "public_pct":  self._public_pct(),
                "min_members": self._min_pub(),   # legacy key, back-compat
                "min_members_public":  self._min_pub(),
                "min_members_private": self._min_priv(),
                "skip_no_post": bool(self.skip_nopost_var.get()),
                "same_state_only": bool(self.same_state_var.get()),
                "include_counties": bool(self.include_counties_var.get()),
                "custom_blocked": self._block_keywords(),
                "search_keywords": self._search_keywords(),
                "_search_templates": resolve_search_keywords(
                    "\n".join(self._search_keywords())),
                "business_mode": self.business_var.get(),
            }
            self._persist_gemini()          # remember keys for next time
            s = load_settings()
            s[f"page_link{SUFFIX}"] = self.page_link_var.get().strip()
            s[f"city{SUFFIX}"] = self.city_var.get().strip()
            s[f"business_mode{SUFFIX}"] = self.business_var.get()
            s["public_pct"] = self._public_pct()
            s["min_members_public"] = self._min_pub()
            s["min_members_private"] = self._min_priv()
            s["skip_no_post"] = bool(self.skip_nopost_var.get())
            s["same_state_only"] = bool(self.same_state_var.get())
            s["include_counties"] = bool(self.include_counties_var.get())
            s["block_keywords"] = self._block_keywords()
            s["search_keywords"] = self._search_keywords()
            save_settings(s)
            self._refresh_gemini_status()
            threading.Thread(target=run_playwright, args=(config,), daemon=True).start()

    def _log(self, text):
        s = text.lstrip()
        tag = ""
        if s[:2] in ("✅", "🎉", "🟢") or s.startswith(("✅", "🎉", "🟢")):
            tag = "ok"
        elif s.startswith(("❌", "⛔", "🚫", "🚨")):
            tag = "err"
        elif s.startswith(("⚠️", "⏸️", "🍁", "☕", "🕗", "🔁", "↻")):
            tag = "warn"
        elif s.startswith("■"):
            tag = "head"
        elif s.startswith(("↻", "🕘", "📡", "🗺", "📍", "🔍")) or s.startswith("   ⏳"):
            tag = "dim"
        self.log_box.config(state="normal")
        self.log_box.insert("end", text + "\n", tag)
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    def _prog_text(self):
        try:
            lim = max(1, int(self.daily_limit_var.get()))
        except Exception:
            lim = 250
        pct = min(100, round(self.joined_today / lim * 100))
        return f"/ {lim}   ·   {pct}%  joined"

    def _update_stats(self):
        self.joined_lbl_var.set(str(self.joined_today))
        self.skipped_lbl_var.set(str(self.skipped_today))
        try:
            self.progress_lbl_var.set(self._prog_text())
        except Exception:
            pass

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
                    self.progress_lbl_var.set(self._prog_text())
                elif t == "skipped":
                    self.skipped_today += 1
                    self._update_stats()
                elif t in ("total", "total_skipped"):
                    pass  # all-time counters removed from the UI — still saved to files
                elif t == "login_done":
                    self._login_open = False
                    self.login_btn.config(
                        text="🔓  Log in to Facebook", state="normal")
                    self.logout_btn.config(state="normal")
                    self.preflight_btn.config(state="normal")
                    self.status_var.set("●  Login done — press START")
                    self._refresh_license_ui()
                elif t == "logout_done":
                    self._logout_open = False
                    self.logout_btn.config(text="🚪  Log out", state="normal")
                    self.login_btn.config(state="normal")
                    self.preflight_btn.config(state="normal")
                    self.status_var.set("●  Logged out — press 'Open browser & log in' to sign in again")
                    self._refresh_license_ui()
                elif t == "preflight_done":
                    self._preflight_open = False
                    self.preflight_btn.config(
                        text="🔎   Pre-flight check",
                        state="normal")
                    self.login_btn.config(state="normal")
                    self.logout_btn.config(state="normal")
                    self.status_var.set("●  Pre-flight done — see the log")
                    self._refresh_license_ui()
                elif t == "autopost_done":
                    self._ap_open = False
                    self.autopost_btn.config(text="📢   Auto-post on Page", state="normal")
                    self.btn.config(state="normal")
                    self.preflight_btn.config(state="normal")
                    self.login_btn.config(state="normal")
                    self.logout_btn.config(state="normal")
                    self.status_var.set("●  Auto-post finished — see the log")
                    self._refresh_license_ui()
                elif t == "stopped":
                    self.running = False
                    self._login_open = False
                    self._run_start = None
                    self.btn.config(text="▶   START", bg=BRAND_BLUE)
                    self.login_btn.config(
                        text="🔓  Log in to Facebook", state="normal")
                    self.logout_btn.config(state="normal")
                    self.preflight_btn.config(state="normal")
                    self.status_var.set("●  Idle — press START to begin")
                    self.now_target_var.set("Search: —")
                    self._log(f"📊 Today: {self.joined_today} joined | {self.skipped_today} skipped this run")
                    self._refresh_license_ui()  # re-lock START if the key expired
        except queue.Empty:
            pass
        # running time in the TODAY card
        try:
            rs = getattr(self, "_run_start", None)
            if rs and self.running:
                sec = int(time.time() - rs)
                h, m = sec // 3600, (sec % 3600) // 60
                self.runtime_var.set(f"● running  {h}h {m:02d}m" if h else f"● running  {m}m")
            else:
                self.runtime_var.set("")
        except Exception:
            pass
        self.root.after(400, self._poll)


def _build_no() -> str:
    try:
        return open(os.path.join(APP_DIR, ".update_ver"), encoding="utf-8").read().strip()
    except Exception:
        return "?"


_MID_ALERT_FILE = os.path.join(APP_DIR, f".mid_alert{SUFFIX}.json")

def _alert_wrong_pc(info: dict) -> None:
    """SECURITY: key kisi doosre PC (machine-id) ke liye locked hai lekin
    yahan use ho rahi hai — matlab ye folder/key kisi aur ko de diya gaya
    hai ya kisi doosre PC pe chalaya ja raha hai. Owner ko Discord pe
    FORAN pata chal jata hai — throttled (ek din mein ek hi baar per
    key, taake retry-loop spam na kare)."""
    if info.get("error") != "This key was issued for a different PC":
        return
    kid = info.get("kid") or "?"
    try:
        data = json.load(open(_MID_ALERT_FILE, encoding="utf-8"))
    except Exception:
        data = {}
    last = data.get(kid, "")
    now = datetime.now()
    try:
        if last and (now - datetime.fromisoformat(last)).total_seconds() < 86400:
            return   # is key ke liye pichle 24h mein already alert ja chuka
    except Exception:
        pass
    data[kid] = now.isoformat(timespec="seconds")
    try:
        with open(_MID_ALERT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass
    try:
        import activity
        activity.send_alert(
            info.get("employee") or "unknown",
            f"🔒 SECURITY: the license for '{info.get('employee') or '?'}' "
            f"(key {kid}) was opened on an UNAUTHORIZED PC — this machine's "
            f"fingerprint doesn't match the one it's locked to, bot refused "
            f"to start. If this isn't {info.get('employee') or 'the assigned employee'}, "
            f"the folder/key has likely been shared without permission.",
            sync=True)
    except Exception:
        pass


def _self_update():
    """Startup par check karo — naye files mile to lene ke baad bot ko
    naye code ke saath restart karo. Frozen .exe par skip (chalti exe
    replace nahi hoti)."""
    if getattr(sys, "frozen", False) or "--no-update" in sys.argv:
        print(f"[build] FB Group Joiner v{APP_VERSION}  -  Tool by {BRAND}  (build {_build_no()}, auto-update off)")
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
    print(f"[build] FB Group Joiner v{APP_VERSION}  -  Tool by {BRAND}  (build {_build_no()})")


if __name__ == "__main__":
    _self_update()
    root = tk.Tk()
    app  = App(root)
    # `py fb_joiner.py 3 --autostart` = UI khulte hi khud START ho jaye
    if "--autostart" in sys.argv:
        root.after(1500, app._toggle)
    root.mainloop()
