"""Build an isolated SQLite verification index preserving every medical source row."""
import argparse,json,re,sqlite3,unicodedata,hashlib,collections,time
from pathlib import Path
import openpyxl
P=Path('/workspace/all-of-hospital_clinic/artifacts/facility-notifications-20261019')
REGIONS={'北海道':[1],'東北':list(range(2,8)),'関東信越':list(range(8,16))+[19,20],'東海北陸':[16,17,21,22,23,24],'近畿':[18,25,26,27,28,29,30],'中国四国':list(range(31,36)),'四国':list(range(36,40)),'九州':list(range(40,48))}
ONLY_BUREAU=None
def norm(s):return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(s or '')))
def refdate(s):
 m=re.search(r'令和\s*(\d+)年\s*(\d+)月\s*(\d+)日\s*現在',unicodedata.normalize('NFKC',s));assert m,s
 y,mo,d=map(int,m.groups());return f'{y+2018:04}-{mo:02}-{d:02}'
def main():
 dest=P/'medical-notifications.sqlite';tmp=P/'medical-notifications.sqlite.tmp';tmp.unlink(missing_ok=True);c=sqlite3.connect(tmp);c.execute('PRAGMA journal_mode=OFF');c.execute('PRAGMA synchronous=OFF');c.execute('CREATE TABLE source_rows(id INTEGER PRIMARY KEY,bureau TEXT,reference_date TEXT,source_file TEXT,sheet TEXT,excel_row INTEGER,prefecture_code TEXT,prefecture TEXT,medical_code TEXT,institution_name TEXT,official_item_name TEXT,item_name_search TEXT,abbreviation TEXT,receipt_number TEXT,calculation_start TEXT,detail_heading TEXT,detail_value TEXT,source_values_json TEXT)')
 sources=[];rejected=[];batch=[];total=0
 files=sorted(list((P/'expanded').glob('*/*.xlsx'))+list((P/'raw').glob('*/*.xlsx')))
 for f in files:
  bureau=f.parent.name
  if ONLY_BUREAU and bureau!=ONLY_BUREAU:continue
  # Combined official ZIPs contain dental/pharmacy members: retained in raw archives,
  # but excluded from the medical index before opening their large worksheets.
  fn=f.name.lower()
  if any(s in fn for s in ['_shika','_sika','_yakkyoku','歯科','薬局']):rejected.append(str(f.relative_to(P)));continue
  w=openpyxl.load_workbook(f,read_only=True,data_only=True)
  for sheet in w:
   it=sheet.iter_rows(values_only=True);header=None;date=None;skip=0;n=0;prefs=collections.Counter();names=set();bounds=[]
   for rownum,row in enumerate(it,1):
    if header is None:
     txt=' '.join(str(v) for v in row if v is not None)
     if '現在' in norm(txt) and '令和' in txt:date=refdate(txt)
     if '受理届出名称' in row and '医療機関番号' in row:
      header=[str(v or '').strip() for v in row];assert len(header)==len(set(header)),header
     if rownum>30 and header is None:raise ValueError(f'Unknown schema {f} {sheet.title}')
     continue
    if not any(v is not None and str(v).strip() for v in row):continue
    vals=list(row[:len(header)]);d=dict(zip(header,vals));kind=norm(d.get('区分'))
    if not kind:skip+=1;continue
    if kind!='医科':skip+=1;continue
    assert date,(f,sheet.title)
    pc=str(d['都道府県コード']).zfill(2);number=norm(d['医療機関番号']).zfill(7);assert re.fullmatch(r'\d{2}',pc) and re.fullmatch(r'\d{7}',number),(pc,number)
    assert 1<=int(pc)<=47;code=pc+'1'+number
    # Some institutions have no filing at all. Preserve their source row,
    # but do not turn the empty name into a fictitious notification.
    official=str(d.get('受理届出名称') or '').strip()
    batch.append((bureau,date,str(f.relative_to(P)),sheet.title,rownum,pc,str(d['都道府県名']),code,str(d['医療機関名称']),official,norm(official),str(d.get('受理記号') or ''),str(d.get('受理番号') or ''),str(d.get('算定開始年月日') or ''),str(d.get('備考（見出し）') or ''),str(d.get('備考（データ）') or ''),json.dumps(d,ensure_ascii=False,default=str)))
    n+=1;total+=1;prefs[pc]+=1;names.add(official)
    if len(batch)>=5000:c.executemany('INSERT INTO source_rows VALUES(NULL,'+','.join('?'*17)+')',batch);batch=[]
   sources.append({'bureau':bureau,'source_file':str(f.relative_to(P)),'sheet':sheet.title,'reference_date':date,'medical_rows':n,'skipped_rows':skip,'prefecture_rows':dict(prefs),'unique_official_names':len(names),'source_sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
   print(bureau,sheet.title,date,n,'total',total,flush=True)
  w.close()
 if batch:c.executemany('INSERT INTO source_rows VALUES(NULL,'+','.join('?'*17)+')',batch)
 c.execute('CREATE INDEX idx_formal_name ON source_rows(item_name_search)');c.execute('CREATE INDEX idx_medical_code ON source_rows(medical_code)');c.execute('CREATE INDEX idx_prefecture ON source_rows(prefecture_code)')
 c.execute("CREATE VIEW filings AS SELECT bureau,reference_date,prefecture_code,prefecture,medical_code,institution_name,official_item_name,item_name_search,abbreviation,receipt_number,calculation_start,COUNT(*) AS source_detail_rows FROM source_rows WHERE official_item_name<>'' GROUP BY bureau,reference_date,prefecture_code,prefecture,medical_code,institution_name,official_item_name,item_name_search,abbreviation,receipt_number,calculation_start")
 c.execute("CREATE TABLE official_names AS SELECT official_item_name,item_name_search,abbreviation,COUNT(*) AS source_rows,COUNT(DISTINCT medical_code) AS institution_count FROM source_rows WHERE official_item_name<>'' GROUP BY official_item_name,item_name_search,abbreviation");c.execute('CREATE INDEX idx_names ON official_names(item_name_search)')
 c.commit();coverage=c.execute('SELECT prefecture_code,prefecture,COUNT(*),COUNT(DISTINCT medical_code) FROM source_rows GROUP BY prefecture_code,prefecture ORDER BY prefecture_code').fetchall();expected=set(REGIONS[ONLY_BUREAU]) if ONLY_BUREAU else set(range(1,48));assert {int(r[0]) for r in coverage}==expected,coverage
 report={'scope':'医科の全届出項目（医科名簿に掲載される併設歯科の届出は原文のまま保持）','source_detail_rows':total,'prefecture_count':len(coverage),'institution_count':c.execute('SELECT COUNT(DISTINCT medical_code) FROM source_rows').fetchone()[0],'official_item_name_count':c.execute("SELECT COUNT(DISTINCT official_item_name) FROM source_rows WHERE official_item_name<>''").fetchone()[0],'no_filing_institution_rows':c.execute("SELECT COUNT(*) FROM source_rows WHERE official_item_name=''").fetchone()[0],'filing_groups':c.execute('SELECT COUNT(*) FROM filings').fetchone()[0],'coverage':[dict(zip(['prefecture_code','prefecture','source_rows','institutions'],r)) for r in coverage],'sheets':sources,'excluded_nonmedical_members':rejected,'production_write':False,'scheduled_execution_enabled':False}
 # Verify an exact formal-name search rather than an abbreviation-only search.
 query='医療安全対策加算１';hits=c.execute('SELECT COUNT(DISTINCT medical_code) FROM source_rows WHERE item_name_search=?',(norm(query),)).fetchone()[0];assert hits>0
 report['formal_search_test']={'query':query,'matching_institutions':hits,'pass':True}
 c.close();tmp.replace(dest);(P/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['coverage','sheets','excluded_nonmedical_members']},ensure_ascii=False),flush=True)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--bureau',choices=list(REGIONS));args=ap.parse_args();P=Path(args.input);ONLY_BUREAU=args.bureau;main()
