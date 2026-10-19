"""Read-only national medical facility-notification collector; no DB or scheduler access."""
import argparse,concurrent.futures,hashlib,io,json,re,subprocess,time,zipfile
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urljoin,urlsplit
import httpx
from bs4 import BeautifulSoup
SEEDS={
'北海道':'https://kouseikyoku.mhlw.go.jp/hokkaido/gyomu/gyomu/hoken_kikan/todokede_juri_ichiran.html',
'東北':'https://kouseikyoku.mhlw.go.jp/tohoku/gyomu/gyomu/hoken_kikan/documents/201805koushin.html',
'関東信越':'https://kouseikyoku.mhlw.go.jp/kantoshinetsu/chousa/kijyun.html',
'東海北陸':'https://kouseikyoku.mhlw.go.jp/tokaihokuriku/newpage_00349.html',
'近畿':'https://kouseikyoku.mhlw.go.jp/kinki/gyomu/gyomu/hoken_kikan/shitei_jokyo_00004.html',
'中国四国':'https://kouseikyoku.mhlw.go.jp/chugokushikoku/chousaka/shisetsukijunjuri.html',
'四国':'https://kouseikyoku.mhlw.go.jp/shikoku/gyomu/gyomu/hoken_kikan/shitei/',
'九州':'https://kouseikyoku.mhlw.go.jp/kyushu/gyomu/gyomu/hoken_kikan/index_00007.html'}
HOST='kouseikyoku.mhlw.go.jp'
def official(u):
 p=urlsplit(u);return p.scheme=='https' and p.hostname==HOST and p.port in (None,443) and not p.username
def get(u):
 if not official(u):raise ValueError('Not official HTTPS')
 last=None
 for attempt in range(3):
  try:
   current=u
   for _ in range(5):
    with httpx.Client(timeout=90,follow_redirects=False) as client:
     with client.stream('GET',current) as response:
      if response.status_code in [301,302,303,307,308]:
       current=urljoin(current,response.headers['location'])
       if not official(current):raise ValueError('External redirect')
       continue
      response.raise_for_status();parts=[];size=0
      for part in response.iter_bytes():
       size+=len(part)
       if size>100*1024*1024:raise ValueError('File exceeds 100 MiB')
       parts.append(part)
      return b''.join(parts),dict(response.headers),current
   raise ValueError('Too many redirects')
  except httpx.HTTPError as e:last=e
 raise last
def clean(s):return re.sub(r'\s+','',s)
def resolve(name,url,html):
 soup=BeautifulSoup(html,'html.parser');alllinks=[(a,urljoin(url,a['href'])) for a in soup.select('a[href]') if official(urljoin(url,a['href']))]
 if name=='北海道':
  full=[u for a,u in alllinks if u.endswith('.xlsx') and '保険医療機関（医科）' in a.find_parent('tr').get_text(' ',strip=True)]
 elif name in ['東北','関東信越','東海北陸','近畿']:
  pat={'東北':r'/shisetsu-touhoku-ika-r\d{4}\.xlsx$','関東信越':r'/shisetsu_ika_r\d{4}(?:-\d+)?\.zip$','東海北陸':r'/\d{4}-06_01-01\.zip$','近畿':r'/\d{4}\.\d{1,2}_sisetukijun_ika\.zip$'}[name]
  full=[u for a,u in alllinks if re.search(pat,urlsplit(u).path)]
 elif name=='中国四国':
  hs=[h for h in soup.select('h2') if '届出受理医療機関名簿' in h.get_text() and not any(k in h.get_text() for k in ['主な','項目別'])];assert len(hs)==1
  table=hs[0].find_next('table');full=[urljoin(url,a['href']) for a in table.select('a[href]') if a['href'].endswith('.zip')]
 elif name=='四国':
  tables=[t for t in soup.select('table') if '届出受理医療機関名簿' in t.get_text() and '項目別' not in t.get_text()];assert len(tables)==1
  rows=[r for r in tables[0].select('tr') if clean(r.get_text()).startswith('医科')];assert len(rows)==1
  full=[urljoin(url,a['href']) for a in rows[0].select('a[href]') if a['href'].endswith('.zip')]
 elif name=='九州':
  tables=[t for t in soup.select('table') if len(t.select('a[href$=".zip"]'))==8 and '福岡' in t.get_text() and '沖縄' in t.get_text()]
  assert tables;full=[urljoin(url,a['href']) for a in tables[0].select('a[href$=".zip"]')]
 else:raise ValueError(name)
 full=list(dict.fromkeys(full));assert len(full)==(8 if name=='九州' else 1),(name,full)
 dictionaries=[]
 for a,u in alllinks:
  label=a.get_text(' ',strip=True)
  if ('略称' in label or '略称' in a.parent.get_text(' ',strip=True)) and u.endswith('.pdf') and not any(k in label for k in ['訪問','令和6年度','検索方法']):
   context=a.parent.get_text(' ',strip=True)
   preceding=a.find_previous('table')
   # Shikoku also publishes a separate visiting-nursing abbreviation dictionary.
   nursing=(name=='四国' and preceding is not None and '訪問看護' in preceding.get_text() and '届出受理医療機関名簿' not in preceding.get_text())
   if not nursing:dictionaries.append(u)
 if name=='北海道':
  dictionaries+=[u for a,u in alllinks if u.endswith('.pdf') and ('基本診療料の施設基準等及び' in a.get_text() or '特掲診療料の施設基準等及び' in a.get_text())]
 # Page-level update dates are metadata only; validate the data reference date from files separately.
 plain=soup.get_text(' ',strip=True);dates=re.findall(r'(?:令和\s*[0-9０-９]+年\s*[0-9０-９]+月\s*[0-9０-９]+日\s*現在|更新日[：:]\s*[0-9/年月日\-]+)',plain)
 return {'bureau':name,'page':url,'full_urls':full,'dictionary_urls':list(dict.fromkeys(dictionaries)),'page_dates':list(dict.fromkeys(dates)),'resolved_at':datetime.now(ZoneInfo('Asia/Tokyo')).isoformat()}
