"""Query semantics for a future production integration; default notification filter OFF."""
import re,unicodedata

def normalize(s):return re.sub(r'\s+','',unicodedata.normalize('NFKC',s or ''))

def search(c,*,use_notifications=False,item_name='',filing_status='filed',limit=20):
 if not 1<=limit<=500:raise ValueError('Invalid result limit')
 # No notification table is read when the optional condition is OFF.
 if not use_notifications:
  return c.execute('SELECT medical_code,institution_name,prefecture FROM facility_master WHERE status=? ORDER BY medical_code LIMIT ?',('現存',limit)).fetchall()
 if filing_status not in ('filed','unfiled'):raise ValueError('届出済の／未届出の を選択してください')
 item=normalize(item_name)
 if not item:raise ValueError('届出項目を正式名称で選択してください')
 tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")};table='current_rows' if 'current_rows' in tables else 'latest_rows' if 'latest_rows' in tables else None
 if table is None:raise ValueError('検証済みの届出データが未取得です。未届出として扱いません。')
 if not c.execute(f'SELECT 1 FROM {table} WHERE item_name_search=? LIMIT 1',(item,)).fetchone():raise ValueError('その正式名称の届出項目は取得済み名簿にありません。未届出の全施設一覧は返しません。')
 exists='EXISTS' if filing_status=='filed' else 'NOT EXISTS'
 # Missing bureaus never turn into false negatives. A reference date is returned
 # with either selection; "unfiled" means absent from that published snapshot.
 sql=f'''SELECT m.medical_code,m.institution_name,m.prefecture,
 (SELECT MIN(n.reference_date) FROM {table} n WHERE n.bureau=m.bureau) AS notification_reference_date
 FROM facility_master m WHERE m.status='現存'
 AND m.bureau IN (SELECT DISTINCT bureau FROM {table})
 AND {exists}(SELECT 1 FROM {table} n WHERE n.medical_code=m.medical_code AND n.item_name_search=?)
 ORDER BY m.medical_code LIMIT ?'''
 return c.execute(sql,(item,limit)).fetchall()
