from pathlib import Path
from html import escape
from market_load import attach_cached_support
SCHOOL_DISPLAY_FIELDS = ["School_Name", "School_Enrollment", "School_Enrollment_Year", "School_Arts", "School_Arts_Range", "School_Sciences", "School_Sciences_Range", "School_College_Prep", "School_SAT", "School_SAT_Year", "School_SAT_Status", "School_SAT_Source", "School_AP_Courses", "School_Dual_Enrollment", "School_Offerings_Year", "School_Status", "School_Selection_Status", "School_Source"]
import json
import streamlit as st
import pandas as pd
import plotly.graph_objects as go


# # SECURITY CONFIG FOR STREAMLIT
# if not st.user.is_logged_in:
#     st.title("Please log in")
#     if st.button("Log in with Google"):
#         st.login()
#     st.stop()

# # Everything below only runs for authenticated users
# st.title("Dashboard")
# st.write(f"Signed in as {st.user.name} ({st.user.email})")

# if st.button("Log out"):
#     st.logout()

# # OAuth/settings converted to a Python config + a minimal allowlist-based auth shim
# # Replace authorized emails with your team list.
# auth = {
#     "redirect_uri": "http://localhost:8501/oauth2callback",
#     "cookie_secret": "a-strong-randomly-generated-secret",
#     "client_id": "xxx",
#     "client_secret": "xxx",
#     "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
# }

# AUTHORIZED_EMAILS = {
#     "jason.stisser@gmail.com",
#     "kelle.stisser@gmail.com",
# }

# # Lightweight in-app authentication wrapper (email allowlist).
# # This creates st.user, st.login() and st.logout() hooks the rest of the app expects.
# if "private_auth" not in st.session_state:
#     st.session_state.private_auth = {"email": None, "name": None}

# class _SimpleUser:
#     @property
#     def is_logged_in(self):
#         return st.session_state.private_auth["email"] is not None

#     @property
#     def email(self):
#         return st.session_state.private_auth["email"]

#     @property
#     def name(self):
#         return st.session_state.private_auth["name"]

# def _login():
#     # simple email-based allowlist login (replace or extend with OAuth as needed)
#     email = st.text_input("Email", key="__auth_email")
#     if st.button("Log in", key="__auth_button"):
#         if email and email.lower() in AUTHORIZED_EMAILS:
#             st.session_state.private_auth["email"] = email.lower()
#             st.session_state.private_auth["name"] = email.split("@", 1)[0]
#             st.experimental_rerun()
#         else:
#             st.error("Unauthorized — contact an administrator to gain access.")

# def _logout():
#     st.session_state.private_auth["email"] = None
#     st.session_state.private_auth["name"] = None
#     st.experimental_rerun()

# # Attach to streamlit namespace so existing checks (st.user.is_logged_in, st.login(), st.logout()) work.
# st.user = _SimpleUser()
# st.login = _login
# st.logout = _logout


# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="Officer Marketplace",
    page_icon="🗺️",
    layout="wide"
)

st.title("Officer Marketplace")
st.caption("CONUS job opportunities")



# =========================================================
# LOAD DATA
# =========================================================
#
# OPTION A:
# If you've already saved your cleaned dataframe:
#
# df = pd.read_csv("clean_jobs.csv")
#
# OPTION B:
# Read your cleaned Excel output:
#
# df = pd.read_excel("clean_jobs.xlsx")
#
# For now, point this to whichever file contains the
# dataframe we just created.
# =========================================================

PROJECT_DIR = Path(__file__).resolve().parent


@st.cache_data
def load_data(jobs_modified, cache_modified, batch_modified, community_modified, school_modified):
    # File timestamps invalidate the UI cache as the enrichment batch progresses.
    jobs = pd.read_csv(PROJECT_DIR / "clean_jobs.csv", dtype={"Zip": str})
    if "UIC Description" not in jobs.columns:
        jobs["UIC Description"] = "Unavailable — reload marketplace export"
    return attach_cached_support(jobs, include_current=True)


def file_version(name):
    path = PROJECT_DIR / name
    return path.stat().st_mtime_ns if path.exists() else 0


