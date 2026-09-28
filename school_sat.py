"""Repeatable SAT imports from reviewed official, school-level source tables.

Run: python school_sat.py --refresh
This is not a universal web search. Providers require reviewed identity/column
mappings. Unconfigured schools remain explicitly 'Not yet researched'.
"""
import argparse
import io
import json
import math
import os
from pathlib import Path
import time
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
PROFILES = ROOT / 'school_data/profiles.json'
RAW = ROOT / 'school_data/sat_sources'
SC_URL = 'https://www.ed.sc.gov/data/test-scores/national-assessments/sat/sat-2025-schools/'
CO_URL = 'https://ed.cde.state.co.us/fs/resource-manager/view/326112fb-532d-4197-9902-a36104cd5573'
SC_SCHOOLS = {'450072001202':('0201003','South Aiken High'), '450390201065':('4301024','Sumter High School')}

def sat_value(value):
    value = float(value)
    if not math.isfinite(value) or not 400 <= value <= 1600:
        raise ValueError('Invalid or suppressed SAT mean')
    return value

def one(frame):
    if len(frame) != 1:
        raise ValueError('School identity or cohort is ambiguous')
    return frame.iloc[0]

def parse_sc(data, school_id, school_name):
    frame = pd.read_excel(io.BytesIO(data), header=None)
    if '2025 Graduating Seniors' not in str(frame.iloc[0,0]) or 'graduating seniors in 2025' not in str(frame.iloc[1,0]):
        raise ValueError('Unrecognized SC cohort')
    if 'Total' not in str(frame.iloc[2,8]):
        raise ValueError('Changed SC column layout')
    ids = frame[0].astype(str).str.replace(r'\.0$','',regex=True).str.zfill(7)
    row = one(frame[ids.eq(school_id) & frame[1].eq(school_name)])
    return sat_value(row[8]), {'tested_count':int(row[3]), 'percentage':float(row[5])*100,
                              'denominator_note':'State-reported active grade-12 enrollment; latest SAT per student.'}

def parse_co(data):
    frame = pd.read_excel(io.BytesIO(data), header=None)
    if '2026 PSAT/SAT' not in str(frame.iloc[3,0]):
        raise ValueError('Unrecognized Colorado cohort')
    row = one(frame[frame[0].eq('SCHOOL') & pd.to_numeric(frame[3],errors='coerce').eq(6937)
                    & frame[4].eq('Pine Creek High School') & frame[5].eq('Total Score')
                    & frame[6].eq('SAT Grade 11')])
    # Use reported total; rounded section means need not sum to that total.
    return sat_value(row[13]), {'tested_count':int(str(row[8]).replace(',','')), 'percentage':float(row[10]),
                               'denominator_note':'Spring grade-11 SAT, not graduating-senior cohort.'}

def fetch(url, name, refresh=False):
    RAW.mkdir(exist_ok=True)
    path = RAW / name
    if path.exists() and not refresh:
        return path.read_bytes()
    for attempt in range(2):
        try:
            response = requests.get(url, timeout=(10,40), headers={'User-Agent':'AssignmentExplorer school research'})
            response.raise_for_status()
            data = response.content
            if not data.startswith(b'PK'):
                raise ValueError('Expected spreadsheet, received another response')
            temp = path.with_suffix('.tmp')
            temp.write_bytes(data)
            os.replace(temp,path)
            return data
        except requests.RequestException:
            if attempt == 1:
                raise RuntimeError('Official SAT source request failed') from None
            time.sleep(2)
    raise RuntimeError('SAT lookup failed')

def refresh_profiles(refresh=False):
    profiles = json.loads(PROFILES.read_text())
    downloaded = {}
    for profile in profiles:
        existing = profile.get('evidence',{}).get('sat_mean',{}).get('value')
        nces = profile['nces_id']
        provider = 'SC' if nces in SC_SCHOOLS else 'CO' if nces == '080192001645' else None
        if provider is None:
            profile.setdefault('sat_lookup', {'status':'Verified source' if existing is not None else 'Not yet researched',
                                               'note':'No automated provider configured; blank is not evidence of unavailable scores.'})
            continue
        url = SC_URL if provider == 'SC' else CO_URL
        try:
            if provider not in downloaded:
                downloaded[provider] = fetch(url, provider.lower()+'.xlsx', refresh)
                time.sleep(1)
            value, participation = parse_sc(downloaded[provider], *SC_SCHOOLS[nces]) if provider == 'SC' else parse_co(downloaded[provider])
            period = '2025 graduates' if provider == 'SC' else '2026 spring, grade 11'
            profile.setdefault('evidence',{})['sat_mean'] = dict(value=value, source_url=url, observation_period=period,
                evidence_note='Official campus-level total SAT mean; school identity and cohort matched explicitly.',
                evidence_scope='campus', complete=True, status='verified')
            profile['sat_participation'] = participation
            profile['sat_lookup'] = {'status':'Verified source','note':'SC 2025 used because 2026 workbook heading conflicts with its cohort note.' if provider=='SC' else 'School-day grade-11 SAT; cohort differs from graduate reports.'}
        except (RuntimeError,ValueError,KeyError,IndexError,TypeError) as error:
            profile['sat_lookup'] = {'status':'Refresh failed — prior score retained' if existing is not None else 'Lookup failed',
                                      'note':type(error).__name__ + '; no score replaced with zero.'}
        print(profile['support_city'] + ': ' + profile['sat_lookup']['status'])
    temp = PROFILES.with_suffix('.tmp')
    temp.write_text(json.dumps(profiles,indent=2))
    os.replace(temp,PROFILES)
    from school_context import rebuild
    return rebuild()

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    result=refresh_profiles(args.refresh)
    print(f'Verified SAT coverage: {result.School_SAT.notna().sum()} / {len(result)} profiles.')
