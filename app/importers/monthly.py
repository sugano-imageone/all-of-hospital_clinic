"""Resolve official files, validate bureau coverage, then queue prefectures globally.

One prefecture remains one atomic DB transaction. Failed prefectures do not block
other validated prefectures; progress survives restarts through import identities
and update_jobs. Automatic scheduling stays disabled until source acceptance.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from zoneinfo import ZoneInfo
from app.domain import REGIONS, PREFECTURES
from app.importers.discover import fetch, current_links
from app.importers.kyushu import parse_archive
from app.importers.national import parse_region
from app.importers.cli import apply_snapshot
from app.importers.planning import Task, order_tasks, run_tasks, time_budget
from app.store import Supabase


def prepare_tasks(bureau, entries, directory, now,*,scope_exclusions=None):
    """Validate every source in a bureau before making any of its writes eligible."""
    if len(entries) != 1 or entries[0].get('resolver') != 'official-current-v1' or entries[0].get('scope') != 'full-medical':
        raise ValueError('検証済みの全件配布元resolverが未登録')
    prefs = REGIONS[bureau]
    links = current_links(bureau)
    if len(links) != (len(prefs) if bureau == '九州厚生局' else 1):
        raise ValueError('配布物の件数が想定と異なります')
    records, sources = [], []
    from urllib.parse import urlsplit
    for i, url in enumerate(links):
        path = directory / ('source' + str(i) + Path(urlsplit(url).path).suffix)
        path.write_bytes(fetch(url))
        if bureau == '九州厚生局':
            data, meta = parse_archive(path, prefs[i], now.isoformat())
            for src in meta:
                src.update(prefecture=PREFECTURES[prefs[i]], source_url=url)
        else:
            excluded=[]
            data, meta, errors = parse_region(path, bureau, now.isoformat(), url,excluded_sources=excluded)
            if scope_exclusions is not None:
                scope_exclusions.extend({'bureau':bureau,'source_url':url,**entry} for entry in excluded)
            if errors:
                raise ValueError('未解決のファイル構造。既存データを維持')
        records.extend(data)
        sources.extend(meta)
    if sorted(src['prefecture_code'] for src in sources) != sorted(prefs):
        raise ValueError('担当県の収集が不完全または重複')
    if any(src['reference_date'][:7] != now.strftime('%Y-%m') for src in sources):
        raise ValueError('公表基準日が対象月ではありません')
    if len({r['code'] for r in records}) != len(records):
        raise ValueError('厚生局内コード重複')
    tasks = []
    for src in sources:
        data = [r for r in records if r['source_file'] == src['filename'] and r['prefecture_code'] == src['prefecture_code']]
        if not data or len(data) != src['record_count']:
            raise ValueError('県別スナップショット件数不一致')
        path = directory / f"{src['prefecture_code']}.json"
        path.write_text(json.dumps(data, ensure_ascii=False))
        tasks.append(Task(src, path))
    return tasks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', default='config/sources.json')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--scheduled', action='store_true')
    parser.add_argument('--budget-seconds', type=int, default=19800, help='取得から再試行までの予算。既定5時間30分')
    parser.add_argument('--max-attempts', type=int, choices=range(1, 4), default=3)
    parser.add_argument('--output', default='artifacts/monthly.json')
    args = parser.parse_args()
    now = datetime.now(ZoneInfo('Asia/Tokyo'))
    if args.scheduled and now.day not in range(20, 26):
        print('JST20〜25日以外のため実行しません')
        return
    started = time.monotonic()
    deadline = started + time_budget(now, args.budget_seconds, args.scheduled)
    month = now.date().replace(day=1).isoformat()
    manifest = json.loads(Path(args.manifest).read_text())['sources']
    db = Supabase(service=True) if args.apply else None
    results, plan, attempts, outcomes = {}, [], [], []
    scope_exclusions=[]
    persisted, persistence_errors = {}, []
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps({'month': month, 'apply': args.apply, 'queue_unit': 'prefecture',
            'budget_seconds': args.budget_seconds, 'elapsed_seconds': round(time.monotonic() - started, 1),
            'plan': plan, 'attempts': attempts, 'prefectures': outcomes, 'results': list(results.values()),
            'scope_exclusions':scope_exclusions,
            'persistence_errors': persistence_errors}, ensure_ascii=False, indent=2))

    def progress(rows):
        nonlocal outcomes
        outcomes = rows
        changed = {r['bureau'] for r in rows if persisted.get(r['prefecture_code']) != r}
        for bureau in changed:
            current = [r for r in rows if r['bureau'] == bureau]
            finished = len(current) == len(REGIONS[bureau]) and all(r['status'] in ('success', 'unchanged', 'validated') for r in current)
            result = {'bureau': bureau, 'status': 'success' if finished else 'failed',
                      'completed': current, 'errors': [] if finished else ['未完了の県あり。次の試行で再確認']}
            results[bureau] = result
            if db:
                try:
                    db.request('POST', '/rest/v1/update_jobs', params={'on_conflict': 'month,bureau'},
                        headers={'Prefer': 'resolution=merge-duplicates'}, json={'month': month, 'bureau': bureau,
                        'status': result['status'], 'attempted_at': datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(), 'details': result})
                except Exception as exc:
                    persistence_errors.append({'bureau': bureau, 'error': type(exc).__name__})
                    continue
            for row in current:
                persisted[row['prefecture_code']] = row
        save()

    try:
        jobs = db.all('update_jobs', [('order', 'month.desc,bureau.asc')]) if db else []
        done = {j['bureau'] for j in jobs if j['month'] == month and j['status'] == 'success'}
        observations = [r for j in jobs for r in j.get('details', {}).get('completed', []) if 'prefecture_code' in r]
        imports = db.all('source_imports', [('select', 'prefecture_code,sha256,parser_version'), ('status', 'eq.success')]) if db else []
        completed = {(r['prefecture_code'], r['sha256'], r['parser_version']) for r in imports}
        with TemporaryDirectory() as tmp:
            tasks = []
            for index, (bureau, prefs) in enumerate(REGIONS.items()):
                if bureau in done:
                    results[bureau] = {'bureau': bureau, 'status': 'previously_succeeded'}
                    continue
                directory = Path(tmp) / str(index)
                directory.mkdir()
                try:
                    if deadline - time.monotonic() < 120:
                        raise ValueError('当日の時間予算不足')
                    tasks.extend(prepare_tasks(bureau, [s for s in manifest if s['bureau'] == bureau], directory, now,
                                               scope_exclusions=scope_exclusions))
                except Exception as exc:
                    result = {'bureau': bureau, 'status': 'failed', 'completed': [],
                              'errors': [type(exc).__name__ + ': 取得・解析・公表月・県の検証に失敗。直前データを維持']}
                    results[bureau] = result
                    if db:
                        db.request('POST', '/rest/v1/update_jobs', params={'on_conflict': 'month,bureau'},
                            headers={'Prefer': 'resolution=merge-duplicates'}, json={'month': month, 'bureau': bureau,
                            'status': 'failed', 'attempted_at': now.isoformat(), 'details': result})
                    save()
            ordered = order_tasks(tasks, observations)
            plan = [{'prefecture_code': t.prefecture_code, 'prefecture': t.source['prefecture'],
                     'count': t.source['record_count'], 'estimated_seconds': t.estimated_seconds} for t in ordered]
            save()
            def apply(task):
                if not db:
                    return {'status': 'validated', 'count': task.source['record_count']}
                return apply_snapshot(db, json.loads(task.path.read_text()), task.source, deadline=deadline)
            outcomes, attempts = run_tasks(ordered, apply, deadline=deadline, max_attempts=args.max_attempts,
                completed_keys=completed, progress=progress)
    finally:
        save()
        if db:
            db.close()
    print(json.dumps({'results': list(results.values()), 'estimated_first_pass_seconds': sum(p['estimated_seconds'] for p in plan),
                      'elapsed_seconds': round(time.monotonic() - started, 1)}, ensure_ascii=False))
    if persistence_errors or any(r['status'] == 'failed' for r in results.values()):
        raise SystemExit(1)

if __name__ == '__main__':
    main()
