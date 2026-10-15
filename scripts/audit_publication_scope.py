"""Read-only comparison of separately published annexes and selected main lists.

Run after downloading the four official files into data/scope-audit.
Dental annex codes are kept as printed; this script never imports or invents IDs.
"""
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile

from app.domain import PREFECTURES
from app.importers.kyushu import normalize, workbook_rows


def main():
    records = [r for p in Path('data/national').glob('*.institutions.json')
               for r in json.loads(p.read_text())]
    codes = {r['code'] for r in records}
    results = []
    for name, region, medical in [
        ('shitei_shikaheisetsu_r0810.zip', 'kantoshinetsu', True),
        ('shitei-touhoku-shikaheisetsu-r0810.xlsx', 'tohoku', True),
        ('shitei_ikaheisetsu_r0810.zip', 'kantoshinetsu', False),
        ('shitei-touhoku-ikaheisetsu-r0810.xlsx', 'tohoku', False),
    ]:
        path = Path('data/scope-audit') / name
        blob = path.read_bytes()
        if path.suffix == '.zip':
            with ZipFile(path) as archive:
                files = [(n, archive.read(n)) for n in archive.namelist()
                         if n.lower().endswith(('.xls', '.xlsx'))]
        else:
            files = [(name, blob)]
        rows = []
        for filename, data in files:
            for sheet, cells in workbook_rows(data, filename):
                pref = (int(re.search(r'/(\d{2})[24]', filename)[1])
                        if region == 'kantoshinetsu'
                        else next(k for k, v in PREFECTURES.items() if v == sheet))
                current = None
                for cell in cells:
                    if len(cell) > 2 and normalize(cell[0]).isdigit():
                        current = {'prefecture_code': pref, 'name': normalize(cell[2]),
                                   'printed_primary_code': normalize(cell[1])}
                        if medical:
                            suffix = re.sub(r'\D', '', normalize(cell[1]))
                            assert len(suffix) == 7
                            current['medical_code'] = f'{pref:02d}1{suffix}'
                            current['in_main'] = current['medical_code'] in codes
                        rows.append(current)
                    elif current and len(cell) > 1 and '(' in normalize(cell[1]):
                        current['printed_parenthesized_code'] = normalize(cell[1])
        results.append({
            'url': f'https://kouseikyoku.mhlw.go.jp/{region}/{name}',
            'sha256': hashlib.sha256(blob).hexdigest(),
            'publication_scope': '医科（歯科併設）' if medical else '歯科（医科併設）',
            'primary_rows': len(rows),
            'medical_codes_already_in_main': sum(r.get('in_main', False) for r in rows) if medical else None,
            'interpretation': ('主一覧に全件収録済み' if medical and all(r['in_main'] for r in rows)
                               else '歯科側の主コードと括弧コード。医科登録の関係は要確認。未登録医科施設数として計上しない'),
            'rows': rows,
        })
    assert sum(r['primary_rows'] for r in results[:2]) == 744
    assert all(r['medical_codes_already_in_main'] == r['primary_rows'] for r in results[:2])
    Path('docs/supplemental-source-audit.json').write_text(json.dumps(
        {'scope': '関東信越・東北の別配布物。全国の補足一覧をすべて確認したものではない',
         'sources': results}, ensure_ascii=False, indent=2))
    print(json.dumps([{k: r[k] for k in ['publication_scope', 'primary_rows',
          'medical_codes_already_in_main']} for r in results], ensure_ascii=False))


if __name__ == '__main__':
    main()
