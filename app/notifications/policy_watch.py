"""Track MHLW pages carrying gazetted notices and attachments; not Chuikyo proposals."""
import argparse,hashlib,json,re
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin,urlsplit
import httpx
from bs4 import BeautifulSoup
from app.notifications.store import connect,dumps

PAGES=[
 'https://www.mhlw.go.jp/stf/newpage_67729.html',
 'https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/0000188411_00074.html']
SCHEMA='''
CREATE TABLE IF NOT EXISTS policy_pages(url TEXT PRIMARY KEY,sha TEXT NOT NULL,catalog_json TEXT NOT NULL,checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS policy_events(id INTEGER PRIMARY KEY,url TEXT NOT NULL,old_sha TEXT NOT NULL,new_sha TEXT NOT NULL,new_links_json TEXT NOT NULL,detected_at TEXT NOT NULL,review_note TEXT,UNIQUE(url,new_sha));
'''
def fetch(url):
    for _ in range(5):
        p=urlsplit(url)
        if p.scheme!='https' or p.hostname!='www.mhlw.go.jp' or p.port not in [None,443] or p.username:raise ValueError('公式の厚生労働省HTTPS以外は取得しません')
        with httpx.stream('GET',url,timeout=45,follow_redirects=False) as r:
            if r.status_code in [301,302,303,307,308]:url=urljoin(url,r.headers['location']);continue
            r.raise_for_status();parts=[];size=0
            for part in r.iter_bytes():
                size+=len(part)
                if size>5*1024*1024:raise ValueError('制度ページのサイズ超過')
                parts.append(part)
            return b''.join(parts)
    raise ValueError('リダイレクト上限')
def catalog(url,html):
    soup=BeautifulSoup(html,'html.parser');main=soup.select_one('#content') or soup.select_one('main') or soup
    found=[]
    for a in main.select('a[href]'):
        target=urljoin(url,a['href']);p=urlsplit(target);label=a.get_text(' ',strip=True)
        if p.scheme=='https' and (p.hostname=='mhlw.go.jp' or (p.hostname or '').endswith('.mhlw.go.jp')) and label and not a['href'].startswith('#'):
            row=a.find_parent('tr') or a.parent
            found.append({'url':target,'label':label,'context':row.get_text(' ',strip=True)[:1600]})
    if len(found)<5:raise ValueError('制度公表ページの構造が変わった可能性があります')
    return found

def watch(db,fetcher=fetch):
    db.executescript(SCHEMA);results=[]
    for url in PAGES:
        items=catalog(url,fetcher(url));encoded=dumps(items);sha=hashlib.sha256(encoded.encode()).hexdigest();now=datetime.now(ZoneInfo('Asia/Tokyo')).isoformat()
        with db:
            old=db.execute('SELECT * FROM policy_pages WHERE url=?',(url,)).fetchone()
            state='baseline' if not old else 'unchanged' if old['sha']==sha else 'review_required'
            if state=='review_required':
                previous=json.loads(old['catalog_json']);before={dumps(x) for x in previous};after={dumps(x) for x in items}
                changed=[{**x,'change':'added_or_changed'} for x in items if dumps(x) not in before]+[{**x,'change':'removed_or_changed'} for x in previous if dumps(x) not in after]
                new=[x for x in changed if re.search(r'告示|官報|別添|別表|訂正',x['label']+' '+x['context'])]
                if not new:state='updated_without_policy_keywords'
                if new:db.execute('INSERT OR IGNORE INTO policy_events(url,old_sha,new_sha,new_links_json,detected_at) VALUES(?,?,?,?,?)',(url,old['sha'],sha,dumps(new),now))
            db.execute('INSERT INTO policy_pages VALUES(?,?,?,?) ON CONFLICT(url) DO UPDATE SET sha=excluded.sha,catalog_json=excluded.catalog_json,checked_at=excluded.checked_at',(url,sha,encoded,now))
        results.append({'url':url,'status':state,'links':len(items),'sha256':sha})
    return results

def unreviewed(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy_events'").fetchone():return []
    slots=','.join('?' for _ in PAGES)
    return db.execute(f'SELECT * FROM policy_events WHERE review_note IS NULL AND url IN ({slots}) ORDER BY id DESC',PAGES).fetchall()

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/notifications/development-v2.sqlite');p.add_argument('--review-event',type=int);p.add_argument('--note',default='');a=p.parse_args();db=connect(a.db)
    try:
        if a.review_event:
            if not a.note.strip():raise ValueError('確認した資料と適用時期についての記録が必要です')
            with db:
                changed=db.execute('UPDATE policy_events SET review_note=? WHERE id=? AND review_note IS NULL',(a.note,a.review_event))
                if changed.rowcount!=1:raise ValueError('未確認の公表変更が見つかりません')
        else:print(dumps(watch(db)))
    finally:db.close()
if __name__=='__main__':main()
