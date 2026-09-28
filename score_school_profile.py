"""Deterministic scorer for evidence extracted under school_search_standard.json.
Usage: python3 score_school_profile.py evidence.json [evidence2.json ...]
Search and source interpretation are separate; this module makes no network requests.
"""
import json
import math
import sys
from pathlib import Path

FIELDS = json.loads(Path(__file__).with_name('school_search_standard.json').read_text())['fields']
COUNT_MAX = {'arts_family_count':6,'arts_progression_count':6,'arts_capstone_families':6,'science_core_count':3,'science_advanced_prep_count':4,'science_college_lab_count':4}
COUNTS = set(COUNT_MAX) | {'arts_advanced_courses'}

def validated(record):
    fields = record.get('evidence', {})
    for key, item in fields.items():
        if key not in FIELDS:
            raise ValueError(f'Unknown field: {key}')
        val = item.get('value')
        if item.get('status') not in ('verified','carried_forward_reviewed','unknown','conflict'):
            raise ValueError(f'{key}: invalid status')
        if item.get('status') in ('unknown','conflict') and val is not None:
            raise ValueError(f'{key}: unknown/conflict must have null value')
        if val is None:
            continue
        if not item.get('source_url') or not item.get('observation_period') or not item.get('evidence_note'):
            raise ValueError(f'{key}: populated evidence needs source, period and note')
        if not isinstance(item.get('complete'), bool):
            raise ValueError(f'{key}: complete must be boolean')
        if item.get('evidence_scope') not in ('campus','district','unknown'):
            raise ValueError(f'{key}: invalid evidence scope')
        if key == 'sat_mean':
            if isinstance(val,bool) or not isinstance(val,(int,float)) or not math.isfinite(val) or not 400 <= val <=1600:
                raise ValueError('Invalid SAT mean')
        elif key in COUNTS:
            if isinstance(val,bool) or not isinstance(val,int) or val < 0 or val > COUNT_MAX.get(key,1000):
                raise ValueError(f'{key}: invalid count')
        elif not isinstance(val,bool):
            raise ValueError(f'{key}: expected boolean')
    return fields

def test(fields,key,minimum=None):
    item=fields.get(key,{})
    if item.get('evidence_scope')!='campus' or item.get('status') not in ('verified','carried_forward_reviewed'):
        return None
    val=item.get('value')
    if val is None:return None
    passed=val >= minimum if minimum is not None else val
    return True if passed else (False if item.get('complete') else None)

def conjunction(*states):
    if False in states:return False
    return None if None in states else True

def ordinal(fields, requirements, floor_key):
    states={}
    accumulated=True
    for level, conditions in requirements.items():
        accumulated=conjunction(accumulated,*(test(fields,*condition) for condition in conditions))
        states[level]=accumulated
    verified=[level for level,state in states.items() if state is True]
    ceiling=max([1]+[level for level,state in states.items() if state is not False])
    score=max(verified) if verified else (1 if test(fields,floor_key) is False else None)
    return {'score':score, 'possible_range':[score or 1,ceiling], 'status':'unknown' if score is None else ('provisional' if ceiling>score else 'supported'), 'unresolved_fields':sorted({c[0] for conditions in requirements.values() for c in conditions if test(fields,*c) is None})}

ARTS={2:[('arts_intro',)],3:[('arts_progression_count',1)],4:[('arts_family_count',2)],5:[('arts_family_count',3)],6:[('arts_family_count',4)],7:[('arts_progression_count',2)],8:[('arts_advanced_courses',2),('arts_public_product',)],9:[('arts_sustained_delivery',),('arts_capstone_families',2)],10:[('arts_external_partner',),('arts_practical_access',)]}
SCIENCE={2:[('science_general',)],3:[('science_foundations',)],4:[('science_core_count',3)],5:[('science_advanced_prep_count',2)],6:[('science_programming_pathway',)],7:[('science_applied_progression',)],8:[('science_college_lab_count',3),('science_calculus',)],9:[('science_mentored_capstone',)],10:[('science_post_intro_college',),('science_practical_access',)]}

def score(record):
    fields=validated(record)
    arts=ordinal(fields,ARTS,'arts_intro')
    science=ordinal(fields,SCIENCE,'science_general')
    sat=fields.get('sat_mean',{})
    value=sat.get('value') if sat.get('evidence_scope')=='campus' and sat.get('status') in ('verified','carried_forward_reviewed') else None
    raw=None if value is None else max(1,min(10,1+9*(value-800)/600))
    prep=None if raw is None else math.floor(raw+0.5)
    result={'model_version':'1.0-screening','school':record['school'],'arts':arts,'sciences':science,'college_prep':{'score':prep,'sat_mean':value,'cohort':sat.get('observation_period'),'participation':record.get('sat_participation'),'status':'unknown' if prep is None else 'test_taker_proxy'},'delta_from_chs':{'arts':None if arts['score'] is None else arts['score']-8,'sciences':None if science['score'] is None else science['score']-7,'college_prep':None if prep is None else prep-4},'overall_score':None,'decision':'Discussion aid; differences in evidence coverage may affect provisional ratings'}
    return result

if __name__=='__main__':
    for filename in sys.argv[1:]:
        print(json.dumps(score(json.loads(Path(filename).read_text())),indent=2))
