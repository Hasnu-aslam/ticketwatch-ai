"""
Station code resolver for Indian Railways.
Maps city names and common aliases to primary IRCTC station codes.
"""

from typing import Optional

CITY_STATION_MAP: dict[str, list[str]] = {
    # Tamil Nadu
    "chennai": ["MAS", "MS", "TBM"],
    "madras": ["MAS", "MS", "TBM"],
    "madurai": ["MDU"],
    "coimbatore": ["CBE"],
    "trichy": ["TPJ"],
    "tiruchirappalli": ["TPJ"],
    "tirunelveli": ["TEN"],
    "salem": ["SA"],
    "erode": ["ED"],
    "nagercoil": ["NCJ"],
    "kanyakumari": ["CAPE"],
    "tuticorin": ["TN"],
    "thoothukudi": ["TN"],
    "rameshwaram": ["RMM"],
    "dindigul": ["DG"],
    "thanjavur": ["TJ"],
    "vellore": ["KPD"],

    # Karnataka
    "bangalore": ["SBC", "YPR", "SMVB"],
    "bengaluru": ["SBC", "YPR", "SMVB"],
    "mysore": ["MYS"],
    "mysuru": ["MYS"],
    "hubli": ["UBL"],
    "mangalore": ["MAQ", "MAJN"],

    # Kerala
    "kochi": ["ERS", "ERN"],
    "cochin": ["ERS", "ERN"],
    "ernakulam": ["ERS", "ERN"],
    "trivandrum": ["TVC"],
    "thiruvananthapuram": ["TVC"],
    "kozhikode": ["CLT"],
    "calicut": ["CLT"],
    "thrissur": ["TCR"],
    "palakkad": ["PGT"],

    # Andhra Pradesh & Telangana
    "hyderabad": ["SC", "HYB", "KCG"],
    "secunderabad": ["SC"],
    "vijayawada": ["BZA"],
    "visakhapatnam": ["VSKP"],
    "vizag": ["VSKP"],
    "tirupati": ["TPTY", "RU"],

    # Major Metros
    "delhi": ["NDLS", "DLI", "NZM", "ANVT"],
    "new delhi": ["NDLS"],
    "mumbai": ["CSMT", "BCT", "LTT", "BDTS"],
    "bombay": ["CSMT", "BCT", "LTT"],
    "pune": ["PUNE"],
    "kolkata": ["HWH", "SDAH", "KOAA"],
    "calcutta": ["HWH", "SDAH"],
    "ahmedabad": ["ADI"],
    "jaipur": ["JP"],
    "lucknow": ["LKO", "LJN"],
    "patna": ["PNBE"],
    "goa": ["MAO", "KRMI"],
}

# Inverted mapping: station code to canonical station description
STATION_CODE_NAMES: dict[str, str] = {
    "MAS": "Chennai Central",
    "MS": "Chennai Egmore",
    "TBM": "Tambaram (Chennai)",
    "MDU": "Madurai Junction",
    "CBE": "Coimbatore Junction",
    "TPJ": "Tiruchirappalli Junction",
    "TEN": "Tirunelveli Junction",
    "SA": "Salem Junction",
    "ED": "Erode Junction",
    "SBC": "KSR Bengaluru",
    "YPR": "Yesvantpur Junction",
    "SMVB": "SMVT Bengaluru",
    "ERS": "Ernakulam Junction (South)",
    "ERN": "Ernakulam Town (North)",
    "TVC": "Thiruvananthapuram Central",
    "SC": "Secunderabad Junction",
    "HYB": "Hyderabad Deccan",
    "BZA": "Vijayawada Junction",
    "NDLS": "New Delhi",
    "NZM": "Hazrat Nizamuddin",
    "CSMT": "Mumbai CSMT",
    "HWH": "Howrah Junction",
}


def resolve_station_code(query_str: str) -> str:
    """
    Given a city name or station code, returns the most likely station code.
    If the string is already a 2-4 uppercase letter code (e.g. 'MAS', 'MDU'), returns as-is.
    Otherwise looks up in the city dictionary and returns the primary station.
    """
    clean = query_str.strip()
    upper = clean.upper()

    # If it's a known station code, return it directly
    if upper in STATION_CODE_NAMES or (len(upper) in (2, 3, 4) and upper.isalpha() and upper in [st for sts in CITY_STATION_MAP.values() for st in sts]):
        return upper

    # If already 3-4 letters uppercase and matches standard station code syntax
    if len(upper) in (3, 4) and upper.isalpha() and upper == clean:
        return upper

    # Lookup by city name
    lower = clean.lower()
    if lower in CITY_STATION_MAP:
        return CITY_STATION_MAP[lower][0]

    # Partial match
    for city, codes in CITY_STATION_MAP.items():
        if city in lower or lower in city:
            return codes[0]

    # Fallback to uppercase
    return upper


def resolve_all_station_codes(query_str: str) -> list[str]:
    """
    Returns all station codes associated with a city (e.g. 'Chennai' -> ['MAS', 'MS', 'TBM']).
    """
    lower = query_str.strip().lower()
    if lower in CITY_STATION_MAP:
        return CITY_STATION_MAP[lower]
    return [resolve_station_code(query_str)]
