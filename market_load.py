"""
market_load.py

IPPS-A Officer Marketplace data-processing pipeline.

Workflow:
    1. Find newest IPPS-A marketplace Excel export
    2. Load job offers
    3. Clean/normalize locations
    4. Geocode unique CONUS locations
    5. Parse marketplace popularity
    6. Estimate current market participation
    7. Normalize popularity
    8. Calculate grade fit
    9. Calculate geographic job density
   10. Calculate Opportunity Score
   11. Export clean_jobs.csv for Streamlit

Run from the project directory:

    python market_load.py

Then launch the Streamlit app:

    streamlit run market_app.py
"""

from pathlib import Path
from school_context import attach_school_context, current_assignment

# Current location is an additional comparison option, not a scored job offer.
CURRENT_ASSIGNMENT = {"City":"NEW BRAUNFELS", "State":"TX", "Zip":"78130",
                      "Latitude":29.703, "Longitude":-98.1245}
from math import radians, sin, cos, sqrt, atan2
import re

import numpy as np
import pandas as pd
from community_context import attach_community_context

from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# Use this helper script to load capital data 
from location_data import (
    get_capital_context, load_location_cache, load_batch_status,
    normalize_assignment_city, cached_assignment_rows, atomic_save_csv,
)



# =========================================================
# CONFIGURATION
# =========================================================

# Directory containing this script
PROJECT_DIR = Path(__file__).resolve().parent

# Automatically use newest IPPS-A export
INPUT_PATTERN = "PS_IP_JO_MRKT_HOME_*.xlsx"

# Output consumed by app.py
OUTPUT_FILE = PROJECT_DIR / "clean_jobs.csv"


# ---------------------------------------------------------
# OPPORTUNITY MODEL
# ---------------------------------------------------------

RADIUS_MILES = 30

WEIGHT_LOCATION = 0.40
WEIGHT_COMPETITION = 0.35
WEIGHT_GRADE = 0.25

GRADE_SCORE = {
    "O4": 1.00,
    "O5": 0.80,
    "O3": 0.30,
}


# ---------------------------------------------------------
# CONUS
# ---------------------------------------------------------

CONUS_STATES = {
    "AL", "AR", "AZ", "CA", "CO", "CT", "DE", "FL", "GA",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "DC",
}


# =========================================================
# LOCATION CLEANUP
# =========================================================

# IPPS-A location labels that either do not geocode well
# or should be converted to a useful geographic label.

LOCATION_FIXES = {
    "INVALID LOCATION - PLACEHOLDER": ("FORT EUSTIS", "VA"),
    "ARLINGTON CMTRY": ("ARLINGTON", "VA"),
    "RICHMOND QM DEPOT": ("RICHMOND", "VA"),
    "FT BENNING": ("FORT BENNING", "GA"),
    "FT RILEY": ("FORT RILEY", "KS"),
    "PRESIDIO OF MONT": ("MONTEREY", "CA"),
    "WHITE SAND MSL RG": ("WHITE SANDS MISSILE RANGE", "NM"),
    "KIRTLAND AFB": ("KIRTLAND AIR FORCE BASE", "NM"),
    "FT SILL": ("FORT SILL", "OK"),
}


# Known ZIPs for locations where Nominatim may return
# inconsistent or missing postal codes.

ZIP_OVERRIDES = {
    ("AUSTIN", "TX"): "78701",
    ("ARLINGTON", "VA"): "22211",
    ("RICHMOND", "VA"): "23219",
    ("FORT BENNING", "GA"): "31905",
    ("FORT RILEY", "KS"): "66442",
    ("MONTEREY", "CA"): "93944",
    ("WHITE SANDS MISSILE RANGE", "NM"): "88002",
    ("KIRTLAND AIR FORCE BASE", "NM"): "87117",
    ("FORT SILL", "OK"): "73503",
    ("CHANTILLY", "VA"): "20151",
    ("REDSTONE ARSENAL", "AL"): "35898",
    ("FT BRAGG", "NC"): "28310",
    ("FORT EUSTIS", "VA"): "23604",
}


