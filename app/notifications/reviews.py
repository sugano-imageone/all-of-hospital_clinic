"""Explicit development review; never auto-approve a revision or connect to production."""
import argparse,json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from app.notifications.store import connect,dumps
from app.notifications.guards import validate_context,compare

def approve(db,snapshot_id,*,note,evidence_url,context=None):
    p=urlsplit(evidence_url)
    if not note.strip() or p.scheme!='https' or not (p.hostname in ['www.kanpo.go.jp','kanpo.go.jp','kanpou.npb.go.jp','mhlw.go.jp'] or (p.hostname or '').endswith('.mhlw.go.jp')) or p.username:raise ValueError('確認理由と厚生労働省の根拠URLが必要です')
    context=validate_context(context or [])
    with db:
        db.execute('BEGIN IMMEDIATE')
        r=db.execute('SELECT r.*,s.prefecture,s.reference_date FROM snapshot_reviews r JOIN snapshots s ON s.id=r.snapshot_id WHERE r.snapshot_id=?',(snapshot_id,)).fetchone()
        if not r or r['status']!='review_required':raise ValueError('切替保留の資料ではありません')
        current=db.execute('SELECT snapshot_id FROM current_snapshots WHERE prefecture=?',(r['prefecture'],)).fetchone()
        if (current[0] if current else None)!=r['old_snapshot_id']:raise ValueError('比較後に現行データが更新されています。再比較が必要です')
        if r['reference_date']>datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat():raise ValueError('将来の基準日は有効化できません')
        if context and not any(e['type'] in ['官報告示','告示'] and e.get('effective_date') and e['effective_date']<=r['reference_date'] and e['published_date']<=datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat() for e in context):raise ValueError('答申・通知・将来予定だけでは切り替えません。適用済みの厚生労働大臣の官報告示が必要です')
        db.execute('INSERT INTO current_snapshots VALUES(?,?) ON CONFLICT(prefecture) DO UPDATE SET snapshot_id=excluded.snapshot_id',(r['prefecture'],snapshot_id))
        db.execute("UPDATE snapshot_reviews SET status='approved',context_json=?,approval_note=? WHERE snapshot_id=?",(dumps(context),dumps({'note':note,'evidence_url':evidence_url,'checked_at':datetime.now(ZoneInfo('Asia/Tokyo')).isoformat()}),snapshot_id))
        # Display names still come from the active original rows, never generated text.
        rows=db.execute('SELECT code,raw_json FROM source_rows WHERE snapshot_id=? AND code IS NOT NULL GROUP BY code',(snapshot_id,)).fetchall()
        for row in rows:
            raw=json.loads(row['raw_json']);db.execute('UPDATE institutions SET name=? WHERE code=?',(raw[7],row['code']))

def recompare(db,snapshot_id):
    with db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT r.status,s.prefecture,s.reference_date FROM snapshot_reviews r JOIN snapshots s ON s.id=r.snapshot_id WHERE r.snapshot_id=?',(snapshot_id,)).fetchone()
        if not row or row['status']!='review_required':raise ValueError('保留中の候補ではありません')
        current=db.execute('SELECT snapshot_id FROM current_snapshots WHERE prefecture=?',(row['prefecture'],)).fetchone()
        if not current:raise ValueError('比較する現行版がありません')
        result=compare(db,current[0],snapshot_id)
        historical='現行より古い基準日' in result['reasons']
        reasons=result['reasons'] or ['再比較後の最終確認待ち']
        db.execute('UPDATE snapshot_reviews SET old_snapshot_id=?,status=?,reasons_json=?,metrics_json=? WHERE snapshot_id=?',(current[0],'historical' if historical else 'review_required',dumps(reasons),dumps(result['metrics']),snapshot_id))

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/notifications/development-v2.sqlite');p.add_argument('--approve',type=int);p.add_argument('--recompare',type=int);p.add_argument('--note',default='');p.add_argument('--evidence-url',default='');p.add_argument('--revision-context');a=p.parse_args();db=connect(a.db)
    try:
        if a.recompare:recompare(db,a.recompare)
        if a.approve:approve(db,a.approve,note=a.note,evidence_url=a.evidence_url,context=json.loads(open(a.revision_context).read()) if a.revision_context else [])
        for r in db.execute("SELECT * FROM snapshot_reviews WHERE status='review_required'"):print(dumps(dict(r)))
    finally:db.close()
if __name__=='__main__':main()
