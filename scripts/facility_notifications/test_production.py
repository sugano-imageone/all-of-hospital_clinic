import datetime,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import production

class TransferTests(unittest.TestCase):
 def database(self,root):
  db=root/'source.sqlite';c=sqlite3.connect(db)
  c.execute('create table source_rows(bureau text,prefecture_code text,medical_code text,reference_date text)')
  c.executemany('insert into source_rows values(?,?,?,?)',[('北海道','01',code,'2026-10-01') for code in ['0110000001','0110000002']])
  c.execute('create table latest_rows(bureau text,medical_code text,item_name_search text,official_item_name text,calculation_start_iso text,payload_json text)')
  payload={'latest_selection_resolved':True,'date_basis':'calculation_start','receipt_date_iso':'','versions':[{'fields':{'算定開始年月日':'令和8年9月1日'}}]}
  c.execute('insert into latest_rows values(?,?,?,?,?,?)',('北海道','0110000001','項目1','項目１','2026-09-01',json.dumps(payload)))
  c.commit();c.close();return db
 def test_bootstrap_transfers_coverage_and_preserves_date_kind(self):
  class Fake:
   calls=[]
   def rpc(self,name,args):
    self.calls.append((name,args))
    return 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa' if name=='begin_notification_snapshot' else {'status':'success'}
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);db=self.database(root);remote=Fake();production.publish(remote,db,'北海道',root)
   metadata=remote.calls[0][1]['p_metadata'];self.assertEqual(metadata['facilities'],2);self.assertEqual(metadata['filings'],1)
   filings=next(args['p_filings'] for name,args in remote.calls if name=='upload_notification_batch' and 'p_filings' in args)
   self.assertIsNone(filings[0]['receipt_date']);self.assertEqual(filings[0]['calculation_start'],'2026-09-01')
   self.assertFalse(any('job' in name for name,args in remote.calls))
   n=len(remote.calls);production.publish(remote,db,'北海道',root);self.assertEqual(len(remote.calls),n)
 def test_incomplete_and_unresolved_sources_never_begin_upload(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);db=self.database(root);c=sqlite3.connect(db)
   with self.assertRaisesRegex(ValueError,'incomplete'):production.validate(c,'東北')
   c.execute("update latest_rows set payload_json='{}'");c.commit()
   with self.assertRaisesRegex(ValueError,'unresolved'):production.validate(c,'北海道')
   c.close()
if __name__=='__main__':unittest.main()