def download_one(task,root):
 name,role,u=task;dest=root/'raw'/name;dest.mkdir(parents=True,exist_ok=True);f=dest/Path(urlsplit(u).path).name
 d={'bureau':name,'role':role,'url':u,'retrieved_at':datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(),'path':str(f.relative_to(root))}
 try:
  b,h,final=get(u);d.update(bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),http_status=200,final_url=final,content_type=h.get('content-type'),last_modified=h.get('last-modified'))
  expected=f.suffix.lower()
  if expected in ['.zip','.xlsx']:
   z=zipfile.ZipFile(io.BytesIO(b));bad=z.testzip();assert bad is None,bad
   if expected=='.xlsx':assert '[Content_Types].xml' in z.namelist()
   else:
    members=[]
    for zi in z.infolist():
     if zi.is_dir():continue
     assert zi.file_size<=150*1024*1024
     data=z.read(zi);suffix=Path(zi.filename).suffix.lower()
     members.append({'member':zi.filename,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
     if suffix in ['.xlsx','.xls','.pdf']:
      target=root/'expanded'/name/(f.stem+'__'+Path(zi.filename).name);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    d['members']=members
  elif expected=='.pdf':assert b.startswith(b'%PDF'),b[:50]
  else:raise ValueError('Unexpected file extension')
  f.write_bytes(b);d['status']='downloaded_validated'
  if expected=='.pdf':
   t=root/'text'/name/(f.stem+'.txt');t.parent.mkdir(parents=True,exist_ok=True);v=subprocess.run(['pdftotext','-layout',str(f),str(t)],capture_output=True);d['text_extracted']=v.returncode==0 and t.exists() and t.stat().st_size>100
 except Exception as e:d.update(status='failed',error=type(e).__name__+': '+str(e))
 return d
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);args=ap.parse_args();root=Path(args.output);root.mkdir(parents=True,exist_ok=True);(root/'source-pages').mkdir(exist_ok=True)
 resolved=[]
 def page(item):
  name,url=item;b,h,final=get(url);(root/'source-pages'/f'{name}.html').write_bytes(b);return resolve(name,url,b)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for r in pool.map(page,SEEDS.items()):resolved.append(r);print('resolved',r['bureau'],len(r['full_urls']),len(r['dictionary_urls']),flush=True)
 (root/'sources.json').write_text(json.dumps(resolved,ensure_ascii=False,indent=2));tasks=[(r['bureau'],role,u) for r in resolved for role,key in [('full','full_urls'),('formal_name_dictionary','dictionary_urls')] for u in r[key]]
 results=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for r in pool.map(lambda t:download_one(t,root),tasks):results.append(r);print(r['bureau'],r['role'],r['status'],r.get('bytes'),flush=True)
 (root/'manifest.json').write_text(json.dumps({'scope':'医科の全届出項目。公式配布ZIPに混在する他種別は原本保存のみ、医科索引には混入させない。','production_write':False,'scheduler_enabled':False,'files':results},ensure_ascii=False,indent=2))
 if any(r['status']=='failed' for r in results):raise SystemExit(1)
if __name__=='__main__':main()
