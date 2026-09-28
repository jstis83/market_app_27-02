"""
location_data.py

Geographic reference and family-support-city model for
Assignment Explorer.

PURPOSE
-------
This module answers:

    "If we were assigned here, what nearby community
     could provide a lifestyle/support environment
     comparable to New Braunfels, Texas?"

This is NOT the family desirability model.

Workflow:

    Assignment
        ↓
    Candidate nearby communities
        ↓
    Population / growth
        ↓
    Retail support
        ↓
    Support City Score
        ↓
    Best comparable support city
"""

from pathlib import Path
from math import radians, sin, cos, atan2, sqrt

import re
import time
import json
import os
import tempfile
from datetime import datetime, timezone

import pandas as pd
import requests
import geonamescache


# =========================================================
# PROJECT CONFIGURATION
# =========================================================

PROJECT_DIR = Path(__file__).resolve().parent

LOCATION_CACHE_FILE = PROJECT_DIR / "location_cache.csv"
BATCH_STATUS_FILE = PROJECT_DIR / "location_batch_status.csv"
MODEL_VERSION = "support-city-v1"

# =========================================================
# OPENSTREETMAP / OVERPASS CONFIGURATION
# =========================================================

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

OVERPASS_TIMEOUT = 45

# Small delay between uncached Overpass requests.
# Keeps the enrichment process polite and reduces
# the chance of rate limiting during batch processing.
OVERPASS_REQUEST_DELAY = 1.0

# =========================================================
# BASELINE
# =========================================================

BASELINE_CITY = "New Braunfels"
BASELINE_STATE = "TX"
BASELINE_ZIP = "78130"

# Representative city-center coordinates.
# These are NOT home coordinates.
BASELINE_LATITUDE = 29.7030
BASELINE_LONGITUDE = -98.1245


# =========================================================
# SUPPORT CITY SEARCH PARAMETERS
# =========================================================

# Maximum distance we will search from an assignment
# for a plausible family-support community.

CITY_SEARCH_RADIUS_MILES = 35

# New-Braunfels-style population target.
TARGET_CITY_POPULATION = 100_000

# Guideline only — NOT a hard exclusion.
MIN_CITY_POPULATION = 75_000

# Positive population growth benchmark.
TARGET_POPULATION_GROWTH = 0.05

# Maximum candidate communities retained per assignment.
MAX_CANDIDATE_CITIES = 10


# =========================================================
# RETAIL / COMMERCIAL DEVELOPMENT PROXY
# =========================================================

RETAIL_WEIGHTS = {
    "Starbucks": 1,
    "Target": 1,
    "Chick-fil-A": 1
}

RETAIL_SEARCH_RADIUS_MILES = {
    "Starbucks": 5,
    "Target": 7,
    "Chick-fil-A": 7
}

RETAIL_MAX_POINTS = sum(
    RETAIL_WEIGHTS.values()
)


def calculate_retail_score(
    starbucks=False,
    target=False,
    chick_fil_a=False
):
    """
    Calculate retail/commercial-development proxy.

    The presence of Starbucks, Target, and Chick-fil-A
    is used as a proxy for commercial investment and
    development around the candidate support city.

    Each retailer contributes equally.

    Returns:
        retail_points
        retail_score
    """

    retailer_presence = {
        "Starbucks": bool(starbucks),
        "Target": bool(target),
        "Chick-fil-A": bool(chick_fil_a)
    }

    retail_points = sum(
        RETAIL_WEIGHTS[retailer]
        for retailer, present
        in retailer_presence.items()
        if present
    )

    retail_score = (
        retail_points / RETAIL_MAX_POINTS
        if RETAIL_MAX_POINTS > 0
        else 0.0
    )

    return retail_points, retail_score

# =========================================================
# SUPPORT CITY MODEL WEIGHTS
# =========================================================

SUPPORT_WEIGHT_RETAIL = 0.40
SUPPORT_WEIGHT_POPULATION = 0.25
SUPPORT_WEIGHT_GROWTH = 0.15
SUPPORT_WEIGHT_DISTANCE = 0.20


# =========================================================
# CENSUS CONFIGURATION
# =========================================================

# Set in Terminal:
#
# export CENSUS_API_KEY="YOUR_KEY"
#
# Then run:
#
# python location_data.py

# CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")
## Hard coding API key for portability
CENSUS_API_KEY = "714aa22271604e6d927cb8badff535d9e5d4d265"

CENSUS_CURRENT_YEAR = 2024
CENSUS_PRIOR_YEAR = 2019


STATE_FIPS = {
    "AL": "01",
    "AK": "02",
    "AZ": "04",
    "AR": "05",
    "CA": "06",
    "CO": "08",
    "CT": "09",
    "DE": "10",
    "DC": "11",
    "FL": "12",
    "GA": "13",
    "HI": "15",
    "ID": "16",
    "IL": "17",
    "IN": "18",
    "IA": "19",
    "KS": "20",
    "KY": "21",
    "LA": "22",
    "ME": "23",
    "MD": "24",
    "MA": "25",
    "MI": "26",
    "MN": "27",
    "MS": "28",
    "MO": "29",
    "MT": "30",
    "NE": "31",
    "NV": "32",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "NY": "36",
    "NC": "37",
    "ND": "38",
    "OH": "39",
    "OK": "40",
    "OR": "41",
    "PA": "42",
    "RI": "44",
    "SC": "45",
    "SD": "46",
    "TN": "47",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "VA": "51",
    "WA": "53",
    "WV": "54",
    "WI": "55",
    "WY": "56",
}


# =========================================================
# STATE CAPITALS
# =========================================================