st.button("Refresh results")
if not (PROJECT_DIR / "clean_jobs.csv").exists():
    st.info("Load the marketplace export before opening this view.")
    st.stop()
df = load_data(*(file_version(name) for name in (
    "clean_jobs.csv", "location_cache.csv", "location_batch_status.csv", "community_cache.csv", "school_cache.csv",
)))


# =========================================================
# CONUS FILTER
# =========================================================

CONUS_STATES = {
    'AL','AR','AZ','CA','CO','CT','DE','FL','GA','ID','IL','IN','IA','KS',
    'KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
    'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT',
    'VT','VA','WA','WV','WI','WY','DC'
}


current_df = df[df.Assignment_Kind.eq('Current')].copy()
show_current = st.sidebar.checkbox('Show current NBTX / Canyon HS baseline', value=True)

map_df = df[
    df['State'].isin(CONUS_STATES)
].copy()


# Remove anything that cannot actually be mapped
map_df = map_df.dropna(
    subset=['Latitude', 'Longitude', 'Opportunity_Score']
)


# =========================================================
# SIDEBAR
# =========================================================

st.sidebar.header("Filters")

grade_options = sorted(map_df['Grade'].dropna().unique())

selected_grades = st.sidebar.multiselect(
    "Job Grade",
    options=grade_options,
    default=grade_options
)


filtered_df = map_df[
    map_df['Grade'].isin(selected_grades)
].copy()


min_score = st.sidebar.slider(
    "Minimum Opportunity Score",
    min_value=0,
    max_value=100,
    value=0,
    step=5
)

filtered_df = filtered_df[
    filtered_df['Opportunity_Score'] >= min_score
]


support_filter = st.sidebar.selectbox(
    "Support-city data", ["All jobs", "Evaluated", "Partial data", "Not evaluated"],
)
if support_filter != "All jobs":
    filtered_df = filtered_df[filtered_df.Support_Status == support_filter]


airport_options = sorted(filtered_df["Airport_Code"].dropna().unique())
airport_filter = st.sidebar.selectbox("Nearest international airport", ["All airports", "Unavailable / pending"] + airport_options)
if airport_filter == "Unavailable / pending":
    filtered_df = filtered_df[filtered_df.Airport_Code.isna()]
elif airport_filter != "All airports":
    filtered_df = filtered_df[filtered_df.Airport_Code == airport_filter]
if st.sidebar.checkbox("Limit distance to airport", value=False):
    airport_limit = st.sidebar.slider("Maximum airport distance (straight-line miles)", 0, 300, 75, 5)
    filtered_df = filtered_df[filtered_df.Airport_Distance_Miles.notna() & (filtered_df.Airport_Distance_Miles <= airport_limit)]
internet_filter = st.sidebar.selectbox("Internet screening", ["All communities", "Regional screen met", "Below screening threshold", "Unknown / pending"])
if internet_filter == "Unknown / pending":
    filtered_df = filtered_df[~filtered_df.Internet_Status.isin(["Regional screen met", "Below screening threshold"])]
elif internet_filter != "All communities":
    filtered_df = filtered_df[filtered_df.Internet_Status == internet_filter]

st.sidebar.subheader("Map layers")
show_support_cities = st.sidebar.checkbox("Support cities", value=True)
show_capitals = st.sidebar.checkbox("State capitals", value=True)
show_airports = st.sidebar.checkbox("International airports", value=True)


# =========================================================
# SUMMARY METRICS
# =========================================================

col1, col2, col3, col4 = st.columns(4)

col1.metric(
    "CONUS Jobs",
    len(filtered_df)
)

col2.metric(
    "Locations",
    filtered_df[['City', 'State']]
    .drop_duplicates()
    .shape[0]
)

col3.metric(
    "Average Opportunity",
    f"{filtered_df['Opportunity_Score'].mean():.1f}"
    if len(filtered_df) else "—"
)

col4.metric(
    "High Opportunity Jobs",
    len(filtered_df[filtered_df['Opportunity_Score'] >= 80])
)


