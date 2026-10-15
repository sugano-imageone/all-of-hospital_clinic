"""Uses TEST_EXISTING_DATABASE_URL, an empty disposable DB with Auth stubs only."""
import os
from pathlib import Path
import uuid
import pytest
import psycopg
from psycopg.types.json import Jsonb
from tests.restore_existing import restore

ROOT=Path(__file__).parents[1]

@pytest.fixture
def existing():
    url=os.getenv('TEST_EXISTING_DATABASE_URL')
    if not url:pytest.skip('TEST_EXISTING_DATABASE_URL is not configured')
    with psycopg.connect(url) as db:
        try:
            restore(db)
            yield db
        finally:db.rollback()

def migrate(db):
    import re
    # Keep the fixture's outer transaction so the test schema is rolled back.
    script=(ROOT/'sql/010_upgrade_existing.sql').read_text()
    script=re.sub(r'^begin;\s*$', '',script,flags=re.MULTILINE)
    script=re.sub(r'^commit;\s*$', '',script,flags=re.MULTILINE)
    db.execute(script)
    bulk=(ROOT/'sql/012_bulk_import.sql').read_text()
    bulk=re.sub(r'^begin;\s*$', '',bulk,flags=re.MULTILINE)
    bulk=re.sub(r'^commit;\s*$', '',bulk,flags=re.MULTILINE)
    db.execute(bulk)

def seed(db):
    uid=uuid.UUID('11111111-1111-1111-1111-111111111111')
    institution=uuid.UUID('22222222-2222-2222-2222-222222222222')
    db.execute('insert into auth.users(id) values(%s)',(uid,))
    db.execute("insert into app_members(user_id,role,enabled) values(%s,'viewer',true)",(uid,))
    src=db.execute("insert into source_imports(bureau,prefecture,filename,import_status,publication_reference_date) values('九州厚生局','福岡県','existing.xlsx','completed','2026-09-01') returning id").fetchone()[0]
    db.execute("insert into medical_institutions(id,insurance_medical_code,prefecture,institution_name,institution_type,source_import_id,departments) values(%s,'4010000001','福岡県','既存の病院','hospital',%s,'内科 外科')",(institution,src))
    db.execute('insert into institution_beds(institution_id,total_beds,general_beds) values(%s,100,100)',(institution,))
    change=db.execute("insert into institution_changes(new_medical_code,change_type,review_status,evidence,effective_date,source_import_id) values('4010000001','information_change','confirmed','existing evidence','2026-09-01',%s) returning id",(src,)).fetchone()[0]
    return uid,institution,src,change

def test_upgrade_preserves_rows_ids_beds_and_evidence(existing):
    db=existing;uid,inst,src,change=seed(db);migrate(db)
    assert db.execute('select id,code,name,kind,total_beds,status,departments,source_import_id from medical_institutions').fetchone()==(inst,'4010000001','既存の病院','病院',100,'現存',['内科 外科'],src)
    assert db.execute('select id,status from source_imports').fetchone()==(src,'success')
    assert db.execute('select id,evidence,event_type from institution_changes').fetchone()==(change,'existing evidence','changed')
    assert db.execute('select institution_id,total_beds from institution_beds').fetchone()==(inst,100)
    assert db.execute('select role,approved from app_members where user_id=%s',(uid,)).fetchone()==('user',True)
    assert db.execute("select data_type from information_schema.columns where table_name='source_imports' and column_name='id'").fetchone()[0]=='bigint'
    assert db.execute("select count(*) from pg_indexes where indexname='idx_medical_name'").fetchone()[0]==1

def test_current_bed_changes_keep_search_values_in_sync(existing):
    db=existing;_,inst,_,_=seed(db);migrate(db)
    db.execute('update institution_beds set total_beds=120,general_beds=60,psychiatric_beds=60 where institution_id=%s',(inst,))
    assert db.execute('select total_beds,psychiatric_class from medical_institutions').fetchone()==(120,'混合型')
    db.execute('update institution_beds set total_beds=10 where institution_id=%s',(inst,))
    assert db.execute('select status,needs_review from medical_institutions').fetchone()==('確認中',True)

