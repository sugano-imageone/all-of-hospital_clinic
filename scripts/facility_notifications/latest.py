"""Use receipt date when present; otherwise calculation start (user-approved fallback)."""
import argparse,collections,datetime,functools,itertools,json,re,sqlite3,unicodedata
from pathlib import Path

@functools.lru_cache(maxsize=10000)
def date_key(value):
 s=unicodedata.normalize('NFKC',str(value or '')).strip()
 if not s:return ''
 s=s.replace('元年','1年')
 m=re.search(r'(令和|平成|昭和)\s*(\d+)年\s*(\d+)月\s*(\d+)日',s)
 if m:
  era,y,mo,d=m.groups();year=int(y)+{'令和':2018,'平成':1988,'昭和':1925}[era]
 else:
  m=re.search(r'(\d{4})[年/.-]\s*(\d{1,2})[月/.-]\s*(\d{1,2})',s)
  if not m:raise ValueError('Unrecognized date: '+s)
  year,mo,d=map(int,m.groups())
 return datetime.date(year,int(mo),int(d)).isoformat()

def select_latest(rows):
 """Use one comparable date basis per group, never receipt-number ordering."""
 ranked=[]
 for row in rows:
  raw=json.loads(row['source_values_json'])
  receipt=date_key(raw.get('受理年月日') or raw.get('受理日'))
  ranked.append((receipt,row,raw))
 receipt_present=any(r[0] for r in ranked)
 basis='receipt_date' if receipt_present else 'calculation_start'
 if not receipt_present:ranked=[(date_key(raw.get('算定開始年月日')),row,raw) for _,row,raw in ranked]
 complete=all(r[0] for r in ranked)
 best=max(r[0] for r in ranked) if complete else ''
 chosen=[r for r in ranked if r[0]==best] if complete else ranked
 versions={}
 for rank,row,raw in chosen:
  key=(str(raw.get('受理記号') or ''),str(raw.get('受理番号') or ''),rank,str(raw.get('算定開始年月日') or ''),str(raw.get('個別有効開始年月日') or ''))
  if key not in versions:
   base={k:v for k,v in raw.items() if k not in ['備考（見出し）','備考（データ）']};versions[key]={'fields':base,'details':[],'source_rows':[]}
  v=versions[key];detail={'heading':raw.get('備考（見出し）'),'value':raw.get('備考（データ）')}
  if (detail['heading'] or detail['value']) and detail not in v['details']:v['details'].append(detail)
  v['source_rows'].append({'file':row['source_file'],'sheet':row['sheet'],'row':row['excel_row']})
 single=len(versions)==1
 resolved=complete or single
 status='latest_by_'+basis if complete else 'single_filing_no_order_needed' if single else 'unresolved_missing_comparable_dates'
 # Effective dates are descriptive only. Do not publish a synthetic latest date
 # when multiple unorderable versions remain.
 fields=next(iter(versions.values()))['fields'] if single else {}
 receipts={(str(r[2].get('受理記号') or ''),str(r[2].get('受理番号') or '')) for r in chosen}
 return {'date_basis':basis,'receipt_date_iso':best if basis=='receipt_date' else '','selection_status':status,'latest_selection_resolved':resolved,'single_version_resolved':single,'calculation_start_iso':best if basis=='calculation_start' else date_key(fields.get('算定開始年月日')),'individual_start_iso':date_key(fields.get('個別有効開始年月日')),'same_date_multiple_receipts':complete and len(receipts)>1,'date_unknown':not complete,'versions':list(versions.values()),'source_detail_rows':len(chosen),'older_source_rows_removed':len(rows)-len(chosen)}

def build(db):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row
 c.execute('DROP TABLE IF EXISTS latest_rows');c.execute('CREATE TABLE latest_rows(bureau TEXT,reference_date TEXT,prefecture_code TEXT,prefecture TEXT,medical_code TEXT,institution_name TEXT,official_item_name TEXT,item_name_search TEXT,calculation_start_iso TEXT,individual_start_iso TEXT,same_date_multiple_receipts INTEGER,date_unknown INTEGER,payload_json TEXT,PRIMARY KEY(bureau,medical_code,item_name_search))')
 c.execute('CREATE INDEX IF NOT EXISTS idx_medical_item ON source_rows(medical_code,item_name_search)')
 cursor=c.execute("SELECT * FROM source_rows WHERE official_item_name<>'' ORDER BY medical_code,item_name_search")
 counts=collections.Counter();batch=[]
 for key,group in itertools.groupby(cursor,key=lambda r:(r['medical_code'],r['item_name_search'])):
  rows=list(group);v=select_latest(rows);first=rows[0]
  batch.append(tuple(first[k] for k in ['bureau','reference_date','prefecture_code','prefecture','medical_code','institution_name','official_item_name','item_name_search'])+(v['calculation_start_iso'],v['individual_start_iso'],int(v['same_date_multiple_receipts']),int(v['date_unknown']),json.dumps(v,ensure_ascii=False,default=str)))
  counts['latest_institution_item_groups']+=1;counts['older_source_rows_removed']+=v['older_source_rows_removed'];counts['same_date_multiple_receipts']+=v['same_date_multiple_receipts'];counts['date_unknown']+=v['date_unknown']
  counts['unresolved_latest_groups']+=not v['latest_selection_resolved']
  if len(batch)>=2000:c.executemany('INSERT INTO latest_rows VALUES('+','.join('?'*13)+')',batch);batch=[]
 if batch:c.executemany('INSERT INTO latest_rows VALUES('+','.join('?'*13)+')',batch)
 c.execute('CREATE INDEX idx_latest_formal ON latest_rows(item_name_search)');c.commit();c.close();return dict(counts)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--database',required=True);a=p.parse_args();report=build(a.database);Path(a.database).with_name('latest-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
