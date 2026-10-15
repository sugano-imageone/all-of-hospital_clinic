"""Audit medical and dental workbooks only; pharmacy is excluded by user scope."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile
from app.domain import normalize,PREFECTURES
from app.importers.kyushu import workbook_rows,parse_archive

def inspect_all(directory):
    report=[]
    for pref in range(40,48):
        paths=list(Path(directory).rglob(f'0005040{pref-36:02d}.zip'))
        if len(paths)!=1: raise ValueError('県別ZIPが一意に見つかりません')
        path=paths[0];dental=set();supplements=[];files=[]
        with ZipFile(path) as z:
            for info in z.infolist():
                if 'yakkyoku' in info.filename.lower(): continue
                if not info.filename.lower().endswith(('.xls','.xlsx')): continue
                blob=z.read(info)
                for sheet,rows in workbook_rows(blob,info.filename):
                    header=' '.join(normalize(v) for row in rows[:11] for v in row)
                    match=re.search(r'現在\s+(医科|歯科)\s',header)
                    if not match or PREFECTURES[pref] not in header: raise ValueError('内容区分・県名不一致')
                    category=match.group(1);primaries=[]
                    for row in rows:
                        if len(row)<3:continue
                        if re.fullmatch(r'\d+',normalize(row[0])) and row[1] and row[2]:
                            digits=re.sub(r'\D','',normalize(row[1]))
                            if len(digits)!=7: raise ValueError('元コードが7桁ではありません')
                            primaries.append(digits)
                        elif category=='医科' and re.fullmatch(r'\([\d,・. ]+\)',normalize(row[1])):
                            supplements.append(re.sub(r'\D','',normalize(row[1])))
                    if len(primaries)!=len(set(primaries)): raise ValueError('元個票コード重複')
                    if category=='歯科': dental.update(primaries)
                    files.append({'file':info.filename,'sheet':sheet,'category':category,
                                  'count':len(primaries),'sha256':hashlib.sha256(blob).hexdigest()})
        records,_=parse_archive(path,pref)
        medical_files=[f for f in files if f['category']=='医科']
        if sum(f['count'] for f in medical_files)!=len(records): raise ValueError('医科個票数と解析施設数の不一致')
        report.append({'prefecture':PREFECTURES[pref],'archive':path.name,'files':files,
           'medical_records':len(records),'supplemental_dental_codes':len(supplements),
           'supplemental_codes_present_in_dental_list':sum(c in dental for c in supplements),
           'supplemental_codes_missing_from_dental_list':sorted(set(c for c in supplements if c not in dental)),
           'included_as_independent_medical_record':0,'pharmacy':'確認対象外'})
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',default='artifacts/medical-dental-inspection.json');args=p.parse_args()
    report=inspect_all(args.input);path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    for r in report:print(r['prefecture'],[(f['category'],f['count']) for f in r['files']], '補足歯科コード',r['supplemental_dental_codes'],'歯科一覧一致',r['supplemental_codes_present_in_dental_list'])
if __name__=='__main__':main()
