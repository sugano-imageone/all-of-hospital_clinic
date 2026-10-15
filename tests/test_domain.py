import pytest
from pydantic import ValidationError
from app.domain import classify,institution_code,REGIONS
from app.store import Search,search_params
from app.importers.kyushu import japanese_date

def test_code_preserves_zero_and_medical_category():
    assert institution_code('021,003,9',40)=='4010210039'
    assert institution_code('01・1110・7',47)=='4710111107'
    assert institution_code('01,1110,3',41)=='4110111103'
    for text in ['(033,864,6)','4020210039','3910210039','033,864','医長264']:
        with pytest.raises(ValueError): institution_code(text,40)

@pytest.mark.parametrize('total,expected',[(0,'診療所'),(1,'診療所'),(19,'診療所'),(20,'病院'),(200,'病院'),(None,None)])
def test_bed_thresholds(total,expected): assert classify(total)['kind']==expected

def test_psychiatric_unknown_and_conflicts():
    assert classify(None,'病院',None)['psychiatric_class']=='不明'
    assert classify(100,'病院',100)['psychiatric_class']=='精神病床のみ'
    assert classify(100,'病院',80)['psychiatric_class']=='精神病床80%以上'
    assert classify(100,'病院',79)['psychiatric_class']=='混合型'
    assert classify(19,'病院',0)['needs_review']
    assert classify(100,'病院',101)['needs_review']
    assert classify(None,'診療所')['clinic_beds'] is None

def test_reference_and_created_dates():
    assert japanese_date('令和 8年10月 1日現在')=='2026-10-01'
    assert japanese_date('令和 8年10月 5日作成')=='2026-10-05'
    assert japanese_date('令元. 5. 1')=='2019-05-01'

def test_search_independent_category_and_excluded_statuses():
    q=search_params(Search(kind='病院',bed_type='psychiatric',minimum=1,maximum=19))
    assert ('kind','eq.病院') in q
    assert ('psychiatric_beds','gte.1') in q
    assert ('status','eq.現存') in q
    assert '確認中' not in str(q)
    assert ('status','in.(現存,休止)') in search_params(Search(include_suspended=True))
    with pytest.raises(ValidationError): Search(minimum=30,maximum=20)

def test_fukui_belongs_to_kinki():
    assert 18 in REGIONS['近畿厚生局'] and 18 not in REGIONS['東海北陸厚生局']

def test_all_prefectures_exactly_once():
    assert sorted(p for ps in REGIONS.values() for p in ps)==list(range(1,48))

def test_scalar_filters_are_not_quoted_as_literal_quotes():
    q=search_params(Search(name='船員保険',bureau='九州厚生局'))
    assert ('name','ilike.%船員保険%') in q
    assert ('bureau','eq.九州厚生局') in q
