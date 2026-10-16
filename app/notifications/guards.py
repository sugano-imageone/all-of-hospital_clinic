"""Snapshot comparison gates: large changes stay staged, never silently replace current data."""
import hashlib,json
from collections import Counter
from datetime import date,datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

DEFAULT_LIMITS={'row_change_ratio':.20,'filing_turnover_ratio':.25,'institution_turnover_ratio':.10,'detail_turnover_ratio':.50,'minimum_changed_filings':20,'minimum_changed_institutions':10,'minimum_changed_details':50}

def ids(db,snapshot,column):
    if column not in ['code','filing_id']:raise ValueError('不正な比較列')
    return {r[0] for r in db.execute(f'SELECT DISTINCT {column} FROM source_rows WHERE snapshot_id=? AND {column} IS NOT NULL',(snapshot,))}
def fingerprints(db,snapshot):
    return Counter(hashlib.sha256(r[0].encode()).hexdigest() for r in db.execute('SELECT raw_json FROM source_rows WHERE snapshot_id=?',(snapshot,)))
def compare(db,old,new,limits=None):
    thresholds={**DEFAULT_LIMITS,**(limits or {})};oldrow=db.execute('SELECT * FROM snapshots WHERE id=?',(old,)).fetchone();newrow=db.execute('SELECT * FROM snapshots WHERE id=?',(new,)).fetchone()
    if not oldrow or not newrow or oldrow['prefecture']!=newrow['prefecture']:raise ValueError('比較対象が不正です')
    before=ids(db,old,'filing_id');after=ids(db,new,'filing_id');bc=ids(db,old,'code');ac=ids(db,new,'code')
    added=len(after-before);removed=len(before-after);added_codes=len(ac-bc);removed_codes=len(bc-ac)
    bfp=fingerprints(db,old);afp=fingerprints(db,new);detail_added=sum((afp-bfp).values());detail_removed=sum((bfp-afp).values())
    row_ratio=abs(newrow['row_count']-oldrow['row_count'])/max(oldrow['row_count'],1)
    filing_ratio=(added+removed)/max(len(before),1);code_ratio=(added_codes+removed_codes)/max(len(bc),1);detail_ratio=(detail_added+detail_removed)/max(oldrow['row_count'],1)
    def names(snapshot):return {r[0] for r in db.execute('SELECT DISTINCT f.official_name FROM filings f JOIN source_rows r ON r.filing_id=f.id WHERE r.snapshot_id=?',(snapshot,))}
    bn=names(old);an=names(new);reasons=[]
    if row_ratio>thresholds['row_change_ratio']:reasons.append('元行数の変動が許容値を超過')
    if added+removed>=thresholds['minimum_changed_filings'] and filing_ratio>thresholds['filing_turnover_ratio']:reasons.append('届出の追加・削除が許容値を超過')
    if added_codes+removed_codes>=thresholds['minimum_changed_institutions'] and code_ratio>thresholds['institution_turnover_ratio']:reasons.append('医療機関の入替が許容値を超過')
    if detail_added+detail_removed>=thresholds['minimum_changed_details'] and detail_ratio>thresholds['detail_turnover_ratio']:reasons.append('詳細値の変動が許容値を超過')
    if newrow['reference_date']<oldrow['reference_date']:reasons.append('現行より古い基準日')
    if newrow['reference_date']>datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat():reasons.append('将来の基準日')
    metrics={'old_snapshot':old,'new_snapshot':new,'old_reference_date':oldrow['reference_date'],'new_reference_date':newrow['reference_date'],'old_rows':oldrow['row_count'],'new_rows':newrow['row_count'],'row_change_ratio':row_ratio,'old_filings':len(before),'new_filings':len(after),'added_filings':added,'removed_filings':removed,'filing_turnover_ratio':filing_ratio,'added_institutions':added_codes,'removed_institutions':removed_codes,'institution_turnover_ratio':code_ratio,'added_detail_rows':detail_added,'removed_detail_rows':detail_removed,'detail_turnover_ratio':detail_ratio,'added_official_names':sorted(an-bn),'removed_official_names':sorted(bn-an),'thresholds':thresholds}
    return {'status':'review_required' if reasons else 'accepted','reasons':reasons,'metrics':metrics}

def validate_context(entries):
    if not isinstance(entries,list):raise ValueError('改定根拠はリストで指定してください')
    for e in entries:
        if e.get('type') not in ['答申','官報告示','告示','通知']:raise ValueError('官報告示・通知・答申の区別が必要です')
        p=urlsplit(e.get('url',''))
        if p.scheme!='https' or not (p.hostname in ['www.kanpo.go.jp','kanpo.go.jp','kanpou.npb.go.jp','mhlw.go.jp'] or (p.hostname or '').endswith('.mhlw.go.jp')) or p.username or p.port not in [None,443]:raise ValueError('改定根拠は厚生労働省の公式URLが必要です')
        date.fromisoformat(e['published_date'])
        if e.get('effective_date'):date.fromisoformat(e['effective_date'])
        if not e.get('title'):raise ValueError('根拠資料の正式名称が必要です')
        if e['type'] in ['官報告示','告示']:
            if e.get('issuer')!='厚生労働大臣' or not all(e.get(k) for k in ['notice_number','annex_reference','effective_date','gazette_reference']):raise ValueError('厚生労働大臣の告示番号・官報掲載情報・本文／別添の該当箇所・施行日が必要です')
    return entries