st.caption(
    "Support-city scores compare nearby communities with the New Braunfels benchmark. "
    "They use retail (40%), population (25%), growth (15%), and straight-line distance (20%). "
    "Missing components are excluded and the remaining weights rescaled."
)
locations_summary = filtered_df[["City", "State", "Support_Status"]].drop_duplicates()
st.caption(
    f"Support-city coverage: {locations_summary.Support_Status.eq('Evaluated').sum()} evaluated, "
    f"{locations_summary.Support_Status.eq('Partial data').sum()} partial, "
    f"{(~locations_summary.Support_Status.isin(['Evaluated', 'Partial data'])).sum()} pending or unavailable."
)


def school_hover(row):
    def number(key):
        v=row.get(key)
        return f"{float(v):g}" if pd.notna(v) else "Unavailable"
    return (f"<br>School: {escape(str(row.get('School_Name', 'Unavailable')))}"
            f"<br>Enrollment: {number('School_Enrollment')} ({escape(str(row.get('School_Enrollment_Year', '')))} )"
            f"<br>Arts / STEM supported minimums: {number('School_Arts')} / {number('School_Sciences')} (1–10)"
            f"<br>SAT college-prep proxy: {number('School_College_Prep')} / 10"
            f"<br>{escape(str(row.get('School_Status', 'Awaiting research')))}")


def support_hover(row):
    if pd.isna(row.Support_City):
        return "<br>Support city: " + escape(str(row.Support_Status))
    score = f"{row.Support_City_Score:.1f}" if pd.notna(row.Support_City_Score) else "Unavailable"
    airport = (f"{escape(str(row.Airport_Code))}: {row.Airport_Distance_Miles:.1f} mi from support city"
               if pd.notna(row.Airport_Distance_Miles) else "Unavailable / pending")
    tech = f"{row.Tech_Environment_Score:.1f}" if pd.notna(row.Tech_Environment_Score) else "Unavailable"
    return (f"<br>Nearest international airport: {airport}"
            f"<br>Internet/tech proxy: {tech}; {escape(str(row.Internet_Status))}"
            f"<br>Support city: {escape(str(row.Support_City))}, {escape(str(row.Support_State))}"
            f"<br>Support score: {score} ({escape(str(row.Support_Status))})")


# =========================================================
# MAP
# =========================================================

st.subheader("Marketplace Opportunity Map")

st.caption(
    "Each circle represents one job offer. "
    "Larger circles indicate higher Opportunity Scores. "
    "Small red dots mark selected support cities; gold stars mark state capitals; blue diamonds mark international airports. "
    "Hover over a reference marker for details. Market layers follow the job filters; the current baseline is controlled by its own checkbox."
)


# ---------------------------------------------------------
# Marker sizing
#
# Plotly marker size is diameter in pixels.
# Map the 0–100 score into a visually useful range.
# ---------------------------------------------------------

MIN_MARKER_SIZE = 7
MAX_MARKER_SIZE = 28

filtered_df['Marker_Size'] = (
    MIN_MARKER_SIZE
    + (
        filtered_df['Opportunity_Score'] / 100
    ) * (MAX_MARKER_SIZE - MIN_MARKER_SIZE)
)


# ---------------------------------------------------------
# Slightly separate jobs sharing identical coordinates
#
# Otherwise all 13 Leavenworth jobs would sit directly
# on top of one another and look like a single job.
#
# This creates a tiny deterministic visual offset.
# It does NOT change the underlying geographic data.
# ---------------------------------------------------------

filtered_df['Map_Latitude'] = filtered_df['Latitude']
filtered_df['Map_Longitude'] = filtered_df['Longitude']


for _, group in filtered_df.groupby(['Latitude', 'Longitude']):

    if len(group) <= 1:
        continue

    indices = group.index.tolist()

    offsets = [
        (i - (len(indices) - 1) / 2) * 0.025
        for i in range(len(indices))
    ]

    for idx, offset in zip(indices, offsets):
        filtered_df.loc[idx, 'Map_Longitude'] += offset


# ---------------------------------------------------------
# Hover text
# ---------------------------------------------------------

