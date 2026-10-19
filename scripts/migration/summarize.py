"""Publish only non-secret measured validation results, never data or credentials."""
import argparse
import json
from pathlib import Path
import sqlite3


def summarize(root, destination):
    def read(name):
        return json.loads((root/name).read_text())
    public=read('medical-public.report.json')
    notifications=read('notifications-two-versions.report.json')
    staging=read('notifications-largest-staging.report.json')
    history=read('history-compact-mixed.report.json')
    all_zlib=read('history-compressed.report.json')
    current=read('notifications-current-only.report.json')
    c=sqlite3.connect((root/'medical-public.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    cold_names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE tbl_name IN ('institution_changes','institution_bed_history') AND type IN ('table','index')")}
    c.close()
    cold_bytes=sum(public['table_and_index_bytes'].get(n,0) for n in cold_names)
    hot_bytes=public['bytes']-cold_bytes
    baseline=public['bytes']+notifications['bytes']
    peak=baseline+staging['bytes']
    compressed_baseline=hot_bytes+history['bytes']+notifications['bytes']
    compressed_peak=compressed_baseline+staging['bytes']
    single_snapshot_rows=sum(x['filings']+x['facilities'] for k,x in notifications['snapshots'].items() if k.startswith('trial-0-'))
    n=sqlite3.connect((root/'notifications-two-versions.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    item_rows=n.execute("SELECT count(*) FROM notification_items WHERE snapshot_id LIKE 'trial-0-%'").fetchone()[0]
    n.close()
    initial_rows=sum(x['rows'] for x in public['tables'].values())+2*(single_snapshot_rows+item_rows+8)+1
    monthly_notifications=single_snapshot_rows+item_rows+8+8+8
    # This is a planning case, not an observed monthly update or billing count.
    institutions=public['tables']['medical_institutions']['rows']
    monthly_facility_case=institutions*(1+5+1)+47
    report={
      'checked_date_jst':'2026-10-19','main_sha':'8022e8edbdfde9dc60ea21faac9e01f476d627df',
      'account_readiness':{'turso':'screenshot_confirmed_sugano-imageone_Free_imageone-medical_Tokyo_empty_database','firebase':'screenshot_confirmed_imageone-medical_Spark_email_password_enabled','cloudflare':'not_created_user_confirmed'},
      'maintenance':{'pages':read('pages-audit.json')['maintenance'],'supabase':read('remote-audit.json')[0]['audit']['maintenance'],'changed':False},
      'official_validation':{k:v for k,v in read('validation.json').items() if k in ('source_detail_rows','prefecture_count','institution_count','official_item_name_count')},
      'latest_validation':read('latest-validation.json'),
      'public_tables':public['tables'],
      'capacity_bytes':{'free_limit_conservative_decimal':5_000_000_000,'operational_target_80_percent':4_000_000_000,
         'all_public_medical_and_history':public['bytes'],'notification_two_copies_including_indexes':notifications['bytes'],
         'largest_bureau_staging':staging['bytes'],'uncompressed_steady':baseline,'uncompressed_staging_peak':peak,
         'uncompressed_peak_headroom':5_000_000_000-peak,'cold_history_original_pages':cold_bytes,'hot_medical_pages':hot_bytes,
         'lossless_compressed_history_file':history['bytes'],'compressed_steady':compressed_baseline,
         'rejected_all_rows_zlib_without_rowid_trial':all_zlib['bytes'],
         'compressed_staging_peak':compressed_peak,'compressed_peak_headroom':5_000_000_000-compressed_peak},
      'capacity_qualification':'Sum of actual local SQLite files/pages, including indexes. Two copies use identical latest originals; not two historical source periods. Does not establish remote Turso storage/PITR accounting, future growth, user-log storage, or other databases in the organization.',
      'current_only_policy':{
         'user_decision':'After validated adoption retire the previous medical/filing snapshot. Keep institution change and bed history only for the current JST update cycle. Preserve users, roles, access and login history. Failed prefectures/bureaus retain their previous successful data.',
         'notification_current_only_bytes':current['bytes'],
         'all_current_cycle_history_verified_month':'2026-10',
         'current_change_history_rows':public['tables']['institution_changes']['rows'],
         'current_bed_history_rows':public['tables']['institution_bed_history']['rows'],
         'uncompressed_steady_bytes':public['bytes']+current['bytes'],
         'uncompressed_largest_bureau_staging_peak_bytes':public['bytes']+current['bytes']+staging['bytes'],
         'mixed_history_steady_bytes':hot_bytes+history['bytes']+current['bytes'],
         'mixed_history_largest_bureau_staging_peak_bytes':hot_bytes+history['bytes']+current['bytes']+staging['bytes'],
         'initial_table_rows_to_import':initial_rows-(single_snapshot_rows+item_rows+8),
         'monthly_write_planning_including_first_old_history_retirement':monthly_notifications+monthly_facility_case+monthly_notifications+history['verified_rows'],
         'migration_plus_monthly_plus_full_retry_planning':initial_rows-(single_snapshot_rows+item_rows+8)+2*(monthly_notifications+monthly_facility_case+monthly_notifications+history['verified_rows']),
         'write_budget_note':'Conservative planning counts retired table rows as writes too. Initial migration plus monthly replacement and a complete retry in the same month may exceed 10 million; require usage checks, unchanged-content skip, upload checkpoints and a stop threshold before enabling the schedule.',
         'actual_deletion_executed':False,
         'note':'All 144 source imports and all exported history rows belong to the 2026-10 acquisition/update cycle. No history removal was needed to produce these measured current-cycle capacities. Archive storage, allocator high-water and remote usage still need validation.'},
      'history_roundtrip_verified_rows':history['verified_rows'],
      'writes_planning_rows':{'initial_public_plus_two_notification_versions_table_rows':initial_rows,
         'monthly_notifications_all_bureaus_table_rows':monthly_notifications,'monthly_facility_full_refresh_case':monthly_facility_case,
         'monthly_combined_case':monthly_notifications+monthly_facility_case,'combined_with_one_full_retry_case':2*(monthly_notifications+monthly_facility_case),
         'limit':10_000_000,'caveat':'Planning table rows only. Remote import method, index/build effects, retries, deletes, audit events and aborted writes must be checked against Turso telemetry; no billed writes were measured.'},
      'local_benchmark':read('benchmark/benchmark-report.json'),
      'xlsx_before_streaming':read('benchmark/export-metrics-before-streaming.json'),
      'integrity':{'public':public['integrity'],'notifications':notifications['integrity'],'history':history['integrity'],
                   'notification_fk_violations':notifications['foreign_key_violations']},
      'production_writes':False,'production_cutover':False,'free_operation_confirmed':False,
      'remaining_gates':['account setup','real Firebase/Turso/Worker integration','full Pages UI integration','member and legacy-log transfer','Worker CPU and billed usage','real browser memory','restore and retention approval']
    }
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'capacity_bytes':report['capacity_bytes'],'writes_planning_rows':report['writes_planning_rows']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summarize(a.root,a.output)
