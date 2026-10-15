"""Verify master protections against the upgraded production-shaped schema."""
import re
from pathlib import Path
import psycopg
import pytest
from tests.test_existing_migration import existing, migrate

OWNER='2530b4ab-17b3-4f22-829c-c6b21ef5a13c'
ADMIN='2530b4ab-17b3-4f22-829c-c6b21ef5a130'
VIEWER='2530b4ab-17b3-4f22-829c-c6b21ef5a131'

def test_master_role_repeatable_and_protected(existing):
    db=existing
    db.execute('insert into auth.users(id,email) values(%s,%s)',(OWNER,'sugano@imageone.co.jp'))
    migrate(db)
    for uid,role in [(ADMIN,'admin'),(VIEWER,'user')]:
        db.execute('insert into auth.users(id) values(%s)',(uid,))
        db.execute('update app_members set role=%s,approved=true where user_id=%s',(role,uid))
    script=(Path(__file__).parents[1]/'sql/015_master_role.sql').read_text()
    script=re.sub(r'^(begin|commit);\s*$', '',script,flags=re.MULTILINE)
    db.execute(script);db.execute(script)
    assert db.execute('select role,approved from app_members where user_id=%s',(OWNER,)).fetchone()==('master',True)
    def caller(uid):
        db.execute('reset role')
        db.execute("select set_config('request.jwt.claim.sub',%s,true)",(uid,))
        db.execute("select set_config('request.jwt.claim.role','authenticated',true)")
        db.execute('set local role authenticated')
    caller(VIEWER)
    with pytest.raises(psycopg.Error,match='admin required'):
        with db.transaction():db.execute("select set_member(%s,'admin',true)",(ADMIN,))
    caller(ADMIN)
    assert db.execute('select is_admin(),is_master()').fetchone()==(True,False)
    with pytest.raises(psycopg.Error,match='master membership is protected'):
        with db.transaction():db.execute("select set_member(%s,'user',false)",(OWNER,))
    with pytest.raises(psycopg.Error,match='only the designated owner'):
        with db.transaction():db.execute("select set_member(%s,'master',true)",(VIEWER,))
    with pytest.raises(psycopg.Error):
        with db.transaction():db.execute("update app_members set role='admin' where user_id=%s",(VIEWER,))
    caller(OWNER)
    assert db.execute('select is_admin(),is_master()').fetchone()==(True,True)
    db.execute("select set_member(%s,'admin',true)",(VIEWER,))
    assert db.execute('select role from app_members where user_id=%s',(VIEWER,)).fetchone()==('admin',)
