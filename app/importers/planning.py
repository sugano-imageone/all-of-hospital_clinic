"""Prefecture-level capacity planning and bounded retries for one update day."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
import json
import time as clock
from zoneinfo import ZoneInfo
from fastapi import HTTPException

JST = ZoneInfo('Asia/Tokyo')

@dataclass
class Task:
    source: dict
    path: Path
    estimated_seconds: float = 0

    @property
    def prefecture_code(self):
        return self.source['prefecture_code']


def order_tasks(tasks, observations=(), *, fallback_per_record=.025, safety_factor=1.5):
    """Longest estimated task first; break ties by record count and prefecture code.

    Exclude unchanged snapshots from throughput samples: they do not measure writes.
    Retain the slowest of up to three recent successful samples per prefecture.
    """
    samples = {}
    for row in observations:
        if row.get('status') != 'success' or not row.get('count') or row.get('seconds', 0) <= 0:
            continue
        values = samples.setdefault(row['prefecture_code'], [])
        if len(values) < 3:
            values.append(row['seconds'] / row['count'])
    for task in tasks:
        per_record = max(samples.get(task.prefecture_code, [fallback_per_record]))
        task.estimated_seconds = round(2 + task.source['record_count'] * per_record * safety_factor, 1)
    return sorted(tasks, key=lambda t: (-t.estimated_seconds, -t.source['record_count'], t.prefecture_code))


def time_budget(now, configured_seconds, scheduled=False):
    """Scheduled attempts never deliberately run into the next JST update day."""
    if configured_seconds <= 0:
        raise ValueError('budget must be positive')
    if not scheduled:
        return configured_seconds
    local = now.astimezone(JST)
    midnight = datetime.combine(local.date() + timedelta(days=1), time.min, JST)
    return max(0, min(configured_seconds, (midnight - local).total_seconds() - 30))


def run_tasks(tasks, apply, *, deadline, max_attempts=3, completed_keys=frozenset(),
              progress=lambda rows: None, monotonic=clock.monotonic, sleep=clock.sleep):
    """One DB writer. Retry failed prefectures after every untouched prefecture.

    Persist each outcome before moving on. Exact source identity skips already
    committed snapshots; the DB's idempotence check remains the final authority.
    """
    pending = list(tasks)
    outcomes = {}
    history = []
    for attempt in range(1, max_attempts + 1):
        retry = []
        for task in pending:
            src = task.source
            key = (task.prefecture_code, src['sha256'], src['parser_version'])
            row = {'prefecture_code': task.prefecture_code, 'prefecture': src['prefecture'],
                   'bureau': src['bureau'], 'count': src['record_count'], 'attempt': attempt,
                   'estimated_seconds': task.estimated_seconds, 'sha256': src['sha256'],
                   'parser_version': src['parser_version']}
            if key in completed_keys:
                row.update(status='unchanged', seconds=0)
            elif deadline - monotonic() < 120:
                row.update(status='deferred', seconds=0, error='当日の実行時間予算不足')
            else:
                started = monotonic()
                try:
                    result = apply(task)
                    row.update(status=result['status'], result=result)
                except HTTPException as exc:
                    row.update(status='failed', error='認証またはデータAPI処理に失敗', http_status=exc.status_code)
                    if exc.status_code in (502, 503) and attempt < max_attempts:
                        retry.append(task)
                except Exception as exc:
                    # Validation errors require investigation, not blind reimport.
                    row.update(status='failed', error=type(exc).__name__ + ': 検証または登録に失敗')
                row['seconds'] = round(monotonic() - started, 3)
            outcomes[task.prefecture_code] = row
            history.append(row)
            progress(list(outcomes.values()))
        if not retry:
            break
        pending = retry
        delay = min(30, 5 * attempt)
        if deadline - monotonic() < 120 + delay:
            break
        sleep(delay)
    return list(outcomes.values()), history


def main():
    import argparse
    parser = argparse.ArgumentParser(description='県別件数から更新時間と取込順を見積もる')
    parser.add_argument('--directory', default='data/national')
    parser.add_argument('--output', default='artifacts/update-capacity.json')
    args = parser.parse_args()
    tasks = []
    for path in sorted(Path(args.directory).glob('*.sources.json')):
        for source in json.loads(path.read_text()):
            tasks.append(Task(source, path))
    if not tasks or len({t.prefecture_code for t in tasks}) != len(tasks):
        raise ValueError('県別入力が空または重複')
    ordered = order_tasks(tasks)
    first = round(sum(t.estimated_seconds for t in ordered), 1)
    reserve = 1800
    report = {'scope': '検証済み主一覧。全国網羅性は別途確認中',
        'record_count': sum(t.source['record_count'] for t in ordered), 'prefecture_count': len(ordered),
        'model': {'fallback_seconds_per_record': .025, 'safety_factor': 1.5,
                  'request_overhead_seconds': 2, 'download_parse_reserve_seconds': reserve},
        'estimated_first_pass_seconds': first,
        'estimated_three_passes_with_download_seconds': round(first * 3 + reserve, 1),
        'run_budget_seconds': 14400, 'target_day_seconds': 86400,
        'production_bulk_performance_verified': False,
        'prefectures': [{'prefecture_code': t.prefecture_code, 'prefecture': t.source['prefecture'],
                         'record_count': t.source['record_count'], 'estimated_seconds': t.estimated_seconds} for t in ordered]}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'prefectures'}, ensure_ascii=False))

if __name__ == '__main__':
    main()
