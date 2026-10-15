"""Read-only link discovery. Candidates require layout/scope verification before activation."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urljoin,urlsplit
import httpx
from bs4 import BeautifulSoup

OFFICIAL_HOST='kouseikyoku.mhlw.go.jp'
SEEDS={
 '北海道厚生局':'https://kouseikyoku.mhlw.go.jp/hokkaido/gyomu/gyomu/hoken_kikan/zentai_ichiran.html',
 '東北厚生局':'https://kouseikyoku.mhlw.go.jp/tohoku/gyomu/gyomu/hoken_kikan/itiran.html',
 '関東信越厚生局':'https://kouseikyoku.mhlw.go.jp/kantoshinetsu/chousa/shitei.html',
 '東海北陸厚生局':'https://kouseikyoku.mhlw.go.jp/tokaihokuriku/newpage_00287.html',
 '近畿厚生局':'https://kouseikyoku.mhlw.go.jp/kinki/tyousa/shinkishitei.html',
 '中国四国厚生局':'https://kouseikyoku.mhlw.go.jp/chugokushikoku/chousaka/iryoukikanshitei.html',
 '四国厚生支局':'https://kouseikyoku.mhlw.go.jp/shikoku/gyomu/gyomu/hoken_kikan/shitei/',
 '九州厚生局':'https://kouseikyoku.mhlw.go.jp/kyushu/gyomu/gyomu/hoken_kikan/index_00006.html'}

def official_url(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.hostname==OFFICIAL_HOST and p.port in [None,443] and not p.username

def fetch(url,max_bytes=40*1024*1024):
    if not official_url(url): raise ValueError('公式HTTPSホスト以外にはアクセスできません')
    # Redirects must be checked before following to prevent arbitrary outbound requests.
    for _ in range(5):
        with httpx.stream('GET',url,timeout=60,follow_redirects=False) as response:
            if response.status_code in [301,302,303,307,308]:
                url=urljoin(url,response.headers['location'])
                if not official_url(url): raise ValueError('公式ホスト外へのリダイレクト')
                continue
            response.raise_for_status();parts=[];size=0
            for part in response.iter_bytes():
                size+=len(part)
                if size>max_bytes: raise ValueError('ダウンロードサイズ超過')
                parts.append(part)
            return b''.join(parts)
    raise ValueError('リダイレクトが多すぎます')

def discover(bureau,url):
    html=fetch(url,5*1024*1024)
    soup=BeautifulSoup(html,'html.parser')
    links=[]
    for a in soup.select('a[href]'):
        target=urljoin(url,a['href'])
        if not official_url(target): continue
        if urlsplit(target).path.lower().endswith(('.zip','.xls','.xlsx','.pdf','.html','/')):
            links.append({'url':target,'label':a.get_text(' ',strip=True),'verified':False})
    return {'bureau':bureau,'page':url,'candidates':links,'status':'discovered_not_approved'}

def current_links(bureau,html=None):
    """Resolve the current full medical listing. Content adapters validate date/region next."""
    page=SEEDS[bureau]
    soup=BeautifulSoup(fetch(page) if html is None else html,'html.parser')
    patterns={'東北厚生局':r'/shitei-touhoku-ika-r\d{4}\.xlsx$',
              '関東信越厚生局':r'/shitei_ika_r\d{4}(?:-\d+)?\.zip$',
              '東海北陸厚生局':r'/\d{4}-01-01\.zip$',
              '近畿厚生局':r'/\d{4}\.\d{1,2}_kikanzentai_ika\.zip$'}
    if bureau=='九州厚生局':
        for table in soup.select('table'):
            links=[urljoin(page,a['href']) for a in table.select('a[href]') if a['href'].lower().endswith('.zip')]
            text=table.get_text(' ',strip=True)
            if len(links)==8 and '福岡' in text and '沖縄' in text:
                if not all(official_url(u) for u in links):raise ValueError('非公式配布先')
                return links
    else:
        matches=[]
        for a in soup.select('a[href]'):
            url=urljoin(page,a['href'])
            if not official_url(url):continue
            if bureau in patterns:
                eligible=bool(re.search(patterns[bureau],urlsplit(url).path))
            else:
                row=a.find_parent('tr')
                context=re.sub(r'\s+','',row.get_text(' ',strip=True)) if row else ''
                eligible=(context.startswith('医科') and urlsplit(url).path.lower().endswith('.xlsx' if bureau=='北海道厚生局' else '.zip'))
                if eligible and bureau=='四国厚生支局':
                    table=a.find_parent('table')
                    eligible=bool(table and 'コード内容別医療機関一覧表' in table.get_text())
            if eligible:matches.append(url)
        # Pages are current-listing tables, not an archive-date guessing heuristic.
        unique=list(dict.fromkeys(matches))
        if len(unique)==1:return unique
    raise ValueError('最新版の医科全件リンクを一意に解決できません。公式ページ変更を確認してください')

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default='artifacts/discovery.json');args=p.parse_args()
    results=[]
    for bureau,url in SEEDS.items():
        try: results.append(discover(bureau,url))
        except (ValueError,httpx.HTTPError) as e:
            results.append({'bureau':bureau,'page':url,'status':'failed','error':type(e).__name__})
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps([{'bureau':r['bureau'],'status':r['status'],'candidates':len(r.get('candidates',[]))} for r in results],ensure_ascii=False))
    if any(r['status']=='failed' for r in results): raise SystemExit(1)
if __name__=='__main__':main()
