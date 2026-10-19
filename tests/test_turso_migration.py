import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db():
    c = sqlite3.connect(':memory:')
    c.executescript((ROOT / 'migration/schema.sql').read_text())
    yield c
    c.close()


def search(q):
    script = "import {buildSearch} from './worker/src/search.mjs'; console.log(JSON.stringify(buildSearch(JSON.parse(process.argv[1]))));"
    return json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script, json.dumps(q)], cwd=ROOT))


def test_unfiled_requires_facility_item_and_validated_adopted_coverage(db):
    # Use the exact generated API SQL, exercising the SQLite joins and anti-join.
    from_fields = subprocess.check_output(['node', '--input-type=module', '-e', "import {PUBLIC_FIELDS} from './worker/src/search.mjs';console.log(JSON.stringify(PUBLIC_FIELDS));"], cwd=ROOT)
    fields = json.loads(from_fields)
    db.execute('CREATE TABLE medical_institutions (' + ','.join(f+' TEXT' for f in fields) + ')')
    db.executemany("INSERT INTO medical_institutions(code,bureau,kind,status) VALUES(?,'北海道厚生局','診療所','現存')", [('0110000001',), ('0110000002',), ('0110000003',)])
    db.execute("INSERT INTO notification_snapshots VALUES('s','北海道厚生局','staging','{}','{}',2,1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO notification_current VALUES('北海道厚生局','s')")
    db.execute("INSERT INTO notification_items VALUES('s','検査','検査')")
    db.executemany("INSERT INTO notification_facilities VALUES('s',?,1,'2026-10-01')", [('0110000001',), ('0110000002',)])
    db.execute("INSERT INTO notification_filings VALUES('s','0110000001','検査','calculation_start',NULL,'2026-10-01','{}')")
    q = search({'kind':'診療所','use_notifications':True,'notification_item':'検査','notification_status':'unfiled'})
    assert db.execute(q['sql'], q['args']).fetchall() == []
    db.execute("UPDATE notification_snapshots SET state='validated' WHERE id='s'")
    db.execute("INSERT INTO notification_current VALUES('北海道厚生局','s')")
    rows = db.execute(q['sql'], q['args']).fetchall()
    assert [r[fields.index('code')] for r in rows] == ['0110000002']
    q = search({'use_notifications':True,'notification_item':'未取得項目','notification_status':'unfiled'})
    assert db.execute(q['sql'],q['args']).fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE notification_snapshots SET state='staging' WHERE id='s'")


def test_parameterized_search_and_export_page_have_same_conditions():
    q = search({'name':"' OR 1=1 --_%",'minimum':1,'maximum':19,'kind':'診療所','page_size':1000})
    assert "OR 1=1" not in q['sql']
    assert q['args'][-2:] == [1000,0]
    assert q['countArgs'] == q['args'][:-2]
    assert q['sql'].split(' FROM ')[1].split(' ORDER BY ')[0] == q['countSql'].split(' FROM ')[1]


def test_maintenance_default_is_on(db):
    assert db.execute('SELECT active FROM application_maintenance').fetchone() == (1,)


def test_retry_only_failed_current_month_and_jst_date(db):
    import datetime
    from scripts.migration.snapshots import targets, NAMES
    utc = datetime.timezone.utc
    with pytest.raises(ValueError):
        targets(db, datetime.datetime(2026, 10, 25, tzinfo=utc))
    all_bureaus = targets(db, datetime.datetime(2026, 10, 19, 15, tzinfo=utc))
    assert set(all_bureaus) == set(NAMES.values())
    with db:
        db.execute("UPDATE notification_jobs SET status='success' WHERE bureau=?", (all_bureaus[0],))
    assert targets(db, datetime.datetime(2026, 10, 24, 15, tzinfo=utc)) == all_bureaus[1:]
    with pytest.raises(ValueError):
        targets(db, datetime.datetime(2026, 11, 24, 15, tzinfo=utc))


