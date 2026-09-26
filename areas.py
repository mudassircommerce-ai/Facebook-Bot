# Target areas — CAR (car detailing) 40 miles radius, DUCT (duct cleaning)
# 60 miles radius mein groups search hote hain. Bot ke interface par
# "Car / Duct" toggle in mein se ek chunta hai.
# Naya area add karne ke baad  scratch_build_cache.py / build_area_cache.py
# dobara chalao taake areas_cache.json update ho jaye.

CAR_AREAS = [
    "Portland OR",
    "Pittsburgh PA",
    "Raleigh NC",
    "Charlotte NC",
    "El Paso TX",
    "Baton Rouge LA",
    "Phoenix AZ",
    "Las Vegas NV",
    "New Jersey",
    "Atlantic City NJ",
    "New York",
    "Long Island NY",
    "Salt Lake City UT",
    "Chicago IL",
    "Grand Rapids MI",
    "Philadelphia PA",
    "Minneapolis MN",
    "St Louis MO",
    "Kansas City MO",
    "San Francisco CA",
    "San Jose CA",
    "Los Angeles CA",
    "San Diego CA",
    "Houston TX",
    "Dallas TX",
    "Fort Worth TX",
    "Austin TX",
    "San Antonio TX",
    "Seattle WA",
    "Washington DC",
    "Baltimore MD",
    "Atlanta GA",
    "Savannah GA",
    "Detroit MI",
    "Boston MA",
    "Denver CO",
    "Hartford CT",
    "New Haven CT",
    "Cincinnati OH",
    "Columbus OH",
    "Nashville TN",
    "Knoxville TN",
    "Memphis TN",
    "Louisville KY",
    "Lexington KY",
    "Pensacola FL",
    "Tampa FL",
    "Orlando FL",
    "Miami FL",
    "Jacksonville FL",
    "Tallahassee FL",
    "Indianapolis IN",
    "Oklahoma City OK",
    "Charleston SC",
    "Omaha NE",
    "Rhode Island",
]

DUCT_AREAS = [
    "Los Angeles CA",
    "San Jose CA",
    "San Francisco CA",
    "Sacramento CA",
    "San Diego CA",
    "Denver CO",
    "Colorado Springs CO",
    "Seattle WA",
    "Portland OR",
    "Nashville TN",
    "Minneapolis MN",
    "San Antonio TX",
    "Dallas TX",
    "Houston TX",
    "Austin TX",
    "Indianapolis IN",
    "Charlotte NC",
    "Raleigh NC",
    "Boston MA",
    "Connecticut",
    "Philadelphia PA",
    "New Jersey",
    "Salt Lake City UT",
    "Atlanta GA",
    "Cleveland OH",
    "Kansas City MO",
    "St Louis MO",
    "Phoenix AZ",
    "Miami FL",
    "Jacksonville FL",
    "Tampa FL",
    "Sarasota FL",
    "Naples FL",
    "Fort Lauderdale FL",
    "Orlando FL",
    "Fort Myers FL",
    "Oklahoma City OK",
    "Boise ID",
    "Knoxville TN",
    "Myrtle Beach SC",
    "Virginia Beach VA",
    "Rhode Island",
]

# Default / backward-compat: kuch purana code sirf AREAS dekhta hai.
AREAS = CAR_AREAS

# mode string -> list
# GARAGE — sirf ADMIN key par.
GARAGE_AREAS = [
    # California
    "Los Angeles CA", "San Jose CA", "San Francisco CA", "Sacramento CA", "San Diego CA",
    # Colorado
    "Denver CO", "Colorado Springs CO",
    # Illinois
    "Chicago IL",
    # Washington
    "Seattle WA",
    # Oregon
    "Portland OR",
    # Tennessee
    "Nashville TN",
    # Minnesota
    "Minneapolis MN",
    # Texas
    "San Antonio TX", "Dallas TX", "Houston TX", "Austin TX",
    # Indiana
    "Indianapolis IN",
    # North Carolina
    "Charlotte NC", "Raleigh NC",
    # Massachusetts
    "Boston MA",
    # Connecticut
    "New Haven CT",
    # Pennsylvania
    "Philadelphia PA",
    # New Jersey (poora state)
    "New Jersey",
    # Utah
    "Salt Lake City UT",
    # Georgia
    "Atlanta GA",
    # Ohio
    "Cleveland OH",
    # Kansas / Missouri
    "Kansas City MO", "St Louis MO",
    # Arizona
    "Phoenix AZ",
    # Florida
    "Miami FL", "Jacksonville FL", "Tampa FL", "Sarasota FL", "Naples FL",
    "Fort Lauderdale FL", "Orlando FL", "Fort Myers FL",
    # Oklahoma
    "Oklahoma City OK",
    # Virginia
    "Virginia Beach VA",
]

# ── DUCT TEST mode (sirf ADMIN key par) ─────────────────────────
# Ye mode duct_test_areas.json use karta hai — jo build_duct_test_areas.py
# ne duct_test_source.txt (USA SERVICE AREAS list) se banaya. Structure:
#   { "California": ["Los Angeles County CA", "Vernon CA", ...], ... }
# Baaki modes (car/duct/garage) mein "area" ek city/state hota hai jise bot
# radius se expand karta hai. Yahan file mein pehle se 50-mile ke andar ke
# saare sub-areas maujood hain — isliye UI mein sirf STATE dikhta hai aur
# bot us state ke sub-areas ko SEEDHE search-target banata hai (koi radius
# expansion nahi). File na mile / kharab ho -> khali dict (mode chalega
# nahi, baaki bot par asar nahi).
import json as _json
import os as _os

def _load_duct_test():
    try:
        p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                          "duct_test_areas.json")
        with open(p, encoding="utf-8") as f:
            d = _json.load(f)
        # normalize: state -> clean list of strings
        out = {}
        for st, items in (d or {}).items():
            lst = [str(x).strip() for x in (items or []) if str(x).strip()]
            if st and lst:
                out[str(st).strip()] = lst
        return out
    except Exception:
        return {}

DUCT_TEST_BY_STATE = _load_duct_test()
DUCT_TEST_STATES = sorted(DUCT_TEST_BY_STATE.keys())

AREA_LISTS = {"car": CAR_AREAS, "duct": DUCT_AREAS, "garage": GARAGE_AREAS,
              "duct_test": DUCT_TEST_STATES}

def areas_for(mode: str):
    # duct_test -> dropdown mein sirf STATE names
    return AREA_LISTS.get((mode or "car").lower(), CAR_AREAS)

def duct_test_targets(state: str = "") -> list:
    """Duct Test mode ke search targets.

    state khali / 'ALL' jaisa -> har state ke saare sub-areas (dedupe).
    warna sirf us state ke sub-areas. State name case-insensitive match.
    """
    st = (state or "").strip()
    if st and st.lower() not in ("all", "all areas"):
        # exact ya case-insensitive
        if st in DUCT_TEST_BY_STATE:
            return list(DUCT_TEST_BY_STATE[st])
        for k, v in DUCT_TEST_BY_STATE.items():
            if k.lower() == st.lower():
                return list(v)
        return []
    # ALL — sab states, dedupe order-preserve
    out, seen = [], set()
    for k in DUCT_TEST_STATES:
        for a in DUCT_TEST_BY_STATE[k]:
            if a.lower() not in seen:
                seen.add(a.lower())
                out.append(a)
    return out

# Har area ke liye yeh search terms use hote hain
SEARCH_TEMPLATES = [
    "{area} community",
    "{area} neighborhood",
    "{area} local residents",
    "{area} buy sell trade",
    "{area} community group",
]