filtered_df['Hover_Text'] = (
    "<b>" + filtered_df['Duty Title'].astype(str) + "</b>"
    + "<br>"
    + filtered_df['City'].astype(str)
    + ", "
    + filtered_df['State'].astype(str)

    + "<br>Command: "
    + filtered_df['UIC Description'].fillna('Unavailable').astype(str).map(escape)

    + "<br><br>Grade: "
    + filtered_df['Grade'].astype(str)

    + "<br>Opportunity Score: "
    + filtered_df['Opportunity_Score'].round(1).astype(str)

    + "<br>Interested Officers: "
    + filtered_df['Interested_Officers'].astype(int).astype(str)

    + "<br>Popularity: "
    + (filtered_df['Normalized_Popularity'] * 100).round(1).astype(str)
    + "%"

    + "<br>Jobs within 30 miles: "
    + filtered_df['Nearby_Jobs'].astype(int).astype(str)
)


if not filtered_df.empty:
    filtered_df["Hover_Text"] += filtered_df.apply(support_hover, axis=1) + filtered_df.apply(school_hover, axis=1)


# ---------------------------------------------------------
# Build map
# ---------------------------------------------------------

fig = go.Figure()


fig.add_trace(
    go.Scattergeo(

        lon=filtered_df['Map_Longitude'],
        lat=filtered_df['Map_Latitude'],

        mode='markers',
        name="Job offers",
        showlegend=True,

        text=filtered_df['Hover_Text'],

        hovertemplate=(
            "%{text}"
            "<extra></extra>"
        ),

        marker=dict(
            size=filtered_df['Marker_Size'],

            # Use opportunity score for marker intensity
            color=filtered_df['Opportunity_Score'],

            colorscale='Viridis',

            cmin=0,
            cmax=100,

            opacity=0.70,

            line=dict(
                width=0.5,
                color='white'
            ),

            colorbar=dict(
                title="Opportunity"
            )
        )
    )
)


def format_observation(value, spec=".1f"):
    return format(float(value), spec) if pd.notna(value) else "Unavailable"


