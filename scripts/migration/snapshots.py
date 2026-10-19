"""Local/libSQL-compatible validation and atomic pointer switch primitives.

No network connection and no deletion. The caller must supply an expected current
pointer, preventing an overlapping publisher from silently replacing a newer run.
"""
import datetime
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'facility_notifications'))
from build_index import REGIONS
from production import NAMES


def adopt(c, snapshot, expected_current, today=None, prune_previous=False, archive_receipt=None):
    today = today or datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date()
    c.execute('BEGIN IMMEDIATE')
    try:
        row = c.execute('SELECT bureau,state,reference_dates,expected_facilities,expected_filings,manifest_json FROM notification_snapshots WHERE id=?', (snapshot,)).fetchone()
        if row is None:
            raise ValueError('Unknown snapshot')
        bureau, state, dates, expected_facilities, expected_filings, manifest = row
        short = next((b for b, full in NAMES.items() if full == bureau), None)
        if short is None or state != 'validated':
            raise ValueError('Validated official bureau required')
        dates = json.loads(dates)
        if {int(n) for n in dates} != set(REGIONS[short]):
            raise ValueError('Incomplete prefecture dates')
        if any(datetime.date.fromisoformat(d) > today for d in dates.values()):
            raise ValueError('Future reference date')
        found = {r[0] for r in c.execute('SELECT DISTINCT prefecture_code FROM notification_facilities WHERE snapshot_id=?', (snapshot,))}
        if found != set(REGIONS[short]):
            raise ValueError('Incomplete prefecture coverage')
        for table, expected in [('notification_facilities', expected_facilities), ('notification_filings', expected_filings)]:
            if expected <= 0 or c.execute(f'SELECT count(*) FROM {table} WHERE snapshot_id=?', (snapshot,)).fetchone()[0] != expected:
                raise ValueError('Incomplete snapshot')
        bad = c.execute("SELECT count(*) FROM notification_filings WHERE snapshot_id=? AND (json_extract(payload_json,'$.latest_selection_resolved') IS NOT 1 OR coalesce(json_array_length(payload_json,'$.versions'),0)=0)", (snapshot,)).fetchone()[0]
        if bad or c.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Invalid filing payload or references')
        missing_refs = c.execute("SELECT count(*) FROM notification_filings n,json_each(n.payload_json,'$.versions') v WHERE n.snapshot_id=? AND (coalesce(json_array_length(v.value,'$.source_rows'),0)=0 OR json_type(v.value,'$.fields') IS NOT 'object' OR json_type(v.value,'$.details') IS NOT 'array')", (snapshot,)).fetchone()[0]
        if missing_refs:
            raise ValueError('Missing original versions/details/row references')
        if not json.loads(manifest).get('files'):
            raise ValueError('Missing original provenance')
        current = c.execute('SELECT snapshot_id FROM notification_current WHERE bureau=?', (bureau,)).fetchone()
        if (current[0] if current else None) != expected_current:
            raise ValueError('Current pointer changed')
        if current:
            old_dates = json.loads(c.execute('SELECT reference_dates FROM notification_snapshots WHERE id=?', current).fetchone()[0])
            if any(dates.get(k, '') < date for k, date in old_dates.items()):
                raise ValueError('Reference date regression')
            if prune_previous and current[0] != snapshot:
                receipt=archive_receipt or {}
                if receipt.get('snapshot_id')!=current[0] or receipt.get('restore_verified') is not True or not re.fullmatch(r'[a-f0-9]{64}',receipt.get('sha256','')) or not receipt.get('archive_location'):
                    raise ValueError('Verified restore archive required before retiring previous data')
        c.execute('INSERT INTO notification_current VALUES(?,?) ON CONFLICT(bureau) DO UPDATE SET snapshot_id=excluded.snapshot_id', (bureau, snapshot))
        if prune_previous and current and current[0] != snapshot:
            previous=current[0]
            if c.execute('SELECT 1 FROM notification_current WHERE snapshot_id=?',(previous,)).fetchone():
                raise ValueError('Cannot retire a referenced snapshot')
            # Only the previously adopted version of this bureau; not other bureaus,
            # another job's candidate, institutions, memberships, or login history.
            for table in ('notification_filings','notification_items','notification_facilities'):
                c.execute(f'DELETE FROM {table} WHERE snapshot_id=?',(previous,))
            c.execute('DELETE FROM notification_snapshots WHERE id=?',(previous,))
        c.commit()
    except Exception:
        c.rollback()
        raise


