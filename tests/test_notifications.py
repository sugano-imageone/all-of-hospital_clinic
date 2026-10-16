import hashlib,json
from pathlib import Path
import openpyxl
import pytest
from app.notifications.store import HEADERS,connect,import_file,audit,code10
from app.notifications.sources import resolve

def workbook(path,rows=None,label='[令和８年１０月１日 現在 ]令和８年１０月１日作成',marker=None):
    b=openpyxl.Workbook();s=b.active;s.title='Sheet1';s.append([label]);s.append(HEADERS)
    base={k:None for k in HEADERS};base.update({'項番':'1','都道府県コード':'01','都道府県名':'北海道','区分':'医科','医療機関番号':'0111887','医療機関名称':'公式病院','受理届出名称':'一般病棟入院基本料','受理記号':'一般入院','受理番号':'第1号','算定開始年月日':'令和 8年 6月 1日'})
    for changes in (rows if rows is not None else [{},{'備考（見出し）':'病床数','備考（データ）':'37床'},{'備考（見出し）':'病棟数','備考（データ）':'1棟'}]):s.append([dict(base,**changes)[k] for k in HEADERS])
    if marker:b.properties.description=marker
    b.save(path);return path

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'dev.sqlite');yield c;c.close()

def test_repeat_import_and_repacked_xlsx_do_not_duplicate(db,tmp_path):
    p=workbook(tmp_path/'a.xlsx');r=import_file(db,p,'hokkaido');before=audit(db)
    assert r[0]['rows']==3 and r[0]['filings']==1
    assert import_file(db,p,'hokkaido')[0]['status']=='unchanged';assert audit(db)==before
    q=workbook(tmp_path/'renamed.xlsx',marker='different zip metadata')
    assert hashlib.sha256(q.read_bytes()).digest()!=hashlib.sha256(p.read_bytes()).digest()
    assert import_file(db,q,'hokkaido')[0]['status']=='unchanged_content';assert audit(db)==before

def test_multiple_filings_and_all_duplicate_detail_rows_are_preserved(db,tmp_path):
    p=workbook(tmp_path/'a.xlsx',[{}, {'備考（見出し）':'病床数','備考（データ）':'37床'}, {'備考（見出し）':'病床数','備考（データ）':'37床'}, {'受理届出名称':'療養病棟入院基本料','受理番号':'第2号'}])
    import_file(db,p,'hokkaido');a=audit(db)
    assert a['current_institutions']==1 and a['current_filings']==2 and a['raw_rows']==4
    assert db.execute('SELECT code FROM institutions').fetchone()[0]=='0110111887'
    raw=[json.loads(r[0]) for r in db.execute('SELECT raw_json FROM source_rows ORDER BY row_number')]
    assert len(raw)==4 and raw[1]==raw[2] and raw[1][19]=='37床'

def test_revised_snapshot_replaces_current_and_preserves_history(db,tmp_path):
    import_file(db,workbook(tmp_path/'old.xlsx'),'hokkaido')
    import_file(db,workbook(tmp_path/'new.xlsx',[{}, {'備考（見出し）':'病床数','備考（データ）':'38床'}, {'備考（見出し）':'病棟数','備考（データ）':'1棟'}]),'hokkaido')
    a=audit(db);assert a['snapshots']==2 and a['raw_rows']==6 and a['current_rows']==3 and a['current_filings']==1
    old=workbook(tmp_path/'older.xlsx',[{}],label='[令和８年９月１日 現在 ]令和８年９月１日作成')
    assert not import_file(db,old,'hokkaido')[0]['active'];assert audit(db)['current_rows']==3

def test_invalid_row_rolls_back_whole_prefecture(db,tmp_path):
    import_file(db,workbook(tmp_path/'good.xlsx'),'hokkaido');before=audit(db)
    p=workbook(tmp_path/'bad.xlsx',[{}, {'医療機関番号':'bad'}])
    with pytest.raises(ValueError):import_file(db,p,'hokkaido')
    assert audit(db)==before

@pytest.mark.parametrize('row',[{'受理届出名称':None},{'都道府県コード':'02'},{'区分':'歯科'}])
def test_missing_official_name_or_wrong_scope_is_rejected(db,tmp_path,row):
    with pytest.raises(ValueError):import_file(db,workbook(tmp_path/'bad.xlsx',[row]),'hokkaido')
    assert audit(db)['snapshots']==0

def test_codes_keep_leading_zeroes():
    assert code10(1,'0111887')=='0110111887'
    with pytest.raises(ValueError):code10(1,111887)

def test_shikoku_resolver_selects_roster_not_designation():
    html='<table><tr><th>コード内容別医療機関一覧表</th></tr><tr><td>医科</td><td><a href="/shikoku/wrong.zip">ZIP</a></td></tr></table><table><tr><th>届出受理医療機関名簿</th></tr><tr><td>医 科</td><td><a href="/shikoku/right.zip">ZIP</a></td></tr></table>'
    assert resolve('shikoku',html)==['https://kouseikyoku.mhlw.go.jp/shikoku/right.zip']

