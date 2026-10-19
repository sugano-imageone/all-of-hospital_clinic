import json,sqlite3,tempfile,unittest
from pathlib import Path
from latest import select_latest,date_key
from monthly import init,targets,replace_bureau
from build_index import REGIONS

def row(date,receipt='第1号',heading='',value='',individual='',received=''):
 return {'source_values_json':json.dumps({'受理届出名称':'医療安全対策加算１','受理記号':'医療安全１','受理番号':receipt,'受理年月日':received,'算定開始年月日':date,'個別有効開始年月日':individual,'備考（見出し）':heading,'備考（データ）':value},ensure_ascii=False),'source_file':'official.xlsx','sheet':'Sheet1','excel_row':5}

class LatestTests(unittest.TestCase):
 def test_latest_date_not_receipt_number(self):
  r=select_latest([row('令和7年1月1日','第999号'),row('令和8年1月1日','第1号')]);self.assertEqual(r['calculation_start_iso'],'2026-01-01');self.assertEqual(len(r['versions']),1);self.assertEqual(r['versions'][0]['fields']['受理番号'],'第1号')
 def test_details_not_counted_as_older_filings(self):
  r=select_latest([row('令和8年1月1日',heading='病床数',value='10床'),row('令和8年1月1日',heading='病棟数',value='1棟')]);self.assertEqual(len(r['versions']),1);self.assertEqual(len(r['versions'][0]['details']),2)
 def test_same_date_ties_flagged_not_guessed(self):
  r=select_latest([row('令和8年1月1日','第1号'),row('令和8年1月1日','第2号')]);self.assertTrue(r['same_date_multiple_receipts']);self.assertEqual(len(r['versions']),2)
 def test_individual_validity_does_not_guess_equal_start_order(self):
  r=select_latest([row('令和8年1月1日',individual='令和8年2月1日'),row('令和8年1月1日',individual='令和8年3月1日')]);self.assertEqual(len(r['versions']),2);self.assertFalse(r['single_version_resolved'])
 def test_receipt_date_overrides_calculation_start(self):
  r=select_latest([row('令和8年3月1日',receipt='第999号',received='令和8年1月1日'),row('令和8年2月1日',received='令和8年1月20日')]);self.assertEqual(r['receipt_date_iso'],'2026-01-20');self.assertEqual(r['calculation_start_iso'],'2026-02-01');self.assertEqual(len(r['versions']),1)
 def test_mixed_missing_receipt_date_not_compared_to_effective_date(self):
  r=select_latest([row('令和8年3月1日',receipt='第999号'),row('令和8年2月1日',received='令和8年1月20日')]);self.assertFalse(r['latest_selection_resolved']);self.assertEqual(len(r['versions']),2)
 def test_unknown_date_does_not_invent_order(self):self.assertTrue(select_latest([row('')])['date_unknown'])
 def test_era_dates(self):self.assertEqual(date_key('平成元年１月８日'),'1989-01-08')
 def test_invalid_date_rejected(self):
  with self.assertRaises(ValueError):date_key('令和8年13月1日')

class MonthlyTests(unittest.TestCase):
 def setUp(self):self.c=sqlite3.connect(':memory:');init(self.c)
 def tearDown(self):self.c.close()
 def test_twentieth_all_then_twentyfifth_failed_only(self):
  self.assertEqual(set(targets(self.c,'2026-10',20)),set(REGIONS))
  with self.c:self.c.execute("UPDATE monthly_jobs SET status='success' WHERE bureau<>'北海道'")
  self.assertEqual(targets(self.c,'2026-10',25),['北海道'])
 def test_retry_without_twentieth_state_stops(self):
  with self.assertRaises(ValueError):targets(self.c,'2026-10',25)
 def test_no_attempt_on_other_days(self):
  with self.assertRaises(ValueError):targets(self.c,'2026-10',21)
 def candidate(self,path,bureau='北海道',pref='01',code='new'):
  d=sqlite3.connect(path);init(d);d.execute('ALTER TABLE current_rows RENAME TO latest_rows');d.execute('INSERT INTO latest_rows VALUES('+','.join('?'*13)+')',(bureau,'2026-10-01',pref,'北海道',code,'病院','正式名称','正式名称','2026-01-01','',0,0,'{"latest_selection_resolved":true}'));d.commit();d.close()
 def test_full_replace_removes_disappeared_not_additive(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'candidate.sqlite';self.candidate(p,code='old');replace_bureau(self.c,'北海道',p,'2026-10','now',{})
   p.unlink();self.candidate(p,code='new');replace_bureau(self.c,'北海道',p,'2026-10','now',{})
   self.assertEqual(self.c.execute('SELECT medical_code FROM current_rows').fetchall(),[('new',)])
 def test_failed_validation_preserves_old_snapshot(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'candidate.sqlite';self.candidate(p,code='old');replace_bureau(self.c,'北海道',p,'2026-10','now',{});p.unlink();self.candidate(p,pref='02',code='bad')
   with self.assertRaises(ValueError):replace_bureau(self.c,'北海道',p,'2026-10','now',{})
   self.assertEqual(self.c.execute('SELECT medical_code FROM current_rows').fetchall(),[('old',)])
 def test_older_publication_does_not_replace_current(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'candidate.sqlite';self.candidate(p,code='old');replace_bureau(self.c,'北海道',p,'2026-10','now',{});p.unlink();self.candidate(p,code='bad')
   with sqlite3.connect(p) as d:d.execute("UPDATE latest_rows SET reference_date='2026-09-01'")
   with self.assertRaises(ValueError):replace_bureau(self.c,'北海道',p,'2026-10','now',{})
   self.assertEqual(self.c.execute('SELECT medical_code FROM current_rows').fetchall(),[('old',)])
if __name__=='__main__':unittest.main()