def test_atomic_adoption_rejects_incomplete_and_concurrent_changes(db):
    import datetime
    from scripts.migration.snapshots import adopt
    payload = json.dumps({'latest_selection_resolved':True,'versions':[{'fields':{},'details':[],'source_rows':[{'file':'x','sheet':'Sheet1','row':3}]}]})
    with db:
        db.execute("INSERT INTO notification_snapshots VALUES('s','北海道厚生局','validated','{\"1\":\"2026-10-01\"}','{\"files\":[{}]}',2,1)")
        db.execute("INSERT INTO notification_items VALUES('s','検査','検査')")
        db.execute("INSERT INTO notification_facilities VALUES('s','0110000001',1,'2026-10-01')")
        db.execute("INSERT INTO notification_filings VALUES('s','0110000001','検査','calculation_start',NULL,NULL,?)", (payload,))
    with pytest.raises(ValueError, match='Incomplete snapshot'):
        adopt(db, 's', None, datetime.date(2026,10,19))
    assert db.execute('SELECT count(*) FROM notification_current').fetchone()[0] == 0
    with db:
        db.execute("INSERT INTO notification_facilities VALUES('s','0110000002',1,'2026-10-01')")
    adopt(db, 's', None, datetime.date(2026,10,19))
    with pytest.raises(ValueError, match='Current pointer changed'):
        adopt(db, 's', None, datetime.date(2026,10,19))
    assert db.execute('SELECT snapshot_id FROM notification_current').fetchone() == ('s',)


def test_member_update_is_atomic_audited_and_protects_master_and_self(db):
    with db:
        db.executemany('INSERT INTO app_members(firebase_uid,email,role,approved) VALUES(?,?,?,1)',
                       [('owner','owner@example.invalid','master'),('admin','admin@example.invalid','admin'),('viewer','viewer@example.invalid','user')])
    for actor, target in [('viewer','admin'),('admin','owner'),('admin','admin')]:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO member_actions(actor_uid,target_uid,role,approved,disabled) VALUES(?,?,'admin',1,0)", (actor,target))
    assert db.execute('SELECT count(*) FROM member_actions').fetchone()[0] == 0
    with db:
        db.execute("INSERT INTO member_actions(actor_uid,target_uid,role,approved,disabled) VALUES('admin','viewer','user',0,1)")
    approved, disabled, revoked = db.execute("SELECT approved,disabled,tokens_valid_after FROM app_members WHERE firebase_uid='viewer'").fetchone()
    assert (approved,disabled) == (0,1)
    assert revoked > 0
    assert db.execute('SELECT count(*) FROM member_actions').fetchone()[0] == 1


def test_new_month_replaces_then_retires_previous_snapshot_atomically(db):
    from scripts.migration.snapshots import adopt
    import datetime
    payload=json.dumps({'latest_selection_resolved':True,'versions':[{'fields':{},'details':[],'source_rows':[{'file':'source.xlsx','sheet':'Sheet1','row':3}]}]})
    with db:
        for snapshot,date in [('old','2026-09-01'),('new','2026-10-01')]:
            db.execute('INSERT INTO notification_snapshots VALUES(?,?,\'validated\',?,?,1,1)',(snapshot,'北海道厚生局',json.dumps({'1':date}),json.dumps({'files':[{}]})))
            db.execute('INSERT INTO notification_facilities VALUES(?,?,1,?)',(snapshot,'0110000001',date))
            db.execute('INSERT INTO notification_items VALUES(?,?,?)',(snapshot,'検査','検査'))
            db.execute('INSERT INTO notification_filings VALUES(?,?,?,\'calculation_start\',NULL,NULL,?)',(snapshot,'0110000001','検査',payload))
        db.execute("INSERT INTO app_members(firebase_uid,email,role) VALUES('member','viewer@example.invalid','user')")
        db.execute("INSERT INTO app_login_history(session_key,firebase_uid,email,actor_role,auth_time) VALUES('session','member','viewer@example.invalid','user',1)")
    adopt(db,'old',None,datetime.date(2026,10,19))
    with pytest.raises(ValueError,match='Verified restore archive required'):
        adopt(db,'new','old',datetime.date(2026,10,19),prune_previous=True)
    assert db.execute('SELECT snapshot_id FROM notification_current').fetchone()==('old',)
    receipt={'snapshot_id':'old','restore_verified':True,'sha256':'a'*64,'archive_location':'offline-unit-test-fixture'}
    adopt(db,'new','old',datetime.date(2026,10,19),prune_previous=True,archive_receipt=receipt)
    assert db.execute('SELECT snapshot_id FROM notification_current').fetchone()==('new',)
    assert db.execute('SELECT id FROM notification_snapshots').fetchall()==[('new',)]
    for table in ('notification_facilities','notification_items','notification_filings'):
        assert db.execute('SELECT DISTINCT snapshot_id FROM '+table).fetchall()==[('new',)]
    assert db.execute('SELECT count(*) FROM app_members').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM app_login_history').fetchone()[0]==1


