"""Convert verified official latest rows, preserving selected versions and provenance.

Local conversion only; does not select a production snapshot or prune anything.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'facility_notifications'))
from production import validate, NAMES
from build_index import REGIONS

SCHEMA = Path(__file__).resolve().parents[2] / 'migration/schema.sql'


def convert(source, destination, root, copies=1, bureau_filter=None):
    if destination.exists():
        raise ValueError('Destination exists; no overwrite')
    src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    manifest = json.loads((root / 'manifest.json').read_text())
    files = manifest['files']
    for f in files:
        if f['role'] == 'full':
            if f['status'] != 'downloaded_validated' or hashlib.sha256((root / f['path']).read_bytes()).hexdigest() != f['sha256']:
                raise ValueError('Original archive hash mismatch')
    # Validate referenced expanded files, not just the outer ZIPs.
    checks = json.loads((root / 'validation.json').read_text())
    for sheet in checks['sheets']:
        if hashlib.sha256((root / sheet['source_file']).read_bytes()).hexdigest() != sheet['source_sha256']:
            raise ValueError('Worksheet file hash mismatch')
    c = sqlite3.connect(destination)
    c.executescript(SCHEMA.read_text())
    counts = {}
    try:
        for bureau in ([bureau_filter] if bureau_filter else REGIONS):
            facilities, refs, expected = validate(src, bureau)
            for copy in range(copies):
                snapshot = f'trial-{copy}-{bureau}'
                provenance = {'files': [f for f in files if f['bureau'] == bureau],
                              'sheets': [f for f in checks['sheets'] if f['bureau'] == bureau]}
                with c:
                    c.execute('INSERT INTO notification_snapshots VALUES(?,?,?,?,?,?,?)',
                              (snapshot, NAMES[bureau], 'staging', json.dumps(refs), json.dumps(provenance, ensure_ascii=False), len(facilities), expected))
                    c.executemany('INSERT INTO notification_facilities VALUES(?,?,?,?)',
                                  [(snapshot, f['code'], f['prefecture_code'], f['reference_date']) for f in facilities])
                    c.execute('ATTACH DATABASE ? AS source_index', (str(source),))
                    c.execute('INSERT INTO notification_items SELECT ?,item_name_search,min(official_item_name) FROM source_index.latest_rows WHERE bureau=? GROUP BY item_name_search', (snapshot, bureau))
                c.execute('DETACH DATABASE source_index')
                cursor = src.execute('SELECT * FROM latest_rows WHERE bureau=? ORDER BY medical_code,item_name_search', (bureau,))
                n = 0
                while rows := cursor.fetchmany(2000):
                    values = []
                    for row in rows:
                        payload = json.loads(row['payload_json'])
                        if not payload.get('versions') or not all(v.get('source_rows') for v in payload['versions']):
                            raise ValueError('Missing selected versions or original row references')
                        for v in payload['versions']:
                            # Institution attributes already live in the facility master.
                            v['fields'] = {k: value for k, value in v['fields'].items() if k in ('受理記号','受理番号','受理日','受理年月日','算定開始年月日','個別有効開始年月日')}
                        values.append((snapshot, row['medical_code'], row['item_name_search'], payload['date_basis'], payload['receipt_date_iso'] or None, row['calculation_start_iso'] or None, json.dumps(payload, ensure_ascii=False, separators=(',', ':'))))
                    with c:
                        c.executemany('INSERT INTO notification_filings VALUES(?,?,?,?,?,?,?)', values)
                    n += len(values)
                if n != expected:
                    raise ValueError('Filing count mismatch')
                with c:
                    c.execute("UPDATE notification_snapshots SET state='validated' WHERE id=?", (snapshot,))
                counts[snapshot] = {'filings': n, 'facilities': len(facilities)}
        c.execute('VACUUM')
        report = {'copies': copies, 'snapshots': counts, 'bytes': destination.stat().st_size,
                  'integrity': c.execute('PRAGMA integrity_check').fetchone()[0],
                  'foreign_key_violations': c.execute('PRAGMA foreign_key_check').fetchall(),
                  'current_pointers': c.execute('SELECT count(*) FROM notification_current').fetchone()[0],
                  'table_and_index_bytes': dict(c.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name')),
                  'production_write': False}
        destination.with_suffix('.report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    finally:
        c.close()
        src.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--copies', type=int, choices=(1, 2), default=2)
    p.add_argument('--bureau', choices=list(REGIONS))
    a = p.parse_args()
    print(json.dumps(convert(a.source, a.output, a.root, a.copies, a.bureau), ensure_ascii=False))
