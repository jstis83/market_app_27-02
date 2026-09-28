"""Cached airport access and provisional internet/tech community screening.

The Streamlit app only reads the cache. Run this module to enrich selected
support cities; use --refresh to retry county lookups and --refresh-reference
for new DOT, OurAirports, FCC and Census snapshots. No credentials are logged.
"""
from pathlib import Path
import argparse
import json
import math
import time
import pandas as pd
import requests
import location_data as loc

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / 'community_data'
CACHE_FILE = PROJECT_DIR / 'community_cache.csv'
INTERNET_WEIGHT = .70
TECH_WEIGHT = .30
MIN_COVERAGE_PCT = 95.0  # Configurable community-screening threshold, not an FCC reliability standard.
MODEL_VERSION = 'community-v1'
KEYS = ['Support_City','Support_State','Support_Latitude','Support_Longitude']
FIELDS = ['Airport_Name','Airport_Code','Airport_Latitude','Airport_Longitude','Airport_Distance_Miles','Airport_Service_Period','Airport_Status',
          'Community_County','Community_County_FIPS','Internet_Coverage_Pct','Internet_Score','Internet_Status','Internet_Requirement',
          'Tech_Workers','Tech_Worker_Share','Tech_Location_Quotient','Tech_Community_Score','Tech_Status',
          'Tech_Environment_Score','Tech_Environment_Status','Internet_Data_Vintage','Tech_Data_Vintage','Community_Data_Scope','Community_Model']


def safe_get(url, params=None):
    try:
        response=requests.get(url,params=params,timeout=60)
        response.raise_for_status()
        return response
    except requests.RequestException:
        raise RuntimeError('Public reference lookup failed; no credentials logged.') from None


def read_cache():
    if not CACHE_FILE.exists():return pd.DataFrame(columns=KEYS+FIELDS)
    return pd.read_csv(CACHE_FILE,dtype={'Community_County_FIPS':str}).reindex(columns=KEYS+FIELDS)


def attach_community_context(jobs):
    """Offline join; recalculates screening from saved observations, never API calls."""
    base=jobs.drop(columns=FIELDS,errors='ignore')
    cache=read_cache().drop_duplicates(KEYS,keep='last')
    for column in ['Support_Latitude', 'Support_Longitude']:
        base[column] = pd.to_numeric(base[column], errors='coerce')
        cache[column] = pd.to_numeric(cache[column], errors='coerce')
    result=base.merge(cache,on=KEYS,how='left',validate='many_to_one')
    for column in ['Airport_Distance_Miles','Internet_Coverage_Pct','Internet_Score','Tech_Workers','Tech_Worker_Share','Tech_Location_Quotient','Tech_Community_Score','Tech_Environment_Score']:
        result[column]=pd.to_numeric(result[column],errors='coerce')
    for column in ['Airport_Status','Internet_Status','Tech_Status','Tech_Environment_Status']:
        result[column]=result[column].fillna('Pending enrichment')
    result['Internet_Requirement']=result.Internet_Requirement.fillna('Unknown — address verification required')
    return result


def finite(value, minimum=0, maximum=None):
    try:value=float(value)
    except (TypeError,ValueError):return None
    if not math.isfinite(value) or value<minimum or (maximum is not None and value>maximum):return None
    return value


def calculate_tech_environment(coverage_pct, tech_score, coverage_reliable=True):
    """Internet is a gate: missing/low coverage cannot be rescued by tech.

    Even above the county screening threshold, address reliability is unverified.
    Missing tech is never reweighted into a deceptively complete score.
    """
    coverage=finite(coverage_pct,maximum=100) if coverage_reliable else None
    tech=finite(tech_score,maximum=100)
    result={'Internet_Score':coverage,'Tech_Environment_Score':None}
    if coverage is None:
        result.update(Internet_Status='Unknown',Internet_Requirement='Unknown — address verification required',Tech_Environment_Status='Blocked — internet unknown')
    elif coverage<MIN_COVERAGE_PCT:
        result.update(Internet_Status='Below screening threshold',Internet_Requirement='Insufficient regional coverage — verify address',Tech_Environment_Status='Blocked — internet coverage')
    else:
        result.update(Internet_Status='Regional screen met',Internet_Requirement='Address verification required',Tech_Environment_Status='Provisional regional proxy' if tech is not None else 'Incomplete — tech data unavailable')
        if tech is not None:result['Tech_Environment_Score']=round(INTERNET_WEIGHT*coverage+TECH_WEIGHT*tech,1)
    return result


