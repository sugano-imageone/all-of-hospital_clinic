"""Local read-only formal-name search for acquisition verification."""
import argparse,json,re,sqlite3,unicodedata
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--name',required=True);p.add_argument('--limit',type=int,default=20);a=p.parse_args()
 if not 1<=a.limit<=500:p.error('--limit must be between 1 and 500')
 query=re.sub(r'\s+','',unicodedata.normalize('NFKC',a.name))
 if not query:p.error('Empty search term')
 with sqlite3.connect(Path(a.database).resolve().as_uri()+'?mode=ro',uri=True) as c:
  c.row_factory=sqlite3.Row
  tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  table='current_rows' if 'current_rows' in tables else 'latest_rows' if 'latest_rows' in tables else None
  if table is None:p.error('最新届出の選択済み索引がありません')
  names=c.execute(f'SELECT DISTINCT item_name_search FROM {table} WHERE instr(item_name_search,?)>0',(query,)).fetchall()
  if not names:print(json.dumps({'query':a.name,'matches':[]},ensure_ascii=False));return
  sql=f'SELECT bureau,reference_date,prefecture,medical_code,institution_name,official_item_name,calculation_start_iso,individual_start_iso,same_date_multiple_receipts FROM {table} WHERE item_name_search IN ('+','.join('?' for _ in names)+') ORDER BY prefecture,medical_code,official_item_name LIMIT ?'
  rows=c.execute(sql,[r[0] for r in names]+[a.limit]).fetchall()
  print(json.dumps({'query':a.name,'matching_name_variants':len(names),'limit':a.limit,'matches':[dict(r) for r in rows]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
