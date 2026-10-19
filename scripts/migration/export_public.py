"""Read-only export of the existing public medical data to an isolated SQLite file.

Never exports auth.users, membership, access logs, or credentials. No remote DDL.
Requires maintenance throughout; export is a capacity trial, not a cutover backup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.request

TABLES = ('source_imports', 'medical_institutions', 'institution_beds',
          'institution_changes', 'institution_bed_history', 'institution_links')
PROJECT = 'jnoukmkrtmpgchdpcskc'


def query(sql):
    req = urllib.request.Request(
        f'https://api.supabase.com/v1/projects/{PROJECT}/database/query',
        data=json.dumps({'query': sql, 'read_only': True}).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['SUPABASE_ACCESS_TOKEN'],
                 'Content-Type': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.load(response)
        except Exception:
            if attempt == 2:
                raise RuntimeError('Read-only export request failed; no credentials logged') from None
            time.sleep(attempt + 1)


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def check_maintenance():
    if query('select active from public.application_maintenance where singleton') != [{'active': True}]:
        raise RuntimeError('Maintenance required')


def export(destination, batch_size=5000, resume=False):
    if destination.exists() and not resume:
        raise ValueError('Destination must not exist; existing exports are preserved')
    destination.parent.mkdir(parents=True, exist_ok=True)
    check_maintenance()
    report = {'project': PROJECT, 'purpose': 'capacity trial; not a consistent cutover backup',
              'remote_writes': False, 'tables': {}}
    c = sqlite3.connect(destination)
    c.execute('PRAGMA journal_mode=DELETE')
    try:
        for table in TABLES:
            check_maintenance()
            cols = query("select column_name,data_type from information_schema.columns "
                         f"where table_schema='public' and table_name={literal(table)} order by ordinal_position")
            keys = query("select a.attname as name from pg_index i join pg_attribute a "
                         "on a.attrelid=i.indrelid and a.attnum=any(i.indkey) "
                         f"where i.indrelid='public.{table}'::regclass and i.indisprimary order by array_position(i.indkey,a.attnum)")
            names = [r['column_name'] for r in cols]
            pk = [r['name'] for r in keys]
            if not names or not pk:
                raise ValueError('Missing schema or primary key: ' + table)
            numeric = {'integer', 'bigint', 'smallint', 'boolean'}
            definitions = [ident(r['column_name']) + (' INTEGER' if r['data_type'] in numeric else ' TEXT') for r in cols]
            definitions.append('PRIMARY KEY (' + ','.join(map(ident, pk)) + ')')
            exists = c.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()
            if not exists:
                c.execute('CREATE TABLE ' + ident(table) + '(' + ','.join(definitions) + ')')
            elif [r[1] for r in c.execute('PRAGMA table_info('+ident(table)+')')] != names:
                raise ValueError('Resume schema mismatch')
            count = query(f'select count(*) as n from public.{table}')[0]['n']
            n = c.execute('SELECT count(*) FROM '+ident(table)).fetchone()[0]
            last = c.execute('SELECT '+','.join(map(ident,pk))+' FROM '+ident(table)+' ORDER BY '+','.join(ident(k)+' DESC' for k in pk)+' LIMIT 1').fetchone() if n else None
            if n > count:
                raise ValueError('Resume count exceeds remote count')
            digest = hashlib.sha256()
            # The smaller bed rows can safely be transferred in larger batches.
            page_size = 20000 if table == 'institution_bed_history' else batch_size
            while True:
                where = '' if last is None else ' WHERE (' + ','.join(map(ident, pk)) + ') > (' + ','.join(map(literal, last)) + ')'
                rows = query(f'SELECT * FROM public.{table}' + where + ' ORDER BY ' + ','.join(map(ident, pk)) + f' LIMIT {int(page_size)}')
                if not rows:
                    break
                values = []
                for row in rows:
                    values.append([json.dumps(row.get(k), ensure_ascii=False, separators=(',', ':')) if isinstance(row.get(k), (dict, list)) else row.get(k) for k in names])
                with c:
                    c.executemany('INSERT INTO ' + ident(table) + ' VALUES (' + ','.join('?' for _ in names) + ')', values)
                n += len(rows)
                last = [rows[-1][k] for k in pk]
                print(json.dumps({'table': table, 'exported': n, 'expected': count}), flush=True)
            if n != count or query(f'select count(*) as n from public.{table}')[0]['n'] != count:
                raise ValueError('Source changed during export: ' + table)
            for row in c.execute('SELECT * FROM '+ident(table)+' ORDER BY '+','.join(map(ident,pk))):
                digest.update((json.dumps(row, ensure_ascii=False, separators=(',', ':'))+'\n').encode())
            report['tables'][table] = {'rows': n, 'sha256_sqlite_rows': digest.hexdigest(), 'primary_key': pk}
            destination.with_suffix('.report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        c.execute('CREATE INDEX IF NOT EXISTS trial_location ON medical_institutions(prefecture_code,kind,status,code)')
        c.execute('CREATE INDEX IF NOT EXISTS trial_code ON medical_institutions(code)')
        c.execute('CREATE INDEX IF NOT EXISTS trial_beds ON medical_institutions(total_beds,code)')
        c.execute('CREATE INDEX IF NOT EXISTS trial_kind_status_code ON medical_institutions(kind,status,code)')
        c.commit()
        check_maintenance()
        c.execute('VACUUM')
        report['integrity'] = c.execute('PRAGMA integrity_check').fetchone()[0]
        report['bytes'] = destination.stat().st_size
        report['table_and_index_bytes'] = dict(c.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name'))
    finally:
        c.close()
        destination.with_suffix('.report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=5000)
    parser.add_argument('--resume', action='store_true', help='Resume this capacity trial under maintenance; not a backup consistency guarantee')
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 5000:
        parser.error('batch size must be 1..5000')
    print(json.dumps(export(args.output, args.batch_size, args.resume), ensure_ascii=False))
