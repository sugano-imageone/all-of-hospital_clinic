"""Lossless row compression for cold history; local trial only, never deletes source.

The row SHA256 and a full decompress/compare check make size savings reviewable.
Ordinary SQLite cannot query compressed payload fields; lookup columns stay indexed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

HISTORY = ('institution_changes', 'institution_bed_history')


def compact(source, destination, compress_beds=True):
    if destination.exists():
        raise ValueError('Destination already exists')
    src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    c = sqlite3.connect(destination)
    c.execute('CREATE TABLE history_records(id INTEGER PRIMARY KEY,table_name TEXT NOT NULL,key_json TEXT NOT NULL,institution_id TEXT,source_import_id TEXT,record_zlib BLOB NOT NULL,raw_sha256 TEXT NOT NULL,UNIQUE(table_name,key_json))')
    c.execute('CREATE INDEX history_institution ON history_records(institution_id,table_name,source_import_id)')
    report = {'tables': {}, 'source_modified': False, 'production_write': False, 'mode':'all_zlib' if compress_beds else 'large_changes_zlib_native_beds'}
    native_verified = 0
    try:
        for table in HISTORY:
            columns = src.execute('PRAGMA table_info('+table+')').fetchall()
            pk = [r['name'] for r in sorted(columns, key=lambda r:r['pk']) if r['pk']]
            if table == 'institution_bed_history' and not compress_beds:
                c.execute(src.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone()[0])
                cursor=src.execute('SELECT * FROM '+table+' ORDER BY '+','.join(pk))
                original=hashlib.sha256();n=0
                while rows:=cursor.fetchmany(10000):
                    for row in rows:
                        original.update((json.dumps(dict(row),ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode())
                    with c:
                        c.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in columns)+')',[tuple(r) for r in rows])
                    n+=len(rows)
                stored=hashlib.sha256()
                for row in c.execute('SELECT * FROM '+table+' ORDER BY '+','.join(pk)):
                    value=dict(zip([r['name'] for r in columns],row))
                    stored.update((json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode())
                if stored.digest()!=original.digest():raise ValueError('Native bed history checksum mismatch')
                report['tables'][table]={'rows':n,'mode':'native_lossless','sha256_canonical_rows':original.hexdigest()}
                native_verified+=n
                print(json.dumps({'table':table,**report['tables'][table]}),flush=True)
                continue
            n = raw_bytes = zipped_bytes = 0
            cursor = src.execute('SELECT * FROM '+table)
            while rows := cursor.fetchmany(2000):
                batch = []
                for row in rows:
                    record = dict(row)
                    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                    zipped = zlib.compress(raw, 9)
                    if zlib.decompress(zipped) != raw:
                        raise ValueError('Compression roundtrip mismatch')
                    batch.append((table,json.dumps([record[k] for k in pk]),record.get('institution_id'),str(record.get('source_import_id')),zipped,hashlib.sha256(raw).hexdigest()))
                    n += 1
                    raw_bytes += len(raw)
                    zipped_bytes += len(zipped)
                with c:
                    c.executemany('INSERT INTO history_records(table_name,key_json,institution_id,source_import_id,record_zlib,raw_sha256) VALUES(?,?,?,?,?,?)', batch)
            report['tables'][table] = {'rows': n, 'canonical_json_bytes': raw_bytes, 'compressed_payload_bytes': zipped_bytes}
            print(json.dumps({'table':table, **report['tables'][table]}), flush=True)
        # Verify persisted bytes too, not just the compression function's input.
        checked = 0
        for zipped, digest in c.execute('SELECT record_zlib,raw_sha256 FROM history_records'):
            if hashlib.sha256(zlib.decompress(zipped)).hexdigest() != digest:
                raise ValueError('Stored history checksum mismatch')
            checked += 1
        c.execute('VACUUM')
        report.update(bytes=destination.stat().st_size,verified_rows=checked+native_verified,integrity=c.execute('PRAGMA integrity_check').fetchone()[0],table_and_index_bytes=dict(c.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name')))
        destination.with_suffix('.report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return report
    finally:
        c.close()
        src.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--mode', choices=('all','mixed'), default='mixed')
    a = p.parse_args()
    print(json.dumps(compact(a.source,a.output,a.mode=='all'), ensure_ascii=False))
