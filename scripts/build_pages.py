"""Build a data-free GitHub Pages client; publishable key only, never service keys."""
import argparse,base64,hashlib,html,json,os,shutil
from urllib.parse import urlsplit
from pathlib import Path
from jinja2 import Environment,FileSystemLoader,select_autoescape
from app.domain import PREFECTURES,REGIONS
from app.exports import COLUMNS

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',default='artifacts/pages');args=p.parse_args()
 saved=json.loads(Path('web/public-config.json').read_text()) if Path('web/public-config.json').exists() else {}
 url=os.getenv('SUPABASE_URL',saved.get('url','')).rstrip('/');key=os.getenv('SUPABASE_PUBLISHABLE_KEY',saved.get('key',''))
 parsed=urlsplit(url);assert parsed.scheme=='https' and parsed.hostname.endswith('.supabase.co') and parsed.path in ['','/'] and not parsed.username
 if key.startswith('eyJ'):
  claims=json.loads(base64.urlsafe_b64decode(key.split('.')[1]+'==='));assert claims['role']=='anon'
 else:assert key.startswith('sb_publishable_') and not key.startswith('sb_secret_')
 output=Path(args.output);output.mkdir(parents=True,exist_ok=True);static=output/'static';static.mkdir(exist_ok=True)
 env=Environment(loader=FileSystemLoader('app/templates'),autoescape=select_autoescape(['html']))
 page=env.get_template('index.html').render(prefectures=PREFECTURES,bureaus=REGIONS)
 settings=json.loads(Path('web/site-settings.json').read_text())
 assert isinstance(settings.get('title'),str) and 1<=len(settings['title'])<=120
 page=page.replace('全国医療機関データベース｜イメージワン',html.escape(settings['title']))
 page=page.replace('</nav>','<a id="site-settings-link" class="secondary" href="https://github.com/sugano-imageone/all-of-hospital_clinic/edit/main/web/site-settings.json" target="_blank" rel="noopener" hidden>サイト設定を変更（GitHub）</a></nav>')
 page=page.replace('href="/"','href="./"').replace('href="/static/','href="./static/')
 page=page.replace('<script src="/static/app.js" defer></script>','<script src="./static/jszip.min.js" defer></script><script src="./static/pages.js" defer></script><script src="./static/app.js" defer></script>')
 config=json.dumps({'url':url,'key':key,'columns':COLUMNS,'prefectureCodes':{name:code for code,name in PREFECTURES.items()}},ensure_ascii=False).replace('<','\\u003c')
 csp="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' "+url+"; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'"
 page=page.replace('</head>','<meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="'+csp+'"><script type="application/json" id="cloud-config">'+config+'</script></head>')
 admin='''<section class="card"><h2>ユーザーを追加</h2><p>メールアドレスがログインIDになります。閲覧者は検索・集計の閲覧・ダウンロードを利用できます。</p><form id="member-create" class="fields"><label>メールアドレス（ID）<input name="email" type="email" required autocomplete="off"></label><label>仮パスワード（12文字以上）<input name="password" type="password" minlength="12" maxlength="72" required autocomplete="new-password"></label><button type="button" id="generate-password" class="secondary">仮パスワードを自動生成</button><label>権限<select name="role"><option value="user">閲覧者</option><option value="admin">管理者</option></select></label><button type="submit">ユーザーを追加</button></form><section id="registration-memo" hidden><h3>送付用メモ</h3><p>以下をコピーして、管理者から利用者へメールで送信してください。仮パスワードはこの画面を離れる前に控えてください。</p><label>メール本文<textarea id="registration-memo-text" rows="12" readonly autocomplete="off"></textarea></label><div class="actions"><button type="button" id="copy-registration-memo">メモをコピー</button><button type="button" id="save-registration-memo" class="secondary">テキスト保存</button></div></section></section>'''
 page=page.replace('<div id="members">',admin+'<div id="members">')
 # The existing template has a plain empty members container.
 if 'id="member-create"' not in page:page=page.replace('<div id="members"></div>',admin+'<div id="members"></div>')
 assert 'id="member-create"' in page
 page=page.replace('Auth のアカウント作成・招待は Supabase ダッシュボードで行ってください。本人の権限はこの画面から変更できません。','ユーザー追加と利用承認・権限変更は管理者のみ行えます。自分自身の権限は変更できません。')
 page=page.replace('承認された利用者のみ閲覧・出力できます。','管理者が登録・承認したアカウントでログインしてください。')
 page=page.replace('<p class="muted">アカウントの発行・利用承認は管理者へご依頼ください。</p>', '<button id="connection-check" type="button" class="secondary">接続を確認</button><p id="connection-check-result" role="status"></p><p class="muted">アカウントの発行・利用承認は管理者へご依頼ください。</p>')
 page=page.replace('</nav>','<button type="button" class="nav" data-tab="password-panel">パスワード変更</button></nav>')
 page=page.replace('</div></main>','<section id="password-panel" class="tab card" hidden><h2>パスワード変更</h2><p>仮パスワードでログインした場合は、ご自身のパスワードに変更してください。</p><form id="password-change" class="fields"><label>新しいパスワード（12文字以上）<input name="password" type="password" minlength="12" maxlength="72" required autocomplete="new-password"></label><label>新しいパスワード（確認）<input name="confirmation" type="password" minlength="12" maxlength="72" required autocomplete="new-password"></label><button type="submit">パスワードを変更</button></form></section></div></main>')
 # Keep paired client files in sync after a release, even if older JS is cached.
 for filename,source in [('app.js','app/static/app.js'),('pages.js','web/pages.js'),('style.css','app/static/style.css')]:
  revision=hashlib.sha256(Path(source).read_bytes()).hexdigest()[:12]
  page=page.replace('./static/'+filename+'"','./static/'+filename+'?v='+revision+'"')
 (output/'index.html').write_text(page);(output/'.nojekyll').write_text('')
 for name in ['app.js','style.css','user-guide.html']:shutil.copy2(Path('app/static')/name,static/name)
 shutil.copy2('web/pages.js',static/'pages.js');shutil.copy2('web/node_modules/jszip/dist/jszip.min.js',static/'jszip.min.js');shutil.copy2('web/node_modules/jszip/LICENSE.markdown',output/'JSZip-LICENSE.txt')
 assert not list(output.glob('*.xlsx')) and not list(output.glob('*.json'))
 print('GitHub Pages client built; no medical dataset, passwords or service key included')

if __name__=='__main__':main()
