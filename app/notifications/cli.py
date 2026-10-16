"""Development only: python -m app.notifications.cli --fetch (no Supabase connection)."""
import argparse
import json
from pathlib import Path
from app.notifications.sources import acquire
from app.notifications.store import connect,import_file,audit

def main():
    p=argparse.ArgumentParser(description='届出全体名簿をローカル開発DBへ取得・重複検査付き取込。本番には接続しません。')
    p.add_argument('--revision-context',help='公式の答申・告示・通知の根拠JSON。異常変動の自動承認には使用しません')
    p.add_argument('--fetch',action='store_true');p.add_argument('--manifest',default='data/notifications/manifest.json')
    p.add_argument('--cache',default='data/notifications/files');p.add_argument('--db',default='data/notifications/development-v2.sqlite')
    p.add_argument('--report',default='artifacts/notifications-import.json');args=p.parse_args()
    manifest=Path(args.manifest)
    if args.fetch:
        entries=acquire(args.cache);manifest.parent.mkdir(parents=True,exist_ok=True);manifest.write_text(json.dumps(entries,ensure_ascii=False,indent=2))
    else:entries=json.loads(manifest.read_text())
    context=json.loads(Path(args.revision_context).read_text()) if args.revision_context else []
    db=connect(args.db);results=[]
    try:
        if args.fetch:
            from app.notifications.policy_watch import watch
            print(json.dumps({'policy_watch':watch(db)},ensure_ascii=False),flush=True)
        for entry in entries:
            try:
                for result in import_file(db,entry['path'],entry['bureau'],entry['url'],revision_context=context):
                    results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
            except Exception as e:
                results.append({'status':'failed','bureau':entry['bureau'],'error':str(e)});print(json.dumps(results[-1],ensure_ascii=False),flush=True)
        report={'environment':'local-development-only','database':str(Path(args.db).resolve()),'results':results,'audit':audit(db)}
        out=Path(args.report);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report['audit']),flush=True)
        if any(r['status'] in ['failed','review_required'] for r in results) or not report['audit']['row_counts_match'] or report['audit']['foreign_key_errors'] or report['audit']['pending_review']:raise SystemExit(1)
    finally:db.close()
if __name__=='__main__':main()
