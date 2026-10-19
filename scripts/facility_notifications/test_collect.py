import unittest
import collect

class CollectorTests(unittest.TestCase):
 def test_next_month_url_comes_from_page(self):
  r=collect.resolve('関東信越',collect.SEEDS['関東信越'],'<a href="shisetsu_ika_r0811.zip">医科</a>')
  self.assertTrue(r['full_urls'][0].endswith('shisetsu_ika_r0811.zip'))
 def test_missing_medical_full_list_rejected(self):
  with self.assertRaises(AssertionError):collect.resolve('関東信越',collect.SEEDS['関東信越'],'<a href="shisetsu_shika_r0810.zip">歯科</a>')
 def test_ambiguous_full_list_rejected(self):
  with self.assertRaises(AssertionError):collect.resolve('関東信越',collect.SEEDS['関東信越'],'<a href="shisetsu_ika_r0810.zip">医科</a><a href="shisetsu_ika_r0811.zip">医科</a>')
 def test_external_sources_rejected(self):
  for u in ['http://kouseikyoku.mhlw.go.jp/a.zip','https://example.com/a.zip','https://kouseikyoku.mhlw.go.jp@evil.test/a.zip']:
   self.assertFalse(collect.official(u))
 def test_chugoku_and_shikoku_separate(self):
  self.assertNotEqual(collect.SEEDS['中国四国'],collect.SEEDS['四国'])
  from build_index import REGIONS
  self.assertEqual(REGIONS['中国四国'],[31,32,33,34,35]);self.assertEqual(REGIONS['四国'],[36,37,38,39])
 def test_shikoku_full_medical_table(self):
  html='<table><caption>届出受理医療機関名簿</caption><tr><td>医科</td><td><a href="medical.zip">全件</a></td></tr><tr><td>歯科</td><td><a href="dental.zip">全件</a></td></tr></table><table><caption>項目別</caption><tr><td>医科<a href="selected.zip">項目</a></td></tr></table>'
  r=collect.resolve('四国',collect.SEEDS['四国'],html)
  self.assertEqual(r['full_urls'],[collect.SEEDS['四国']+'medical.zip'])

if __name__=='__main__':unittest.main()
