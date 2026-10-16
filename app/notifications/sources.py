"""Resolve official FULL medical rosters; never use a production DB or credentials."""
import hashlib
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from app.importers.discover import fetch, official_url

PAGES = {
 'hokkaido': 'https://kouseikyoku.mhlw.go.jp/hokkaido/gyomu/gyomu/hoken_kikan/todokede_juri_ichiran.html',
 'tohoku': 'https://kouseikyoku.mhlw.go.jp/tohoku/gyomu/gyomu/hoken_kikan/documents/201805koushin.html',
 'kantoshinetsu': 'https://kouseikyoku.mhlw.go.jp/kantoshinetsu/chousa/kijyun.html',
 'tokaihokuriku': 'https://kouseikyoku.mhlw.go.jp/tokaihokuriku/newpage_00349.html',
 'kinki': 'https://kouseikyoku.mhlw.go.jp/kinki/gyomu/gyomu/hoken_kikan/shitei_jokyo_00004.html',
 'chugokushikoku': 'https://kouseikyoku.mhlw.go.jp/chugokushikoku/chousaka/shisetsukijunjuri.html',
 'shikoku': 'https://kouseikyoku.mhlw.go.jp/shikoku/gyomu/gyomu/hoken_kikan/shitei/index.html',
 'kyushu': 'https://kouseikyoku.mhlw.go.jp/kyushu/gyomu/gyomu/hoken_kikan/index_00007.html',
}
PREFECTURES = {'hokkaido': [1], 'tohoku': list(range(2,8)),
 'kantoshinetsu': [8,9,10,11,12,13,14,15,19,20], 'tokaihokuriku': [16,17,21,22,23,24],
 'kinki': [18,25,26,27,28,29,30], 'chugokushikoku': list(range(31,36)),
 'shikoku': list(range(36,40)), 'kyushu': list(range(40,48))}

def resolve(bureau, html):
    page=PAGES[bureau];soup=BeautifulSoup(html,'html.parser');matches=[]
    patterns={'tohoku':r'/shisetsu-touhoku-ika-r\d+\.xlsx$',
              'kantoshinetsu':r'/shisetsu_ika_r\d+\.zip$',
              'kinki':r'/\d{4}\.\d{1,2}_sisetukijun_ika\.zip$'}
    if bureau=='kyushu':
        # Only the first complete current table; older months are on the same page.
        for table in soup.select('table'):
            text=table.get_text('',strip=True)
            urls=[urljoin(page,a['href']) for a in table.select('a[href]') if a['href'].lower().endswith('.zip')]
            if all(p in text for p in ['福岡','佐賀','沖縄']) and len(urls)==8:
                if not all(official_url(u) for u in urls):raise ValueError('非公式の配布先')
                return urls
    for a in soup.select('a[href]'):
        url=urljoin(page,a['href']);path=urlsplit(url).path;label=a.get_text('',strip=True)
        if not official_url(url):continue
        eligible=False
        if bureau in patterns:eligible=bool(re.search(patterns[bureau],path))
        elif bureau=='hokkaido':
            row=a.find_parent('tr');text=row.get_text('',strip=True) if row else ''
            eligible=text.startswith('保険医療機関（医科）') and path.endswith('.xlsx')
        elif bureau=='tokaihokuriku':eligible='届出受理医療機関名簿（医科）' in label and path.endswith('.zip')
        elif bureau=='chugokushikoku':
            h=a.find_previous(['h2','h3','h4']);text=h.get_text('',strip=True) if h else ''
            eligible='届出受理医療機関名簿' in text and '主な' not in text and path.endswith('.zip')
        elif bureau=='shikoku':
            table=a.find_parent('table');row=a.find_parent('tr')
            text=table.get_text('',strip=True) if table else '';rowtext=re.sub(r'\s+','',row.get_text('',strip=True)) if row else ''
            eligible='届出受理医療機関名簿' in text and '届出項目別' not in text and rowtext.startswith('医科') and path.endswith('.zip')
        if eligible:matches.append(url)
    matches=list(dict.fromkeys(matches))
    if len(matches)!=1:raise ValueError(f'{bureau}: 全体医科の配布元が一意でありません ({len(matches)})')
    return matches

def acquire(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);results=[]
    for bureau,page in PAGES.items():
        html=fetch(page,5*1024*1024);(directory/(bureau+'.html')).write_bytes(html)
        for url in resolve(bureau,html):
            data=fetch(url);digest=hashlib.sha256(data).hexdigest()
            path=directory/(bureau+'-'+digest+Path(urlsplit(url).path).suffix)
            if not path.exists():path.write_bytes(data)
            results.append({'bureau':bureau,'page':page,'url':url,'path':str(path.resolve()),'sha256':digest,'bytes':len(data)})
    return results