def test_existing_admin_only_history_remains_private_but_summary_available(existing):
    db=existing;uid,_,_,_=seed(db);migrate(db)
    db.execute("select set_config('request.jwt.claim.sub',%s,true)",(str(uid),))
    db.execute("select set_config('request.jwt.claim.role','authenticated',true)")
    db.execute('set local role authenticated')
    assert db.execute('select count(*) from source_imports').fetchone()[0]==0
    assert db.execute('select count(*) from institution_changes').fetchone()[0]==0
    metadata=db.execute('select source_metadata()').fetchone()[0]
    assert len(metadata)==1 and 'error_summary' not in metadata[0]
    assert db.execute('select monthly_change_counts()').fetchone()[0]=={'2026-09':{'変更数':1}}
    with pytest.raises(psycopg.Error):
        with db.transaction():db.execute("update app_members set role='admin'")

def test_import_works_with_bigint_and_wide_current_bed_table(existing):
    from tests.test_postgres import record,source,import_data
    db=existing;migrate(db)
    result=import_data(db,source(),[record()]);assert result['status']=='success' and isinstance(result['source_import_id'],int)
    assert db.execute('select count(*) from institution_beds').fetchone()[0]==1
    assert db.execute('select count(*) from institution_bed_history').fetchone()[0]==5
    assert db.execute('select total_beds,general_beds from institution_beds').fetchone()==(100,100)
    assert db.execute('select event_type,change_type from institution_changes').fetchone()==('baseline','undetermined')
    assert import_data(db,source(),[record()])['status']=='unchanged'

def test_migration_second_execution_refuses_without_changing_data(existing):
    db=existing;seed(db);migrate(db)
    with pytest.raises(psycopg.Error,match='already applied'):
        with db.transaction():migrate(db)
    assert db.execute('select count(*) from medical_institutions').fetchone()[0]==1

def test_unknown_institution_type_does_not_become_psychiatric_classification(existing):
    db=existing;_,inst,_,_=seed(db);migrate(db)
    db.execute('update medical_institutions set kind=null where id=%s',(inst,))
    db.execute('update institution_beds set psychiatric_beds=0 where institution_id=%s',(inst,))
    assert db.execute('select psychiatric_class,needs_review from medical_institutions').fetchone()==('不明',False)


def test_bulk_import_preserves_manual_classification_and_old_evidence(existing):
    from tests.test_postgres import record, source, import_data
    db=existing;migrate(db)
    rows=[record(f'40100000{i:02d}') for i in range(1,4)]
    first=import_data(db,source(count=3),rows)
    assert first['strategy']=='set-based-v1'
    db.execute("update medical_institutions set opening_classification='事業承継等' where code=%s",(rows[0]['code'],))
    later=[{**r,'reference_date':'2026-11-01','total_beds':120} for r in rows]
    second=import_data(db,source('b','2026-11-01',3),later)
    assert db.execute('select count(*) from institution_bed_history').fetchone()[0]==30
    assert db.execute('select count(*) from institution_beds where total_beds=120').fetchone()[0]==3
    event=db.execute("select before_data->>'total_beds',after_data->>'total_beds',opening_classification from institution_changes where source_import_id=%s and new_medical_code=%s",(second['source_import_id'],rows[0]['code'])).fetchone()
    assert event==('100','120','事業承継等')
    assert db.execute('select to_regclass(\'pg_temp.directory_import_work\')').fetchone()[0] is None


def test_bulk_duplicate_input_rolls_back_entire_snapshot(existing):
    from tests.test_postgres import record, source, import_data
    db=existing;migrate(db)
    with pytest.raises(psycopg.Error):
        with db.transaction(): import_data(db,source(count=2),[record(),record()])
    assert db.execute('select count(*) from medical_institutions').fetchone()[0]==0
    assert db.execute('select count(*) from source_imports').fetchone()[0]==0