STATE_CAPITALS = {
    "AL": ("Montgomery", 32.3777, -86.3006),
    "AZ": ("Phoenix", 33.4484, -112.0740),
    "AR": ("Little Rock", 34.7465, -92.2896),
    "CA": ("Sacramento", 38.5816, -121.4944),
    "CO": ("Denver", 39.7392, -104.9903),
    "CT": ("Hartford", 41.7658, -72.6734),
    "DE": ("Dover", 39.1582, -75.5244),
    "FL": ("Tallahassee", 30.4383, -84.2807),
    "GA": ("Atlanta", 33.7490, -84.3880),
    "ID": ("Boise", 43.6150, -116.2023),
    "IL": ("Springfield", 39.7817, -89.6501),
    "IN": ("Indianapolis", 39.7684, -86.1581),
    "IA": ("Des Moines", 41.5868, -93.6250),
    "KS": ("Topeka", 39.0473, -95.6752),
    "KY": ("Frankfort", 38.2009, -84.8777),
    "LA": ("Baton Rouge", 30.4515, -91.1871),
    "ME": ("Augusta", 44.3106, -69.7795),
    "MD": ("Annapolis", 38.9784, -76.4922),
    "MA": ("Boston", 42.3601, -71.0589),
    "MI": ("Lansing", 42.7325, -84.5555),
    "MN": ("Saint Paul", 44.9537, -93.0900),
    "MS": ("Jackson", 32.2988, -90.1848),
    "MO": ("Jefferson City", 38.5767, -92.1735),
    "MT": ("Helena", 46.5891, -112.0391),
    "NE": ("Lincoln", 40.8136, -96.7026),
    "NV": ("Carson City", 39.1638, -119.7674),
    "NH": ("Concord", 43.2081, -71.5376),
    "NJ": ("Trenton", 40.2171, -74.7429),
    "NM": ("Santa Fe", 35.6870, -105.9378),
    "NY": ("Albany", 42.6526, -73.7562),
    "NC": ("Raleigh", 35.7796, -78.6382),
    "ND": ("Bismarck", 46.8083, -100.7837),
    "OH": ("Columbus", 39.9612, -82.9988),
    "OK": ("Oklahoma City", 35.4676, -97.5164),
    "OR": ("Salem", 44.9429, -123.0351),
    "PA": ("Harrisburg", 40.2732, -76.8867),
    "RI": ("Providence", 41.8240, -71.4128),
    "SC": ("Columbia", 34.0007, -81.0348),
    "SD": ("Pierre", 44.3683, -100.3510),
    "TN": ("Nashville", 36.1627, -86.7816),
    "TX": ("Austin", 30.2672, -97.7431),
    "UT": ("Salt Lake City", 40.7608, -111.8910),
    "VT": ("Montpelier", 44.2601, -72.5754),
    "VA": ("Richmond", 37.5407, -77.4360),
    "WA": ("Olympia", 47.0379, -122.9007),
    "WV": ("Charleston", 38.3498, -81.6326),
    "WI": ("Madison", 43.0731, -89.4012),
    "WY": ("Cheyenne", 41.1400, -104.8202),

    # Included because of the large NCR assignment market.
    "DC": ("Washington", 38.9072, -77.0369),
}


# =========================================================
# MODULE CACHES
# =========================================================

_CITY_INDEX = None
_CENSUS_CACHE = {}
_LAST_OVERPASS_REQUEST = None


# =========================================================
# DISTANCE
# =========================================================

def haversine_miles(lat1, lon1, lat2, lon2):
    """
    Calculate great-circle distance between two
    latitude/longitude points.

    Returns miles.
    """

    values = [lat1, lon1, lat2, lon2]

    if any(pd.isna(value) for value in values):
        return None

    earth_radius = 3958.8

    lat1 = radians(float(lat1))
    lon1 = radians(float(lon1))
    lat2 = radians(float(lat2))
    lon2 = radians(float(lon2))

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        sin(dlat / 2) ** 2
        + cos(lat1)
        * cos(lat2)
        * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(
        sqrt(a),
        sqrt(1 - a),
    )

    return earth_radius * c


# =========================================================
# STATE CAPITAL FUNCTIONS
# =========================================================

def get_state_capital(state):
    """
    Return state-capital reference data.
    """

    state = str(state).strip().upper()

    capital_data = STATE_CAPITALS.get(state)

    if capital_data is None:
        return None

    name, latitude, longitude = capital_data

    return {
        "capital": name,
        "latitude": latitude,
        "longitude": longitude,
    }


def get_capital_context(
    state,
    assignment_lat,
    assignment_lon,
):
    """
    Return state capital and distance from assignment.
    """

    capital = get_state_capital(state)

    if capital is None:
        return {
            "State_Capital": None,
            "Capital_Latitude": None,
            "Capital_Longitude": None,
            "Capital_Distance_Miles": None,
        }

    distance = haversine_miles(
        assignment_lat,
        assignment_lon,
        capital["latitude"],
        capital["longitude"],
    )

    return {
        "State_Capital": capital["capital"],
        "Capital_Latitude": capital["latitude"],
        "Capital_Longitude": capital["longitude"],
        "Capital_Distance_Miles": (
            round(distance, 1)
            if distance is not None
            else None
        ),
    }


# =========================================================
# RETAIL SUPPORT
# =========================================================

def calculate_retail_support(
    starbucks=False,
    target=False,
    chick_fil_a=False,
):
    """
    Calculate retail-support score.

    Starbucks, Target, and Chick-fil-A are used as
    commercial-development indicators.

    Returns raw points and normalized 0-1 score.
    """

    observations = {
        "Starbucks": bool(starbucks),
        "Target": bool(target),
        "Chick-fil-A": bool(chick_fil_a),
    }

    points = sum(
        RETAIL_WEIGHTS[retailer]
        for retailer, present in observations.items()
        if present
    )

    score = (
        points / RETAIL_MAX_POINTS
        if RETAIL_MAX_POINTS > 0
        else 0.0
    )

    return {
        "Retail_Points": points,
        "Retail_Max_Points": RETAIL_MAX_POINTS,
        "Retail_Score": round(score, 3),
        **observations,
    }
# =========================================================
# OPENSTREETMAP RETAIL LOOKUP
# =========================================================

def miles_to_meters(miles):
    """
    Convert miles to meters for Overpass radius queries.
    """

    return float(miles) * 1609.344


