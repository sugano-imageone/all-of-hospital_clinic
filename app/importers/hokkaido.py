"""Hokkaido user policy approved 2026-10-15.

Blank total beds -> 0 / unbedded clinic; >=20 hospital, <=19 clinic;
all listed institutions -> active. Preserve the original blank and policy provenance.
Bed-type counts remain unknown. These are operating rules, not source observations.
"""
import hashlib
import re
from datetime import datetime,timezone
from app.domain import institution_code,classify,normalize,BEDS
from app.importers.kyushu import japanese_date,owner_group

def parse_hokkaido(blob,filename,sheet,rows,retrieved_at=None):
    header=' '.join(normalize(v) for r in rows[:7] for v in r)
    if '郡市区別医療機関一覧表' not in header or '医科' not in header:raise ValueError('北海道の表構造不一致')
    ref=next((japanese_date(v) for r in rows[:7] for v in r if '現在' in normalize(v)),None)
    created=next((japanese_date(v) for r in rows[:7] for v in r if '作成' in normalize(v)),None)
    if not ref or not created:raise ValueError('北海道の資料日付不明')
    retrieved_at=retrieved_at or datetime.now(timezone.utc).isoformat();records=[]
    for i,row in enumerate(rows,1):
        if len(row)<34:continue
        if not normalize(row[2]).isdigit() or not row[6] or not row[10]:continue
        if normalize(row[27])!='医':raise ValueError('北海道の医科区分不一致')
        codes=normalize(row[6]).splitlines()
        if len(codes)>2 or (len(codes)==2 and not re.fullmatch(r'\([\d, -]+\)',codes[1])):
            raise ValueError('北海道の補足コード構造不明')
        code=institution_code(codes[0],1)
        bed=normalize(row[30])
        if bed and not bed.isdigit():raise ValueError('北海道の病床数不明表記')
        total=int(bed) if bed else 0
        address=normalize(row[13]);postal=re.search(r'〒(\d{3})[-ー−](\d{4})',address)
        address=re.sub(r'〒\d{3}[-ー−]\d{4}\s*','',address).replace('\n',' ')
        local=address.removeprefix('北海道');city=re.match(r'(.+?市.+?区|.+?市|.+?郡.+?[町村]|.+?[町村])',local)
        r={'code':code,'prefecture_code':1,'prefecture':'北海道','bureau':'北海道厚生局',
          'municipality':city[1] if city else None,'name':normalize(row[10]),'postal_code':'-'.join(postal.groups()) if postal else None,
          'address':address,'phone':normalize(row[17]),'owner':normalize(row[20]),'owner_type':owner_group(normalize(row[20])),
          'manager':normalize(row[23]),'departments':[], 'total_beds':total,
          **{k+'_beds':None for k in BEDS},**classify(total),
          'status':'現存','needs_review':False,'designation_date':None,'renewal_date':None,
          'change_reason':None,'opening_classification':'判定保留','reference_date':ref,
          'file_created_date':created,'retrieved_at':retrieved_at,'source_file':filename,
          'raw':{'sheet':sheet,'start_row':i,'cells':list(row),
                 'policy':'hokkaido-user-rule-2026-10-15-v1',
                 'total_beds_origin':'source' if bed else 'user_rule_blank_as_zero',
                 'kind_origin':'user_rule_threshold_20',
                 'status_origin':'user_rule_listed_as_active',
                 'source_total_beds':int(bed) if bed else None}}
        records.append(r)
    if not records:raise ValueError('北海道の医科施設なし')
    source={'filename':filename,'sheet':sheet,'prefecture_code':1,'prefecture':'北海道','bureau':'北海道厚生局',
            'sha256':hashlib.sha256(blob).hexdigest(),'parser_version':'hokkaido-user-rule-2026-10-15-v1',
            'reference_date':ref,'file_created_date':created,'retrieved_at':retrieved_at,'record_count':len(records)}
    return records,[source]