def nearest_airport(latitude,longitude,airports):
    missing={name:None for name in FIELDS if name.startswith('Airport_')}
    missing['Airport_Status']='Unavailable'
    if finite(latitude,minimum=-90,maximum=90) is None or finite(longitude,minimum=-180,maximum=180) is None:return missing
    valid=airports.copy()
    for c in ['latitude_deg','longitude_deg','scheduled_passengers']:
        valid[c]=pd.to_numeric(valid[c],errors='coerce')
    valid=valid[valid.latitude_deg.between(-90,90)&valid.longitude_deg.between(-180,180)&(valid.scheduled_passengers>0)].dropna(subset=['iata_code'])
    if valid.empty:return missing
    valid['distance']=valid.apply(lambda r:loc.haversine_miles(latitude,longitude,r.latitude_deg,r.longitude_deg),axis=1)
    best=valid.sort_values(['distance','iata_code']).iloc[0]
    return dict(Airport_Name=best['name'],Airport_Code=best.iata_code,Airport_Latitude=best.latitude_deg,Airport_Longitude=best.longitude_deg,
                Airport_Distance_Miles=round(best.distance,1),Airport_Service_Period=best.Service_Period,Airport_Status='Reported scheduled international service')


def occupation_values(row):
    total=finite(row.get('C24010_001E'));male=finite(row.get('C24010_008E'));female=finite(row.get('C24010_044E'))
    if total is None or total==0 or male is None or female is None or male+female>total:return None,None
    return male+female,(male+female)/total


def tech_metrics(row,national_share):
    workers,share=occupation_values(row)
    if share is None or not national_share:return dict(Tech_Workers=None,Tech_Worker_Share=None,Tech_Location_Quotient=None,Tech_Community_Score=None,Tech_Status='Unavailable')
    quotient=share/national_share
    # Two times the national concentration receives full proxy credit.
    return dict(Tech_Workers=int(workers),Tech_Worker_Share=share,Tech_Location_Quotient=round(quotient,3),Tech_Community_Score=round(min(quotient/2,1)*100,1),Tech_Status='ACS county resident estimate')


def lookup_county(latitude,longitude):
    data=safe_get('https://geocoding.geo.census.gov/geocoder/geographies/coordinates',{'x':longitude,'y':latitude,'benchmark':'Public_AR_Current','vintage':'Current_Current','layers':'Counties','format':'json'}).json()
    counties=data.get('result',{}).get('geographies',{}).get('Counties',[])
    if len(counties)!=1:raise ValueError('County match unavailable')
    return counties[0]['GEOID'],counties[0]['NAME']


def enrich_communities(refresh=False, current_only=False):
    from market_load import attach_cached_support,OUTPUT_FILE
    airports=pd.read_csv(DATA_DIR/'international_airports.csv')
    broadband=pd.read_csv(DATA_DIR/'broadband_counties.csv',dtype={'fips':str});broadband['fips']=broadband.fips.str.zfill(5)
    tech=pd.read_csv(DATA_DIR/'tech_counties.csv',dtype={'state':str,'county':str});tech['fips']=tech.state.str.zfill(2)+tech.county.str.zfill(3)
    national=pd.read_csv(DATA_DIR/'tech_national.csv').iloc[0]
    _,national_share=occupation_values(national)
    metadata=json.loads((DATA_DIR/'sources.json').read_text())
    jobs=attach_cached_support(pd.read_csv(OUTPUT_FILE,dtype={'Zip':str}), include_current=True)
    target=jobs[jobs.Assignment_Kind.eq("Current")] if current_only else jobs
    cities=target[KEYS].dropna().drop_duplicates()
    # Include Austin as an explicit calibration reference, whether selected or not.
    baseline=pd.DataFrame([dict(zip(KEYS,['Austin','TX',30.2672,-97.7431]))])
    if not current_only:
        cities=pd.concat([cities,baseline],ignore_index=True).drop_duplicates(KEYS)
    cache=read_cache()
    for _,city in cities.iterrows():
        same=(cache[KEYS]==city[KEYS]).all(axis=1)
        old=cache.loc[same]
        record={**{field:None for field in FIELDS},**city.to_dict()}
        record.update(nearest_airport(city.Support_Latitude,city.Support_Longitude,airports))
        try:
            if not refresh and not old.empty and pd.notna(old.iloc[-1].Community_County_FIPS):
                fips=str(old.iloc[-1].Community_County_FIPS).zfill(5);county=old.iloc[-1].Community_County
            else:
                fips,county=lookup_county(city.Support_Latitude,city.Support_Longitude)
                time.sleep(.25)
            record.update(Community_County=county,Community_County_FIPS=fips)
            b=broadband[broadband.fips==fips];t=tech[tech.fips==fips]
            coverage=None;reliable=False
            if not b.empty:
                coverage=finite(b.iloc[0].fixed_100_20_pct,maximum=100)
                reliable=b.iloc[0].fixed_100_20_pct_unreliable=='reliable'
            record['Internet_Coverage_Pct']=coverage if reliable else None
            record.update(tech_metrics(t.iloc[0] if not t.empty else {},national_share))
            record.update(calculate_tech_environment(coverage,record['Tech_Community_Score'],reliable))
        except (RuntimeError,ValueError,KeyError):
            record.update(Tech_Status='Unavailable',**calculate_tech_environment(None,None))
        record.update(Internet_Data_Vintage=metadata['internet_vintage'],Tech_Data_Vintage=metadata['tech_vintage'],Community_Data_Scope=metadata['geography'],Community_Model=MODEL_VERSION)
        cache=pd.DataFrame([*cache.loc[~same].to_dict('records'),record],columns=KEYS+FIELDS)
        loc.atomic_save_csv(cache,CACHE_FILE)
        print(f"{city.Support_City}, {city.Support_State}: airport {record['Airport_Code']}; {record['Tech_Environment_Status']}",flush=True)
    loc.atomic_save_csv(attach_cached_support(jobs, include_current=True), OUTPUT_FILE)
    return cache


