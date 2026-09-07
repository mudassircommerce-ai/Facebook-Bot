# Target areas — har ek ke liye 40 miles radius mein groups search hote hain.
# Do lists: CAR (car detailing) aur DUCT (duct cleaning). Bot ke interface
# par "Car / Duct" toggle in mein se ek chunta hai.
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
]

# Default / backward-compat: kuch purana code sirf AREAS dekhta hai.
AREAS = CAR_AREAS

# mode string -> list
AREA_LISTS = {"car": CAR_AREAS, "duct": DUCT_AREAS}

def areas_for(mode: str):
    return AREA_LISTS.get((mode or "car").lower(), CAR_AREAS)

# Har area ke liye yeh search terms use hote hain
SEARCH_TEMPLATES = [
    "{area} community",
    "{area} neighborhood",
    "{area} local residents",
    "{area} buy sell trade",
    "{area} community group",
]