def test_history_retention_only_after_success_and_preserves_failed_prefecture(db):
    from scripts.migration.snapshots import retire_history_for_cycle
    with db:
        db.execute('CREATE TABLE source_imports(id INTEGER PRIMARY KEY,prefecture_code INTEGER,status TEXT,retrieved_at TEXT)')
        db.executemany('INSERT INTO source_imports VALUES(?,?,\'success\',?)',[(9,1,'2026-09-20T00:00:00Z'),(10,1,'2026-10-25T00:00:00Z'),(11,1,'2026-10-20T00:00:00Z'),(8,2,'2026-09-20T00:00:00Z')])
        db.execute('CREATE TABLE medical_institutions(id TEXT PRIMARY KEY,prefecture_code INTEGER,source_import_id INTEGER)')
        db.executemany('INSERT INTO medical_institutions VALUES(?,?,?)',[('one',1,10),('two',2,8)])
        for table in ('institution_changes','institution_bed_history'):
            db.execute('CREATE TABLE '+table+'(institution_id TEXT,source_import_id INTEGER)')
            db.executemany('INSERT INTO '+table+' VALUES(?,?)',[('one',9),('one',10),('one',11),('two',8)])
    with pytest.raises(ValueError,match='whole update cycle'):
        with db:retire_history_for_cycle(db,'2026-10',[1],[10],None)
    with pytest.raises(ValueError,match='Verified previous history archive required'):
        with db:retire_history_for_cycle(db,'2026-10',[1],[10,11],None)
    assert db.execute('SELECT count(*) FROM institution_changes').fetchone()[0]==4
    proof={'cycle_month':'2026-10','restore_verified':True,'sha256':'b'*64,'archive_location':'offline-test','source_import_ids':[9]}
    with db:
        deleted=retire_history_for_cycle(db,'2026-10',[1],[10,11],proof)
    assert deleted=={'institution_changes':1,'institution_bed_history':1}
    for table in deleted:
        assert db.execute('SELECT * FROM '+table+' ORDER BY institution_id,source_import_id').fetchall()==[('one',10),('one',11),('two',8)]
    with pytest.raises(ValueError,match='not been adopted'):
        with db:
            db.execute("UPDATE medical_institutions SET source_import_id=9 WHERE id='one'")
            retire_history_for_cycle(db,'2026-10',[1],[10,11],proof)


@pytest.mark.parametrize('compress_beds',[True,False])
def test_cold_history_compression_is_lossless(tmp_path,compress_beds):
    import hashlib
    import zlib
    from scripts.migration.compact_history import compact
    source=tmp_path/'source.sqlite'
    c=sqlite3.connect(source)
    for table in ('institution_changes','institution_bed_history'):
        c.execute('CREATE TABLE '+table+'(id TEXT PRIMARY KEY,institution_id TEXT,source_import_id INTEGER,payload TEXT)')
        c.execute('INSERT INTO '+table+' VALUES(?,?,?,?)',('row-1','facility',1,json.dumps({'備考':'原本を保持','null':None})))
    c.commit();c.close()
    report=compact(source,tmp_path/'compressed.sqlite',compress_beds)
    assert report['verified_rows']==2
    c=sqlite3.connect(tmp_path/'compressed.sqlite')
    for payload,digest in c.execute('SELECT record_zlib,raw_sha256 FROM history_records'):
        raw=zlib.decompress(payload)
        assert hashlib.sha256(raw).hexdigest()==digest
        assert json.loads(json.loads(raw)['payload'])['備考']=='原本を保持'
    if not compress_beds:
        assert c.execute('SELECT payload FROM institution_bed_history').fetchone()[0] == json.dumps({'備考':'原本を保持','null':None})
