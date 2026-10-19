import sqlite3,unittest
from query import search
class QueryTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:');self.c.execute('CREATE TABLE facility_master(medical_code TEXT,institution_name TEXT,prefecture TEXT,bureau TEXT,status TEXT)');self.c.executemany('INSERT INTO facility_master VALUES(?,?,?,?,?)',[('1','届出済施設','北海道','北海道','現存'),('2','未掲載施設','北海道','北海道','現存'),('3','局未取得施設','東京都','関東信越','現存'),('4','休止施設','北海道','北海道','休止')])
 def tearDown(self):self.c.close()
 def data(self):
  self.c.execute('CREATE TABLE current_rows(medical_code TEXT,bureau TEXT,item_name_search TEXT,reference_date TEXT)');self.c.execute("INSERT INTO current_rows VALUES('1','北海道','医療安全対策加算1','2026-10-01')")
 def test_default_off_reads_no_notification_table(self):self.assertEqual(len(search(self.c)),3)
 def test_off_ignores_saved_item_and_status(self):self.assertEqual(len(search(self.c,item_name='不存在項目',filing_status='unfiled')),3)
 def test_filed(self):
  self.data();self.assertEqual([r[0] for r in search(self.c,use_notifications=True,item_name='医療安全対策加算１')],['1'])
 def test_unfiled_excludes_unacquired_bureau_and_suspended(self):
  self.data();self.assertEqual([r[0] for r in search(self.c,use_notifications=True,item_name='医療安全対策加算１',filing_status='unfiled')],['2'])
 def test_unknown_item_does_not_return_everyone_as_unfiled(self):
  self.data()
  with self.assertRaises(ValueError):search(self.c,use_notifications=True,item_name='未知の正式名称',filing_status='unfiled')
 def test_no_data_is_not_unfiled(self):
  with self.assertRaises(ValueError):search(self.c,use_notifications=True,item_name='医療安全対策加算１',filing_status='unfiled')
if __name__=='__main__':unittest.main()