def add_reference_layers(figure, jobs, support=True, capitals=True):
    """Add one marker per selected support city/capital at its true coordinates.

    Keep assignment-specific scores in hover entries, since the same support
    city can have different distances and data coverage for different assignments.
    """
    if support:
        candidates = jobs.dropna(subset=["Support_City", "Support_Latitude", "Support_Longitude"])
        points = []
        keys = ["Support_City", "Support_State", "Support_Latitude", "Support_Longitude"]
        for (city, state, latitude, longitude), group in candidates.groupby(keys, sort=True):
            lines = [f"<b>{escape(str(city))}, {escape(str(state))}</b>", "Selected support city"]
            assignments = group.drop_duplicates(["City", "State", "Latitude", "Longitude"])
            for _, row in assignments.iterrows():
                lines.extend([
                    f"<br><b>For {escape(str(row.City))}, {escape(str(row.State))}</b>",
                    f"Support score: {format_observation(row.Support_City_Score)} / 100",
                    f"Distance: {format_observation(row.Support_Distance_Miles)} miles (straight line)",
                    f"Population: {format_observation(row.Population, ',.0f')}",
                    f"Population growth: {format_observation(row.Population_Growth, '.1%')}",
                ])
                for retailer in ("Starbucks", "Target", "Chick-fil-A"):
                    presence = row[retailer]
                    known = row.Retail_Status == "Available" and pd.notna(presence)
                    found = known and str(presence).lower() in ("true", "1", "1.0")
                    label = "Within radius" if found else "Not found within radius" if known else "Unavailable"
                    distance = row[f"{retailer}_Distance_Miles"]
                    nearest = f"; nearest observed {float(distance):.1f} mi" if known and pd.notna(distance) else ""
                    lines.append(f"{retailer}: {label}{nearest}")
                lines.extend([
                    f"Airport: {escape(str(row.Airport_Code)) if pd.notna(row.Airport_Code) else 'Unavailable'} — {format_observation(row.Airport_Distance_Miles)} mi",
                    f"Internet coverage: {format_observation(row.Internet_Coverage_Pct)}% (county population proxy)",
                    f"Tech environment: {format_observation(row.Tech_Environment_Score)} — {escape(str(row.Tech_Environment_Status))}",
                    f"Internet requirement: {escape(str(row.Internet_Requirement))}",
                    f"Data: {escape(str(row.Support_Status))}; retail: {escape(str(row.Retail_Status))}",
                    f"Score coverage: {format_observation(row.Available_Weight, '.0%')}",
                ])
            lines.append(school_hover(group.iloc[0]))
            points.append((latitude, longitude, "<br>".join(lines)))
        if points:
            figure.add_trace(go.Scattergeo(
                lat=[p[0] for p in points], lon=[p[1] for p in points],
                text=[p[2] for p in points], mode="markers", name="Support cities",
                showlegend=True, hovertemplate="%{text}<extra></extra>",
                marker=dict(size=6, symbol="circle", color="#E64B35", opacity=1,
                            line=dict(width=1, color="white")),
            ))
    if capitals:
        points = []
        valid = jobs.dropna(subset=["State_Capital", "Capital_Latitude", "Capital_Longitude"])
        for (capital, latitude, longitude), group in valid.groupby(
            ["State_Capital", "Capital_Latitude", "Capital_Longitude"], sort=True,
        ):
            states = ", ".join(sorted(group.State.unique()))
            label = "National capital" if states == "DC" else "State capital"
            lines = [f"<b>{escape(str(capital))}, {escape(states)}</b>", label]
            for _, row in group.drop_duplicates(["City", "State", "Latitude", "Longitude"]).iterrows():
                lines.append(f"{escape(str(row.City))}, {escape(str(row.State))}: "
                             f"{format_observation(row.Capital_Distance_Miles)} miles (straight line)")
            lines.append(school_hover(group.iloc[0]))
            points.append((latitude, longitude, "<br>".join(lines)))
        if points:
            figure.add_trace(go.Scattergeo(
                lat=[p[0] for p in points], lon=[p[1] for p in points],
                text=[p[2] for p in points], mode="markers", name="State capitals",
                showlegend=True, hovertemplate="%{text}<extra></extra>",
                marker=dict(size=11, symbol="star", color="#F2B701",
                            line=dict(width=1, color="#4A3820")),
            ))


def add_airport_layer(figure, jobs):
    airports = jobs.dropna(subset=["Airport_Code", "Airport_Latitude", "Airport_Longitude"])
    points = []
    for (code, latitude, longitude), group in airports.groupby(["Airport_Code", "Airport_Latitude", "Airport_Longitude"], sort=True):
        first = group.iloc[0]
        lines = [f"<b>{escape(str(first.Airport_Name))} ({escape(str(code))})</b>",
                 "Reported scheduled international passenger service",
                 f"DOT reporting period: {escape(str(first.Airport_Service_Period))}"]
        for _, row in group.drop_duplicates(["Support_City", "Support_State", "Support_Latitude", "Support_Longitude"]).iterrows():
            lines.append(f"{escape(str(row.Support_City))}, {escape(str(row.Support_State))}: "
                         f"{format_observation(row.Airport_Distance_Miles)} miles (straight line)")
        points.append((latitude, longitude, "<br>".join(lines)))
    if points:
        figure.add_trace(go.Scattergeo(lat=[p[0] for p in points],lon=[p[1] for p in points],text=[p[2] for p in points],
            mode="markers",name="International airports",showlegend=True,hovertemplate="%{text}<extra></extra>",
            marker=dict(size=9,symbol="diamond",color="#1677D2",line=dict(width=1,color="white"))))


if show_current and not current_df.empty:
    current = current_df.iloc[0]
    fig.add_trace(go.Scattergeo(lat=[current.Latitude], lon=[current.Longitude],
        mode="markers", name="Current — NBTX", marker=dict(size=10, symbol="diamond", color="#8756B3"),
        text=["Current assignment — New Braunfels city center (not home/work address)" + support_hover(current) + school_hover(current)],
        hovertemplate="%{text}<extra></extra>"))
