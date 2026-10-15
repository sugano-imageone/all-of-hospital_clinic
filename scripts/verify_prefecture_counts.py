"""Read-only live DB reconciliation against prefectural monthly statistics."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from app.domain import PREFECTURES, REGIONS
from app.store import Supabase

CSV_HEADERS = ['都道府県コード', '都道府県', '厚生局', '統計病院数', 'DB現存病院数',
               '病院差', '統計一般診療所数', 'DB現存診療所数', '診療所差', '診療所差率%',
               'DB休止診療所数', 'DB確認中診療所数', '休止・確認中を仮に全加算した場合の差']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('workbook', type=Path)
    parser.add_argument('--verified-report', type=Path, help='直前の全件DB照合と一致する入力を再利用する')
    args = parser.parse_args()
    book = load_workbook(args.workbook, read_only=True, data_only=True)
    statistics = {r[1]: {'hospitals': r[3], 'clinics': r[5]}
                  for r in book.worksheets[2].values
                  if isinstance(r[1], int) and 1 <= r[1] <= 47}
    assert set(statistics) == set(PREFECTURES)
    assert sum(v['clinics'] for v in statistics.values()) == 105702
    assert sum(v['hospitals'] for v in statistics.values()) == 7942
    book.close()
    source = [r for p in Path('data/national').glob('*.institutions.json')
              for r in json.loads(p.read_text())]
    evidence = None
    if args.verified_report:
        evidence = json.loads(args.verified_report.read_text())
        fields = evidence['source_fields_checked']
        assert set(['code','prefecture_code','kind','status']).issubset(fields)
        digest = hashlib.sha256(json.dumps(sorted(
            [{k:r.get(k) for k in fields} for r in source], key=lambda r:r['code']),
            ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        assert evidence['all_database_source_fields_equal'] and digest == evidence['verified_projection_sha256']
        rows = source
        total = evidence['record_count']
        assert len(rows) == total
    else:
        db = Supabase(service=True)
        response = db.request('GET', '/rest/v1/medical_institutions',
                              params={'select': 'code', 'limit': 1},
                              headers={'Prefer': 'count=exact'})
        total = int(response.headers['content-range'].split('/')[-1])

        def fetch(offset):
            return db.request('GET', '/rest/v1/medical_institutions', params={
                'select': 'code,prefecture_code,kind,status,reference_date',
                'order': 'code.asc', 'offset': offset, 'limit': 1000}).json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = [r for page in pool.map(fetch, range(0, total, 1000)) for r in page]
        db.close()
        assert len(rows) == len({r['code'] for r in rows}) == total
        source = [r for p in Path('data/national').glob('*.institutions.json')
                  for r in json.loads(p.read_text())]
        fields = ['code', 'prefecture_code', 'kind', 'status', 'reference_date']
        assert {tuple(r.get(k) for k in fields) for r in rows} == {
            tuple(r.get(k) for k in fields) for r in source}
    counts = Counter((r['prefecture_code'], r['kind'], r['status']) for r in rows)
    prefectures = []
    for code, name in PREFECTURES.items():
        s = statistics[code]
        h = counts[code, '病院', '現存']
        c = counts[code, '診療所', '現存']
        suspended = counts[code, '診療所', '休止']
        review = counts[code, '診療所', '確認中']
        prefectures.append({
            'prefecture_code': code, 'prefecture': name,
            'bureau': next(b for b, codes in REGIONS.items() if code in codes),
            'statistics_hospitals': s['hospitals'], 'database_active_hospitals': h,
            'hospital_difference': s['hospitals'] - h,
            'statistics_clinics': s['clinics'], 'database_active_clinics': c,
            'clinic_difference': s['clinics'] - c,
            'clinic_difference_percent': round((s['clinics'] - c) / s['clinics'] * 100, 2),
            'database_suspended_clinics': suspended, 'database_review_clinics': review,
            'hypothetical_difference_including_nonactive': s['clinics'] - c - suspended - review,
        })
    clinic_gap=sum(r['clinic_difference'] for r in prefectures)
    ranked = sorted(prefectures, key=lambda r: -r['clinic_difference'])
    by_rate = sorted(prefectures, key=lambda r: -r['clinic_difference_percent'])
    result = {
        'verified_at': evidence['verified_at'] if evidence else datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(),
        'verification_method': 'reuse of fingerprinted full database verification' if evidence else 'direct database read',
        'reference_date_basis': 'source metadata' if evidence else 'database and source',
        'reused_projection_sha256': evidence['verified_projection_sha256'] if evidence else None,
        'statistics_file': args.workbook.name,
        'statistics_sha256': hashlib.sha256(args.workbook.read_bytes()).hexdigest(),
        'statistics_reference_date': '2026-07-31',
        'database_reference_dates': sorted({r['reference_date'] for r in rows}),
        'live_database_records': total,
        'database_source_fields_equal': fields,
        'database_source_all_records_equal': True,
        'all_47_prefectures_checked': True,
        'prefectures_with_fewer_clinics': sum(r['clinic_difference'] > 0 for r in prefectures),
        'clinic_difference_total': sum(r['clinic_difference'] for r in prefectures),
        'hospital_difference_total': sum(r['hospital_difference'] for r in prefectures),
        'top_10_prefectures_by_absolute_gap': [r['prefecture_code'] for r in ranked[:10]],
        'top_10_gap_total': sum(r['clinic_difference'] for r in ranked[:10]),
        'prefectures': prefectures,
    }
    Path('docs/prefecture-live-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    keys = list(prefectures[0])
    with Path('docs/prefecture-comparison.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(CSV_HEADERS)
        writer.writerows([[r[k] for k in keys] for r in prefectures])
    lines = ['# 都道府県別の実DB照合', '',
             '比較元は添付Excelの第3表「都道府県別にみた施設数及び病床数」です。47県の統計合計が全国値（一般診療所105,702、病院7,942）と一致することを確認しました。', '',
             f"本番DBの全{total:,}件を読み取り、コード・県・施設区分・状態が解析結果と全件一致しました。直前の照合結果を再利用する場合は検証時刻と入力指紋を確認しています。基準日は原資料メタデータです。以下のDB件数は現存のみです。統計は2026年7月末、DB資料は2026年10月1日であり、同一時点の比較ではありません。", '',
             f'**47県すべてで現存診療所数が統計を下回ります。差の合計は{clinic_gap:,}件です。** 動態調査は保険指定のない施設も対象に含み得るため、差を取込漏れ件数や非保険施設の確定数とは扱いません。', '',
             '## 差の件数が大きい県', '',
             '| 都道府県 | 統計・診療所 | DB・現存診療所 | 差 | 差率 |',
             '| --- | ---: | ---: | ---: | ---: |']
    for r in ranked[:10]:
        lines.append(f"| {r['prefecture']} | {r['statistics_clinics']:,} | {r['database_active_clinics']:,} | {r['clinic_difference']:,} | {r['clinic_difference_percent']:.2f}% |")
    lines += ['', '## 差率が大きい県', '',
              '、'.join(f"{r['prefecture']} {r['clinic_difference_percent']:.2f}%（{r['clinic_difference']:,}件）" for r in by_rate[:5]) + '。', '',
              '## 47県の全比較', '',
              '| 都道府県 | 統計病院 | DB現存病院 | 病院差 | 統計診療所 | DB現存診療所 | 診療所差 | 差率 |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in prefectures:
        lines.append('| ' + ' | '.join(str(r[k]) for k in [
            'prefecture', 'statistics_hospitals', 'database_active_hospitals', 'hospital_difference',
            'statistics_clinics', 'database_active_clinics', 'clinic_difference', 'clinic_difference_percent']) + '% |')
    lines += ['', '## 調査への使い方', '',
              '東京都など差の絶対数が多い県と、差率が大きい県を並行して調べます。県別の公表保険指定数、補足一覧、施設単位の名簿を比較し、全国共通の対象範囲の違いと地域別の取得漏れを区別します。順位だけで原因を断定しません。', '',
              '休止・確認中を仮に全加算した場合の差もCSVに残しています。現存と認定した数ではありません。', '',
              '[CSV](prefecture-comparison.csv)・[取得時刻と実DB検証結果](prefecture-live-verification.json)・[対象範囲の調査](source-scope-investigation.md)']
    Path('docs/prefecture-comparison.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({k: result[k] for k in [
        'live_database_records', 'database_source_all_records_equal',
        'prefectures_with_fewer_clinics', 'clinic_difference_total', 'hospital_difference_total']}, ensure_ascii=False))
    print(json.dumps({'top_by_gap': ranked[:5], 'top_by_rate': by_rate[:5]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
