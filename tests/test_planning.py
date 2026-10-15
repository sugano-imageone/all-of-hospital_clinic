from datetime import datetime
from pathlib import Path
import json
from fastapi import HTTPException
from app.importers.planning import Task, order_tasks, run_tasks, time_budget, JST
from app.importers import monthly


def task(code, count=100, bureau='九州厚生局'):
    return Task({'prefecture_code': code, 'prefecture': str(code), 'bureau': bureau,
                 'record_count': count, 'sha256': str(code), 'parser_version': 'test'}, Path('unused'))


def test_queue_uses_prefectures_globally_not_bureaus():
    tasks = [task(40, 4537), task(13, 13960, '関東信越厚生局'), task(27, 8875, '近畿厚生局'), task(12, 3724, '関東信越厚生局')]
    assert [t.prefecture_code for t in order_tasks(tasks)] == [13, 27, 40, 12]


def test_recent_throughput_changes_estimated_order_and_ignores_unchanged():
    observations = [dict(prefecture_code=1, count=100, seconds=20, status='success'),
                    dict(prefecture_code=2, count=100, seconds=.001, status='unchanged')]
    tasks = order_tasks([task(1, 100), task(2, 500)], observations)
    assert [t.prefecture_code for t in tasks] == [1, 2]
    assert tasks[1].estimated_seconds > 10


def test_failed_prefecture_retries_after_other_prefectures_and_checkpoints():
    calls, progress = [], []
    def apply(t):
        calls.append(t.prefecture_code)
        if calls == [13]:
            raise HTTPException(503)
        return {'status': 'success', 'count': t.source['record_count']}
    rows, attempts = run_tasks([task(13), task(27)], apply, deadline=1000,
        monotonic=lambda: 0, sleep=lambda _: None, progress=lambda rows: progress.append(list(rows)))
    assert calls == [13, 27, 13]
    assert len(progress) == 3 and len(attempts) == 3
    assert all(r['status'] == 'success' for r in rows)


def test_committed_source_skips_writes_and_new_source_still_runs():
    calls = []
    rows, _ = run_tasks([task(13), task(27)], lambda t: calls.append(t.prefecture_code) or {'status': 'success'},
        deadline=1000, monotonic=lambda: 0, completed_keys={(13, '13', 'test')})
    assert calls == [27] and rows[0]['status'] == 'unchanged'


def test_budget_exhaustion_defers_without_starting_partial_snapshot():
    def forbidden(t):
        raise AssertionError('must not start a request without sufficient time')
    rows, _ = run_tasks([task(13)], forbidden, deadline=119, monotonic=lambda: 0)
    assert rows[0]['status'] == 'deferred'


def test_transient_retries_are_bounded_and_auth_errors_do_not_retry():
    calls = []
    def apply(t):
        calls.append(t.prefecture_code)
        raise HTTPException(503 if t.prefecture_code == 13 else 401)
    rows, _ = run_tasks([task(13), task(27)], apply, deadline=1000,
        monotonic=lambda: 0, sleep=lambda _: None)
    assert calls == [13, 27, 13, 13]
    assert all(r['status'] == 'failed' for r in rows)


def test_scheduled_budget_respects_jst_midnight_and_manual_budget():
    now = datetime(2026, 10, 20, 23, 50, tzinfo=JST)
    assert time_budget(now, 14400, True) == 570
    assert time_budget(now, 14400, False) == 14400


def test_monthly_worker_globally_orders_47_prefectures_and_persists_progress(monkeypatch, tmp_path):
    tasks = []
    for bureau, prefs in monthly.REGIONS.items():
        for code in prefs:
            t = task(code, code * 10, bureau)
            t.path = tmp_path / f'{code}.json'
            t.path.write_text('[]')
            tasks.append(t)
    calls, writes = [], []
    class DB:
        def __init__(self, **kwargs): pass
        def all(self, *args): return []
        def request(self, method, path, **kwargs): writes.append(kwargs['json'])
        def close(self): pass
    monkeypatch.setattr(monthly, 'Supabase', DB)
    monkeypatch.setattr(monthly, 'prepare_tasks', lambda bureau, *args: [t for t in tasks if t.source['bureau'] == bureau])
    monkeypatch.setattr(monthly, 'apply_snapshot', lambda db, records, src: calls.append(src['prefecture_code']) or {'status': 'success', 'count': src['record_count']})
    output = tmp_path / 'report.json'
    monkeypatch.setattr('sys.argv', ['monthly', '--apply', '--output', str(output)])
    monthly.main()
    assert calls == list(range(47, 0, -1))
    assert len(writes) == 47
    final = {r['bureau']: r for r in writes}
    assert len(final) == 8 and all(r['status'] == 'success' for r in final.values())
    report = json.loads(output.read_text())
    assert report['queue_unit'] == 'prefecture' and len(report['prefectures']) == 47
