"""Build a self-contained internal review bundle from validated local main lists.

No API keys, sessions, raw parser rows, or Auth accounts enter this package.
The demo intercepts local API calls and cannot write to production.
"""
import argparse
from collections import Counter
from datetime import datetime
import html
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from app.main import app
from app.domain import summarize, PREFECTURES
from app.exports import workbook_bytes, COLUMNS

BRIDGE = r'''
const review=JSON.parse(document.getElementById('review-data').textContent);
const dataset=review.rows.map(values=>Object.fromEntries(review.keys.map((key,i)=>[key,values[i]])));
function filtered(q){
 const bed=(q.bed_type||'total')+'_beds';
 const contains=(r,k)=>!q[k]||String(r[k]||'').toLocaleLowerCase().includes(String(q[k]).toLocaleLowerCase());
 const rows=dataset.filter(r=>r.kind===(q.kind||'病院')&&(['現存',...(q.include_suspended?['休止']:[])].includes(r.status))
  &&(!q.prefectures?.length||q.prefectures.includes(r.prefecture))
  &&['bureau','owner_type','clinic_beds','psychiatric_class','code'].every(k=>!q[k]||r[k]===q[k])
  &&['municipality','name','owner'].every(k=>contains(r,k))
  &&(q.minimum==null||(r[bed]!=null&&r[bed]>=q.minimum))
  &&(q.maximum==null||(r[bed]!=null&&r[bed]<=q.maximum)));
 const sort=q.sort||'code',direction=q.direction==='desc'?-1:1;
 rows.sort((a,b)=>a[sort]==null?(b[sort]==null?0:1):b[sort]==null?-1:
  (typeof a[sort]==='number'?(a[sort]-b[sort]):String(a[sort]).localeCompare(String(b[sort]),'ja'))*direction||a.code.localeCompare(b.code));
 return rows;
}
const response=d=>new Response(JSON.stringify(d),{status:200,headers:{'Content-Type':'application/json'}});
window.fetch=async function(url,options={}){
 if(url==='/api/me')return response({email:'社内レビュー用 · オフラインデモ',role:'user'});
 if(url==='/api/dashboard')return response(review.dashboard);
 if(url==='/api/search'){
  const q=JSON.parse(options.body||'{}'),rows=filtered(q),page=q.page||1;
  return response({items:rows.slice((page-1)*50,page*50),total:rows.length,page,page_size:50});
 }
 if(url==='/api/export/csv'){
  const q=JSON.parse(options.body||'{}');
  const safe=v=>{let text=String(v??'');if(/^[\s]*[=+\-@\t\r]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';};
  const lines=[review.columns.map(c=>safe(c[1])).join(','),...filtered(q).map(r=>review.columns.map(c=>safe(r[c[0]])).join(','))];
  return new Response('\ufeff'+lines.join('\r\n'),{headers:{'Content-Type':'text/csv;charset=utf-8'}});
 }
 return new Response(JSON.stringify({detail:'確認用デモのため、この操作は利用できません。'}),{status:403,headers:{'Content-Type':'application/json'}});
};
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data', default='data/national')
    parser.add_argument('--output', default='artifacts/上司確認用')
    parser.add_argument('--database-report', default='artifacts/production-verification.json')
    args=parser.parse_args()
    directory=Path(args.data);out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    records=[];sources=[]
    for source in sorted(directory.glob('*.sources.json')):
        sources.extend(json.loads(source.read_text()))
        records.extend(json.loads(Path(str(source).replace('.sources.json','.institutions.json')).read_text()))
    assert len({r['code'] for r in records})==len(records),'duplicate codes'
    assert len({s['prefecture_code'] for s in sources})==47,'47-prefecture main-list input required'
    production=json.loads(Path(args.database_report).read_text())
    zero_verified=production.get('all_database_source_fields_equal') and production.get('null_bed_counts') and all(n==0 for n in production['null_bed_counts'].values())
    zero_update_status=production.get('bed_zero_update_status','完了（全件DB照合済み）' if zero_verified else 'この資料では未確認')
    summary=summarize(records)
    active=[r for r in records if r['status']=='現存']
    active_hospitals=sum(r['kind']=='病院' for r in active)
    active_clinics=sum(r['kind']=='診療所' for r in active)
    review_count=sum(r['status']=='確認中' for r in records)
    sample_count=production['fukuoka_hospital_20_200_count']
    stat_clinics=105702
    clinic_gap=stat_clinics-active_clinics
    note='確認用・47都道府県の保険医療機関（通常医科主一覧）。現存のみ。非保険施設は対象外。一部分類は確認中。北海道はユーザー指定ルール適用。'
    (out/'全国医療機関マスター.xlsx').write_bytes(workbook_bytes(active,sources,note))
    print('Excel generated',len(active),flush=True)
    keys=list(dict.fromkeys([k for k,_ in COLUMNS]+['clinic_beds']))
    dashboard={**summary,'collected':[PREFECTURES[p] for p in sorted({s['prefecture_code'] for s in sources})],
        'missing':[],'status_counts':dict(Counter(r['status'] for r in records)),
        'quality_review_count':sum(bool(r.get('needs_review')) for r in records),
        'sources':sources,'monthly':{},'note':'病床数の未記載は0床として扱います。収録対象は保険医療機関（通常医科主一覧）です。保険指定のない施設は含みません。初回取込を新規指定・純新規開業に数えていません。'}
    payload={'keys':keys,'rows':[[r.get(k) for k in keys] for r in records],
             'columns':COLUMNS,'dashboard':dashboard}
    blob=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    with TestClient(app) as client:
        page=client.get('/').text
    css=Path('app/static/style.css').read_text()+'\n#login-panel{display:none}.review-banner{background:#e8f3ef;border:1px solid #bbd9cc;border-radius:12px;padding:18px 24px;margin-bottom:24px;line-height:1.8}.review-banner strong{color:#124938}#account button{display:none}.actions a{white-space:nowrap;text-decoration:none;padding:10px 14px;border:1px solid #c6d8d4;border-radius:8px;font-size:14px}\n'
    page=page.replace('<link rel="stylesheet" href="/static/style.css">','<style>'+css+'</style>')
    page=page.replace('<script src="/static/app.js" defer></script>','')
    page=page.replace('href="/"','href="#"')
    page=page.replace('<main>',f'<main><aside class="review-banner"><strong>社内レビュー用デモ｜2026年10月1日基準の医科主一覧</strong><br>検索対象：47都道府県・現存 {len(active):,} 件。対象は保険医療機関（医科）です。一部分類は確認中です。<br>本番DB登録状況：{production["import_count"]} / 47 都道府県・{production["record_count"]:,} 件（作成時点）。診療所は{active_clinics:,}件を収録。2026年7月の動態調査105,702件とは対象範囲が異なり、保険指定のない施設は含みません。病床0床統一の本番反映：{html.escape(zero_update_status)}。このデモは本番データを変更しません。 <a href="はじめに.html">説明資料へ</a></aside>',1)
    page=page.replace('<button type="button" data-export="xlsx" class="secondary">Excel</button>', '<a href="全国医療機関マスター.xlsx" download class="secondary">Excelマスター</a>')
    page=page.replace('<button type="button" data-export="master" class="secondary">全取得県マスター</button>','')
    js=Path('app/static/app.js').read_text().replace('loadSession();\n','loadSession();\nsetTimeout(()=>runSearch(),0);\n')
    page=page.replace('</body>','<script id="review-data" type="application/json">'+blob+'</script><script>'+BRIDGE+'\n'+js+'</script></body>')
    (out/'検索デモ.html').write_text(page)
    today=datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y年%m月%d日 %H:%M JST')
    cover=f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>全国医療機関データベース｜社内レビュー</title><style>body{{font-family:system-ui,sans-serif;background:#f2f6f5;color:#143a40;max-width:1040px;margin:auto;padding:36px 24px;line-height:1.9}}h1{{font-size:34px;line-height:1.5}}.card{{background:white;border:1px solid #dbe6e2;padding:24px;border-radius:16px;margin:22px 0}}.stats{{display:flex;gap:16px;flex-wrap:wrap}}.stat{{flex:1;min-width:160px;background:#143f49;color:white;padding:20px;border-radius:14px}}strong.n{{display:block;font-size:34px}}a{{color:#0b6957}}.button{{display:inline-block;background:#0e6c58;color:white;padding:12px 22px;border-radius:10px;text-decoration:none;margin:8px 16px 8px 0}}small{{color:#5b7274}}table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #e1e9e6;text-align:left;padding:10px}}</style><body><p>IMAGE ONE · MEDICAL INSTITUTION DIRECTORY</p><h1>全国の医療機関を、<br>地域と病床から探す。</h1><p>社内レビュー用｜{today}</p><a class="button" href="検索デモ.html">検索デモを開く</a><a class="button" href="全国医療機関マスター.xlsx">Excelマスターを開く</a><div class="stats"><div class="stat">主一覧の解析範囲<strong class="n">47 都道府県</strong></div><div class="stat">現存施設<strong class="n">{len(active):,} 件</strong></div><div class="stat">本番DB登録<strong class="n">{production['record_count']:,} 件</strong></div></div><section class="card" style="border:2px solid #b5751d"><h2>収録対象は保険医療機関（医科）です</h2><p>現存の内訳は病院{active_hospitals:,}件・診療所{active_clinics:,}件です。2026年7月の医療施設動態調査105,702件に対し、{clinic_gap:,}件（{clinic_gap/stat_clinics*100:.2f}%）の差があります。動態調査は非保険施設も含み得るため、両者の総数が一致する前提ではありません。</p><p>47都道府県の公式PDF・Excel・本番DBの97,457コードが一致しています。非保険施設は今回の収録対象外です。</p></section><section class="card"><h2>今回、操作して確認できること</h2><ol><li>医療機関検索で「福岡県・病院・20〜200床」を指定すると{sample_count}件。名称、開設者、病床種別でも絞り込めます。</li><li>病院／診療所を切り替え、複数県・有床／無床の条件で比較できます。</li><li>集計ダッシュボードで現存施設の内訳を確認し、検索結果をCSVへ出力できます。</li><li>Excelは病院・診療所・精神科病院・集計・取得元など6シートです。各データシートのフィルターを利用できます。</li></ol></section><section class="card"><h2>本番アプリの検証状況</h2><p>Supabase認証・管理者承認・検索・CSV／Excel出力を一時アカウントで確認済みです。一般利用者の管理画面へのアクセス拒否、承認取消後の即時拒否も確認しました。</p><p>本番DBは作成時点で{production['import_count']}都道府県・{production['record_count']:,}件を登録済みです。病床0床統一の本番反映：{html.escape(zero_update_status)}。本番Webホスティングは未公開です。このファイルはログイン不要の確認用デモです。</p></section><section class="card"><h2>月次更新：都道府県単位のキュー</h2><p>公式配布物を取得・検証し、県別件数と実測時間から処理順を決定します。時間のかかる県を先に処理し、失敗県は後続県を止めず同日中に最大3回試行します。成功済みの同一データは重複登録しません。</p><p>現行97,457件の保守的な試算は初回約81分、最大3回の試行と取得・解析の余裕を含め約4時間33分です。1回の予算は5時間30分。JST20〜25日の毎時再試行を実装し、24時間内の更新を目標とします。定期実行の有効化と本番での所要時間の受入確認は残っています。</p></section><section class="card"><h2>原資料の確認が必要な施設</h2><p><a href="病床情報の要確認一覧.csv">病床情報の要確認一覧（{production.get("quality_review_count",0)}件）</a> ／ <a href="区分と病床の確認待ち.csv">区分と指定病床の確認待ち（0件）</a>。病床情報の要確認フラグは掲載状態とは別に保持し、原資料が現存の施設は通常集計に含めます。</p></section><section class="card"><h2>判断前に確認したいこと</h2><ul><li>通常医科主一覧を対象とします。歯科側別表を独立医科施設として加算しません。施設の掲載状態と病床品質フラグを分け、区分と病床の不一致{review_count}件は確認中として通常検索から除外します。</li><li>ご指定により、病床数の未記載項目は全国で0床に統一しました。元資料の表記は内部に保持しています。</li><li>初回取込は新規指定や純新規開業として数えません。差分は根拠を区別して蓄積します。</li><li>Microsoft 365本体でのExcel動的検索数式の再計算は未検証です。データシートの通常フィルターとブラウザデモを今回の確認に利用してください。</li><li>公式資料の未公表・形式変更・DB障害がある場合は更新を止め、前回のデータを維持します。</li></ul></section><small>ZIPを展開し、「はじめに.html」をChromeまたはEdgeで開いてください。デモはネットワーク通信・本番DBへの書込みを行いません。APIキー・利用者の認証情報は含みません。</small></body></html>'''
    (out/'はじめに.html').write_text(cover)
    (out/'説明.txt').write_text('ZIPをすべて展開して、はじめに.html をChromeまたはEdgeで開いてください。\n検索デモ.html：医科主一覧の検索・集計・CSV出力\n全国医療機関マスター.xlsx：現存施設の6シートExcel。動的検索数式のM365再計算は未検証です。通常フィルターをご利用ください。\n本番の公開Webアプリとは別の社内確認用ファイルです。\n')
    zip_path=out.parent/'上司確認用パッケージ.zip'
    with ZipFile(zip_path,'w',ZIP_DEFLATED) as z:
        for path in sorted(out.iterdir()):
            if path.is_file(): z.write(path,path.name)
    print(json.dumps({'directory':str(out),'zip':str(zip_path),'records':len(records),'active':len(active),'zip_bytes':zip_path.stat().st_size},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
