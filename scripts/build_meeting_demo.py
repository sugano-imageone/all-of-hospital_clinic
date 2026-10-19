"""Offline meeting file from official snapshots; no production identities or credentials."""
import argparse,base64,gzip,hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from jinja2 import Environment,FileSystemLoader,select_autoescape
from app.domain import PREFECTURES,REGIONS,summarize
from app.exports import COLUMNS

def build(data,notifications,out):
    out.mkdir(parents=True,exist_ok=True);records=[];sources=[]
    for f in sorted(data.glob('*.sources.json')):
        sources.extend(json.loads(f.read_text()));records.extend(json.loads(Path(str(f).replace('.sources.json','.institutions.json')).read_text()))
    assert len({s['prefecture_code'] for s in sources})==47
    assert len({r['code'] for r in records})==len(records)
    # Only public facility fields are included. No raw source objects or Auth data.
    keys=list(dict.fromkeys([k for k,_ in COLUMNS]+['clinic_beds']))
    db=sqlite3.connect(notifications.resolve().as_uri()+'?mode=ro',uri=True)
    codes=[r[0] for r in db.execute('SELECT DISTINCT medical_code FROM latest_rows ORDER BY medical_code')];code_index={c:i for i,c in enumerate(codes)}
    by_name={};labels={}
    for code,name,label in db.execute('SELECT medical_code,item_name_search,official_item_name FROM latest_rows ORDER BY item_name_search,medical_code'):
        by_name.setdefault(name,[]).append(code_index[code]);labels.setdefault(name,label)
    bureau_names=['北海道厚生局','東北厚生局','関東信越厚生局','東海北陸厚生局','近畿厚生局','中国四国厚生局','四国厚生支局','九州厚生局']
    short=['北海道','東北','関東信越','東海北陸','近畿','中国四国','四国','九州'];bureaus=[]
    for name,key in zip(bureau_names,short):
        dates=[r[0] for r in db.execute('SELECT DISTINCT reference_date FROM latest_rows WHERE bureau=? ORDER BY reference_date',(key,))]
        master=sorted({s['reference_date'] for s in sources if s['bureau']==name})
        assert dates and master,(name,dates,master)
        bureaus.append({'bureau':name,'notification_dates':dates,'master_dates':master})
    db.close()
    dashboard={**summarize(records),'collected':[PREFECTURES[p] for p in sorted({s['prefecture_code'] for s in sources})],
        'missing':[],'status_counts':dict(Counter(r['status'] for r in records)),'quality_review_count':sum(bool(r.get('needs_review')) for r in records),
        'sources':sources,'monthly':{},'note':'厚生局の公表する保険医療機関（医科）を収録しています。'}
    payload={'keys':keys,'rows':[[r.get(k) for k in keys] for r in records],'columns':COLUMNS,'sources':sources,'dashboard':dashboard,
        'notifications':{'codes':codes,'items':[[labels[n],sorted(set(by_name[n]))] for n in sorted(by_name)],'bureaus':bureaus}}
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode();encoded=base64.b64encode(gzip.compress(raw,compresslevel=9,mtime=0)).decode()
    env=Environment(loader=FileSystemLoader('app/templates'),autoescape=select_autoescape(['html']))
    page=env.get_template('index.html').render(prefectures=PREFECTURES,bureaus=REGIONS)
    page=page.replace('<link rel="stylesheet" href="/static/style.css">','<style>'+Path('app/static/style.css').read_text()+'.meeting-banner{padding:14px 22px;background:#e8f3ef;border:1px solid #c6dbd3;border-radius:12px;margin-bottom:20px}.meeting-banner p{margin:6px 0}.reference-card{margin:20px 0}.reference-card table{font-size:13px}.notification-controls{margin:18px 0;padding:16px;border:1px solid #dbe6e2;border-radius:10px}[hidden]{display:none!important}</style>')
    page=page.replace('<script src="/static/app.js" defer></script>','')
    page=page.replace('href="/"','href="#"').replace('href="/static/user-guide.html"','href="はじめに.html"')
    banner='<aside class="meeting-banner"><strong>会議用デモ｜2026年10月21日</strong><p>医療機関・届出事項は厚生局の公表データ。ログインはデモ用アカウントです。</p><a href="はじめに.html" target="_blank">操作手順・デモ用アカウント</a> <button type="button" id="reset-meeting-demo" class="secondary">デモを初期化</button></aside>'
    page=page.replace('<main>','<main>'+banner,1)
    controls='<div class="notification-controls"><label class="inline"><input type="checkbox" id="use-notifications" name="use_notifications">届出事項を検索に利用する</label><div id="notification-fields" class="fields" hidden><label>届出項目（正式名称）<input name="notification_name" list="notification-options" placeholder="正式名称を入力して候補から選択" autocomplete="off"><datalist id="notification-options"></datalist></label><label>届出の有無<select name="filing_status"><option value="filed">届出済の</option><option value="unfiled">未届出の</option></select></label></div></div>'
    page=page.replace('<div class="search-footer">',controls+'<div class="search-footer">',1)
    dates='<section class="card reference-card"><h2>データ基準日</h2><div class="table-wrap"><table><thead><tr><th>厚生局・支局</th><th>医療機関データ</th><th>届出事項データ</th></tr></thead><tbody id="bureau-reference-dates"></tbody></table></div></section>'
    page=page.replace('<section class="card results">',dates+'<section class="card results">',1)
    import re
    for kind in ['full','master']:
        page=re.sub(r'<button\b[^>]*data-export="'+kind+r'"[^>]*>.*?</button>','',page)
    js=Path('app/static/app.js').read_text().replace("q.include_suspended=data.has('include_suspended');","q.include_suspended=data.has('include_suspended');q.use_notifications=data.has('use_notifications');")
    # User management is outside this meeting's scope; login history has its own tab.
    js=js.replace("$('#admin-nav').hidden=!['admin','master'].includes(user.role);","$('#admin-nav').hidden=true;")
    production_js=Path('web/pages.js').read_text();exports=production_js[production_js.index('function csv('):production_js.index('async function adminFunction(')]
    exports=production_js[production_js.index('function summary('):production_js.index('async function dashboardRows(')]+exports
    library=Path('web/node_modules/jszip/dist/jszip.min.js').read_text();bridge=Path('scripts/meeting_demo/bridge.js').read_text()
    bootstrap="""(async()=>{try{const bytes=Uint8Array.from(atob(document.getElementById('meeting-data').textContent.trim()),c=>c.charCodeAt(0));const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));const meetingData=JSON.parse(await new Response(stream).text());\n"""
    ending="\nprepareMeetingDemo();document.getElementById('meeting-loading').remove();}catch(e){document.getElementById('meeting-loading').textContent='Chrome または Edge の最新版で開いてください：'+e.message;}})();"
    page=page.replace('<section id="login-panel"','<p id="meeting-loading" role="status">デモデータを準備しています…</p><section id="login-panel"',1)
    script=library+'\n'+bootstrap+exports+'\n'+bridge+'\n'+js+ending
    assert '</script' not in script.lower()
    page=page.replace('</body>','<script id="meeting-data" type="application/octet-stream">'+encoded+'</script><script>'+script+'</script></body>')
    (out/'会議用デモ.html').write_text(page)
    active=sum(r['status']=='現存' for r in records)
    cover=f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>10月21日 会議用デモ</title><style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;background:#f4f7f6;color:#173e43;line-height:1.9}}section{{background:white;border:1px solid #dbe6e2;border-radius:14px;padding:24px;margin:20px 0}}a{{color:#086852}}.button{{display:inline-block;background:#136c58;color:white;border-radius:8px;padding:12px 22px;text-decoration:none}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;border-bottom:1px solid #dae4e0;padding:10px}}code{{font-size:15px}}</style><body><p>IMAGE ONE · MEETING DEMO</p><h1>医療機関を探し、社内の活用へ。</h1><p>2026年10月21日 会議用</p><a class="button" href="会議用デモ.html">会議用デモを開く</a><section><h2>会議での操作（約5分）</h2><ol><li>閲覧者でログインし、「福岡県・病院・20～200床」で検索します。</li><li>検索欄の下で8局・支局の基準日を確認します。中国四国と四国支局は別々に表示します。</li><li>「届出事項を検索に利用する」をONにし、「医療安全対策加算１」を候補から選択します。「届出済の」「未届出の」を切り替えて検索します。</li><li>CSVまたはExcelで検索結果を出力します。</li><li>ログアウトし、管理者でログインします。「ログイン履歴」を開き、先ほどの閲覧者のログイン日時を確認します。</li></ol></section><section><h2>デモ用アカウント</h2><table><tr><th>権限</th><th>メールアドレス</th><th>パスワード</th></tr><tr><td>閲覧者</td><td><code>viewer@example.invalid</code></td><td><code>DemoViewer2026!</code></td></tr><tr><td>管理者</td><td><code>admin@example.invalid</code></td><td><code>DemoAdmin2026!</code></td></tr></table><p>このファイル専用の架空アカウントです。実在のアカウントや本番のパスワードは使いません。</p></section><section><h2>収録データと利用方法</h2><p>厚生局の医科主一覧：47都道府県、現存{active:,}施設。届出事項：{len(by_name):,}種類の検索用正式名称。各局の基準日はデモ画面で確認できます。</p><p>ZIPをすべて展開し、この「はじめに.html」をChromeまたはEdgeで開きます。ネット接続やインストールは不要です。端末にダウンロードしておけば、会議室でも操作できます。</p><p>このデモのログイン履歴は、その端末・ブラウザ内のデモ操作で作成されます。実際の社内利用者の履歴とは別です。「デモを初期化」で最初から始められます。本番DBや公開サイトへは接続しません。</p></section></body></html>'''
    (out/'はじめに.html').write_text(cover)
    (out/'説明.txt').write_text('ZIPをすべて展開し、はじめに.htmlをChromeまたはEdgeで開いてください。\n医療機関・届出事項は厚生局の公表データです。デモ専用の架空アカウントを使います。実在の利用者履歴や本番認証情報は含みません。\n')
    (out/'JSZip-LICENSE.txt').write_text(Path('web/node_modules/jszip/LICENSE.markdown').read_text())
    report={'medical_rows':len(records),'active_institutions':active,'prefectures':47,'notification_names':len(by_name),'notification_institution_item_groups':sum(len(set(v)) for v in by_name.values()),'bureaus':bureaus,'production_connection':False,'production_accounts_included':False,'demo_html_sha256':hashlib.sha256((out/'会議用デモ.html').read_bytes()).hexdigest()}
    (out/'検証情報.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    archive=out.parent/'会議用デモ_20261021.zip'
    with ZipFile(archive,'w',ZIP_DEFLATED) as z:
        for f in sorted(out.iterdir()):
            if f.is_file() and not f.name.startswith('検証用'):z.write(f,f.name)
    print(json.dumps({**report,'zip':str(archive),'zip_bytes':archive.stat().st_size},ensure_ascii=False),flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True,type=Path);p.add_argument('--notifications',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();build(a.data,a.notifications,a.output)
