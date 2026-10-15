"""Reconcile a supplied medical-facility monthly aggregate against validated lists."""
import argparse,csv,hashlib,html,json,re
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from openpyxl import load_workbook
from app.domain import normalize


def main():
    p=argparse.ArgumentParser();p.add_argument('workbook');p.add_argument('--data',default='data/national');p.add_argument('--output',default='artifacts/統計照合');args=p.parse_args()
    path=Path(args.workbook);book=load_workbook(path,data_only=True,read_only=True)
    rows=[r for f in Path(args.data).glob('*.institutions.json') for r in json.loads(f.read_text())]
    active=[r for r in rows if r['status']=='現存']
    clinics=[r for r in active if r['kind']=='診療所'];hospitals=[r for r in active if r['kind']=='病院']
    first=list(book.worksheets[0].values)
    values={re.sub(r'\s+','',normalize(r[1])):r[2] for r in first if len(r)>2 and isinstance(r[2],(int,float))}
    period=normalize(first[3][2]);stats_clinics=values['一般診療所'];stats_hospitals=values['病院']
    prefectures=[]
    for r in book.worksheets[2].values:
        if isinstance(r[1],int) and 1<=r[1]<=47:
            local=[x for x in rows if x['prefecture_code']==r[1]]
            c=sum(x['status']=='現存' and x['kind']=='診療所' for x in local)
            h=sum(x['status']=='現存' and x['kind']=='病院' for x in local)
            prefectures.append({'prefecture_code':r[1],'prefecture':local[0]['prefecture'],
              'statistics_hospitals':r[3],'master_active_hospitals':h,'hospital_difference':r[3]-h,
              'statistics_clinics':r[5],'master_active_clinics':c,'clinic_difference':r[5]-c,
              'clinic_difference_percent':round((r[5]-c)/r[5]*100,2)})
    assert len(prefectures)==47
    assert sum(r['statistics_clinics'] for r in prefectures)==stats_clinics
    assert sum(r['statistics_hospitals'] for r in prefectures)==stats_hospitals
    identity=json.loads(Path('docs/code-identity-audit.json').read_text())
    primary=json.loads(Path('docs/primary-row-audit.json').read_text())
    result={'statistics_file':path.name,'statistics_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
      'statistics_period':period,'master_reference_dates':sorted({r['reference_date'] for r in rows}),
      'verified_at':datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(),
      'statistics_clinics':stats_clinics,'master_active_clinics':len(clinics),
      'clinic_difference':stats_clinics-len(clinics),'clinic_difference_percent':round((stats_clinics-len(clinics))/stats_clinics*100,2),
      'statistics_hospitals':stats_hospitals,'master_active_hospitals':len(hospitals),'hospital_difference':stats_hospitals-len(hospitals),
      'clinic_status_counts':dict(Counter(r['status'] for r in rows if r['kind']=='診療所')),
      'all_selected_primary_rows_match':primary['all_match'],'code_identity_audit':identity,
      'cause':'未特定。取得済み主一覧の行落ち・名称や7桁での重複排除・表示だけの誤りは確認結果から否定。別配布物・対象範囲を施設コード単位で追加確認する。',
      'prefectures':prefectures}
    Path('docs/statistical-reconciliation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    with (out/'都道府県別照合.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['都道府県','統計病院数','DB現存病院数','病院差','統計一般診療所数','DB現存診療所数','診療所差','差率%'])
        for r in prefectures:w.writerow([r[k] for k in ['prefecture','statistics_hospitals','master_active_hospitals','hospital_difference','statistics_clinics','master_active_clinics','clinic_difference','clinic_difference_percent']])
    table=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [r['prefecture'],r['statistics_clinics'],r['master_active_clinics'],r['clinic_difference'],str(r['clinic_difference_percent'])+'%'])+'</tr>' for r in prefectures)
    doc=f'''<!doctype html><html lang="ja"><meta charset="utf-8"><title>医療施設動態調査との照合</title><style>body{{font-family:system-ui,sans-serif;color:#193d45;max-width:980px;margin:32px auto;padding:20px;line-height:1.8}}h1{{font-size:28px}}table{{border-collapse:collapse;width:100%;margin:20px 0}}th,td{{padding:8px;border:1px solid #cad7d7;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#edf4f2}}.notice{{background:#fff3df;border-left:5px solid #b77b22;padding:18px}}@media print{{body{{margin:0;font-size:11px}}h2{{break-after:avoid}}tr{{break-inside:avoid}}}}</style><body><h1>医療施設動態調査との件数差の照合</h1><p>比較元：添付 geppou0807.xlsx（{html.escape(period)}・月末現在）<br>当システム：2026年10月1日基準の厚生局医科主一覧・現存施設</p><p class="notice"><b>一般診療所の差は{stats_clinics-len(clinics):,}件（約{(stats_clinics-len(clinics))/stats_clinics*100:.1f}%）です。表示だけの問題ではなく、本番DBにもこの差があります。差の全要因をまだ特定できていないため、全国全診療所を網羅した完成版とは扱いません。</b></p><table><tr><th>施設区分</th><th>医療施設動態調査</th><th>DB・現存</th><th>差</th></tr><tr><td>病院</td><td>{stats_hospitals:,}</td><td>{len(hospitals):,}</td><td>{stats_hospitals-len(hospitals):,}</td></tr><tr><td>一般診療所</td><td>{stats_clinics:,}</td><td>{len(clinics):,}</td><td>{stats_clinics-len(clinics):,}</td></tr></table><h2>検証で確認できたこと</h2><ul><li>取得済み47都道府県の主一覧は、元Excelの施設行97,457行と解析97,457件が一致しました。</li><li>本番DBの10桁コード全件を読み出し、原資料側のコード集合と一致しました。欠落コードは0件です。</li><li>末尾7桁だけならユニーク数は90,582件になり、6,875件が失われます。実際のDBは97,457件で、衝突する施設も別々に保持しています。</li><li>同名の「佐藤病院」7施設、「林病院」5施設、「広瀬病院」5施設も、異なる10桁コードで全件保持しています。</li><li>現存診療所88,057件に休止695件・確認中750件を仮にすべて加えても89,502件で、差16,200件が残ります。</li><li>病床数不明の現存診療所80,732件も収録しており、空欄病床の施設を一律に捨てた問題ではありません。</li></ul><h2>原因として未確定のこと</h2><p>取得対象から漏れている別配布物、保険指定一覧と医療法上の一般診療所全数の対象範囲、基準日の違いを照合します。開設者別の集計差は掲載漏れの原因を証明しないため、原因の件数として計上しません。7月と10月の時点差だけで17,645件を説明する根拠もありません。</p><p>添付ファイルは集計表であり、個別施設の医療機関コードは含まれません。未収録施設の特定には、追加の施設単位の一次資料との照合が必要です。正式な10桁コードを照合基準とし、名称や7桁だけでの自動統合は行いません。</p><h2>都道府県別の一般診療所数</h2><p>正の差は医療施設動態調査の件数が当システムより多いことを表します。基準日は異なります。</p><table><thead><tr><th>県</th><th>統計</th><th>DB現存</th><th>差</th><th>差率</th></tr></thead><tbody>{table}</tbody></table></body></html>'''
    if Path('docs/insured-count-control.json').exists():
        control=json.loads(Path('docs/insured-count-control.json').read_text())
        section=f'''<h2>追加で確認した一次資料</h2><p>関東信越厚生局の令和7年度事業年報は、2026年3月末の保険指定医科診療所を{control['insured_medical_clinics']:,}施設と公表しています。当DBの同地域は2026年10月1日で{control['current_master_active_clinics']:,}件、動態調査は2026年7月末で{control['dynamic_statistics_clinics']:,}件です。公式の保険指定数自体にも差があることを確認しました。ただし、基準日が異なるため全国17,645件の原因を説明済みとはしません。</p><p>出典：<a href="{control['pdf_url']}">関東信越厚生局・事業年報 冊子75頁（PDF83頁）</a></p><p>別配布の医科側歯科併設一覧は、関東529件・東北215件が主一覧に全件収録済みでした。計744件を追加すると重複になります。歯科側の医科併設の扱いと、施設単位の対象範囲照合は未完了です。</p>'''
        doc=doc.replace('<h2>都道府県別の一般診療所数</h2>',section+'<h2>都道府県別の一般診療所数</h2>')
    (out/'件数差の分析.html').write_text(doc)
    Path('docs/statistical-reconciliation.md').write_text(f'# 医療施設動態調査との照合\n\n添付`{path.name}`の{period}を比較元にしました。一般診療所{stats_clinics:,}件に対し、当システムの現存診療所は{len(clinics):,}件、差{stats_clinics-len(clinics):,}件です。病院は{stats_hospitals:,}件対{len(hospitals):,}件、差{stats_hospitals-len(hospitals):,}件です。\n\n[県別の数値とコード監査](statistical-reconciliation.json)に記録しています。7桁重複・同名削除・取得済み主一覧内部の行落ち・表示だけの誤りは検証結果から否定しました。\n\n原因は未特定です。開設者別の集計差から掲載除外を断定しません。取得対象の別配布物と、医療法上の一般診療所全数・保険指定一覧の施設単位の照合を続けます。\n')
    with Path('docs/statistical-reconciliation.md').open('a') as f:
        f.write('\n[一次資料で確認した追加調査・残る接続制限](source-scope-investigation.md)\n\n[47都道府県の本番DB直接照合・比較表](prefecture-comparison.md)\n')
    print(json.dumps({k:result[k] for k in ['statistics_period','statistics_clinics','master_active_clinics','clinic_difference','hospital_difference']},ensure_ascii=False))

if __name__=='__main__':main()