def query_overpass_retail(
    latitude,
    longitude,
):
    """
    Query OpenStreetMap through Overpass for nearby
    retail and food-service features.

    Overpass performs only the geographic/category search.
    Retailer identification is performed locally in Python.

    This is substantially lighter than running multiple
    regex searches on the Overpass server.
    """

    max_radius_miles = max(
        RETAIL_SEARCH_RADIUS_MILES.values()
    )

    radius_meters = int(
        miles_to_meters(max_radius_miles)
    )

    query = f"""
    [out:json][timeout:{OVERPASS_TIMEOUT}];

    (
      nwr["shop"]
          (around:{radius_meters},{latitude},{longitude});

      nwr["amenity"="cafe"]
          (around:{radius_meters},{latitude},{longitude});

      nwr["amenity"="fast_food"]
          (around:{radius_meters},{latitude},{longitude});

      nwr["amenity"="restaurant"]
          (around:{radius_meters},{latitude},{longitude});
    );

    out center tags;
    """

    headers = {
        "User-Agent": (
            "AssignmentExplorer/1.0 "
            "(personal decision-support project)"
        ),
        "Accept": "application/json",
    }

    # Wait between completed requests, including failures.
    global _LAST_OVERPASS_REQUEST
    if _LAST_OVERPASS_REQUEST is not None:
        remaining = OVERPASS_REQUEST_DELAY - (time.monotonic() - _LAST_OVERPASS_REQUEST)
        if remaining > 0:
            time.sleep(remaining)

    try:
        response = requests.get(
            OVERPASS_URL, params={"data": query}, headers=headers,
            timeout=OVERPASS_TIMEOUT + 15,
        )
        response.raise_for_status()
        data = response.json()
        # Overpass can return HTTP 200 with a runtime-error remark
        # and incomplete elements. Such results are not evidence of absence.
        if (not isinstance(data, dict) or data.get("remark")
                or not isinstance(data.get("elements"), list)
                or any(not isinstance(item, dict) for item in data["elements"])):
            return None
        return data["elements"]
    except (requests.exceptions.RequestException, ValueError):
        print("WARNING: Retail data unavailable; retail weight will be excluded.")
        return None
    finally:
        _LAST_OVERPASS_REQUEST = time.monotonic()


def get_osm_element_coordinates(element):
    """
    Extract latitude/longitude from an Overpass element.

    Nodes contain lat/lon directly.

    Ways and relations returned with 'out center' contain
    coordinates in the center object.
    """

    latitude = element.get("lat")
    longitude = element.get("lon")

    if latitude is not None and longitude is not None:
        return float(latitude), float(longitude)

    center = element.get(
        "center",
        {},
    )

    latitude = center.get("lat")
    longitude = center.get("lon")

    if latitude is None or longitude is None:
        return None, None

    return float(latitude), float(longitude)


def identify_retailer(element):
    """
    Identify which of our three retail indicators an
    OpenStreetMap feature represents.
    """

    tags = element.get(
        "tags",
        {},
    )

    text = " ".join(
        [
            str(tags.get("name", "")),
            str(tags.get("brand", "")),
            str(tags.get("operator", "")),
        ]
    ).lower()

    if "starbucks" in text:
        return "Starbucks"

    if "target" in text:
        return "Target"

    # Normalize punctuation for Chick-fil-A.
    normalized = re.sub(
        r"[^a-z]",
        "",
        text,
    )

    if "chickfila" in normalized:
        return "Chick-fil-A"

    return None


def lookup_retail_support(
    latitude,
    longitude,
):
    """
    Determine whether Starbucks, Target, and Chick-fil-A
    are present within their retailer-specific radii of a
    candidate support city.

    Returns retail presence, raw points, normalized retail
    score, and nearest observed distance for each retailer.
    """

    elements = query_overpass_retail(
        latitude,
        longitude,
    )

    if elements is None:
        return {
            "Retail_Status": "Unavailable",
            "Retail_Points": None,
            "Retail_Max_Points": RETAIL_MAX_POINTS,
            "Retail_Score": None,
            **{name: None for name in RETAIL_WEIGHTS},
            **{f"{name}_Distance_Miles": None for name in RETAIL_WEIGHTS},
        }

    nearest = {
        "Starbucks": None,
        "Target": None,
        "Chick-fil-A": None,
    }

    for element in elements:

        retailer = identify_retailer(
            element
        )

        if retailer is None:
            continue

        element_lat, element_lon = (
            get_osm_element_coordinates(
                element
            )
        )

        if (
            element_lat is None
            or element_lon is None
        ):
            continue

        distance = haversine_miles(
            latitude,
            longitude,
            element_lat,
            element_lon,
        )

        if distance is None:
            continue

        current_nearest = nearest[
            retailer
        ]

        if (
            current_nearest is None
            or distance < current_nearest
        ):
            nearest[retailer] = distance

    # -----------------------------------------------------
    # Apply retailer-specific radii
    # -----------------------------------------------------

    starbucks = (
        nearest["Starbucks"] is not None
        and nearest["Starbucks"]
        <= RETAIL_SEARCH_RADIUS_MILES[
            "Starbucks"
        ]
    )

    target = (
        nearest["Target"] is not None
        and nearest["Target"]
        <= RETAIL_SEARCH_RADIUS_MILES[
            "Target"
        ]
    )

    chick_fil_a = (
        nearest["Chick-fil-A"] is not None
        and nearest["Chick-fil-A"]
        <= RETAIL_SEARCH_RADIUS_MILES[
            "Chick-fil-A"
        ]
    )

    retail = calculate_retail_support(
        starbucks=starbucks,
        target=target,
        chick_fil_a=chick_fil_a,
    )

    retail["Retail_Status"] = "Available"

    # Keep nearest distances for diagnostics.
    retail[
        "Starbucks_Distance_Miles"
    ] = (
        round(nearest["Starbucks"], 1)
        if nearest["Starbucks"] is not None
        else None
    )

    retail[
        "Target_Distance_Miles"
    ] = (
        round(nearest["Target"], 1)
        if nearest["Target"] is not None
        else None
    )

    retail[
        "Chick-fil-A_Distance_Miles"
    ] = (
        round(nearest["Chick-fil-A"], 1)
        if nearest["Chick-fil-A"] is not None
        else None
    )

    return retail