def targets(c, now):
    """No simulated date CLI: production callers must pass the actual aware clock."""
    if now.tzinfo is None:
        raise ValueError('Timezone-aware clock required')
    jst = now.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    if jst.day not in (20, 25):
        raise ValueError('Only JST 20th or 25th')
    month = jst.strftime('%Y-%m')
    states = dict(c.execute('SELECT bureau,status FROM notification_jobs WHERE month=?', (month,)))
    bureaus = list(NAMES.values())
    if jst.day == 25:
        if set(states) != set(bureaus):
            raise ValueError('No complete 20th job state; refusing full fallback')
        return [b for b in bureaus if states[b] == 'failed']
    with c:
        for bureau in bureaus:
            c.execute("INSERT OR IGNORE INTO notification_jobs VALUES(?,?,'failed',NULL,'{}')", (month, bureau))
    return [b for b in bureaus if states.get(b) != 'success']


def retire_history_for_cycle(c, cycle_month, successful_prefectures, retained_source_ids, archive_receipt):
    """Call INSIDE the validated facility-adoption transaction, never from a timer.

    Retained IDs are every successful source in this JST update cycle (20th/25th),
    not inferred from the official reference date, which may legitimately be older.
    A failed/unacquired prefecture must never enter successful_prefectures.
    Does not commit, delete sources/institutions, or touch account/log tables.
    """
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',cycle_month):
        raise ValueError('Invalid update cycle')
    prefs=sorted(set(successful_prefectures))
    kept=sorted(set(retained_source_ids),key=str)
    if not prefs or any(not isinstance(p,int) or not 1<=p<=47 for p in prefs) or not kept:
        raise ValueError('Explicit successful prefectures and retained sources required')
    ps=','.join('?' for _ in prefs);ks=','.join('?' for _ in kept)
    sources=c.execute(f'SELECT id,prefecture_code,status FROM source_imports WHERE id IN ({ks})',kept).fetchall()
    if len(sources)!=len(kept) or {r[1] for r in sources}!=set(prefs) or any(r[2]!='success' for r in sources):
        raise ValueError('Validated complete cycle sources required')
    cycle_ids={str(r[0]) for r in c.execute(f"SELECT id FROM source_imports WHERE prefecture_code IN ({ps}) AND status='success' AND strftime('%Y-%m',datetime(retrieved_at,'+9 hours'))=?",[*prefs,cycle_month])}
    if cycle_ids!={str(v) for v in kept}:
        raise ValueError('Retained source IDs must cover the whole update cycle')
    adopted={r[0] for r in c.execute(f'SELECT DISTINCT prefecture_code FROM medical_institutions WHERE source_import_id IN ({ks})',kept)}
    if adopted!=set(prefs):
        raise ValueError('New facility source has not been adopted')
    retired=set()
    for table in ('institution_changes','institution_bed_history'):
        retired.update(str(r[0]) for r in c.execute(f'SELECT DISTINCT h.source_import_id FROM {table} h JOIN medical_institutions m ON m.id=h.institution_id WHERE m.prefecture_code IN ({ps}) AND h.source_import_id NOT IN ({ks})',[*prefs,*kept]))
    if retired:
        receipt=archive_receipt or {}
        if receipt.get('cycle_month')!=cycle_month or receipt.get('restore_verified') is not True or not re.fullmatch(r'[a-f0-9]{64}',receipt.get('sha256','')) or not receipt.get('archive_location') or not retired.issubset({str(v) for v in receipt.get('source_import_ids',[])}):
            raise ValueError('Verified previous history archive required')
    deleted={}
    for table in ('institution_changes','institution_bed_history'):
        result=c.execute(f'DELETE FROM {table} WHERE source_import_id NOT IN ({ks}) AND EXISTS(SELECT 1 FROM medical_institutions m WHERE m.id={table}.institution_id AND m.prefecture_code IN ({ps}))', [*kept,*prefs])
        deleted[table]=result.rowcount
    return deleted