def refresh_references():
    import io
    from datetime import datetime,timezone
    DATA_DIR.mkdir(exist_ok=True)
    url='https://data.transportation.gov/resource/xgub-n9bw.json'
    latest=safe_get(url,{'$select':'max(data_dte) as latest'}).json()[0]['latest']
    end=pd.Timestamp(latest);start=end-pd.DateOffset(months=11)
    routes=safe_get(url,{'$select':'usg_apt,fg_apt,carrier,sum(scheduled) as scheduled_passengers,count(distinct data_dte) as active_months',
        '$where':f"data_dte >= '{start.strftime('%Y-%m-%dT00:00:00')}' AND scheduled > 0",
        '$group':'usg_apt,fg_apt,carrier','$limit':50000}).json()
    routes=pd.DataFrame(routes)
    routes['scheduled_passengers']=pd.to_numeric(routes.scheduled_passengers,errors='coerce')
    routes['active_months']=pd.to_numeric(routes.active_months,errors='coerce')
    routes=routes[(routes.active_months>=3)&(routes.scheduled_passengers>=1000)]
    # Recurrence avoids qualifying an airport solely from one-off diversions.
    rows=routes.groupby('usg_apt',as_index=False).scheduled_passengers.sum().to_dict('records')
    all_airports=pd.read_csv(io.StringIO(safe_get('https://davidmegginson.github.io/ourairports-data/airports.csv').text))
    airports=all_airports[(all_airports.iso_country=='US')&all_airports.iata_code.notna()&all_airports.type.isin(['large_airport','medium_airport','small_airport'])].merge(pd.DataFrame(rows).rename(columns={'usg_apt':'iata_code'}),on='iata_code',validate='one_to_one')
    if airports.empty:raise ValueError('No airport references returned')
    airports['Service_Period']=f'{start:%Y-%m} to {end:%Y-%m}'
    loc.atomic_save_csv(airports[['name','iata_code','latitude_deg','longitude_deg','scheduled_passengers','Service_Period']],DATA_DIR/'international_airports.csv')
    broadband=pd.DataFrame(safe_get('https://c2h.fcc.gov/api/crosstabCountyData/getAllCountyData').json())
    loc.atomic_save_csv(broadband[['fips','county','state','fixed_100_20_pct','fixed_100_20_pct_unreliable']],DATA_DIR/'broadband_counties.csv')
    meta=safe_get('https://c2h.fcc.gov/api/variabledata/getAllVariableData').json()
    internet_vintage=next(v['tooltip'] for v in meta if v['data_col']=='fixed_100_20_pct')
    variables='NAME,C24010_001E,C24010_008E,C24010_044E,C24010_001M,C24010_008M,C24010_044M'
    for geography,name in [('county:*','tech_counties.csv'),('us:1','tech_national.csv')]:
        rows=safe_get('https://api.census.gov/data/2024/acs/acs5',{'get':variables,'for':geography,'key':loc.CENSUS_API_KEY}).json()
        loc.atomic_save_csv(pd.DataFrame(rows[1:],columns=rows[0]),DATA_DIR/name)
    (DATA_DIR/'sources.json').write_text(json.dumps(dict(downloaded_utc=datetime.now(timezone.utc).isoformat(),airport_period=airports.Service_Period.iloc[0],airport_qualification='At least one carrier/foreign-airport route with scheduled passengers in 3+ months and 1,000+ passengers over the reporting year; recurring-service proxy',airport_source=url,airport_coordinates_source='https://ourairports.com/data/',internet_source='https://c2h.fcc.gov/bh-data.html',internet_vintage=internet_vintage,tech_source='https://api.census.gov/data/2024/acs/acs5/groups/C24010.html',tech_vintage='2020–2024 ACS 5-year',geography='County containing the support-city center; regional proxy, not city/address data'),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--refresh-reference',action='store_true')
    args=parser.parse_args()
    if args.refresh_reference or not (DATA_DIR/'sources.json').exists():refresh_references()
    enrich_communities(args.refresh)
