import os
import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from typing import Literal
from app.domain import BEDS, PREFECTURES

class Search(BaseModel):
    kind: Literal['病院','診療所'] = '病院'
    prefectures: list[str] = Field(default_factory=list)
    bureau: str = ''
    municipality: str = Field(default='',max_length=100)
    name: str = Field(default='',max_length=150)
    code: str = Field(default='',pattern=r'^\d{0,10}$')
    owner: str = Field(default='',max_length=150)
    owner_type: str = Field(default='',max_length=100)
    clinic_beds: Literal['','有床','無床'] = ''
    bed_type: Literal['total','general','medical','psychiatric','tuberculosis','infectious'] = 'total'
    minimum: int | None = Field(default=None,ge=0,le=100000)
    maximum: int | None = Field(default=None,ge=0,le=100000)
    psychiatric_class: Literal['','不明','精神病床なし','精神病床のみ','精神病床80%以上','混合型'] = ''
    notifications_enabled: bool = False
    notification_item: str = Field(default='',max_length=500)
    notification_status: Literal['filed','not_filed'] = 'filed'
    include_suspended: bool = False
    sort: Literal['code','name','prefecture','total_beds','reference_date'] = 'code'
    direction: Literal['asc','desc'] = 'asc'
    page: int = Field(default=1,ge=1,le=100000)
    @model_validator(mode='after')
    def validate_range(self):
        if self.minimum is not None and self.maximum is not None and self.minimum>self.maximum:
            raise ValueError('病床数の下限が上限を超えています')
        if any(p not in PREFECTURES.values() for p in self.prefectures):
            raise ValueError('不明な都道府県です')
        if self.notifications_enabled and not self.notification_item:
            raise ValueError('正式な届出項目を選択してください')
        if not self.notifications_enabled:
            self.notification_item='';self.notification_status='filed'
        return self

def settings():
    url = os.getenv('SUPABASE_URL','').rstrip('/')
    key = os.getenv('SUPABASE_PUBLISHABLE_KEY','')
    if not url.startswith('https://') or not key:
        raise HTTPException(503,'Supabase接続設定が未完了です')
    return url,key

class Supabase:
    def __init__(self,token=None,service=False):
        self.url,self.key = settings()
        if service:
            self.key = os.getenv('SUPABASE_SECRET_KEY','')
            if not self.key: raise RuntimeError('SUPABASE_SECRET_KEY が必要です')
            if self.key.startswith('eyJ'): token = self.key
        self.client = httpx.Client(base_url=self.url,headers={
            'apikey':self.key, **({'Authorization':f'Bearer {token}'} if token else {})}, timeout=60)
    def close(self): self.client.close()
    def request(self,method,path,**kwargs):
        try: r=self.client.request(method,path,**kwargs)
        except httpx.HTTPError: raise HTTPException(503,'Supabaseへの接続に失敗しました') from None
        if r.is_error:
            # Never include upstream response bodies, tokens or raw DB errors in browser/log output.
            raise HTTPException(r.status_code if r.status_code in [401,403] else 502,'認証またはデータ処理に失敗しました')
        return r
    def rpc(self,name,payload,*,timeout=None):
        response=self.request('POST',f'/rest/v1/rpc/{name}',json=payload,
                              **({'timeout':timeout} if timeout is not None else {}))
        return response.json() if response.content else None
    def all(self,table,params=None):
        result=[]
        for offset in range(0,200000,1000):
            data=self.request('GET',f'/rest/v1/{table}',params=[*(params or []),('offset',str(offset)),('limit','1000')]).json()
            result.extend(data)
            if len(data)<1000: return result
        raise HTTPException(413,'出力件数が上限を超えました。条件を絞ってください')
    def audit(self,action,details=None):
        self.rpc('record_access',{'p_action':action,'p_details':details or {}})

def literal(text):
    # PostgREST quoted scalar, including filter punctuation; wildcards are literal in text searches.
    return '"'+text.replace('\\','\\\\').replace('"','\\"')+'"'

def search_params(search):
    q=[('kind','eq.'+search.kind),('status','in.(現存,休止)' if search.include_suspended else 'eq.現存'),
       ('order',f'{search.sort}.{search.direction}.nullslast,code.asc')]
    for key in ['bureau','owner_type','clinic_beds','psychiatric_class']:
        value=getattr(search,key)
        if value: q.append((key,'eq.'+value))
    if search.prefectures: q.append(('prefecture','in.('+','.join(literal(p) for p in search.prefectures)+')'))
    for key in ['municipality','name','owner']:
        value=getattr(search,key)
        if value:
            value=value.replace('\\','\\\\').replace('%',r'\%').replace('_',r'\_').replace('*',r'\*')
            q.append((key,'ilike.%'+value+'%'))
    if search.code: q.append(('code','eq.'+search.code))
    bed=search.bed_type+'_beds'
    if search.minimum is not None: q.append((bed,'gte.'+str(search.minimum)))
    if search.maximum is not None: q.append((bed,'lte.'+str(search.maximum)))
    # Hospital / clinic classification never depends on the selected bed category.
    if search.notifications_enabled:
        q += [('p_item',search.notification_item),('p_status','filed' if search.notification_status=='filed' else 'unfiled')]
    return q
