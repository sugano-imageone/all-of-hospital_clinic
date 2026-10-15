"""Local validation never writes to Supabase unless --apply is explicit."""
import argparse
import json
from pathlib import Path
from app.domain import PREFECTURES, summarize
from app.importers.kyushu import parse_archive
from app.exports import workbook_bytes

def parse_batch(directory,output):
    records,sources,inspection=[],[],[]
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    for pref in range(40,48):
        filename=f'0005040{pref-36:02d}.zip'
        files=list(Path(directory).rglob(filename))
        if len(files)!=1: raise ValueError(f'{filename}: 1ファイル必要（検出数 {len(files)}）')
        data,meta=parse_archive(files[0],pref)
        for src in meta: src.update(prefecture=PREFECTURES[pref],status='validated_local')
        records.extend(data);sources.extend(meta)
        inspection.append({'prefecture':PREFECTURES[pref],'archive':filename,'count':len(data),
          'hospitals':sum(r['kind']=='病院' for r in data),'clinics':sum(r['kind']=='診療所' for r in data),
          'active':sum(r['status']=='現存' for r in data),'suspended':sum(r['status']=='休止' for r in data),
          'review':sum(r['status']=='確認中' for r in data),'unknown_beds':sum(r['total_beds'] is None for r in data)})
    if len(set(r['code'] for r in records))!=len(records): raise ValueError('都道府県間コード重複')
    for name,value in [('institutions',records),('sources',sources),('summary',{'prefectures':inspection,'summary':summarize(records)})]:
        (output/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    # Unknown/review records remain in JSON for review but are excluded from ordinary output.
    eligible=[r for r in records if r['status']=='現存']
    (output/'全国医療機関マスター.xlsx').write_bytes(workbook_bytes(eligible,sources,'九州8県・ローカル検証。現存のみ。Supabase未登録。'))
    return records,sources,inspection

def apply_snapshot(db,records,source):
    data=[r for r in records if r['source_file']==source['filename'] and r['prefecture_code']==source['prefecture_code']]
    return db.rpc('import_snapshot',{'p_source':source,'p_records':data})

def main():
    parser=argparse.ArgumentParser(description='九州8県の提供ZIPを検証し、必要時のみDB登録')
    parser.add_argument('--input',required=True);parser.add_argument('--output',default='data/validated')
    parser.add_argument('--apply',action='store_true',help='DB構造を事前確認しマイグレーション適用後のみ使用')
    args=parser.parse_args()
    records,sources,inspection=parse_batch(args.input,args.output)
    print(json.dumps(inspection,ensure_ascii=False,indent=2))
    if args.apply:
        from app.store import Supabase
        db=Supabase(service=True)
        try:
            for source in sources: print(apply_snapshot(db,records,source))
        finally: db.close()
    else: print('ローカル検証のみ。Supabaseには書き込んでいません。')

if __name__=='__main__': main()