# =========================================================
# POPULATION SCORE
# =========================================================

def calculate_population_score(population):
    """
    Normalize city population against the
    New-Braunfels-style target.

    100k+ receives full credit.
    Smaller communities receive partial credit.
    """

    if population is None or pd.isna(population):
        return None

    population = float(population)

    if population <= 0:
        return 0.0

    score = population / TARGET_CITY_POPULATION

    return round(
        min(score, 1.0),
        3,
    )


# =========================================================
# GROWTH SCORE
# =========================================================

def calculate_growth_score(growth_rate):
    """
    Convert population growth into a 0-1 score.

    0.05 = 5% growth.

    5%+ receives full credit.
    Flat or declining population receives zero.
    """

    if growth_rate is None or pd.isna(growth_rate):
        return None

    growth_rate = float(growth_rate)

    if growth_rate <= 0:
        return 0.0

    score = (
        growth_rate
        / TARGET_POPULATION_GROWTH
    )

    return round(
        min(score, 1.0),
        3,
    )


# =========================================================
# DISTANCE SCORE
# =========================================================

def calculate_distance_score(distance_miles):
    """
    Score practical distance from assignment.

    0 miles -> 1.0
    35 miles -> 0.0
    """

    if distance_miles is None or pd.isna(distance_miles):
        return None

    distance_miles = max(
        float(distance_miles),
        0,
    )

    if distance_miles >= CITY_SEARCH_RADIUS_MILES:
        return 0.0

    score = (
        1
        - distance_miles
        / CITY_SEARCH_RADIUS_MILES
    )

    return round(score, 3)


# =========================================================
# SUPPORT CITY SCORE
# =========================================================

def calculate_support_city_score(
    population=None,
    growth_rate=None,
    distance_miles=None,
    retail_score=None,
):
    """
    Calculate the objective family-support-city score.

    This is NOT family desirability.

    Components:
        Retail presence       40%
        Population            25%
        Population growth     15%
        Assignment distance   20%

    Retail score represents the presence of:
        - Starbucks
        - Target
        - Chick-fil-A

    Each retailer contributes equally to the retail score.

    Missing components are excluded and the remaining
    weights are automatically rescaled.

    Returns:
        dict containing each normalized component score
        and the final Support_City_Score on a 0-100 scale.
    """

    # -----------------------------------------------------
    # Calculate normalized component scores
    # -----------------------------------------------------

    population_score = calculate_population_score(
        population
    )

    growth_score = calculate_growth_score(
        growth_rate
    )

    distance_score = calculate_distance_score(
        distance_miles
    )

    # Retail score should already be normalized 0-1.
    # Protect against values outside the expected range.
    if retail_score is None or pd.isna(retail_score):
        retail_score = None
    else:
        retail_score = max(
            0.0,
            min(1.0, float(retail_score)),
        )

    # -----------------------------------------------------
    # Assemble weighted components
    # -----------------------------------------------------

    components = {
        "Retail": (
            retail_score,
            SUPPORT_WEIGHT_RETAIL,
        ),
        "Population": (
            population_score,
            SUPPORT_WEIGHT_POPULATION,
        ),
        "Growth": (
            growth_score,
            SUPPORT_WEIGHT_GROWTH,
        ),
        "Distance": (
            distance_score,
            SUPPORT_WEIGHT_DISTANCE,
        ),
    }

    # -----------------------------------------------------
    # Weighted score
    # -----------------------------------------------------

    weighted_total = 0.0
    available_weight = 0.0

    component_scores = {}

    for name, (score, weight) in components.items():

        component_scores[
            f"{name}_Score"
        ] = score

        if score is not None:
            weighted_total += score * weight
            available_weight += weight

    # -----------------------------------------------------
    # Rescale when data is missing
    # -----------------------------------------------------

    if available_weight == 0:
        final_score = None

    else:
        final_score = (
            weighted_total
            / available_weight
        )

    # -----------------------------------------------------
    # Return
    # -----------------------------------------------------

    return {
        **component_scores,

        "Available_Weight": round(
            available_weight,
            2,
        ),

        "Support_City_Score": (
            round(final_score * 100, 1)
            if final_score is not None
            else None
        ),
    }

# =========================================================
# OFFLINE US CITY INDEX
# =========================================================