reference_jobs = pd.concat([filtered_df, current_df], ignore_index=True) if show_current else filtered_df
add_reference_layers(fig, reference_jobs, show_support_cities, show_capitals)
if show_airports:
    add_airport_layer(fig, reference_jobs)


# =========================================================
# MAP STYLE
# =========================================================

fig.update_geos(

    scope='usa',

    projection_type='albers usa',

    showland=True,

    showlakes=True,

    showsubunits=True,

    subunitcolor="gray",

    countrycolor="gray"
)


fig.update_layout(

    height=700,
    showlegend=True,
    legend=dict(orientation="h", y=1.03, x=0, itemsizing="constant"),
    uirevision="marketplace-map",

    margin=dict(
        l=0,
        r=0,
        t=10,
        b=0
    ),

    geo=dict(
        bgcolor='rgba(0,0,0,0)'
    )
)


st.plotly_chart(
    fig,
    use_container_width=True
)


# =========================================================
# JOB TABLE
# =========================================================

st.subheader("Job Offers and Current Baseline")
st.caption("School scores are provisional screening aids. Arts/STEM numbers are supported minimums; the ranges show unresolved evidence. Blank means unknown, not zero. CHS baseline: arts 8, STEM 7, college prep 4. Do not reject a school based on an incomplete minimum score. Enrollment: 2024–25; national course data: 2021–22. Professional ratings for Current remain blank.")


detail_df = pd.concat([current_df, filtered_df], ignore_index=True) if show_current else filtered_df.copy()
display_df = detail_df[
    [
        'Assignment_Kind',
        'Duty Title',
        'UIC Description',
        'City',
        'State',
        'Zip',
        'Grade',
        'Interested_Officers',
        'Normalized_Popularity',
        'Nearby_Jobs',
        'Opportunity_Score',
        'Support_City', 'Support_State', 'Support_City_Score',
        'Support_Distance_Miles', 'Support_Status', 'Retail_Status',
        'Retail_Points', 'Available_Weight',
        'Airport_Name', 'Airport_Code', 'Airport_Distance_Miles',
        'Internet_Coverage_Pct', 'Internet_Status', 'Tech_Community_Score',
        'Tech_Environment_Score', 'Tech_Environment_Status', 'Community_County', 'Internet_Requirement'
    ] + SCHOOL_DISPLAY_FIELDS
].copy()


display_df["Support_City"] = display_df["Support_City"].where(
    display_df["Support_City"].notna(),
    display_df["Support_Status"].replace({"Not evaluated": "Pending enrichment"}),
)

display_df = display_df.sort_values(
    ['Assignment_Kind', 'Opportunity_Score'],
    ascending=[True, False]
)


