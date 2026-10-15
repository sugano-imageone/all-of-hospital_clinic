"""Adapters for content-validated official regional workbooks.

The national runner reports unsupported layouts explicitly; never substitutes estimates.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO
from app.domain import PREFECTURES,REGIONS
from app.importers.kyushu import parse_workbook,workbook_rows,MAX_SIZE,normalize

def identify_prefecture(filename,sheet,rows,bureau):
    header=' '.join(normalize(v) for r in rows[:11] for v in r)
    found=[p for p in REGIONS[bureau] if PREFECTURES[p] in header or PREFECTURES[p]==sheet]
    if len(found)==1: return found[0],False
    if bureau=='関東信越厚生局':
        # Kanto omits prefecture in header; official ZIP entries encode NN1 (prefecture+medical).
        name=filename.replace('\\','/').split('/')[-1]
        m=re.match(r'(\d{2})1コード内容別一覧表',name)
        if m and int(m[1]) in REGIONS[bureau]: return int(m[1]),True
    raise ValueError('内容から都道府県を特定できません')

def parse_region(path,bureau,retrieved_at=None,source_url=None):
    path=Path(path);blob=path.read_bytes()
    if len(blob)>MAX_SIZE: raise ValueError('配布ファイルサイズ超過')
    if path.suffix=='.zip':
        with ZipFile(BytesIO(blob)) as z:
            if sum(i.file_size for i in z.infolist())>100*1024*1024: raise ValueError('ZIP展開サイズ超過')
            files=[(i.filename,z.read(i)) for i in z.infolist() if i.filename.lower().endswith(('.xls','.xlsx')) and 'yakkyoku' not in i.filename.lower()]
    else: files=[(path.name,blob)]
    records,sources,errors=[],[],[]
    for filename,data in files:
        for sheet,rows in workbook_rows(data,filename):
            header=' '.join(normalize(v) for r in rows[:11] for v in r)
            if '歯科' in header and not re.search(r'現在\s+医科\s',header):continue
            try:
                if bureau=='北海道厚生局':
                    from app.importers.hokkaido import parse_hokkaido
                    parsed,meta=parse_hokkaido(data,filename,sheet,rows,retrieved_at)
                    pref=1
                else:
                    pref,missing=identify_prefecture(filename,sheet,rows,bureau)
                    parsed,meta=parse_workbook(data,filename,pref,retrieved_at,bureau=bureau,
                         allow_missing_prefecture=missing,_sheets=[(sheet,rows)])
                for s in meta:
                    s.update(prefecture=PREFECTURES[pref],source_url=source_url,
                       archive_sha256=hashlib.sha256(blob).hexdigest(),status='validated_local')
                records.extend(parsed);sources.extend(meta)
            except ValueError as e:
                errors.append({'file':filename,'sheet':sheet,'error':str(e)})
    if len({r['code'] for r in records})!=len(records): raise ValueError('厚生局内コード重複')
    return records,sources,errors

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--input',default='data/raw');p.add_argument('--output',default='data/national')
    p.add_argument('--fetch',action='store_true');p.add_argument('--export',action='store_true')
    args=p.parse_args()
    aliases={'hokkaido.xlsx':'北海道厚生局','tohoku.xlsx':'東北厚生局','kanto.zip':'関東信越厚生局',
       'tokai.zip':'東海北陸厚生局','kinki.zip':'近畿厚生局','chugoku.zip':'中国四国厚生局','shikoku.zip':'四国厚生支局'}
    input_dir=Path(args.input);input_dir.mkdir(parents=True,exist_ok=True)
    if args.fetch:
        from app.importers.discover import current_links,fetch
        urls={}
        for alias,bureau in list(aliases.items()):
            links=current_links(bureau)
            if len(links)!=1:raise ValueError('厚生局の全件配布物が一意ではありません')
            data=fetch(links[0]);(input_dir/alias).write_bytes(data);urls[alias]=links[0]
        for pref,url in zip(REGIONS['九州厚生局'],current_links('九州厚生局'),strict=True):
            alias=f'kyushu-{pref}.zip';(input_dir/alias).write_bytes(fetch(url));urls[alias]=url
        (input_dir/'urls.json').write_text(json.dumps(urls,ensure_ascii=False,indent=2))
    else:urls=json.loads((input_dir/'urls.json').read_text())
    for pref in range(40,48):
        alias=f'kyushu-{pref}.zip'
        if alias in urls:aliases[alias]='九州厚生局'
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    report=[];all_records=[];all_sources=[]
    for filename,bureau in aliases.items():
        try:
            if bureau=='九州厚生局':
                from app.importers.kyushu import parse_archive
                pref=int(filename[7:9]);records,sources=parse_archive(input_dir/filename,pref)
                for src in sources:src.update(prefecture=PREFECTURES[pref],source_url=urls[filename],status='validated_local')
                errors=[]
            else:records,sources,errors=parse_region(input_dir/filename,bureau,source_url=urls[filename])
            (out/(filename+'.institutions.json')).write_text(json.dumps(records,ensure_ascii=False))
            (out/(filename+'.sources.json')).write_text(json.dumps(sources,ensure_ascii=False))
            all_records.extend(records);all_sources.extend(sources)
            item={'bureau':bureau,'file':filename,'downloaded':True,'record_count':len(records),
               'prefectures':sorted({r['prefecture_code'] for r in records}),'errors':errors,'source_url':urls[filename]}
        except Exception as e:item={'bureau':bureau,'file':filename,'downloaded':True,'record_count':0,'errors':[{'error':str(e)}]}
        report.append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if args.export:
        from app.exports import workbook_bytes
        (out/'全国医療機関マスター.xlsx').write_bytes(workbook_bytes(
            [r for r in all_records if r['status']=='現存'],all_sources,
            '公式主一覧のローカル検証。現存のみ。北海道は2026-10-15指定ルール適用。近畿医科併設は未解決。全国完全版ではありません。Supabase未登録。'))
if __name__=='__main__':main()