def build_city_index():
    """
    Build an in-memory index of U.S. cities using
    geonamescache.

    No internet call is required.
    """

    global _CITY_INDEX

    if _CITY_INDEX is not None:
        return _CITY_INDEX

    gc = geonamescache.GeonamesCache()

    cities = gc.get_cities()

    records = []

    for city in cities.values():

        if city.get("countrycode") != "US":
            continue

        state = city.get("admin1code")

        if state not in STATE_FIPS:
            continue

        try:
            latitude = float(
                city["latitude"]
            )

            longitude = float(
                city["longitude"]
            )

            population = int(
                city.get(
                    "population",
                    0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
            KeyError,
        ):
            continue

        records.append(
            {
                "City": city["name"],
                "State": state,
                "Latitude": latitude,
                "Longitude": longitude,
                "GeoNames_Population": population,
            }
        )

    _CITY_INDEX = pd.DataFrame(records)

    return _CITY_INDEX


# =========================================================
# CANDIDATE SUPPORT CITY DISCOVERY
# =========================================================

def find_candidate_cities(
    assignment_latitude,
    assignment_longitude,
    assignment_state=None,
    radius_miles=None,
    max_candidates=None,
):
    """
    Find candidate support communities around an assignment.

    Population is NOT a hard exclusion.

    A smaller suburb with strong infrastructure may
    be preferable to a larger central city.
    """

    # Resolve defaults here rather than in the function
    # signature. This also prevents future ordering problems.

    if radius_miles is None:
        radius_miles = (
            CITY_SEARCH_RADIUS_MILES
        )

    if max_candidates is None:
        max_candidates = (
            MAX_CANDIDATE_CITIES
        )

    city_index = (
        build_city_index()
        .copy()
    )

    city_index[
        "Distance_Miles"
    ] = city_index.apply(
        lambda row: haversine_miles(
            assignment_latitude,
            assignment_longitude,
            row["Latitude"],
            row["Longitude"],
        ),
        axis=1,
    )

    candidates = city_index[
        city_index[
            "Distance_Miles"
        ]
        <= radius_miles
    ].copy()

    if candidates.empty:
        return candidates

    candidates[
        "Discovery_Population_Score"
    ] = (
        candidates[
            "GeoNames_Population"
        ]
        / TARGET_CITY_POPULATION
    ).clip(
        lower=0,
        upper=1,
    )

    candidates[
        "Discovery_Distance_Score"
    ] = (
        1
        - candidates[
            "Distance_Miles"
        ]
        / radius_miles
    ).clip(
        lower=0,
        upper=1,
    )

    # Preliminary discovery only.
    #
    # Population 60%
    # Distance   40%

    candidates[
        "Discovery_Score"
    ] = (
        0.60
        * candidates[
            "Discovery_Population_Score"
        ]
        +
        0.40
        * candidates[
            "Discovery_Distance_Score"
        ]
    )

    candidates = (
        candidates.sort_values(
            [
                "Discovery_Score",
                "GeoNames_Population",
            ],
            ascending=False,
        )
    )

    return (
        candidates
        .head(max_candidates)
        .reset_index(drop=True)
    )


# =========================================================
# CENSUS ACS
# =========================================================

def normalize_place_name(name):
    """
    Normalize Census place names for matching.

    Example:

        "Leavenworth city, Kansas"
            -> "leavenworth"
    """

    if not name:
        return ""

    name = str(name).lower()

    # Remove state portion.
    name = name.split(",")[0]

    suffixes = [
        " city",
        " town",
        " village",
        " borough",
        " municipality",
        " cdp",
    ]

    for suffix in suffixes:

        if name.endswith(suffix):
            name = name[
                :-len(suffix)
            ]

    name = re.sub(
        r"[^a-z0-9\s\-']",
        "",
        name,
    )

    return name.strip()


def get_census_places(
    state,
    year=None,
):
    """
    Download ACS population estimates for all
    Census places within a state.

    Results are cached in memory.
    """

    if year is None:
        year = CENSUS_CURRENT_YEAR

    state = (
        str(state)
        .strip()
        .upper()
    )

    cache_key = (
        state,
        year,
    )

    if cache_key in _CENSUS_CACHE:
        return _CENSUS_CACHE[
            cache_key
        ]

    if not CENSUS_API_KEY:
        raise RuntimeError(
            "\nCensus API key not found.\n\n"
            "Set it in Terminal with:\n\n"
            'export CENSUS_API_KEY="YOUR_KEY"\n'
        )

    state_fips = STATE_FIPS.get(
        state
    )

    if state_fips is None:
        raise ValueError(
            f"Unknown state: {state}"
        )

    url = (
        "https://api.census.gov/data/"
        f"{year}/acs/acs5"
    )

    params = {
        "get": "NAME,B01003_001E",
        "for": "place:*",
        "in": f"state:{state_fips}",
        "key": CENSUS_API_KEY,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        rows = response.json()
    except (requests.exceptions.RequestException, ValueError):
        # Never expose the request URL: it contains the Census credential.
        raise RuntimeError("Census population lookup failed.") from None

    if not rows or len(rows) < 2:
        return pd.DataFrame()

    header = rows[0]

    census_df = pd.DataFrame(
        rows[1:],
        columns=header,
    )

    census_df = census_df.rename(
        columns={
            "B01003_001E":
                "Population"
        }
    )

    census_df[
        "Population"
    ] = pd.to_numeric(
        census_df[
            "Population"
        ],
        errors="coerce",
    )

    census_df[
        "Normalized_Name"
    ] = (
        census_df[
            "NAME"
        ]
        .apply(
            normalize_place_name
        )
    )

    _CENSUS_CACHE[
        cache_key
    ] = census_df

    return census_df


def get_city_demographics(
    city,
    state,
):
    """
    Get current population and approximate
    five-year population growth.
    """

    normalized_city = (
        normalize_place_name(city)
    )

    current = get_census_places(
        state,
        CENSUS_CURRENT_YEAR,
    )

    prior = get_census_places(
        state,
        CENSUS_PRIOR_YEAR,
    )

    current_match = current[
        current[
            "Normalized_Name"
        ]
        == normalized_city
    ]

    prior_match = prior[
        prior[
            "Normalized_Name"
        ]
        == normalized_city
    ]

    if current_match.empty:

        return {
            "Population": None,
            "Prior_Population": None,
            "Population_Growth": None,
        }

    current_population = (
        current_match
        .iloc[0][
            "Population"
        ]
    )

    prior_population = None
    growth = None

    if not prior_match.empty:

        prior_population = (
            prior_match
            .iloc[0][
                "Population"
            ]
        )

        if (
            pd.notna(
                prior_population
            )
            and prior_population > 0
            and pd.notna(
                current_population
            )
        ):

            growth = (
                current_population
                - prior_population
            ) / prior_population

    return {
        "Population": (
            int(current_population)
            if pd.notna(
                current_population
            )
            else None
        ),

        "Prior_Population": (
            int(prior_population)
            if (
                prior_population
                is not None
                and pd.notna(
                    prior_population
                )
            )
            else None
        ),

        "Population_Growth": (
            round(
                float(growth),
                4,
            )
            if growth is not None
            else None
        ),
    }


# =========================================================
# COMPLETE CANDIDATE EVALUATION
# =========================================================

def evaluate_support_city(
    city,
    state,
    latitude,
    longitude,
    assignment_latitude,
    assignment_longitude,
    population=None,
    growth_rate=None,
    starbucks=False,
    target=False,
    chick_fil_a=False
    
):
    """
    Fully evaluate one candidate support city.
    """

    distance = haversine_miles(
        assignment_latitude,
        assignment_longitude,
        latitude,
        longitude,
    )

    retail = (
        calculate_retail_support(
            starbucks=starbucks,
            target=target,
            chick_fil_a=chick_fil_a
            
        )
    )

    support = (
        calculate_support_city_score(
            population=population,
            growth_rate=growth_rate,
            distance_miles=distance,
            retail_score=retail[
                "Retail_Score"
            ],
        )
    )

    return {
        "Support_City": city,
        "Support_State": state,
        "Support_Latitude": latitude,
        "Support_Longitude": longitude,

        "Support_Distance_Miles": (
            round(distance, 1)
            if distance is not None
            else None
        ),

        "Population": population,
        "Population_Growth":
            growth_rate,

        **retail,
        **support,
    }


# =========================================================
# ASSIGNMENT SUPPORT-CITY DISCOVERY
# =========================================================

def discover_support_cities(
    assignment_city,
    assignment_state,
    assignment_latitude,
    assignment_longitude,
    refresh_retail=False,
):
    """Rank nearby communities using the complete support-city model.

    Successful retail observations are reused from the location cache.
    Unavailable or legacy observations are retried; refresh_retail bypasses
    cached observations. Missing retail is excluded by the shared scorer.
    """
    candidates = find_candidate_cities(
        assignment_latitude, assignment_longitude, assignment_state,
    )
    if candidates.empty:
        return pd.DataFrame(columns=CACHE_COLUMNS)

    cache = load_location_cache()
    retail_fields = [
        "Retail_Status", "Retail_Points", "Retail_Max_Points", "Retail_Score",
        *RETAIL_WEIGHTS,
        *[f"{name}_Distance_Miles" for name in RETAIL_WEIGHTS],
    ]
    enriched = []
    for _, candidate in candidates.iterrows():
        try:
            demographics = get_city_demographics(candidate["City"], candidate["State"])
            demographics_status = "Available" if pd.notna(demographics["Population"]) else "Unmatched"
        except (RuntimeError, requests.exceptions.RequestException):
            demographics = {"Population": None, "Prior_Population": None, "Population_Growth": None}
            demographics_status = "Unavailable"
        population = demographics["Population"]
        if population is None or pd.isna(population):
            population = int(candidate["GeoNames_Population"])

        matches = cache[
            (cache["Support_City"] == candidate["City"])
            & (cache["Support_State"] == candidate["State"])
            & (cache["Support_Latitude"] == candidate["Latitude"])
            & (cache["Support_Longitude"] == candidate["Longitude"])
            & (cache["Retail_Status"] == "Available")
            & cache["Retail_Score"].notna()
            & cache[list(RETAIL_WEIGHTS)].notna().all(axis=1)
        ]
        if not refresh_retail and not matches.empty:
            retail = matches.iloc[-1][retail_fields].to_dict()
        else:
            retail = lookup_retail_support(candidate["Latitude"], candidate["Longitude"])

        support = calculate_support_city_score(
            population=population,
            growth_rate=demographics["Population_Growth"],
            distance_miles=candidate["Distance_Miles"],
            retail_score=retail["Retail_Score"],
        )
        enriched.append({
            "Assignment_City": assignment_city,
            "Assignment_State": assignment_state,
            "Assignment_Latitude": assignment_latitude,
            "Assignment_Longitude": assignment_longitude,
            "Demographics_Status": demographics_status,
            "Support_City": candidate["City"],
            "Support_State": candidate["State"],
            "Support_Latitude": candidate["Latitude"],
            "Support_Longitude": candidate["Longitude"],
            "Support_Distance_Miles": round(candidate["Distance_Miles"], 1),
            **demographics,
            "Population": population,
            **retail,
            **support,
            **get_capital_context(assignment_state, assignment_latitude, assignment_longitude),
        })

    result = pd.DataFrame(enriched).reindex(columns=CACHE_COLUMNS).sort_values(
        "Support_City_Score", ascending=False, na_position="last", kind="stable",
    ).reset_index(drop=True)
    same_assignment = (
        cache["Assignment_City"].map(normalize_assignment_city)
        == normalize_assignment_city(assignment_city)
    ) & (
        cache["Assignment_State"].astype(str).str.strip().str.upper()
        == str(assignment_state).strip().upper()
    )
    remaining = cache.loc[~same_assignment]
    updated = result if remaining.empty else pd.concat([remaining, result], ignore_index=True)
    save_location_cache(updated)
    return result


# =========================================================
# LOCATION CACHE
# =========================================================

CACHE_COLUMNS = [
    "Assignment_City",
    "Assignment_State",
    "Assignment_Latitude",
    "Assignment_Longitude",
    "Demographics_Status",

    "Support_City",
    "Support_State",
    "Support_Latitude",
    "Support_Longitude",
    "Support_Distance_Miles",

    "Population",
    "Prior_Population",
    "Population_Growth",
    "Retail_Status",
    "Starbucks_Distance_Miles",
    "Target_Distance_Miles",
    "Chick-fil-A_Distance_Miles",
    "Available_Weight",

    "Starbucks",
    "Target",
    "Chick-fil-A",

    "Retail_Points",
    "Retail_Max_Points",
    "Retail_Score",

    "Population_Score",
    "Growth_Score",
    "Distance_Score",
    "Support_City_Score",

    "State_Capital",
    "Capital_Latitude",
    "Capital_Longitude",
    "Capital_Distance_Miles",
]


def atomic_save_csv(frame, path):
    """Replace a complete CSV atomically so readers never see half a file."""
    path = Path(path)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(handle)
    try:
        frame.to_csv(temporary, index=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def normalize_assignment_city(city):
    return re.sub(r"^FT[. ]+", "FORT ", str(city).strip().upper())


BATCH_COLUMNS = [
    "Assignment_City", "Assignment_State", "Assignment_Latitude",
    "Assignment_Longitude", "Model_Version", "Status", "Candidate_Count",
    "Retail_Unavailable", "Updated_UTC", "Error",
]


def load_batch_status():
    if not BATCH_STATUS_FILE.exists():
        return pd.DataFrame(columns=BATCH_COLUMNS)
    return pd.read_csv(BATCH_STATUS_FILE).reindex(columns=BATCH_COLUMNS)


def cached_assignment_rows(cache, city, state, latitude, longitude):
    """Match the assignment identity and coordinates, excluding stale results."""
    mask = (
        cache["Assignment_City"].map(normalize_assignment_city) == normalize_assignment_city(city)
    ) & (cache["Assignment_State"].astype(str).str.strip().str.upper() == str(state).strip().upper())
    for column, value in (("Assignment_Latitude", latitude), ("Assignment_Longitude", longitude)):
        mask &= (pd.to_numeric(cache[column], errors="coerce") - float(value)).abs() < 0.000001
    return cache.loc[mask]


def run_location_batch(jobs_file=None, limit=None, refresh=False):
    """Resume enrichment of unique mapped CONUS assignments, checkpointing each.

    A failed assignment does not stop the batch. Partial assignments are tried
    again on the next invocation; successful retail observations remain reusable.
    """
    import fcntl
    from market_load import CONUS_STATES

    jobs_file = Path(jobs_file) if jobs_file else PROJECT_DIR / "clean_jobs.csv"
    jobs = pd.read_csv(jobs_file)
    locations = jobs[["City", "State", "Latitude", "Longitude"]].copy()
    locations["City"] = locations["City"].map(normalize_assignment_city)
    locations["State"] = locations["State"].astype(str).str.strip().str.upper()
    for col in ("Latitude", "Longitude"):
        locations[col] = pd.to_numeric(locations[col], errors="coerce")
    locations = locations[
        locations.State.isin(CONUS_STATES)
        & locations.Latitude.between(-90, 90)
        & locations.Longitude.between(-180, 180)
    ].drop_duplicates()
    if locations.duplicated(["City", "State"]).any():
        raise ValueError("Conflicting coordinates for an assignment; review clean_jobs.csv.")
    if limit is not None and limit < 1:
        raise ValueError("Batch limit must be positive.")

    # Advisory lock is released even if the process is interrupted.
    with (PROJECT_DIR / ".location_batch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A location enrichment batch is already running.") from None
        status = load_batch_status()
        processed = 0
        for _, row in locations.iterrows():
            same = (
                status.Assignment_City.map(normalize_assignment_city) == row.City
            ) & (status.Assignment_State == row.State)
            previous = status.loc[same]
            reusable = False
            if not refresh and not previous.empty:
                last = previous.iloc[-1]
                same_coordinates = (
                    pd.notna(last.Assignment_Latitude) and pd.notna(last.Assignment_Longitude)
                    and abs(float(last.Assignment_Latitude) - row.Latitude) < 0.000001
                    and abs(float(last.Assignment_Longitude) - row.Longitude) < 0.000001
                )
                if same_coordinates and last.Model_Version == MODEL_VERSION:
                    if last.Status == "No candidates":
                        reusable = True
                    elif last.Status == "Complete":
                        cached = cached_assignment_rows(load_location_cache(), row.City, row.State, row.Latitude, row.Longitude)
                        reusable = (len(cached) == last.Candidate_Count and not cached.empty
                                    and cached.Retail_Status.eq("Available").all())
            if reusable:
                print(f"Cached: {row.City}, {row.State}", flush=True)
                continue
            if limit is not None and processed >= limit:
                break
            print(f"Enriching: {row.City}, {row.State}", flush=True)
            record = {
                "Assignment_City": row.City, "Assignment_State": row.State,
                "Assignment_Latitude": row.Latitude, "Assignment_Longitude": row.Longitude,
                "Model_Version": MODEL_VERSION, "Candidate_Count": 0,
                "Retail_Unavailable": 0, "Error": None,
            }
            try:
                results = discover_support_cities(row.City, row.State, row.Latitude, row.Longitude,
                                                  refresh_retail=refresh)
                missing = int(results.Retail_Status.ne("Available").sum())
                partial = missing > 0 or results.Demographics_Status.eq("Unavailable").any()
                record.update(Candidate_Count=len(results), Retail_Unavailable=missing,
                              Status="No candidates" if results.empty else "Partial" if partial else "Complete")
            except Exception as error:
                # Store only the class, never request URLs or credential-bearing messages.
                record.update(Status="Failed", Error=type(error).__name__)
            record["Updated_UTC"] = datetime.now(timezone.utc).isoformat()
            remaining = status.loc[~same]
            new = pd.DataFrame([record])
            status = new if remaining.empty else pd.concat([remaining, new], ignore_index=True)
            atomic_save_csv(status.reindex(columns=BATCH_COLUMNS), BATCH_STATUS_FILE)
            processed += 1
            print(f"  {record['Status']}: {record['Candidate_Count']} candidates; "
                  f"{record['Retail_Unavailable']} retail lookups unavailable", flush=True)
        return status


def create_empty_location_cache():
    """
    Create empty cache with expected schema.
    """

    return pd.DataFrame(
        columns=CACHE_COLUMNS
    )


def load_location_cache():
    """Load observations and migrate legacy caches without inventing data."""
    if not LOCATION_CACHE_FILE.exists():
        return create_empty_location_cache()
    cache = pd.read_csv(LOCATION_CACHE_FILE)
    return cache.reindex(columns=CACHE_COLUMNS)


def save_location_cache(cache_df):
    """
    Save support-city observations.
    """

    cache_df = cache_df.copy()

    for column in CACHE_COLUMNS:

        if column not in cache_df.columns:
            cache_df[column] = None

    cache_df = cache_df[
        CACHE_COLUMNS
    ]

    atomic_save_csv(cache_df, LOCATION_CACHE_FILE)


def find_cached_location(
    assignment_city,
    assignment_state,
):
    """
    Find highest-scoring cached support city
    for an assignment.
    """

    cache = load_location_cache()

    if cache.empty:
        return None

    city = (
        str(assignment_city)
        .strip()
        .upper()
    )

    state = (
        str(assignment_state)
        .strip()
        .upper()
    )

    matches = cache[
        (
            cache[
                "Assignment_City"
            ]
            .astype(str)
            .str.upper()
            == city
        )
        &
        (
            cache[
                "Assignment_State"
            ]
            .astype(str)
            .str.upper()
            == state
        )
    ]

    if matches.empty:
        return None

    matches = matches.sort_values(
        "Support_City_Score",
        ascending=False,
        na_position="last",
    )

    return (
        matches
        .iloc[0]
        .to_dict()
    )


# =========================================================
# BASELINE REFERENCE
# =========================================================

def get_baseline_profile():
    """
    Return conceptual New Braunfels benchmark.
    """

    retail = (
        calculate_retail_support(
            starbucks=True,
            target=True,
            chick_fil_a=True
            
        )
    )

    return {
        "City": BASELINE_CITY,
        "State": BASELINE_STATE,
        "Zip": BASELINE_ZIP,

        "Latitude":
            BASELINE_LATITUDE,

        "Longitude":
            BASELINE_LONGITUDE,

        "Target_Population":
            TARGET_CITY_POPULATION,

        "Target_Growth":
            TARGET_POPULATION_GROWTH,

        **retail,
    }


# =========================================================
# MAIN EXECUTION
# =========================================================

def main():
    """
    Execute the location-data pipeline.

    Current responsibilities:
        1. Load existing location cache
        2. Report current cache status
        3. Prepare location reference data for use by
           Assignment Explorer

    Support-city discovery and cache population will be
    added here as the enrichment pipeline is developed.
    """

    print()
    print("=" * 70)
    print("ASSIGNMENT EXPLORER — LOCATION DATA")
    print("=" * 70)

    # -----------------------------------------------------
    # Baseline
    # -----------------------------------------------------

    baseline = get_baseline_profile()

    print()
    print("Family-support baseline:")
    print(
        f"  {baseline['City']}, "
        f"{baseline['State']} "
        f"{baseline['Zip']}"
    )

    print(
        f"  Target population: "
        f"{baseline['Target_Population']:,}"
    )

    print(
        f"  Target growth: "
        f"{baseline['Target_Growth']:.1%}"
    )

    print(
        f"  Retail benchmark: "
        f"{baseline['Retail_Score']:.0%}"
    )

    # -----------------------------------------------------
    # Model configuration
    # -----------------------------------------------------

    print()
    print("Support-city model:")

    print(
        f"  Search radius: "
        f"{CITY_SEARCH_RADIUS_MILES} miles"
    )

    print(
        f"  Retail weight:      "
        f"{SUPPORT_WEIGHT_RETAIL:.0%}"
    )

    print(
        f"  Population weight:  "
        f"{SUPPORT_WEIGHT_POPULATION:.0%}"
    )

    print(
        f"  Growth weight:      "
        f"{SUPPORT_WEIGHT_GROWTH:.0%}"
    )

    print(
        f"  Distance weight:    "
        f"{SUPPORT_WEIGHT_DISTANCE:.0%}"
    )

    # -----------------------------------------------------
    # Location cache
    # -----------------------------------------------------

    cache = load_location_cache()

    print()
    print("Location cache:")

    if cache.empty:

        print(
            "  No cached support-city "
            "evaluations found."
        )

    else:

        assignment_count = (
            cache[
                [
                    "Assignment_City",
                    "Assignment_State",
                ]
            ]
            .drop_duplicates()
            .shape[0]
        )

        support_city_count = (
            cache[
                [
                    "Support_City",
                    "Support_State",
                ]
            ]
            .drop_duplicates()
            .shape[0]
        )

        print(
            f"  Cached records: "
            f"{len(cache):,}"
        )

        print(
            f"  Assignment locations: "
            f"{assignment_count:,}"
        )

        print(
            f"  Support cities: "
            f"{support_city_count:,}"
        )

    # -----------------------------------------------------
    # Complete
    # -----------------------------------------------------

    print()
    print(
        "Location reference model ready."
    )

    print("=" * 70)
    print()


## Test: 
# =========================================================
# RETAIL DEVELOPMENT TEST
# =========================================================

def test_retail_lookup():

    print()
    print("=" * 70)
    print("OPENSTREETMAP RETAIL TEST")
    print("=" * 70)

    print()
    print("Testing New Braunfels, TX...")
    print()

    result = lookup_retail_support(
        latitude=BASELINE_LATITUDE,
        longitude=BASELINE_LONGITUDE,
    )

    for key, value in result.items():
        print(
            f"{key}: {value}"
        )

    print()
    print("=" * 70)

def test_fort_leavenworth():
    """Run the live discovery, demographics, retail, scoring and cache pipeline."""
    results = discover_support_cities(
        "FORT LEAVENWORTH", "KS", 39.3547, -94.9216,
    )
    if results.empty:
        raise RuntimeError("No Fort Leavenworth support-city candidates found.")
    print(results[[
        "Support_City", "Support_State", "Support_Distance_Miles",
        "Population", "Population_Growth", "Retail_Status", "Retail_Points",
        "Available_Weight", "Support_City_Score",
    ]].to_string(index=False))
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-fort-leavenworth", action="store_true")
    parser.add_argument("--batch", action="store_true", help="Resume unique CONUS assignment enrichment")
    parser.add_argument("--jobs-file", type=Path)
    parser.add_argument("--limit", type=int, help="Maximum uncached assignments this run")
    parser.add_argument("--refresh", action="store_true", help="Refresh completed assignments and retail data")
    args = parser.parse_args()
    if args.batch:
        summary = run_location_batch(args.jobs_file, args.limit, args.refresh)
        print(summary["Status"].value_counts().to_string())
    elif args.test_fort_leavenworth:
        test_fort_leavenworth()
    else:
        main()
