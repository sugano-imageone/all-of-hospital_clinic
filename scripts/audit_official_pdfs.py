"""Independently compare official medical PDFs to imported Excel code sets."""
import hashlib
import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.domain import PREFECTURES, REGIONS
from app.importers.discover import SEEDS, fetch


def resolve(bureau):
    page = SEEDS[bureau]
    soup = BeautifulSoup(fetch(page), 'html.parser')
    found = []
    for anchor in soup.select('a[href]'):
        url = urljoin(page, anchor['href'])
        if urlsplit(url).hostname != 'kouseikyoku.mhlw.go.jp' or not url.endswith('.pdf'):
            continue
        label = re.sub(r'\s+', '', anchor.get_text())
        row = anchor.find_parent('tr')
        context = re.sub(r'\s+', '', row.get_text()) if row else ''
        if bureau == '東北厚生局':
            eligible = bool(re.search(r'/shitei-\d{2}[^/]+-ika-r\d{4}\.pdf$', url))
        elif bureau == '関東信越厚生局':
            eligible = bool(re.search(r'/\d{2}1shitei_ika_', url))
        elif bureau == '東海北陸厚生局':
            eligible = 'コード内容別医療機関一覧表' in label and '医科' in label
        elif bureau == '近畿厚生局':
            eligible = bool(re.search(r'_kikanzentai_[a-z]+_ika\.pdf$', url))
        elif bureau == '九州厚生局':
            eligible = label.startswith('医科')
        else:
            eligible = context.startswith('医科')
        if eligible and url not in found:
            found.append(url)
    prefectures = REGIONS[bureau]
    # Official pages place the current main-list table first; verify content below.
    if bureau in ['九州厚生局', '四国厚生支局']:
        found = found[:len(prefectures)]
    assert len(found) == len(prefectures), (bureau, len(found))
    return [(pref, url) for pref, url in zip(prefectures, found, strict=True)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-pdfs', action='store_true')
    args = parser.parse_args()
    data = [r for p in Path('data/national').glob('*.institutions.json')
            for r in json.loads(p.read_text())]
    expected = {p: {r['code'] for r in data if r['prefecture_code'] == p} for p in PREFECTURES}
    if args.local_pdfs:
        previous = json.loads(Path('docs/official-pdf-code-audit.json').read_text())
        paths_by_hash = {hashlib.sha256(p.read_bytes()).hexdigest(): p
                         for p in Path('data/scope-audit').glob('medical-main-*.pdf')}
        jobs = [(r['prefecture_code'], r['url'], paths_by_hash[r['sha256']]) for r in previous['prefectures']]
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [(p, url, Path('data/scope-audit') / f'medical-main-{p}.pdf')
                    for group in pool.map(resolve, REGIONS) for p, url in group]
    assert {p for p, _, _ in jobs} == set(PREFECTURES)
    def verify(job):
        pref, url, path = job
        if not args.local_pdfs:
            blob = fetch(url)
            path.write_bytes(blob)
            subprocess.run(['pdftotext', '-layout', str(path), str(path.with_suffix('.txt'))], check=True)
        else:
            blob = path.read_bytes()
        text = path.with_suffix('.txt').read_text()
        # Shikoku lists Kagawa before Tokushima. Resolve the actual PDF header.
        identified = [p for p, name in PREFECTURES.items() if f'[{name}]' in text[:500]]
        if identified:
            assert len(identified) == 1
            pref = identified[0]
        result = {'prefecture_code': pref, 'prefecture': PREFECTURES[pref], 'url': url,
                  'sha256': hashlib.sha256(blob).hexdigest(), 'excel_count': len(expected[pref])}
        if pref != 1:
            assert re.search(r'令和\s*8年\s*10月\s*1日現在\s+医科\s', text[:500]), (pref, text[:160])
        matches = [(n, c) for n, c in re.findall(r'^\s*(\d{1,5})[^\S\n]+(\d(?:[,・.\- \t]*\d){6})(?!\d)(?=[^\S\n])', text, re.M)
                   if len(re.sub(r'\D', '', c)) == 7]
        sequence = [int(n) for n, _ in matches]
        codes = [f'{pref:02d}1' + re.sub(r'\D', '', c) for _, c in matches]
        consecutive = sequence == list(range(1, len(sequence) + 1))
        result.update(pdf_count=len(codes), consecutive_sequence=consecutive,
                      sequence_scope='郡市区ごとに連番リセット' if pref == 1 else '県内通し連番',
                      unique_pdf_codes=len(set(codes)),
                      missing_in_excel=sorted(set(codes) - expected[pref]),
                      absent_from_pdf=sorted(expected[pref] - set(codes)))
        result['status'] = ('match' if (consecutive or pref == 1) and len(codes) == len(set(codes))
                            and set(codes) == expected[pref] else 'mismatch_or_unparsed')
        print(json.dumps({k: result[k] for k in ['prefecture', 'status', 'excel_count']}), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(verify, jobs))
    assert len(results) == 47 and {r['prefecture_code'] for r in results} == set(PREFECTURES)
    report = {'scope': '公式の通常医科PDFと取得済みExcelの独立した10桁コード照合。別表や非保険施設の網羅性とは区別する',
              'verified_at': datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(),
              'pdf_records': sum(r['pdf_count'] for r in results),
              'excel_records': sum(r['excel_count'] for r in results),
              'all_code_sets_equal': all(r['status'] == 'match' for r in results),
              'matched_prefectures': sum(r['status'] == 'match' for r in results),
              'prefectures': sorted(results, key=lambda r: r['prefecture_code'])}
    Path('docs/official-pdf-code-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('matched_prefectures', report['matched_prefectures'])


if __name__ == '__main__':
    main()
