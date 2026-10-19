import os
from datetime import date
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote, urlsplit
from typing import Annotated
from fastapi import FastAPI, Request, Response, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from app.store import Supabase, Search, search_params
from app.domain import PREFECTURES, REGIONS, summarize
from app.exports import csv_bytes, workbook_bytes, full_csv_bytes

ROOT=Path(__file__).parent
app=FastAPI(title='全国医療機関データベース｜イメージワン',docs_url=None,redoc_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
templates=Jinja2Templates(directory=ROOT/'templates')

@app.middleware('http')
async def security_headers(request,call_next):
    if request.method in ['POST','PUT','PATCH','DELETE']:
        origin=request.headers.get('origin')
        # Browser forms and session cookies require same-origin requests.
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail':'別サイトからの操作は許可されていません'},status_code=403)
        if request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'別サイトからの操作は許可されていません'},status_code=403)
    response=await call_next(request)
    response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
      'X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
      'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
    return response

def authorized(request:Request):
    token=request.cookies.get('access_token')
    if not token: raise HTTPException(401,'ログインが必要です')
    db=Supabase(token)
    try:
        user=db.request('GET','/auth/v1/user').json()
        members=db.request('GET','/rest/v1/app_members',params={'user_id':'eq.'+user['id'],'select':'user_id,role,approved'}).json()
        if not members or not members[0]['approved']: raise HTTPException(403,'管理者の利用承認が必要です')
        yield db,{**user,**members[0]}
    finally: db.close()

Auth=Annotated[tuple,Depends(authorized)]

def cookies(response,tokens):
    secure=os.getenv('APP_SECURE_COOKIES','true').lower()!='false'
    response.set_cookie('access_token',tokens['access_token'],httponly=True,secure=secure,samesite='strict',max_age=tokens.get('expires_in',3600),path='/')
    response.set_cookie('refresh_token',tokens['refresh_token'],httponly=True,secure=secure,samesite='strict',max_age=7*86400,path='/auth')

@app.get('/',response_class=HTMLResponse)
def index(request:Request):
    return templates.TemplateResponse(request=request,name='index.html',context={'prefectures':PREFECTURES,'bureaus':REGIONS})

@app.get('/health')
def health():
    configured=bool(os.getenv('SUPABASE_URL') and os.getenv('SUPABASE_PUBLISHABLE_KEY'))
    return {'process':'ok','supabase_configured':configured,'database_verified':False}

@app.post('/auth/login')
def login(email:Annotated[str,Form(max_length=254)],password:Annotated[str,Form(max_length=256)]):
    db=Supabase()
    try: tokens=db.request('POST','/auth/v1/token?grant_type=password',json={'email':email,'password':password}).json()
    finally: db.close()
    verified=Supabase(tokens['access_token'])
    try:
        uid=tokens['user']['id']
        membership=verified.request('GET','/rest/v1/app_members',params={'user_id':'eq.'+uid,'select':'role,approved'}).json()
        if not membership or not membership[0]['approved']:raise HTTPException(403,'管理者の利用承認が必要です')
        verified.rpc('record_login',{})
    finally:verified.close()
    response=JSONResponse({'ok':True});cookies(response,tokens);return response

@app.post('/auth/refresh')
def refresh(request:Request):
    token=request.cookies.get('refresh_token')
    if not token: raise HTTPException(401,'再ログインしてください')
    db=Supabase()
    try: tokens=db.request('POST','/auth/v1/token?grant_type=refresh_token',json={'refresh_token':token}).json()
    finally: db.close()
    response=JSONResponse({'ok':True});cookies(response,tokens);return response

@app.post('/auth/logout')
def logout(request:Request):
    token=request.cookies.get('access_token')
    if token:
        db=Supabase(token)
        try: db.request('POST','/auth/v1/logout')
        finally: db.close()
    response=JSONResponse({'ok':True});response.delete_cookie('access_token',path='/');response.delete_cookie('refresh_token',path='/auth');return response

@app.get('/api/me')
def me(auth:Auth):
    _,user=auth;return {'email':user.get('email'),'role':user['role'],'user_id':user['id']}

@app.post('/api/search')
def search(query:Search,auth:Auth):
    db,_=auth
    # Fetch only public output fields; raw parser rows are not needed in result pages.
    params=search_params(query)+[('select','*'),('offset',str((query.page-1)*50)),('limit','50')]
    r=db.request('GET','/rest/v1/medical_institutions',params=params,headers={'Prefer':'count=exact'})
    total=r.headers.get('content-range','*/0').split('/')[-1]
    if total=='*': raise HTTPException(502,'正確な件数を取得できませんでした')
    rows=r.json()
    for row in rows: row.pop('raw',None)
    db.audit('search',{'filters':query.model_dump(),'count':int(total)})
    return {'items':rows,'total':int(total),'page':query.page,'page_size':50}

