"""Real PostgreSQL integration; TEST_DATABASE_URL must point to disposable test DB."""
import os
import json
from copy import deepcopy
import pytest
import psycopg
from psycopg.types.json import Jsonb

@pytest.fixture
def db():
    url=os.getenv('TEST_DATABASE_URL')
    if not url: pytest.skip('TEST_DATABASE_URL is not configured (isolated DB required)')
    with psycopg.connect(url) as connection:
        try: yield connection
        finally: connection.rollback()

def source(sha='a',day='2026-10-01',count=1):
    return {'prefecture_code':40,'prefecture':'福岡県','bureau':'九州厚生局','filename':'test.xlsx',
            'sha256':sha*64,'parser_version':'integration-test','reference_date':day,
            'file_created_date':day,'retrieved_at':day+'T00:00:00+00:00','record_count':count}

def record(code='4010000001',day='2026-10-01'):
    return {'code':code,'prefecture_code':40,'prefecture':'福岡県','bureau':'九州厚生局',
            'name':'Integration Hospital','address':'福岡市','departments':[],
            'kind':'病院','status':'現存','total_beds':100,'general_beds':100,
            'psychiatric_class':'不明','needs_review':False,'opening_classification':'判定保留',
            'reference_date':day,'file_created_date':day,'retrieved_at':day+'T00:00:00+00:00',
            'source_file':'test.xlsx','raw':{},'change_reason':'新規','designation_date':day}

def import_data(db,src,records):
    db.execute("select set_config('request.jwt.claim.role','service_role',true)")
    return db.execute('select public.import_snapshot(%s,%s)',(Jsonb(src),Jsonb(records))).fetchone()[0]

def test_atomic_import_idempotence_no_false_new_opening(db):
    assert import_data(db,source(),[record()])['status']=='success'
    assert import_data(db,source(),[record()])['status']=='unchanged'
    assert db.execute("select count(*) from institution_changes where event_type='new_designation'").fetchone()[0]==0
    assert db.execute('select count(*) from institution_beds').fetchone()[0]==5
    assert db.execute("select medical_beds from medical_institutions where code='4010000001'").fetchone()[0] is None
    bad=record('4020000002')
    with pytest.raises(psycopg.Error):
        with db.transaction(): import_data(db,source('b',count=2),[record(),bad])
    assert db.execute('select count(*) from source_imports').fetchone()[0]==1

def test_count_drop_rejected_and_previous_data_maintained(db):
    rows=[record(f'40100000{i:02d}') for i in range(1,11)]
    import_data(db,source(count=10),rows)
    with pytest.raises(psycopg.Error,match='count dropped'):
        with db.transaction(): import_data(db,source('b','2026-11-01',1),[record(day='2026-11-01')])
    assert db.execute('select count(*) from medical_institutions').fetchone()[0]==10

def test_missing_candidate_never_automatically_closed(db):
    rows=[record(f'40100000{i:02d}') for i in range(1,6)]
    import_data(db,source(count=5),rows)
    later=[{**r,'reference_date':'2026-11-01','file_created_date':'2026-11-01'} for r in rows[:4]]
    import_data(db,source('b','2026-11-01',4),later)
    assert db.execute("select count(*) from institution_changes where event_type='missing_candidate'").fetchone()[0]==1
    assert db.execute("select status from medical_institutions where code='4010000005'").fetchone()[0]=='現存'

def test_rls_pending_approved_and_no_self_promotion(db):
    import_data(db,source(),[record()])
    uid='11111111-1111-1111-1111-111111111111'
    db.execute('insert into auth.users(id) values(%s)',(uid,))
    db.execute("select set_config('request.jwt.claim.sub',%s,true)",(uid,))
    db.execute("select set_config('request.jwt.claim.role','authenticated',true)")
    db.execute('set local role authenticated')
    assert db.execute('select count(*) from medical_institutions').fetchone()[0]==0
    with pytest.raises(psycopg.Error):
        with db.transaction():db.execute("update app_members set approved=true,role='admin'")
    with pytest.raises(psycopg.Error,match='admin required'):
        with db.transaction():db.execute("select set_member(%s,'admin',true)",(uid,))
    db.execute('reset role');db.execute('update app_members set approved=true where user_id=%s',(uid,))
    db.execute('set local role authenticated')
    assert db.execute('select count(*) from medical_institutions').fetchone()[0]==1
    db.execute("select record_access('test_read','{}')")
    assert db.execute('select count(*) from access_logs').fetchone()[0]==0 # Non-admin cannot read audit logs.

def test_organization_designation_count_is_distinct_from_pure_openings(db):
    import_data(db,source(),[record()])
    old={**record(),'reference_date':'2026-11-01'}
    new={**record('4010000002','2026-11-01'),'change_reason':'組織変更'}
    import_data(db,source('b','2026-11-01',2),[old,new])
    rows=db.execute("select opening_classification from institution_changes where event_type='new_designation'").fetchall()
    assert rows==[('判定保留',)]

def test_parser_revision_is_not_a_source_reported_change(db):
    import_data(db,source(),[record()])
    src={**source(),'parser_version':'integration-test-revised'}
    import_data(db,src,[{**record(),'total_beds':120}])
    assert db.execute("select count(*) from institution_changes where event_type='reclassified'").fetchone()[0]==1
    assert db.execute("select count(*) from institution_changes where event_type='changed'").fetchone()[0]==0