# =========================================================
# FILE HANDLING
# =========================================================

def find_latest_market_file():
    """
    Find the newest IPPS-A marketplace Excel export
    in the project directory.
    """

    files = list(PROJECT_DIR.glob(INPUT_PATTERN))

    if not files:
        raise FileNotFoundError(
            "\nNo IPPS-A marketplace spreadsheet found.\n"
            f"Expected a file matching:\n"
            f"    {INPUT_PATTERN}\n"
            f"in:\n"
            f"    {PROJECT_DIR}\n"
        )

    # Most recently modified matching file
    latest_file = max(
        files,
        key=lambda path: path.stat().st_mtime
    )

    return latest_file


# =========================================================
# LOAD DATA
# =========================================================

def load_market_data(file_path):
    """
    Load the IPPS-A Excel export.
    """

    df = pd.read_excel(file_path)

    # Clean column-name whitespace just in case
    df.columns = df.columns.str.strip()

    return df


# =========================================================
# LOCATION NORMALIZATION
# =========================================================

def normalize_locations(df):
    """
    Standardize City and State values before geocoding.
    """

    df = df.copy()

    df["City"] = (
        df["City"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["State"] = (
        df["State"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    for city, replacement in LOCATION_FIXES.items():

        new_city, new_state = replacement

        mask = df["City"] == city

        df.loc[mask, "City"] = new_city
        df.loc[mask, "State"] = new_state

    return df


# =========================================================
# GEOCODING
# =========================================================

def build_geocoder():
    """
    Configure Nominatim with rate limiting.
    """

    geolocator = Nominatim(
        user_agent="ippsa-officer-marketplace-map"
    )

    return RateLimiter(
        geolocator.geocode,
        min_delay_seconds=1.1,
        max_retries=1,
        error_wait_seconds=2,
        swallow_exceptions=True,
    )


def lookup_location(city, state, geocode):
    """
    Geocode one unique City/State combination.

    OCONUS locations intentionally receive no coordinates
    because the current Streamlit MVP displays CONUS only.
    """

    # ---------------------------------------------
    # OCONUS
    # ---------------------------------------------

    if state not in CONUS_STATES:

        return pd.Series({
            "Zip": "OCONUS",
            "Latitude": np.nan,
            "Longitude": np.nan,
        })

    # ---------------------------------------------
    # CONUS
    # ---------------------------------------------

    known_zip = ZIP_OVERRIDES.get(
        (city, state)
    )

    query = f"{city}, {state}, USA"

    loc = geocode(
        query,
        addressdetails=True,
        exactly_one=True,
        country_codes="us",
    )

    # Geocoder failed
    if loc is None:

        return pd.Series({
            "Zip": known_zip if known_zip else "UNKNOWN",
            "Latitude": np.nan,
            "Longitude": np.nan,
        })

    # ---------------------------------------------
    # ZIP
    # ---------------------------------------------

    if known_zip:

        zipcode = known_zip

    else:

        address = loc.raw.get(
            "address",
            {}
        )

        postcode = str(
            address.get(
                "postcode",
                ""
            )
        )

        match = re.search(
            r"\b\d{5}\b",
            postcode
        )

        if match:

            zipcode = match.group(0)

        else:

            display_name = loc.raw.get(
                "display_name",
                ""
            )

            match = re.search(
                r"\b\d{5}\b",
                display_name
            )

            zipcode = (
                match.group(0)
                if match
                else "UNKNOWN"
            )

    return pd.Series({
        "Zip": zipcode,
        "Latitude": loc.latitude,
        "Longitude": loc.longitude,
    })


def geocode_locations(df):
    """
    Geocode each unique City/State only once,
    then merge results back onto all job offers.
    """

    df = df.copy()

    locations = (
        df[
            ["City", "State"]
        ]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    print(
        f"Unique locations: {len(locations)}"
    )

    prior = pd.read_csv(OUTPUT_FILE, dtype={"Zip": str}) if OUTPUT_FILE.exists() else pd.DataFrame()
    geocode = None
    observations = []
    for _, row in locations.iterrows():
        cached = pd.DataFrame()
        if {"City", "State", "Zip", "Latitude", "Longitude"}.issubset(prior.columns):
            cached = prior[
                (prior.City.map(normalize_assignment_city) == normalize_assignment_city(row.City))
                & (prior.State == row.State)
                & prior.Latitude.notna() & prior.Longitude.notna()
            ]
        if not cached.empty:
            observation = cached.iloc[-1][["Zip", "Latitude", "Longitude"]].to_dict()
        else:
            if geocode is None:
                geocode = build_geocoder()
            observation = lookup_location(row.City, row.State, geocode).to_dict()
        observations.append(observation)
    locations = pd.concat([locations, pd.DataFrame(observations, columns=["Zip", "Latitude", "Longitude"])], axis=1)

    # Remove old versions if script is rerun
    df = df.drop(
        columns=[
            "Zip",
            "Latitude",
            "Longitude",
            "Known_Zip",
        ],
        errors="ignore",
    )

    df = df.merge(
        locations,
        on=["City", "State"],
        how="left",
    )

    return df, locations


# =========================================================
# POPULARITY
# =========================================================

def parse_popularity(df):
    """
    Convert IPPS-A strings such as:

        11/93

    into:

        Interested_Officers = 11
        Market_Count        = 93
        Popularity          = 0.1183

    Popularity remains the RAW popularity field because
    app.py currently expects that schema.
    """

    df = df.copy()

    popularity_parts = (
        df["Popularity"]
        .astype(str)
        .str.strip()
        .str.extract(
            r"(?P<Interested_Officers>\d+)"
            r"\s*/\s*"
            r"(?P<Market_Count>\d+)"
        )
    )

    df["Interested_Officers"] = pd.to_numeric(
        popularity_parts["Interested_Officers"],
        errors="coerce",
    )

    df["Market_Count"] = pd.to_numeric(
        popularity_parts["Market_Count"],
        errors="coerce",
    )

    df["Popularity"] = (
        df["Interested_Officers"]
        / df["Market_Count"]
    )

    return df


# =========================================================
# NORMALIZED POPULARITY
# =========================================================

def calculate_normalized_popularity(df):
    """
    Estimate current marketplace participation using:

        max interested officers
        -----------------------
        total eligible officers

    Then normalize each JO's raw popularity against that
    estimated participation rate.

    This is a marketplace proxy, not a literal probability.
    """

    df = df.copy()

    max_interested = (
        df["Interested_Officers"]
        .max()
    )

    total_officers = (
        df["Market_Count"]
        .max()
    )

    if (
        pd.notna(total_officers)
        and total_officers > 0
    ):

        participation_rate = (
            max_interested
            / total_officers
        )

    else:

        participation_rate = 0.0

    # Preserve raw value explicitly
    df["Raw_Popularity"] = (
        df["Popularity"]
    )

    if participation_rate > 0:

        df["Normalized_Popularity"] = (
            df["Raw_Popularity"]
            / participation_rate
        ).clip(
            lower=0,
            upper=1,
        )

    else:

        df["Normalized_Popularity"] = 0.0

    return (
        df,
        max_interested,
        total_officers,
        participation_rate,
    )


# =========================================================
# GRADE FIT
# =========================================================

def calculate_grade_score(df):
    """
    Score billet grade relative to an O4 officer.
    """

    df = df.copy()

    df["Grade_Score"] = (
        df["Grade"]
        .astype(str)
        .str.strip()
        .str.upper()
        .map(GRADE_SCORE)
        .fillna(0)
    )

    return df


# =========================================================
# COMPETITION
# =========================================================

def calculate_competition_score(df):
    """
    Higher score = less observed competition.

    Normalized popularity:
        0.00 -> Competition Score 1.00
        0.50 -> Competition Score 0.50
        1.00 -> Competition Score 0.00
    """

    df = df.copy()

    df["Competition_Score"] = (
        1 - df["Normalized_Popularity"]
    )

    return df


# =========================================================
# GEOGRAPHIC JOB DENSITY
# =========================================================

def haversine_miles(
    lat1,
    lon1,
    lat2,
    lon2,
):
    """
    Great-circle distance between two points.
    """

    earth_radius = 3958.8

    lat1 = radians(lat1)
    lon1 = radians(lon1)
    lat2 = radians(lat2)
    lon2 = radians(lon2)

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


def calculate_location_depth(df):
    """
    Count jobs within RADIUS_MILES of every JO.

    OCONUS/unmapped jobs receive a count of 1.
    """

    df = df.copy()

    def count_nearby_jobs(row):

        if (
            pd.isna(row["Latitude"])
            or pd.isna(row["Longitude"])
        ):
            return 1

        count = 0

        for _, other in df.iterrows():

            if (
                pd.isna(other["Latitude"])
                or pd.isna(other["Longitude"])
            ):
                continue

            distance = haversine_miles(
                row["Latitude"],
                row["Longitude"],
                other["Latitude"],
                other["Longitude"],
            )

            if distance <= RADIUS_MILES:
                count += 1

        return count

    df["Nearby_Jobs"] = df.apply(
        count_nearby_jobs,
        axis=1,
    )

    # ---------------------------------------------
    # Normalize location depth
    #
    # Log scaling provides diminishing returns:
    # 1 -> 5 matters more than 20 -> 24.
    # ---------------------------------------------

    max_jobs = df["Nearby_Jobs"].max()

    if max_jobs > 1:

        df["Location_Score"] = (
            np.log1p(
                df["Nearby_Jobs"]
            )
            / np.log1p(max_jobs)
        )

    else:

        df["Location_Score"] = 1.0

    return df, max_jobs


# =========================================================
# OPPORTUNITY SCORE
# =========================================================

def calculate_opportunity_score(df):
    """
    Composite Opportunity Score.

    Higher = more favorable marketplace opportunity.

    This is a decision-support proxy, not a probability
    of assignment.
    """

    df = df.copy()

    df["Opportunity_Score"] = 100 * (

        WEIGHT_LOCATION
        * df["Location_Score"]

        + WEIGHT_COMPETITION
        * df["Competition_Score"]

        + WEIGHT_GRADE
        * df["Grade_Score"]
    )

    df["Opportunity_Score"] = (
        df["Opportunity_Score"]
        .round(1)
    )

    return df


SUPPORT_FIELDS = [
    "Support_City", "Support_State", "Support_Latitude", "Support_Longitude",
    "Support_Distance_Miles", "Support_City_Score", "Population", "Population_Growth",
    "Retail_Status", "Retail_Points", "Retail_Max_Points", "Retail_Score",
    "Starbucks", "Target", "Chick-fil-A", "Starbucks_Distance_Miles",
    "Target_Distance_Miles", "Chick-fil-A_Distance_Miles", "Available_Weight",
    "Demographics_Status", "Support_Status",
]


def attach_cached_support(df, include_current=False):
    """Join the best cached support community without making network requests.

    Opportunity scoring is independent of the support-city model. Incomplete
    candidate evaluations are labeled even when the winning city's data is full.
    """
    if include_current:
        df = current_assignment(df, CURRENT_ASSIGNMENT)
    elif "Assignment_Kind" in df:
        df = df[df.Assignment_Kind.ne("Current")].copy()
    cache = load_location_cache()
    batch = load_batch_status()
    records = []
    for _, row in df[["City", "State", "Latitude", "Longitude"]].drop_duplicates().iterrows():
        result = {field: None for field in SUPPORT_FIELDS}
        result.update(City=row.City, State=row.State, Latitude=row.Latitude, Longitude=row.Longitude)
        result.update(get_capital_context(row.State, row.Latitude, row.Longitude))
        if row.State not in CONUS_STATES:
            result["Support_Status"] = "Outside CONUS"
        elif pd.isna(row.Latitude) or pd.isna(row.Longitude):
            result["Support_Status"] = "Coordinates unavailable"
        else:
            matches = cached_assignment_rows(cache, row.City, row.State, row.Latitude, row.Longitude)
            if not matches.empty:
                best = matches.sort_values("Support_City_Score", ascending=False, na_position="last", kind="stable").iloc[0]
                result.update({field: best.get(field) for field in SUPPORT_FIELDS if field != "Support_Status"})
                partial = matches.Retail_Status.ne("Available").any() or matches.Demographics_Status.eq("Unavailable").any()
                result["Support_Status"] = "Partial data" if partial else "Evaluated"
            else:
                previous = cached_assignment_rows(batch, row.City, row.State, row.Latitude, row.Longitude)
                result["Support_Status"] = "Not evaluated"
                if not previous.empty:
                    result["Support_Status"] = {"No candidates": "No nearby candidates", "Failed": "Lookup failed"}.get(previous.iloc[-1].Status, "Not evaluated")
        records.append(result)
    capital_fields = ["State_Capital", "Capital_Latitude", "Capital_Longitude", "Capital_Distance_Miles"]
    base = df.drop(columns=SUPPORT_FIELDS + capital_fields, errors="ignore")
    reference = pd.DataFrame(records, columns=["City", "State", "Latitude", "Longitude"] + SUPPORT_FIELDS + capital_fields)
    combined = base.merge(reference, on=["City", "State", "Latitude", "Longitude"], how="left", validate="many_to_one")
    combined = attach_community_context(combined)
    if "Assignment_Kind" in combined:
        combined.loc[combined.Assignment_Kind.eq("Current"), "Internet_Requirement"] = "Current service: user-reported reliable AT&T, 1297 Mbps down / 1361 Mbps up at modem. Regional screen shown separately."
    return attach_school_context(combined)


# =========================================================
# QUALITY CHECKS
# =========================================================

def validate_output(df):
    """
    Verify that fields required by app.py exist.
    """

    required_columns = [
        "Duty Title",
        "UIC Description",
        "City",
        "State",
        "Grade",
        "Preference",
        "Interested_Officers",
        "Market_Count",
        "Popularity",
        "Zip",
        "Latitude",
        "Longitude",
        "Nearby_Jobs",
        "Grade_Score",
        "Competition_Score",
        "Location_Score",
        "Opportunity_Score",
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:

        raise ValueError(
            "Output is missing required columns:\n"
            + "\n".join(missing)
        )


# =========================================================
# RUN SUMMARY
# =========================================================

def print_summary(
    source_file,
    df,
    locations,
    max_interested,
    total_officers,
    participation_rate,
    max_jobs,
):
    """
    Print a compact processing report.
    """

    conus_locations = locations[
        locations["State"].isin(
            CONUS_STATES
        )
    ]

    unresolved = conus_locations[
        conus_locations["Latitude"].isna()
        | conus_locations["Longitude"].isna()
    ]

    conus_jobs = df[
        df["State"].isin(
            CONUS_STATES
        )
    ]

    print()
    print("=" * 55)
    print("IPPS-A MARKETPLACE PROCESSING")
    print("=" * 55)

    print(
        f"Source:                 "
        f"{source_file.name}"
    )

    print(
        f"Jobs loaded:            "
        f"{len(df)}"
    )

    print(
        f"CONUS jobs:             "
        f"{len(conus_jobs)}"
    )

    print(
        f"Unique locations:       "
        f"{len(locations)}"
    )

    print(
        f"CONUS locations:        "
        f"{len(conus_locations)}"
    )

    print(
        f"Unresolved CONUS:       "
        f"{len(unresolved)}"
    )

    print(
        f"Market size:            "
        f"{int(total_officers)}"
    )

    print(
        f"Max interested:         "
        f"{int(max_interested)}"
    )

    print(
        f"Est. participation:     "
        f"{participation_rate:.1%}"
    )

    print(
        f"Max nearby jobs:        "
        f"{int(max_jobs)}"
    )

    print(
        f"Output:                 "
        f"{OUTPUT_FILE.name}"
    )

    # Show unresolved locations if any
    if len(unresolved) > 0:

        print()
        print("CONUS LOCATIONS REQUIRING REVIEW:")

        for _, row in unresolved.iterrows():

            print(
                f"  - {row['City']}, "
                f"{row['State']}"
            )

    print("=" * 55)
    print()


# =========================================================
# MAIN PIPELINE
# =========================================================

def main():

    print()
    print("Processing IPPS-A marketplace...")

    # -----------------------------------------------------
    # Find newest spreadsheet
    # -----------------------------------------------------

    source_file = (
        find_latest_market_file()
    )

    print(
        f"Using: {source_file.name}"
    )


    # -----------------------------------------------------
    # Load
    # -----------------------------------------------------

    df = load_market_data(
        source_file
    )

    print(
        f"Loaded {len(df)} job offers."
    )


    # -----------------------------------------------------
    # Normalize locations
    # -----------------------------------------------------

    df = normalize_locations(df)


    # -----------------------------------------------------
    # Geocode
    # -----------------------------------------------------

    df, locations = geocode_locations(
        df
    )


    # -----------------------------------------------------
    # Parse raw popularity
    # -----------------------------------------------------

    df = parse_popularity(df)


    # -----------------------------------------------------
    # Normalize popularity
    # -----------------------------------------------------

    (
        df,
        max_interested,
        total_officers,
        participation_rate,
    ) = calculate_normalized_popularity(
        df
    )


    # -----------------------------------------------------
    # Grade
    # -----------------------------------------------------

    df = calculate_grade_score(
        df
    )


    # -----------------------------------------------------
    # Competition
    # -----------------------------------------------------

    df = calculate_competition_score(
        df
    )


    # -----------------------------------------------------
    # Geographic density
    # -----------------------------------------------------

    df, max_jobs = (
        calculate_location_depth(
            df
        )
    )


    # -----------------------------------------------------
    # Opportunity
    # -----------------------------------------------------

    df = calculate_opportunity_score(
        df
    )


    # -----------------------------------------------------
    # Validate Streamlit schema
    # -----------------------------------------------------

    df = attach_cached_support(df)
    validate_output(df)


    # -----------------------------------------------------
    # Sort output
    #
    # This does not affect Streamlit functionality,
    # but makes the CSV easier to inspect manually.
    # -----------------------------------------------------

    df = df.sort_values(
        "Opportunity_Score",
        ascending=False,
    ).reset_index(drop=True)


    # -----------------------------------------------------
    # Save
    # -----------------------------------------------------

    atomic_save_csv(attach_cached_support(df, include_current=True), OUTPUT_FILE)


    # -----------------------------------------------------
    # Summary
    # -----------------------------------------------------

    print_summary(
        source_file=source_file,
        df=df,
        locations=locations,
        max_interested=max_interested,
        total_officers=total_officers,
        participation_rate=participation_rate,
        max_jobs=max_jobs,
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-support", action="store_true", help="Update the existing export from the local support cache only")
    parser.add_argument("--enrich-current", action="store_true", help="Run the standard location and community research for the current NBTX assignment")
    args = parser.parse_args()
    if args.enrich_current:
        import tempfile
        from location_data import run_location_batch
        from community_context import enrich_communities
        jobs = attach_cached_support(pd.read_csv(OUTPUT_FILE, dtype={"Zip":str}), include_current=True)
        atomic_save_csv(jobs, OUTPUT_FILE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.csv"
            jobs[jobs.Assignment_Kind.eq("Current")].to_csv(path, index=False)
            run_location_batch(path)
        enrich_communities(current_only=True)
        print("Current assignment research finished; see data-status fields for any missing observations.")
    elif args.refresh_support:
        jobs = pd.read_csv(OUTPUT_FILE, dtype={"Zip": str})
        jobs = attach_cached_support(jobs, include_current=True)
        validate_output(jobs)
        atomic_save_csv(jobs, OUTPUT_FILE)
        print(f"Updated cached support information for {len(jobs)} jobs.")
    else:
        main()