def monthly_counts(changes):
    months=defaultdict(Counter)
    for row in changes:
        month=row['event_date'][:7];event=row['event_type']
        if event in ['baseline','reclassified']: continue
        months[month][{'new_designation':'新規指定総数','changed':'変更数',
          'missing_candidate':'廃止候補（未確定）','closure_confirmed':'廃止等確定数'}[event]]+=1
        if event=='new_designation' and row.get('opening_classification')=='純新規開業': months[month]['純新規開業数']+=1
    return dict(months)

@app.get('/api/dashboard')
def dashboard(auth:Auth):
    db,_=auth
    fields='code,prefecture,kind,status,clinic_beds,total_beds,psychiatric_class,owner_type,needs_review'
    records=db.all('medical_institutions',[('select',fields),('order','code.asc')])
    sources=db.rpc('source_metadata',{})
    monthly=db.rpc('monthly_change_counts',{})
    collected=sorted({s['prefecture'] for s in sources if s['status']=='success'})
    db.audit('dashboard')
    return {**summarize(records),'collected':collected,'missing':[p for p in PREFECTURES.values() if p not in collected],
            'status_counts':dict(Counter(r['status'] for r in records)),
            'review_by_prefecture':dict(Counter(r['prefecture'] for r in records if r['status']=='確認中')),
            'quality_review_count':sum(bool(r.get('needs_review')) for r in records),
            'sources':sources,'monthly':monthly,
            'note':'集計は現存のみ。病床数の未記載は0床として扱います。対象は保険医療機関の通常医科主一覧です。保険指定のない施設は含みません。新規指定は観測・根拠のある差分のみ。初回取込は月次新規に数えません。'}

@app.post('/api/export/{format}')
def export(format:str,query:Search,auth:Auth):
    db,user=auth
    if format=='full':
        if user['role'] not in ['admin','master']: raise HTTPException(403,'管理者権限が必要です')
        records=db.all('medical_institutions',[('select','*'),('order','code.asc,id.asc')])
        db.audit('export_full',{'count':len(records),'scope':'all_institutions_all_statuses_all_columns'})
        return Response(full_csv_bytes(records),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote('全医療機関データ.csv')})
    if format not in ['csv','xlsx','master']: raise HTTPException(404)
    if format=='master':
        records=db.all('medical_institutions',[('status','eq.現存'),('order','code.asc')])
    else: records=db.all('medical_institutions',search_params(query))
    db.audit('export_'+format,{'filters':query.model_dump(),'count':len(records)})
    if format=='csv': payload=csv_bytes(records);mime='text/csv; charset=utf-8';name='検索結果.csv'
    else:
        sources=db.rpc('source_metadata',{})
        monthly=db.rpc('monthly_change_counts',{})
        payload=workbook_bytes(records,sources,'現存のみ（全取得県）' if format=='master' else '検索条件に一致。休止を含む' if query.include_suspended else '検索条件に一致。現存のみ',monthly)
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';name='全国医療機関マスター.xlsx' if format=='master' else '検索結果.xlsx'
    return Response(payload,media_type=mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name)})

class MemberUpdate(BaseModel):
    user_id:str=Field(pattern=r'^[a-fA-F0-9-]{36}$')
    role:str=Field(pattern=r'^(user|admin)$')
    approved:bool

class LoginHistoryFilters(BaseModel):
    email:str=Field(default='',max_length=254)
    from_date:date|None=None
    to_date:date|None=None
    page:int=Field(default=1,ge=1,le=100000)

@app.post('/api/admin/logins')
def login_history(data:LoginHistoryFilters,auth:Auth):
    db,user=auth
    if user['role'] not in ['admin','master']:raise HTTPException(403,'管理者権限が必要です')
    if data.from_date and data.to_date and data.from_date>data.to_date:raise HTTPException(422,'期間の開始日が終了日を超えています')
    return db.rpc('viewer_login_history',{'p_email':data.email,'p_from':data.from_date.isoformat() if data.from_date else None,'p_to':data.to_date.isoformat() if data.to_date else None,'p_page':data.page})

@app.get('/api/admin/members')
def members(auth:Auth):
    db,user=auth
    if user['role'] not in ['admin','master']: raise HTTPException(403,'管理者権限が必要です')
    db.audit('members_read')
    return db.all('app_members',[('order','created_at.asc,user_id.asc')])

@app.post('/api/admin/members')
def update_member(data:MemberUpdate,auth:Auth):
    db,user=auth
    if user['role'] not in ['admin','master']: raise HTTPException(403,'管理者権限が必要です')
    db.rpc('set_member',{'p_user':data.user_id,'p_role':data.role,'p_approved':data.approved})
    return {'ok':True}
