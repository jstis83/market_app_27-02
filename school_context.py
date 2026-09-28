"""Offline school profiles keyed by support city. Rebuild with --score.
School evidence is separate from assignment data to support future city overrides.
"""
import argparse
import json
import os
from pathlib import Path
import pandas as pd
from score_school_profile import score

ROOT=Path(__file__).resolve().parent
PROFILES=ROOT/'school_data'/'profiles.json'
CACHE=ROOT/'school_cache.csv'
FIELDS=['School_Name','School_NCES_ID','School_Enrollment','School_Enrollment_Year','School_Selection_Status','School_Arts','School_Sciences','School_College_Prep','School_Arts_Delta','School_Sciences_Delta','School_College_Prep_Delta','School_SAT','School_SAT_Year','School_Status','School_Notes','School_Source','School_Sources','School_Model','School_Arts_Range','School_Sciences_Range','School_Data_As_Of','School_AP_Courses','School_Dual_Enrollment','School_Offerings_Year','School_SAT_Status','School_SAT_Source','School_SAT_Participation','School_SAT_Notes']

def atomic_csv(df,path):
    temp=path.with_suffix('.tmp.csv')
    df.to_csv(temp,index=False)
    os.replace(temp,path)

def rebuild():
    records=json.loads(PROFILES.read_text())
    rows=[]
    for p in records:
        result=score(p)
        a,s,c=result['arts'],result['sciences'],result['college_prep']
        values=[a['score'],s['score'],c['score']]
        sources=sorted({x['source_url'] for x in p.get('evidence',{}).values() if x.get('source_url')} | {p['enrollment_source']} | {p.get('reported_metrics',{}).get('source_url',p['enrollment_source'])})
        r={'Support_City':p['support_city'],'Support_State':p['support_state'],'School_Name':p['school'],'School_NCES_ID':p.get('nces_id'),'School_Enrollment':p.get('enrollment'),'School_Enrollment_Year':p.get('enrollment_year'),'School_Selection_Status':p.get('selection_status','Provisional'),'School_Arts':values[0],'School_Sciences':values[1],'School_College_Prep':values[2],'School_Arts_Delta':result['delta_from_chs']['arts'],'School_Sciences_Delta':result['delta_from_chs']['sciences'],'School_College_Prep_Delta':result['delta_from_chs']['college_prep'],'School_SAT':c['sat_mean'],'School_SAT_Year':c['cohort'],'School_Status':'Provisional — partial evidence' if any(v is None for v in values) else 'Provisional screening','School_Notes':p.get('notes','')+' SAT participation is confidence context; blank scores mean unverified; see SAT lookup status.','School_Source':p.get('profile_url') or p['enrollment_source'],'School_Sources':json.dumps(sources),'School_Model':result['model_version'],'School_Arts_Range':str(a['possible_range']),'School_Sciences_Range':str(s['possible_range']),'School_Data_As_Of':p.get('as_of','2026-09-27')}
        r.update(School_AP_Courses=p.get('reported_metrics',{}).get('AP_courses'), School_Dual_Enrollment=p.get('reported_metrics',{}).get('dual_enrollment'), School_Offerings_Year=p.get('reported_metrics',{}).get('offerings_year'))
        r.update(School_SAT_Status=p.get('sat_lookup',{}).get('status', 'Verified source' if c['sat_mean'] is not None else 'Not yet researched'), School_SAT_Source=p.get('evidence',{}).get('sat_mean',{}).get('source_url'), School_SAT_Participation=json.dumps(p.get('sat_participation')), School_SAT_Notes=p.get('sat_lookup',{}).get('note',''))
        rows.append(r)
    frame=pd.DataFrame(rows)
    if frame.duplicated(['Support_City','Support_State']).any():raise ValueError('Duplicate city school profile')
    atomic_csv(frame,CACHE)
    return frame

def current_assignment(df, config=None):
    """Add the current assignment after professional scoring; preserve enrichment."""
    defaults = {'City':'NEW BRAUNFELS', 'State':'TX', 'Zip':'78130',
                'Latitude':29.703, 'Longitude':-98.1245}
    defaults.update(config or {})
    base = df.copy()
    if 'Assignment_Kind' not in base:
        base['Assignment_Kind'] = 'Market'
    existing = base[base.Assignment_Kind.eq('Current')]
    row = existing.iloc[-1].to_dict() if not existing.empty else {col:None for col in base.columns}
    base = base[base.Assignment_Kind.ne('Current')].copy()
    row.update(defaults)
    row.update(JO='CURRENT-NBTX', Assignment_ID='current-nbtx', Assignment_Kind='Current',
               **{'Duty Title':'Current assignment — New Braunfels baseline',
                  'UIC Description':'Current command — details pending',
                  'Current_Internet_Down_Mbps':1297, 'Current_Internet_Up_Mbps':1361,
                  'Current_Internet_Notes':'User-reported reliable AT&T service, measured at modem.'})
    if 'Assignment_ID' not in base:
        base['Assignment_ID'] = base['JO'].astype(str) if 'JO' in base else base.index.astype(str)
    return pd.DataFrame.from_records(base.to_dict('records') + [row])


def attach_school_context(df,cache_path=None):
    base=df.drop(columns=FIELDS,errors='ignore').copy()
    path=Path(cache_path or CACHE)
    if path.exists():
        schools=pd.read_csv(path,dtype={'School_NCES_ID':str})
        for field in FIELDS:
            if field not in schools:schools[field]=None
        base=base.merge(schools[['Support_City','Support_State']+FIELDS],on=['Support_City','Support_State'],how='left',validate='many_to_one')
    else:
        for field in FIELDS:base[field]=None
    base['School_Status']=base['School_Status'].fillna(base['Support_City'].notna().map({True:'Awaiting school research',False:'No support city — school not assessed'}))
    return base

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--score',action='store_true')
    args=parser.parse_args()
    frame=rebuild()
    print(f'Scored {len(frame)} city profiles. Arts: {frame.School_Arts.notna().sum()}, sciences: {frame.School_Sciences.notna().sum()}, SAT proxy: {frame.School_College_Prep.notna().sum()}.')