st.dataframe(
    display_df,
    width="stretch",
    hide_index=True,

    column_config={
        "School_Arts": st.column_config.NumberColumn("Arts minimum / 10", format="%.0f"),
        "School_Sciences": st.column_config.NumberColumn("STEM minimum / 10", format="%.0f"),
        "School_College_Prep": st.column_config.NumberColumn("SAT prep proxy / 10", format="%.0f"),
        "School_Enrollment": st.column_config.NumberColumn("School enrollment", format="%d"),
        "School_Source": st.column_config.LinkColumn("School source"),
        "School_SAT_Source": st.column_config.LinkColumn("SAT source"),
        "Airport_Name": st.column_config.TextColumn("Nearest Qualifying US International Airport"),
        "Airport_Code": st.column_config.TextColumn("Airport"),
        "Airport_Distance_Miles": st.column_config.NumberColumn("Airport Distance (mi, straight line)", format="%.1f"),
        "Internet_Coverage_Pct": st.column_config.NumberColumn("County Internet Coverage (%)", format="%.1f"),
        "Internet_Status": st.column_config.TextColumn("Internet Screen"),
        "Tech_Community_Score": st.column_config.NumberColumn("Tech Community Proxy", format="%.1f"),
        "Tech_Environment_Score": st.column_config.NumberColumn("Internet / Tech (70/30)", format="%.1f"),
        "Tech_Environment_Status": st.column_config.TextColumn("Tech Score Status"),
        "Community_County": st.column_config.TextColumn("Proxy County"),
        "Internet_Requirement": st.column_config.TextColumn("Internet Requirement"),
        "Support_City": st.column_config.TextColumn("Support City"),
        "Support_State": st.column_config.TextColumn("Support State"),
        "Support_City_Score": st.column_config.NumberColumn("Support Score", format="%.1f"),
        "Support_Distance_Miles": st.column_config.NumberColumn("Support Distance (mi)", format="%.1f"),
        "Support_Status": st.column_config.TextColumn("Support Data"),
        "Retail_Status": st.column_config.TextColumn("Retail Data"),
        "Retail_Points": st.column_config.NumberColumn("Retail Indicators", format="%d"),
        "Available_Weight": st.column_config.NumberColumn("Score Coverage", format="percent"),

        "UIC Description": st.column_config.TextColumn(
            "UIC Description (Command)", width="large"
        ),

        "Duty Title": st.column_config.TextColumn(
            "Position"
        ),

        "Interested_Officers": st.column_config.NumberColumn(
            "Interested"
        ),

        "Normalized_Popularity": st.column_config.NumberColumn(
            "Normalized Popularity",
            format="percent"
        ),

        "Nearby_Jobs": st.column_config.NumberColumn(
            "Nearby Jobs"
        ),

        "Opportunity_Score": st.column_config.ProgressColumn(
            "Opportunity",
            min_value=0,
            max_value=100,
            format="%.1f"
        )
    }
)

st.caption("Pending enrichment means the batch has not evaluated that assignment yet. "
           "Use Refresh results to load newly saved results. Low or missing population growth does not exclude a city.")

with st.expander("Airport and internet/tech methodology"):
    st.write("Airport distance is from the selected support-city center, in straight-line miles. This lookup considers US airports only. Airports qualify through a recurring-service proxy: at least one carrier/foreign-airport route with scheduled passengers in 3+ months and 1,000+ passengers in the saved DOT reporting year; routes and schedules may have changed.")
    st.write("Internet/Tech = 70% internet coverage + 30% tech-community score. These are regional proxies for the county containing the support-city center, not city-wide or home-level measurements.")
    st.write("Internet uses the county population percentage with advertised fixed terrestrial service at 100/20 Mbps or above. A provisional 95% coverage screen is required; below-threshold or unknown internet leaves the combined score blank. Missing tech also leaves it blank. Every home still requires provider, speed and reliability verification.")
    st.write("Tech community uses ACS residents in computer/math occupations. The score reaches 100 at twice the national workforce share. Worker counts and data dates appear below. This is a community proxy, not a metro job-market assessment.")
    st.write("Fiber availability and actual uptime are not measured by this data. These scores do not change the existing Support City or Opportunity rankings.")
    st.markdown("Sources: [DOT international passengers](https://data.transportation.gov/Aviation/International_Report_Passengers/xgub-n9bw), [OurAirports](https://ourairports.com/data/), [FCC coverage](https://c2h.fcc.gov/bh-data.html), [Census occupations](https://api.census.gov/data/2024/acs/acs5/groups/C24010.html).")