def test_no_filing_rows_are_preserved_without_inventing_a_name(db,tmp_path):
    blank={k:'' for k in ['受理届出名称','受理記号','受理番号','算定開始年月日']}
    import_file(db,workbook(tmp_path/'none.xlsx',[blank]),'hokkaido')
    a=audit(db);assert a['current_institutions']==1 and a['current_filings']==0 and a['current_rows']==1
    assert db.execute('SELECT filing_id FROM source_rows').fetchone()[0] is None

def test_mixed_dental_rows_do_not_create_false_medical_codes(db,tmp_path):
    p=workbook(tmp_path/'mixed.xlsx',[{'都道府県コード':'35','都道府県名':'山口県'}, {'都道府県コード':'35','都道府県名':'山口県','区分':'歯科','医療機関番号':'9999999'}])
    import_file(db,p,'chugokushikoku');a=audit(db)
    assert a['current_rows']==2 and a['nonmedical_original_rows']==1 and a['current_institutions']==1
    assert db.execute('SELECT count(*) FROM institutions').fetchone()[0]==1

def test_large_change_is_staged_and_repeated_import_stays_blocked(db,tmp_path):
    from app.notifications.reviews import approve
    old=workbook(tmp_path/'old-large.xlsx',[{'備考（データ）':str(i)} for i in range(100)])
    import_file(db,old,'hokkaido');old_current=db.execute('SELECT snapshot_id FROM current_snapshots').fetchone()[0]
    new=workbook(tmp_path/'new-small.xlsx',[{}]);r=import_file(db,new,'hokkaido')[0]
    assert r['status']=='review_required' and not r['active']
    assert audit(db)['current_rows']==100 and audit(db)['raw_rows']==101 and audit(db)['pending_review']==1
    assert db.execute('SELECT snapshot_id FROM current_snapshots').fetchone()[0]==old_current
    assert import_file(db,new,'hokkaido')[0]['status']=='review_required'
    with pytest.raises(ValueError):approve(db,r['snapshot_id'],note='',evidence_url='https://www.mhlw.go.jp/')
    approve(db,r['snapshot_id'],note='元資料の全行と差分を確認したテスト',evidence_url='https://kouseikyoku.mhlw.go.jp/hokkaido/official.html')
    assert audit(db)['current_rows']==1 and audit(db)['pending_review']==0

def test_revision_context_does_not_bypass_large_change(db,tmp_path):
    from app.notifications.reviews import approve
    rows=[{'受理届出名称':'旧名称'+str(i),'受理番号':'第'+str(i)+'号'} for i in range(25)]
    import_file(db,workbook(tmp_path/'before.xlsx',rows),'hokkaido')
    changed=[dict(r,受理届出名称='新名称'+str(i)) for i,r in enumerate(rows)]
    context=[{'type':'答申','title':'将来の改定に関する答申（テスト）','url':'https://www.mhlw.go.jp/official.pdf','published_date':'2026-10-01','effective_date':'2027-06-01'}]
    r=import_file(db,workbook(tmp_path/'after.xlsx',changed),'hokkaido',revision_context=context)[0]
    assert r['status']=='review_required'
    assert len(r['comparison']['metrics']['added_official_names'])==25
    with pytest.raises(ValueError,match='答申'):
        approve(db,r['snapshot_id'],note='答申だけでは承認しない',evidence_url='https://www.mhlw.go.jp/official.pdf',context=context)
    assert audit(db)['pending_review']==1

def test_policy_publication_changes_require_review_even_for_small_data_change(db,tmp_path,monkeypatch):
    from app.notifications import policy_watch
    monkeypatch.setattr(policy_watch,'PAGES',['https://www.mhlw.go.jp/official-index.html'])
    def html(version):return '<main>'+''.join(f'<a href="https://www.mhlw.go.jp/doc{i}.pdf">官報告示資料{i} {version}</a>' for i in range(5))+'</main>'
    assert policy_watch.watch(db,lambda _:html('初回'))[0]['status']=='baseline'
    assert policy_watch.watch(db,lambda _:html('初回'))[0]['status']=='unchanged'
    import_file(db,workbook(tmp_path/'before-policy.xlsx'),'hokkaido')
    assert policy_watch.watch(db,lambda _:html('新しい公表'))[0]['status']=='review_required'
    assert policy_watch.watch(db,lambda _:html('新しい公表'))[0]['status']=='unchanged'
    assert db.execute('SELECT count(*) FROM policy_events').fetchone()[0]==1
    r=import_file(db,workbook(tmp_path/'after-policy.xlsx',[{}, {'備考（見出し）':'病床数','備考（データ）':'38床'}, {'備考（見出し）':'病棟数','備考（データ）':'1棟'}]),'hokkaido')[0]
    assert r['status']=='review_required' and '制度関連公表の更新確認が未了' in r['comparison']['reasons']

