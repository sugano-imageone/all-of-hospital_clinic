"""Domain rules shared by ingestion, search and export. No database side effects."""
import re
import unicodedata
from collections import Counter

PREFECTURES = dict(enumerate([
    '北海道','青森県','岩手県','宮城県','秋田県','山形県','福島県','茨城県','栃木県',
    '群馬県','埼玉県','千葉県','東京都','神奈川県','新潟県','富山県','石川県','福井県',
    '山梨県','長野県','岐阜県','静岡県','愛知県','三重県','滋賀県','京都府','大阪府',
    '兵庫県','奈良県','和歌山県','鳥取県','島根県','岡山県','広島県','山口県','徳島県',
    '香川県','愛媛県','高知県','福岡県','佐賀県','長崎県','熊本県','大分県','宮崎県',
    '鹿児島県','沖縄県'], 1))
REGIONS = {
    '北海道厚生局': [1], '東北厚生局': list(range(2,8)),
    '関東信越厚生局': [8,9,10,11,12,13,14,15,19,20],
    '東海北陸厚生局': [16,17,21,22,23,24],
    '近畿厚生局': [18,25,26,27,28,29,30],
    '中国四国厚生局': [31,32,33,34,35], '四国厚生支局': [36,37,38,39],
    '九州厚生局': list(range(40,48)),
}
BEDS = ['general','medical','psychiatric','tuberculosis','infectious']
BED_LABELS = dict(zip(BEDS, ['一般','療養','精神','結核','感染症']))
STATUSES = ['現存','休止','廃止','辞退','取消','確認中']

def normalize(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip()

def institution_code(value, prefecture):
    text = normalize(value)
    # Parenthesized supplemental dental codes are never independent medical records.
    if any(x in text for x in '()（）'):
        raise ValueError('括弧付きの補足コードは医科施設として取り込めません')
    if re.fullmatch(r'\d{10}', text):
        code = text
    elif re.fullmatch(r'[\d,\s.・-]+', text) and len(re.sub(r'\D','',text)) == 7:
        code = f'{int(prefecture):02d}1' + re.sub(r'\D', '', text)
    else:
        raise ValueError(f'施設コード形式が不明: {text}')
    if code[:2] != f'{int(prefecture):02d}' or code[2] != '1':
        raise ValueError('都道府県または医科区分が一致しません')
    return code

def classify(total, explicit_kind=None, psychiatric=None):
    if total is not None and (not isinstance(total, int) or isinstance(total, bool) or total < 0):
        raise ValueError('病床数は非負整数またはNULLです')
    inferred = None if total is None else ('病院' if total >= 20 else '診療所')
    explicit = explicit_kind if explicit_kind in ['病院','診療所'] else None
    conflict = bool(explicit and inferred and explicit != inferred)
    kind = explicit or inferred
    clinic = None if kind != '診療所' or total is None else ('無床' if total == 0 else '有床')
    psych = '不明'
    if kind == '病院' and total and psychiatric is not None:
        if psychiatric > total or psychiatric < 0:
            conflict = True
        elif psychiatric == 0:
            psych = '精神病床なし'
        elif psychiatric == total:
            psych = '精神病床のみ'
        elif psychiatric / total >= .8:
            psych = '精神病床80%以上'
        else:
            psych = '混合型'
    return dict(kind=kind, clinic_beds=clinic, psychiatric_class=psych, needs_review=conflict)

def summarize(records):
    active = [r for r in records if r['status'] == '現存']
    counter = lambda key: dict(Counter(str(r.get(key) or '不明') for r in active))
    by_pref = {}
    for r in active:
        p = by_pref.setdefault(r['prefecture'], {'病院':0,'診療所':0,'不明':0})
        p[r.get('kind') or '不明'] += 1
    scales = Counter()
    for r in active:
        if r.get('kind') != '病院': continue
        n = r.get('total_beds')
        scales['不明' if n is None else ('20〜99床' if n < 100 else '100〜199床' if n < 200 else '200〜499床' if n < 500 else '500床以上')] += 1
    return {'total':len(active), 'kind':counter('kind'),
            'clinic':dict(Counter(r.get('clinic_beds') or '不明' for r in active if r.get('kind')=='診療所')),
            'psychiatric':dict(Counter(r.get('psychiatric_class','不明') for r in active if r.get('kind')=='病院')),
            'owner_type':counter('owner_type'), 'prefectures':by_pref, 'scales':dict(scales)}
