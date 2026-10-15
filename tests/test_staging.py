import hashlib,json,httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb
from tests.test_existing_migration import existing,migrate
from tests.test_postgres import source,record
from app.importers.cli import snapshot_batches


def begin(db,rows,batches=2):
    db.execute("select set_config('request.jwt.claim.role','service_role',true)")
    src=source(count=len(rows))
    digest=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    return db.execute('select begin_snapshot_upload(%s,%s,%s)',(Jsonb(src),digest,batches)).fetchone()[0]


def upload(db,uid,index,rows):
    db.execute('select upload_snapshot_batch(%s,%s,%s)',(uid,index,Jsonb(rows)))


def test_staging_is_invisible_until_complete_and_resumes(existing):
    db=existing;migrate(db);rows=[record(),record('4010000002')]
    uid=begin(db,rows)['upload_id'];upload(db,uid,0,rows[:1])
    assert db.execute('select count(*) from medical_institutions').fetchone()[0]==0
    assert begin(db,rows)['uploaded_batches']==[0]
    upload(db,uid,0,rows[:1]) # idempotent retry
    with pytest.raises(psycopg.Error,match='incomplete upload'):
        with db.transaction():db.execute('select finish_snapshot_upload(%s)',(uid,))
    upload(db,uid,1,rows[1:])
    result=db.execute('select finish_snapshot_upload(%s)',(uid,)).fetchone()[0]
    assert result['count']==2 and result['transport']=='staged-v1'
    assert db.execute('select count(*) from medical_institutions').fetchone()[0]==2
    assert db.execute('select count(*) from snapshot_upload_batches').fetchone()[0]==0
    assert begin(db,rows)=={'status':'unchanged','count':2}


def test_conflicting_batch_and_region_rejected_without_changes(existing):
    db=existing;migrate(db);rows=[record(),record('4010000002')]
    uid=begin(db,rows)['upload_id'];upload(db,uid,0,rows[:1])
    with pytest.raises(psycopg.Error,match='conflicting batch'):
        with db.transaction():upload(db,uid,0,[{**rows[0],'name':'different'}])
    with pytest.raises(psycopg.Error,match='mismatch'):
        with db.transaction():upload(db,uid,1,[{**rows[1],'prefecture_code':41}])
    assert db.execute('select count(*) from medical_institutions').fetchone()[0]==0


def test_failed_finalize_retains_staging_and_rolls_back_master(existing):
    db=existing;migrate(db);rows=[record(),record()]
    uid=begin(db,rows)['upload_id'];upload(db,uid,0,rows[:1]);upload(db,uid,1,rows[1:])
    with pytest.raises(psycopg.Error):
        with db.transaction():db.execute('select finish_snapshot_upload(%s)',(uid,))
    assert db.execute('select count(*) from medical_institutions').fetchone()[0]==0
    assert db.execute('select count(*) from source_imports').fetchone()[0]==0
    assert db.execute('select count(*) from snapshot_upload_batches').fetchone()[0]==2


def test_staging_unavailable_to_authenticated_users(existing):
    db=existing;migrate(db)
    db.execute("select set_config('request.jwt.claim.role','authenticated',true)")
    db.execute('set local role authenticated')
    for sql in ['select * from snapshot_uploads','select * from snapshot_upload_batches',
                "select begin_snapshot_upload('{}','x',1)"]:
        with pytest.raises(psycopg.Error):
            with db.transaction():db.execute(sql)


def test_upload_batches_bound_record_count_and_utf8_bytes():
    rows=[{'name':'医療機関'*100,'raw':'x'*5000,'id':i} for i in range(1200)]
    batches=list(snapshot_batches(rows))
    assert sum(len(b) for b in batches)==1200
    assert all(len(b)<=200 for b in batches)
    assert all(len(httpx.Request('POST','https://example.invalid/',json={
        'p_upload_id':'11111111-1111-1111-1111-111111111111',
        'p_batch_number':i,'p_records':batch}).content)<512*1024
        for i,batch in enumerate(batches))
