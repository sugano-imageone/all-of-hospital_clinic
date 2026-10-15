import csv
from io import StringIO, BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from app.domain import summarize, PREFECTURES

COLUMNS = [('code','医療機関コード'),('name','医療機関名'),('prefecture','都道府県'),
 ('municipality','市区町村'),('address','住所'),('phone','電話番号'),('owner','開設者'),
 ('owner_type','開設主体'),('kind','施設区分'),('total_beds','総病床数'),('general_beds','一般病床'),
 ('medical_beds','療養病床'),('psychiatric_beds','精神病床'),('tuberculosis_beds','結核病床'),
 ('infectious_beds','感染症病床'),('psychiatric_class','精神科病院分類'),('status','状態'),
 ('reference_date','公表基準日'),('file_created_date','ファイル作成日'),('retrieved_at','取得日'),
 ('bureau','厚生局'),('source_file','元ファイル'),('clinic_beds','有床・無床')]

def safe(value):
    # Prevent spreadsheet formula injection from official source strings.
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r')): return "'"+value
    return value

def csv_bytes(records):
    output=StringIO(newline=''); writer=csv.writer(output)
    writer.writerow([label for _,label in COLUMNS])
    writer.writerows([[safe(r.get(key)) for key,_ in COLUMNS] for r in records])
    return ('\ufeff'+output.getvalue()).encode('utf-8')

def workbook_bytes(records,sources,scope='現存のみ', changes=None):
    records=[r for r in records if r.get('status') in ['現存','休止']]
    book=Workbook();book.remove(book.active)
    form=book.create_sheet('検索・抽出フォーム');dash=book.create_sheet('集計ダッシュボード')
    hospital=book.create_sheet('病院');clinic=book.create_sheet('診療所');psych=book.create_sheet('精神科病院')
    meta=book.create_sheet('取得元・更新情報')
    form.append(['全国医療機関マスター｜イメージワン版'])
    form.append(['対象範囲',scope+'。このブックに含まれるデータのみ検索。全国収集済みを意味しません。'])
    form.append(['地域（空欄で全て）',''])
    form.append(['施設区分','病院'])
    form.append(['病床種別','総病床数'])
    form.append(['病床数下限',20]);form.append(['病床数上限',200])
    form.append(['開設者（部分一致）',''])
    form.append(['該当件数','=IFERROR(ROWS(A13#),0)'])
    form.append(['Microsoft 365用。数式はExcelで再計算。空欄の病床数は範囲検索から除外。'])
    form.append([]);form.append([label for _,label in COLUMNS])
    def data_sheet(sheet,items):
        sheet.append([label for _,label in COLUMNS])
        for r in items: sheet.append([safe(r.get(k)) for k,_ in COLUMNS])
        sheet.auto_filter.ref=sheet.dimensions; sheet.freeze_panes='C2'
        sheet.column_dimensions['A'].width=18
        for row in sheet.iter_rows(min_row=2,max_col=1): row[0].number_format='@'
    h=[r for r in records if r.get('kind')=='病院'];c=[r for r in records if r.get('kind')=='診療所']
    data_sheet(hospital,h);data_sheet(clinic,c)
    data_sheet(psych,[r for r in h if (r.get('psychiatric_beds') or 0)>0])
    # Dynamic arrays use native Microsoft 365 functions; no macros or external links.
    # Empty source sheets use one blank sentinel row, filtered out by nonempty code.
    hr=max(len(h)+1,2);cr=max(len(c)+1,2)
    formula=(f'=_xlfn.LET(d,_xlfn.VSTACK(IF(病院!A2:W{hr}="","",病院!A2:W{hr}),IF(診療所!A2:W{cr}="","",診療所!A2:W{cr})),'
             'b,_xlfn.CHOOSECOLS(d,MATCH(B5,病院!A1:W1,0)),'
             'ok,(_xlfn.CHOOSECOLS(d,1)<>"")*IF(B3="",1,_xlfn.CHOOSECOLS(d,3)=B3)'
             '*IF(B4="全て",1,_xlfn.CHOOSECOLS(d,9)=B4)'
             '*IF(B6="",1,(b<>"")*(b>=B6))*IF(B7="",1,(b<>"")*(b<=B7))'
             '*IF(B8="",1,ISNUMBER(SEARCH(B8,_xlfn.CHOOSECOLS(d,7)))),'
             '_xlfn._xlws.FILTER(d,ok,"該当なし"))')
    form['A13']=formula
    form['B9']='=IF(A13="該当なし",0,IFERROR(ROWS(_xlfn.ANCHORARRAY(A13)),0))'
    for cell,options in [('B3',','.join(PREFECTURES.values())),('B4','全て,病院,診療所'),
                         ('B5','総病床数,一般病床,療養病床,精神病床,結核病床,感染症病床')]:
        if cell=='B3':
            # Inline validation strings have a 255-character limit.
            for i,name in PREFECTURES.items(): meta.cell(i+3,12,name)
            from openpyxl.workbook.defined_name import DefinedName
            book.defined_names.add(DefinedName('都道府県候補',attr_text="'取得元・更新情報'!$L$4:$L$50"))
            dv=DataValidation(type='list',formula1='都道府県候補',allow_blank=True)
        else: dv=DataValidation(type='list',formula1='"'+options+'"',allow_blank=True)
        form.add_data_validation(dv);dv.add(form[cell])
    summary=summarize(records)
    dash.append(['取得済みデータの集計（現存のみ）','件数'])
    for group in ['kind','clinic','psychiatric','owner_type','scales']:
        for label,n in summary[group].items(): dash.append([group+' / '+label,n])
    for pref,counts in summary['prefectures'].items():
        for kind,n in counts.items(): dash.append([pref+' / '+kind,n])
    if changes:
        for month,counts in changes.items():
            for key,n in counts.items(): dash.append([month+' / '+key,n])
    meta.append(['対象範囲',scope]);meta.append(['未収集県は全国集計に含まれません。精神科病院は病院の部分集合。'])
    meta.append(['都道府県','厚生局','元ファイル','公表基準日','作成日','取得日','SHA-256','取込状況','件数'])
    for s in sources:
        meta.append([safe(s.get(k)) for k in ['prefecture','bureau','filename','reference_date',
                     'file_created_date','retrieved_at','sha256','status','record_count']])
    for sheet in book:
        for cell in sheet[1]: cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='113F4A')
        sheet.freeze_panes=sheet.freeze_panes or 'A2'
        for col in ['B','E','G']: sheet.column_dimensions[col].width=32
    form.column_dimensions['A'].width=38;form.column_dimensions['B'].width=60
    output=BytesIO();book.save(output);return output.getvalue()
