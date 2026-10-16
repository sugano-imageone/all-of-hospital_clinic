"""Read-only public bureau master, keyed by the 10-digit insurance code. No Navii."""
import argparse,json,re
from pathlib import Path
from app.notifications.store import connect,dumps

# Search classifications from the official department abbreviation table. Raw values
# are always displayed alongside this grouping; these are not inferred from names.
DEPARTMENT_SOURCE='https://kouseikyoku.mhlw.go.jp/kinki/gyomu/gyomu/hoken_kikan/documents/sinryou-kamoku-itiran.pdf'
ALIASES={'内':'内科','精':'精神科','神':'神経科','神内':'神経内科','呼':'呼吸器科','胃':'胃腸科','消':'消化器科','循':'循環器科','小':'小児科','外':'外科','整外':'整形外科','形外':'形成外科','美外':'美容外科','脳外':'脳神経外科','呼外':'呼吸器外科','心外':'心臓血管外科','小外':'小児外科','皮ひ':'皮膚泌尿器科','皮':'皮膚科','ひ':'泌尿器科','性':'性病科','こう':'こう門科','産婦':'産婦人科','産':'産科','婦':'婦人科','眼':'眼科','耳い':'耳鼻いんこう科','耳':'耳鼻いんこう科','気食':'気管食道科','リハ':'リハビリテーション科','放':'放射線科','麻':'麻酔科','心内':'心療内科','リウ':'リウマチ科','アレ':'アレルギー科','病理':'病理診断科','臨床':'臨床検査科','救命':'救急科','呼内':'呼吸器内科','透析':'人工透析内科','脳内':'脳神経内科','和漢':'漢方内科','胸外':'胸部外科'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS reference_institutions(code TEXT PRIMARY KEY,name TEXT NOT NULL,prefecture INTEGER NOT NULL,kind TEXT,status TEXT,address TEXT,phone TEXT,departments_json TEXT,raw_json TEXT,source_file TEXT);
CREATE INDEX IF NOT EXISTS reference_pref_kind ON reference_institutions(prefecture,kind,status);
CREATE TABLE IF NOT EXISTS institution_departments(code TEXT NOT NULL REFERENCES reference_institutions(code),department TEXT NOT NULL,source_text TEXT NOT NULL,PRIMARY KEY(code,department,source_text));
CREATE INDEX IF NOT EXISTS institution_departments_name ON institution_departments(department,code);
CREATE TABLE IF NOT EXISTS snapshot_institutions(snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),code TEXT NOT NULL REFERENCES institutions(code),PRIMARY KEY(snapshot_id,code));
CREATE INDEX IF NOT EXISTS snapshot_institutions_code ON snapshot_institutions(code,snapshot_id);
'''
def load(db,directory):
    db.executescript(SCHEMA);seen={};stats={};counts=0
    with db:
        for path in sorted(Path(directory).glob('*.institutions.json')):
            for row in json.loads(path.read_text()):
                code=row['code']
                if not re.fullmatch(r'\d{2}1\d{7}',code):raise ValueError('医科10桁コード不正')
                identity=(row['name'],row['address'],row['kind'],row['status'])
                if code in seen and seen[code]!=identity:raise ValueError('同一コードの公開主一覧が競合')
                seen[code]=identity;depts=row.get('departments') or []
                db.execute('INSERT INTO reference_institutions VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,prefecture=excluded.prefecture,kind=excluded.kind,status=excluded.status,address=excluded.address,phone=excluded.phone,departments_json=excluded.departments_json,raw_json=excluded.raw_json,source_file=excluded.source_file',
                 (code,row['name'],row['prefecture_code'],row['kind'],row['status'],row['address'],row.get('phone'),dumps(depts),dumps(row),str(path)))
                db.execute('DELETE FROM institution_departments WHERE code=?',(code,))
                for text in depts:
                    for token in re.split(r'[\s、,／/]+',str(text)):
                        if token:db.execute('INSERT OR IGNORE INTO institution_departments VALUES(?,?,?)',(code,ALIASES.get(token,token),str(text)))
                p=row['prefecture'];stat=stats.setdefault(p,{'institutions':0,'departments_present':0});stat['institutions']+=1;stat['departments_present']+=bool(depts)
        if not seen:raise ValueError('主一覧の医療機関がありません')
        # Membership is derived without deleting any source rows; used to distinguish
        # a genuinely covered institution from a missing/unmatched institution.
        db.execute('INSERT OR IGNORE INTO snapshot_institutions SELECT snapshot_id,code FROM source_rows WHERE code IS NOT NULL GROUP BY snapshot_id,code')
    return {'institutions':len(seen),'departments_present':sum(s['departments_present'] for s in stats.values()),'by_prefecture':stats,'navii_used':False,'department_alias_reference':DEPARTMENT_SOURCE}

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/notifications/development-v2.sqlite');p.add_argument('--directory',required=True);p.add_argument('--report',default='artifacts/notification-departments.json');a=p.parse_args();db=connect(a.db)
    try:
        r=load(db,a.directory);out=Path(a.report);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(r,ensure_ascii=False,indent=2));print(dumps(r))
    finally:db.close()
if __name__=='__main__':main()