st.subheader("Support City Details")
choices = detail_df[["City", "State"]].drop_duplicates()
labels = {f"{row.City}, {row.State}": (row.City, row.State) for _, row in choices.iterrows()}
if labels:
    selected = st.selectbox("Assignment location", list(labels))
    city, state = labels[selected]
    best = detail_df[(detail_df.City == city) & (detail_df.State == state)].iloc[0]
    st.markdown("**School screening**")
    st.write(f"{best.School_Name} — {format_observation(best.School_Enrollment, ',.0f')} students ({best.School_Enrollment_Year})")
    st.write(f"Arts minimum: {format_observation(best.School_Arts)} / 10; STEM minimum: {format_observation(best.School_Sciences)} / 10; SAT prep: {format_observation(best.School_College_Prep)} / 10")
    st.caption(f"Arts range {best.School_Arts_Range}; STEM range {best.School_Sciences_Range}. SAT {format_observation(best.School_SAT, '.0f')} ({best.School_SAT_Year if pd.notna(best.School_SAT_Year) else 'cohort unavailable'}).")
    st.caption(f"SAT lookup: {best.School_SAT_Status}. {best.School_SAT_Notes if pd.notna(best.School_SAT_Notes) else ''}")
    st.caption(str(best.School_Selection_Status) + ". " + str(best.School_Notes))
    st.write(f"Reported AP courses: {format_observation(best.School_AP_Courses, '.0f')}; dual enrollment: {best.School_Dual_Enrollment if pd.notna(best.School_Dual_Enrollment) else 'Unavailable'} (course data {best.School_Offerings_Year if pd.notna(best.School_Offerings_Year) else 'unavailable'}).")
    if pd.notna(best.School_Sources):
        with st.expander("School evidence sources"):
            for source in json.loads(best.School_Sources):
                st.link_button("Open source", source)
    if pd.isna(best.Support_City):
        st.info(f"Support-city information: {best.Support_Status}.")
    else:
        score = f"{best.Support_City_Score:.1f}" if pd.notna(best.Support_City_Score) else "Unavailable"
        st.write(f"**{best.Support_City}, {best.Support_State}** — score {score}; "
                 f"{best.Support_Distance_Miles:.1f} miles from the assignment.")
        if best.Support_Status == "Partial data":
            st.info("Some candidate data is unavailable. The ranking may change after another lookup.")
        if pd.notna(best.Available_Weight) and best.Available_Weight < 1:
            st.caption(f"This score uses {best.Available_Weight:.0%} of the model's original weight; missing components were excluded.")
        if pd.notna(best.Population):
            st.write(f"Population: {int(best.Population):,}")
        st.write("Population growth: " + (f"{best.Population_Growth:.1%}" if pd.notna(best.Population_Growth) else "Unavailable"))
        stores = []
        for retailer in ("Starbucks", "Target", "Chick-fil-A"):
            presence = best[retailer]
            known = best.Retail_Status == "Available" and pd.notna(presence)
            found = str(presence).lower() in ("true", "1", "1.0") if known else False
            distance = best[f"{retailer}_Distance_Miles"]
            stores.append({"Retailer": retailer, "Within search radius": "Yes" if known and found else "No" if known else "Unavailable",
                           "Nearest observed distance (mi)": distance})
        st.dataframe(pd.DataFrame(stores), hide_index=True, width="stretch")
        st.caption("Search radii: Starbucks 5 miles; Target and Chick-fil-A 7 miles. Store absence reflects available OpenStreetMap data.")
        st.markdown("**Airport access**")
        if pd.notna(best.Airport_Distance_Miles):
            st.write(f"{best.Airport_Name} ({best.Airport_Code}) — {best.Airport_Distance_Miles:.1f} straight-line miles from the support city.")
            st.caption(f"Scheduled international passenger service reported: {best.Airport_Service_Period}. Confirm current routes when planning travel.")
        else:
            st.info("Airport information is unavailable or awaiting enrichment.")
        st.markdown("**Internet and tech community — 70/30**")
        st.write(f"Regional proxy: {best.Community_County if pd.notna(best.Community_County) else 'Unavailable'}")
        st.write(f"Internet coverage at 100/20 Mbps: {format_observation(best.Internet_Coverage_Pct)}% of county population")
        st.write(f"Tech workers: {format_observation(best.Tech_Workers, ',.0f')}; workforce share: {format_observation(best.Tech_Worker_Share, '.1%')}; national concentration multiple: {format_observation(best.Tech_Location_Quotient, '.2f')}")
        st.write(f"Internet/tech score: {format_observation(best.Tech_Environment_Score)} — {best.Tech_Environment_Status}")
        st.info(str(best.Internet_Requirement) + ". County coverage does not establish service or reliability at a particular house.")
        st.caption(f"Internet data: {best.Internet_Data_Vintage}; tech data: {best.Tech_Data_Vintage}.")
        st.link_button("Check the FCC broadband map", "https://broadbandmap.fcc.gov/")

else:
    st.info("No jobs match the selected filters.")