def test_concurrent_reimport_is_idempotent(db,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    path=workbook(tmp_path/'concurrent.xlsx');barrier=Barrier(2)
    def run(_):
        c=connect(tmp_path/'dev.sqlite')
        try:barrier.wait(timeout=20);return import_file(c,path,'hokkaido')[0]['status']
        finally:c.close()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert sorted(results)==['imported','unchanged']
    a=audit(db);assert a['snapshots']==1 and a['raw_rows']==3

def test_semantic_unique_constraint_protects_even_if_another_id_is_supplied(db,tmp_path):
    import sqlite3
    import_file(db,workbook(tmp_path/'unique.xlsx'),'hokkaido')
    row=db.execute('SELECT * FROM filings LIMIT 1').fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('INSERT INTO filings VALUES(?,?,?,?,?,?,?)',('different-id',row['code'],row['official_name'],row['signature'],row['receipt_number'],row['start_date'],row['valid_from']))
    db.rollback();assert audit(db)['duplicate_filing_keys']==0

def test_stale_review_requires_recomparison_and_does_not_switch_automatically(db,tmp_path):
    from app.notifications.reviews import approve,recompare
    import_file(db,workbook(tmp_path/'baseline-review.xlsx',[{'備考（データ）':str(i)} for i in range(100)]),'hokkaido')
    a=import_file(db,workbook(tmp_path/'candidate-a.xlsx',[{}]),'hokkaido')[0]
    b=import_file(db,workbook(tmp_path/'candidate-b.xlsx',[{}, {'備考（データ）':'B'}]),'hokkaido')[0]
    approve(db,b['snapshot_id'],note='Bを確認',evidence_url='https://www.mhlw.go.jp/official.pdf')
    with pytest.raises(ValueError,match='再比較'):approve(db,a['snapshot_id'],note='古い比較では承認不可',evidence_url='https://www.mhlw.go.jp/official.pdf')
    recompare(db,a['snapshot_id']);assert audit(db)['current_rows']==2
    assert db.execute('SELECT old_snapshot_id FROM snapshot_reviews WHERE snapshot_id=?',(a['snapshot_id'],)).fetchone()[0]==b['snapshot_id']

def test_gazetted_basis_requires_notice_number_effective_date_and_annex():
    from app.notifications.guards import validate_context
    basis={'type':'官報告示','issuer':'厚生労働大臣','notice_number':'テスト用告示番号','title':'検証用・実在の告示を示すものではない','url':'https://www.kanpo.go.jp/test.pdf','published_date':'2026-03-05','effective_date':'2026-06-01','gazette_reference':'テスト用の官報掲載号・掲載日','annex_reference':'テスト用の別添該当箇所'}
    assert validate_context([basis])==[basis]
    for field in ['notice_number','effective_date','annex_reference','gazette_reference']:
        bad={k:v for k,v in basis.items() if k!=field}
        with pytest.raises(ValueError):validate_context([bad])

def test_chuikyo_page_is_not_a_current_policy_watch_target():
    from app.notifications.policy_watch import PAGES
    assert not any('shingi-chuo' in u for u in PAGES)

def test_answer_and_notice_alone_do_not_authorize_switch_but_complete_gazette_basis_does(db,tmp_path):
    from app.notifications.reviews import approve
    import_file(db,workbook(tmp_path/'gazette-before.xlsx',[{'備考（データ）':str(i)} for i in range(100)]),'hokkaido')
    candidate=import_file(db,workbook(tmp_path/'gazette-after.xlsx',[{}]),'hokkaido')[0]
    for kind in ['通知','答申']:
        context=[{'type':kind,'title':'検証用の資料','url':'https://www.mhlw.go.jp/test.pdf','published_date':'2026-03-05','effective_date':'2026-06-01'}]
        with pytest.raises(ValueError,match='官報告示'):approve(db,candidate['snapshot_id'],note='検証',evidence_url='https://www.mhlw.go.jp/test.pdf',context=context)
        assert audit(db)['current_rows']==100
    context=[{'type':'官報告示','issuer':'厚生労働大臣','notice_number':'テスト用番号','title':'検証用・実在の告示ではない','url':'https://www.kanpo.go.jp/test.pdf','published_date':'2026-03-05','effective_date':'2026-06-01','gazette_reference':'テスト用官報号','annex_reference':'テスト用別添箇所'}]
    approve(db,candidate['snapshot_id'],note='検証用の告示本文・別添を確認した想定',evidence_url='https://www.kanpo.go.jp/test.pdf',context=context)
    assert audit(db)['current_rows']==1
