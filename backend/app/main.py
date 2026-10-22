import os, json, hashlib, secrets, hmac, csv, io, calendar, time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Header
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete, update, Integer
from sqlalchemy.exc import IntegrityError
from .models import Base, Barcode, BankImport, BankTransaction, BankImportRow, MenuProduct, Person, Product, Merchant, Purchase, Item, Payment, Debt, Setting, Rule, Session, RequestKey, SessionLocal
from .schemas import PurchaseIn, ProductIn, PaymentIn, SettlementIn, SettingsIn, RuleIn

TZ=ZoneInfo('Asia/Seoul')
def now(): return datetime.now(timezone.utc).isoformat()
def dump(row): return {c.name:getattr(row,c.name) for c in row.__table__.columns}
def fail(message, status=400): raise HTTPException(status, message)
@asynccontextmanager
async def lifespan(app):
    if len(os.getenv('APP_PASSWORD',''))<12: raise RuntimeError('APP_PASSWORD must contain at least 12 characters')
    yield
app=FastAPI(title='SpendLog',version='1.0.0',lifespan=lifespan)

def db():
    with SessionLocal() as s:
        try:
            # SQLite: serialize writers BEFORE reading, avoiding settlement/edit races.
            if s.bind.dialect.name=='sqlite': s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            elif s.bind.dialect.name=='postgresql': s.connection().exec_driver_sql('SELECT pg_advisory_xact_lock(73481022)')
            yield s
            s.commit()
        except Exception:
            s.rollback(); raise

def auth(request:Request,s=Depends(db, scope="function")):
    token=request.cookies.get('spendlog_session','')
    session=s.get(Session,hashlib.sha256(token.encode()).hexdigest()) if token else None
    if not session or session.expires_at < now(): fail('Войдите в SpendLog',401)
    return True

@app.middleware('http')
async def protect(request,call_next):
    if request.method in ('POST','PUT','DELETE','PATCH'):
        if request.headers.get('x-spendlog')!='1': return JSONResponse({'detail':'Missing CSRF header'},403)
        if int(request.headers.get('content-length','0') or 0)>10_000_000: return JSONResponse({'detail':'Файл больше 10 MB'},413)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api') else 'no-cache'
    response.headers['X-Content-Type-Options']='nosniff'
    return response

@app.exception_handler(IntegrityError)
async def integrity(request,exc): return JSONResponse({'detail':'Конфликт данных. Обновите страницу и повторите.'},409)
attempts=defaultdict(deque)
@app.post('/api/login')
async def login(request:Request,response:Response,s=Depends(db, scope="function")):
    # Single process deployment; bounded in-memory throttling, shared limit avoids spoofed XFF.
    key='login'; q=attempts[key]; t=time.monotonic()
    while q and q[0]<t-300:q.popleft()
    if len(q)>=15: fail('Слишком много попыток. Подождите 5 минут.',429)
    body=await request.json()
    if not hmac.compare_digest(str(body.get('password','')).encode(),os.getenv('APP_PASSWORD','').encode()):
        q.append(t); fail('Неверный пароль',401)
    token=secrets.token_urlsafe(32)
    s.execute(delete(Session).where(Session.expires_at<now()))
    s.add(Session(token_hash=hashlib.sha256(token.encode()).hexdigest(),expires_at=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()))
    response.set_cookie('spendlog_session',token,httponly=True,secure=os.getenv('COOKIE_SECURE','true').lower()=='true',samesite='strict',max_age=2592000,path='/')
    return {'ok':True}
@app.post('/api/logout',dependencies=[Depends(auth)])
def logout(request:Request,response:Response,s=Depends(db, scope="function")):
    s.execute(delete(Session).where(Session.token_hash==hashlib.sha256(request.cookies.get('spendlog_session','').encode()).hexdigest()))
    response.delete_cookie('spendlog_session'); return {'ok':True}
@app.get('/api/health')
def health(): return {'ok':True}

# Mutation keys survive retries and backup/restore. Same key + changed request is rejected.
def replay(s,key,payload):
    if not key or len(key)>100: fail('Требуется Idempotency-Key')
    fp=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    row=s.get(RequestKey,key)
    if row:
        if row.fingerprint!=fp: fail('Ключ операции уже использован',409)
        return fp,json.loads(row.response)
    return fp,None
def remember(s,key,fp,result):
    s.add(RequestKey(key=key,fingerprint=fp,response=json.dumps(result))); return result

def settings(s):
    row=s.get(Setting,'preferences')
    return SettingsIn.model_validate_json(row.value) if row else SettingsIn()
@app.get('/api/settings',dependencies=[Depends(auth)])
def get_settings(s=Depends(db, scope="function")): return settings(s)
@app.put('/api/settings',dependencies=[Depends(auth)])
def put_settings(data:SettingsIn,s=Depends(db, scope="function")):
    s.merge(Setting(key='preferences',value=data.model_dump_json())); return data
@app.get('/api/rules',dependencies=[Depends(auth)])
def rules(s=Depends(db, scope="function")): return [dump(r) for r in s.scalars(select(Rule).order_by(Rule.id))]
@app.post('/api/rules',dependencies=[Depends(auth)])
def add_rule(data:RuleIn,s=Depends(db, scope="function")):
    r=Rule(**data.model_dump());s.add(r);s.flush();return dump(r)
@app.delete('/api/rules/{id}',dependencies=[Depends(auth)])
def delete_rule(id:str,s=Depends(db, scope="function")):
    s.execute(delete(Rule).where(Rule.id==id));return {'ok':True}
@app.get('/api/merchants',dependencies=[Depends(auth)])
def merchants(s=Depends(db, scope="function")):return [dump(m) for m in s.scalars(select(Merchant).order_by(Merchant.name))]
def ensure_barcode(s,data):
    if not s.get(Barcode,data.barcode):s.add(Barcode(**data.model_dump()))

@app.get('/api/barcodes/{barcode}',dependencies=[Depends(auth)])
def barcode_lookup(barcode:str,s=Depends(db, scope="function")):
    row=s.get(Barcode,barcode)
    return dump(row) if row else None

@app.get('/api/products',dependencies=[Depends(auth)])
def products(q:str='',s=Depends(db, scope="function")):
    rows=s.scalars(select(Product).order_by(Product.name)).all()
    return [product_view(s,p) for p in rows if q.lower() in (p.name+' '+p.barcode).lower()]
def product_view(s,p):
    latest=s.execute(select(Item.unit_price,Merchant.name).join(Purchase,Item.purchase_id==Purchase.id).join(Merchant).where(Item.product_id==p.id).order_by(Purchase.occurred_at.desc(),Purchase.created_at.desc()).limit(1)).first()
    return {**dump(p),'category':category_resolver(s)(p.category),'last_price':latest[0] if latest else None,'last_merchant':latest[1] if latest else None}
@app.post('/api/products',dependencies=[Depends(auth)])
def add_product(data:ProductIn,s=Depends(db, scope="function")):
    existing=s.scalar(select(Product).where(Product.barcode==data.barcode))
    if existing:
        if any(getattr(existing,k)!=v for k,v in data.model_dump().items()):fail('Штрихкод уже есть в личном каталоге. Найдите товар заново.',409)
        return product_view(s,existing)
    ensure_barcode(s,data)
    p=Product(**data.model_dump());s.add(p);s.flush();return product_view(s,p)
@app.put('/api/products/{id}',dependencies=[Depends(auth)])
def edit_product(id:str,data:ProductIn,s=Depends(db, scope="function")):
    p=s.get(Product,id)
    if not p:fail('Товар не найден',404)
    ensure_barcode(s,data)
    for k,v in data.model_dump().items():setattr(p,k,v)
    s.flush();return product_view(s,p)
@app.get('/api/products/{id}/prices',dependencies=[Depends(auth)])
def prices(id:str,s=Depends(db, scope="function")):
    return [dict(r._mapping) for r in s.execute(select(Purchase.occurred_at,Item.unit_price,Merchant.name.label('merchant')).join(Item,Item.purchase_id==Purchase.id).join(Merchant).where(Item.product_id==id).order_by(Purchase.occurred_at))]

def merchant(s,name):
    m=s.scalar(select(Merchant).where(Merchant.name==name))
    if not m:m=Merchant(name=name);s.add(m);s.flush()
    return m

def allocate(amount,weights):
    total=sum(weights)
    if total==0:return [0]*len(weights)
    parts=[amount*w//total for w in weights]
    order=sorted(range(len(weights)),key=lambda i: (-(amount*weights[i]%total),i))
    for i in order[:amount-sum(parts)]:parts[i]+=1
    return parts

def write_purchase(s,data,p=None,payment=None):
    merchant_id=merchant(s,data.merchant).id
    if p is None:
        p=Purchase(created_at=now(),version=1);s.add(p)
    p.merchant_id=merchant_id
    p.occurred_at=data.occurred_at.isoformat();p.note=data.note;p.total=data.receipt_total;p.own_amount=data.own_amount
    s.flush()
    own=allocate(data.own_amount,[i.unit_price*i.quantity for i in data.items])
    for item,share in zip(data.items,own):
        if item.product_id and not s.get(Product,item.product_id):fail('Товар не найден',422)
        if item.menu_product_id:
            menu=s.get(MenuProduct,item.menu_product_id)
            if not menu or menu.merchant_id!=merchant_id:fail('Блюдо не относится к выбранному заведению',422)
        s.add(Item(purchase_id=p.id,own_amount=share,**item.model_dump()))
    if p.total:
        if payment:payment.note=p.note;payment.version+=1
        else:s.add(Payment(purchase_id=p.id,amount=-p.total,kind='purchase',occurred_at=p.occurred_at,note=p.note))
    if p.total>p.own_amount:s.add(Debt(purchase_id=p.id,person=data.person,person_id=ensure_person(s,data.person).id,direction='receivable',kind=data.debt_kind,principal=p.total-p.own_amount,occurred_at=p.occurred_at))
    s.flush();return purchase_view(s,p)

def purchase_view(s,p):
    debt=s.scalar(select(Debt).where(Debt.purchase_id==p.id))
    return {**dump(p),'merchant':s.get(Merchant,p.merchant_id).name,'items':[dump(i) for i in s.scalars(select(Item).where(Item.purchase_id==p.id).order_by(Item.id))],'debt':debt_view(s,debt) if debt else None}
@app.get('/api/purchases',dependencies=[Depends(auth)])
def purchases(s=Depends(db, scope="function")):return [purchase_view(s,p) for p in s.scalars(select(Purchase).order_by(Purchase.occurred_at.desc(),Purchase.created_at.desc()))]
@app.post('/api/purchases',dependencies=[Depends(auth)])
def create_purchase(data:PurchaseIn,s=Depends(db, scope="function"),idempotency_key:str=Header()):
    fp,old=replay(s,idempotency_key,{'purchase':data.model_dump(mode='json')})
    if old:return old
    return remember(s,idempotency_key,fp,write_purchase(s,data))
def lock(s,cls,id):
    r=s.scalar(select(cls).where(cls.id==id).with_for_update())
    if not r:fail('Запись не найдена',404)
    return r
def check_version(row,version):
    if row.version!=version:fail('Запись уже изменена. Обновите страницу.',409)
def clear_purchase(s,p,allow_linked=False):
    if not allow_linked and s.scalar(select(BankTransaction.id).join(Payment,BankTransaction.payment_id==Payment.id).where(Payment.purchase_id==p.id)):
        fail('Сначала отмените связь с банковской операцией во вкладке банка',409)
    d=s.scalar(select(Debt).where(Debt.purchase_id==p.id))
    if d and s.scalar(select(Payment.id).where(Payment.debt_id==d.id,Payment.kind=='settlement')):
        fail('Сначала удалите возвраты по этой покупке',409)
    if not allow_linked:s.execute(delete(Payment).where(Payment.purchase_id==p.id))
    s.execute(delete(Debt).where(Debt.purchase_id==p.id))
    s.execute(delete(Item).where(Item.purchase_id==p.id))
@app.put('/api/purchases/{id}',dependencies=[Depends(auth)])
def edit_purchase(id:str,data:PurchaseIn,s=Depends(db, scope="function")):
    p=lock(s,Purchase,id);check_version(p,data.version)
    tx=s.scalar(select(BankTransaction).join(Payment,BankTransaction.payment_id==Payment.id).where(Payment.purchase_id==p.id))
    payment=s.get(Payment,tx.payment_id) if tx else None
    if tx and data.receipt_total!=-tx.amount:fail('Итог должен совпадать с банковским списанием',422)
    clear_purchase(s,p,allow_linked=bool(tx));p.version+=1
    if tx:tx.version+=1
    return write_purchase(s,data,p,payment)
@app.delete('/api/purchases/{id}',dependencies=[Depends(auth)])
def delete_purchase(id:str,version:int,s=Depends(db, scope="function")):
    p=lock(s,Purchase,id);check_version(p,version);clear_purchase(s,p);s.delete(p);return {'ok':True}

def debt_view(s,d):
    settled=sum(abs(p.amount) for p in s.scalars(select(Payment).where(Payment.debt_id==d.id,Payment.kind=='settlement')))
    return {**dump(d),'person':person_display(s,d),'settled':settled,'remaining':d.principal-settled}
@app.get('/api/debts',dependencies=[Depends(auth)])
def debts(s=Depends(db, scope="function")):return [debt_view(s,d) for d in s.scalars(select(Debt).order_by(Debt.occurred_at.desc()))]
@app.post('/api/debts/{id}/settle',dependencies=[Depends(auth)])
def settle(id:str,data:SettlementIn,s=Depends(db, scope="function"),idempotency_key:str=Header()):
    fp,old=replay(s,idempotency_key,{'debt':id,**data.model_dump(mode='json')})
    if old:return old
    d=lock(s,Debt,id)
    if data.occurred_at.isoformat()<d.occurred_at:fail('Возврат не может быть раньше долга',422)
    if data.amount>debt_view(s,d)['remaining']:fail('Сумма больше остатка долга',422)
    p=Payment(debt_id=id,kind='settlement',amount=data.amount*(1 if d.direction=='receivable' else -1),occurred_at=data.occurred_at.isoformat(),note=data.note)
    s.add(p);s.flush();return remember(s,idempotency_key,fp,dump(p))
def write_payment(s,data,p=None):
    if p is None:p=Payment(version=1);s.add(p)
    p.kind=data.kind;p.amount=data.amount*(-1 if data.kind=='loan_given' else 1);p.occurred_at=data.occurred_at.isoformat();p.note=data.note
    if data.kind!='income':
        d=Debt(person=data.person,person_id=ensure_person(s,data.person).id,direction='receivable' if data.kind=='loan_given' else 'payable',kind='loan',principal=data.amount,occurred_at=p.occurred_at)
        s.add(d);s.flush();p.debt_id=d.id
    s.flush();return dump(p)
@app.post('/api/payments',dependencies=[Depends(auth)])
def create_payment(data:PaymentIn,s=Depends(db, scope="function"),idempotency_key:str=Header()):
    fp,old=replay(s,idempotency_key,{'payment':data.model_dump(mode='json')})
    if old:return old
    return remember(s,idempotency_key,fp,write_payment(s,data))
@app.get('/api/payments',dependencies=[Depends(auth)])
def payments(s=Depends(db, scope="function")):
    return [{**dump(p),'bank_transaction_id':s.scalar(select(BankTransaction.id).where(BankTransaction.payment_id==p.id)),'person':person_display(s,s.get(Debt,p.debt_id)) if p.debt_id else ''} for p in s.scalars(select(Payment).order_by(Payment.occurred_at.desc()))]
def detach_payment(s,p):
    if s.scalar(select(BankTransaction.id).where(BankTransaction.payment_id==p.id)):
        fail('Банковские операции изменяются во вкладке банка',409)
    if p.purchase_id:fail('Редактируйте связанную покупку',409)
    if p.debt_id and p.kind!='settlement':
        d=lock(s,Debt,p.debt_id)
        if s.scalar(select(Payment.id).where(Payment.debt_id==d.id,Payment.kind=='settlement')):fail('Сначала удалите возвраты по этому займу',409)
        p.debt_id=None;s.flush();s.delete(d)
@app.put('/api/payments/{id}',dependencies=[Depends(auth)])
def edit_payment(id:str,data:PaymentIn,s=Depends(db, scope="function")):
    p=lock(s,Payment,id);check_version(p,data.version)
    if p.kind=='settlement':fail('Удалите ошибочный возврат и внесите заново',409)
    detach_payment(s,p);p.version+=1;return write_payment(s,data,p)
@app.put('/api/settlements/{id}',dependencies=[Depends(auth)])
def edit_settlement(id:str,data:SettlementIn,version:int,s=Depends(db, scope="function")):
    p=lock(s,Payment,id);check_version(p,version)
    if p.kind!='settlement':fail('Это не возврат',422)
    d=lock(s,Debt,p.debt_id)
    if data.amount>debt_view(s,d)['remaining']+abs(p.amount):fail('Сумма больше долга',422)
    if data.occurred_at.isoformat()<d.occurred_at:fail('Возврат раньше долга',422)
    p.amount=data.amount*(1 if d.direction=='receivable' else -1);p.occurred_at=data.occurred_at.isoformat();p.note=data.note;p.version+=1
    return dump(p)
@app.delete('/api/payments/{id}',dependencies=[Depends(auth)])
def delete_payment(id:str,version:int,s=Depends(db, scope="function")):
    p=lock(s,Payment,id);check_version(p,version);detach_payment(s,p);s.delete(p);return {'ok':True}

def period(anchor,start_day):
    start=anchor.replace(day=start_day)
    if anchor.day<start_day:
        start=(start.replace(day=1)-timedelta(days=1)).replace(day=start_day)
    end=(start.replace(day=28)+timedelta(days=4)).replace(day=start_day)
    return start,end
@app.get('/api/dashboard',dependencies=[Depends(auth)])
def dashboard(anchor:date | None=None,s=Depends(db, scope="function")):
    resolve=category_resolver(s)
    pref=settings(s);start,end=period(anchor or datetime.now(TZ).date(),pref.month_start)
    prev,_=period(start-timedelta(days=1),pref.month_start)
    def localday(v):return datetime.fromisoformat(v).astimezone(TZ).date()
    ps=s.scalars(select(Purchase)).all();pay=s.scalars(select(Payment)).all()
    current=[p for p in ps if start<=localday(p.occurred_at)<end]
    unclassified=[p for p in pay if p.kind=='bank_unclassified' and p.amount<0 and not p.purchase_id]
    previous=sum(p.own_amount for p in ps if prev<=localday(p.occurred_at)<start)-sum(p.amount for p in unclassified if prev<=localday(p.occurred_at)<start)
    cats=defaultdict(int);shops=defaultdict(int);days=defaultdict(int);weeks=defaultdict(int);freq={}
    for p in current:
        shops[s.get(Merchant,p.merchant_id).name]+=p.own_amount
        day=localday(p.occurred_at);days[str(day)]+=p.own_amount;weeks[str(day-timedelta(days=day.weekday()))]+=p.own_amount
        for i in s.scalars(select(Item).where(Item.purchase_id==p.id)):
            cats[resolve(i.category)]+=i.own_amount
            key=i.menu_product_id or i.product_id or i.name
            if key not in freq:freq[key]={'name':i.name,'quantity':0,'amount':0}
            freq[key]['quantity']+=i.quantity;freq[key]['amount']+=i.own_amount
    pending=[p for p in unclassified if start<=localday(p.occurred_at)<end]
    bank_rows={t.payment_id:t for t in s.scalars(select(BankTransaction))}
    for payment in pending:
        amount=-payment.amount;day=localday(payment.occurred_at)
        tx=bank_rows.get(payment.id)
        cats[resolve(tx.expense_category if tx and tx.expense_category else 'Банк: без категории')]+=amount;shops[tx.merchant if tx and tx.merchant else 'Банковская операция']+=amount
        days[str(day)]+=amount;weeks[str(day-timedelta(days=day.weekday()))]+=amount
    movements=[p for p in pay if start<=localday(p.occurred_at)<end]
    series=[{'name':str(start+timedelta(days=n)),'amount':days[str(start+timedelta(days=n))]} for n in range((end-start).days)]
    months=[]
    cursor=start
    for _ in range(6):
        a,b=period(cursor,pref.month_start);months.append({'name':str(a),'amount':sum(p.own_amount for p in ps if a<=localday(p.occurred_at)<b)-sum(p.amount for p in unclassified if a<=localday(p.occurred_at)<b) });cursor=a-timedelta(days=1)
    expense=sum(p.own_amount for p in current)-sum(p.amount for p in pending)
    return {'start':str(start),'end':str(end-timedelta(days=1)),'income':sum(p.amount for p in movements if p.kind=='income'),'expenses':expense,'cash_flow':sum(p.amount for p in movements),'cash_in':sum(p.amount for p in movements if p.amount>0),'cash_out':-sum(p.amount for p in movements if p.amount<0),'previous_expenses':previous,'unclassified_outflow':-sum(p.amount for p in pending),'unclassified_inflow':sum(p.amount for p in movements if p.kind=='bank_unclassified' and p.amount>0),'purchase_count':len(current),'average_purchase':round(sum(p.own_amount for p in current)/len(current)) if current else 0,'daily_average':round(expense/max(1,min((datetime.now(TZ).date()-start).days+1,(end-start).days))),'categories':[{'name':k,'amount':v,'limit':pref.category_limits.get(k,next((v for raw,v in pref.category_limits.items() if resolve(raw)==k),0))} for k,v in sorted(cats.items(),key=lambda x:-x[1])],'merchants':[{'name':k,'amount':v} for k,v in sorted(shops.items(),key=lambda x:-x[1])],'daily':series,'weekly':[{'name':k,'amount':v} for k,v in sorted(weeks.items())],'monthly':months[::-1],'frequent':sorted(freq.values(),key=lambda x:-x['quantity'])[:10],'limit':pref.monthly_limit}

TABLES={'barcodes':Barcode,'products':Product,'merchants':Merchant,'menu_products':MenuProduct,'purchases':Purchase,'items':Item,'people':Person,'debts':Debt,'payments':Payment,'settings':Setting,'rules':Rule,'request_keys':RequestKey,'bank_imports':BankImport,'bank_transactions':BankTransaction,'bank_import_rows':BankImportRow}
def snapshot(s):return {'format':'spendlog','version':4,'currency':'KRW','tables':{name:[dump(r) for r in s.scalars(select(cls))] for name,cls in TABLES.items()}}
@app.get('/api/export',dependencies=[Depends(auth)])
def export(format:str='json',s=Depends(db, scope="function")):
    data=snapshot(s)
    if format=='json':return Response(json.dumps(data,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="spendlog.json"'})
    if format!='csv':fail('Неизвестный формат')
    out=io.StringIO();w=csv.writer(out);w.writerow(['table','record_json'])
    for table,rows in data['tables'].items():
        for row in rows:w.writerow([table,json.dumps(row,ensure_ascii=False)])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="spendlog.csv"'})

def validate_snapshot(data):
    if not isinstance(data,dict):fail('Ожидается объект резервной копии',422)
    if data.get('format')!='spendlog' or data.get('version') not in (1,2,3,4) or data.get('currency')!='KRW':fail('Неподдерживаемый формат резервной копии',422)
    tables=data.get('tables',{})
    if not isinstance(tables,dict) or any(not isinstance(rows,list) for rows in tables.values()):fail('Некорректные таблицы',422)
    if data['version']<3:tables.setdefault('menu_products',[])
    if data['version']<4:tables.setdefault('people',[])
    legacy=set(TABLES)-{'barcodes','bank_imports','bank_transactions','bank_import_rows'}
    if data['version']==1 and set(tables)==legacy:
        for name in set(TABLES)-legacy:tables[name]=[]
    if set(tables)!=set(TABLES):fail('Неполная резервная копия',422)
    # Roundtrip financial validation is performed on the transaction after tentative merge.

def validate_ledger(s):
    def canonical_date(value):
        dt=datetime.fromisoformat(value)
        if not dt.tzinfo or dt.utcoffset()!=timedelta(0) or dt.isoformat()!=value:fail('Даты копии должны быть в каноническом UTC',422)
    for barcode in s.scalars(select(Barcode)):
        ProductIn.model_validate(dump(barcode))
    for product in s.scalars(select(Product)):
        ProductIn.model_validate({k:getattr(product,k) for k in ('barcode','name','category')})
    for menu in s.scalars(select(MenuProduct)):
        if not s.get(Merchant,menu.merchant_id) or not menu.name.strip() or not menu.category.strip() or not 0<=menu.price<=1_000_000_000 or menu.version<1:fail('Некорректная карточка меню',422)
    for item in s.scalars(select(Item)):
        if item.menu_product_id:
            menu=s.get(MenuProduct,item.menu_product_id);purchase=s.get(Purchase,item.purchase_id)
            if item.product_id or not menu or menu.merchant_id!=purchase.merchant_id:fail('Нарушена связь меню и чека',422)
    for rule in s.scalars(select(Rule)):
        RuleIn.model_validate({'match':rule.match,'category':rule.category})
    for p in s.scalars(select(Purchase)):
        canonical_date(p.occurred_at);canonical_date(p.created_at)
        if p.version<1 or p.total>1_000_000_000:fail('Некорректная покупка',422)
        rows=s.scalars(select(Item).where(Item.purchase_id==p.id)).all()
        if not rows or sum(i.unit_price*i.quantity for i in rows)!=p.total or sum(i.own_amount for i in rows)!=p.own_amount:fail('Нарушена сумма покупки',422)
        payment=s.scalars(select(Payment).where(Payment.purchase_id==p.id)).all()
        if (p.total and (len(payment)!=1 or payment[0].amount!=-p.total or payment[0].kind!='purchase' or payment[0].debt_id or (payment[0].occurred_at!=p.occurred_at and not s.scalar(select(BankTransaction.id).where(BankTransaction.payment_id==payment[0].id))))) or (not p.total and payment):fail('Нарушена связь покупки и платежа',422)
        d=s.scalar(select(Debt).where(Debt.purchase_id==p.id))
        if p.total>p.own_amount:
            if not d or d.principal!=p.total-p.own_amount or d.direction!='receivable' or d.kind not in ('friend','reimbursement') or d.occurred_at!=p.occurred_at:fail('Нарушена доля долга',422)
        elif d:fail('Лишний долг покупки',422)
    for person in s.scalars(select(Person)):
        if not person.name.strip() or len(person.name)>200 or person.name_key!=person_name_key(person.name) or len(person.note)>2000 or person.version<1:fail('Некорректная карточка человека',422)
    for d in s.scalars(select(Debt)):
        if d.person_id and not s.get(Person,d.person_id):fail('Не найден человек долга',422)
        canonical_date(d.occurred_at)
        if not d.person.strip() or len(d.person)>200:fail('Некорректное имя',422)
        if d.direction not in ('receivable','payable') or debt_view(s,d)['remaining']<0:fail('Некорректный долг',422)
        if not d.purchase_id:
            loans=s.scalars(select(Payment).where(Payment.debt_id==d.id,Payment.kind.in_(['loan_given','loan_received']))).all()
            if d.kind!='loan' or len(loans)!=1 or abs(loans[0].amount)!=d.principal or loans[0].occurred_at!=d.occurred_at:fail('Некорректный заём',422)
    for p in s.scalars(select(Payment)):
        canonical_date(p.occurred_at);canonical_date(p.created_at)
        if p.version<1 or abs(p.amount)>1_000_000_000:fail('Некорректная сумма или версия',422)
        dt=datetime.fromisoformat(p.occurred_at)
        if not dt.tzinfo or dt.utcoffset()!=timedelta(0):fail('В копии даты должны быть UTC',422)
        if p.kind=='income':
            if p.amount<=0 or p.debt_id or p.purchase_id:fail('Некорректный доход',422)
        elif p.kind=='purchase':
            if not p.purchase_id or p.debt_id or p.amount>=0:fail('Некорректный платёж',422)
        elif p.kind in ('settlement','loan_given','loan_received'):
            d=s.get(Debt,p.debt_id) if p.debt_id else None
            if not d or p.purchase_id:fail('Нет связанного долга',422)
            expected=1 if (p.kind=='loan_received' or (p.kind=='settlement' and d.direction=='receivable')) else -1
            if p.amount*expected<=0 or (p.kind=='loan_given' and d.direction!='receivable') or (p.kind=='loan_received' and d.direction!='payable') or p.occurred_at<d.occurred_at:fail('Неверное направление платежа',422)
        elif p.kind in ('bank_unclassified','bank_transfer'):
            if p.purchase_id or p.debt_id or not s.scalar(select(BankTransaction.id).where(BankTransaction.payment_id==p.id)):fail('Банковский платёж без происхождения',422)
        else:fail('Неизвестный тип платежа',422)
    validate_bank_ledger(s)
    validate_categories(s)
    settings(s)
@app.post('/api/import',dependencies=[Depends(auth)])
async def import_data(request:Request,format:str='json',s=Depends(db, scope="function")):
    raw=await request.body()
    if len(raw)>10_000_000:fail('Файл больше 10 MB',413)
    try:
        if format=='json':data=json.loads(raw)
        elif format=='csv':
            data={'format':'spendlog','version':4,'currency':'KRW','tables':{k:[] for k in TABLES}}
            reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
            if reader.fieldnames!=['table','record_json']:fail('Используйте CSV, экспортированный SpendLog',422)
            for row in reader:data['tables'][row['table']].append(json.loads(row['record_json']))
            if any('person_id' not in row for row in data['tables']['debts']):data['version']=3
            if any('menu_product_id' not in row for row in data['tables']['items']) or any('expense_category' not in row for row in data['tables']['bank_transactions']):data['version']=2
            if any('created_at' not in row for row in data['tables']['payments']):data['version']=1
        else:fail('Неизвестный формат',422)
        validate_snapshot(data);added=0;skipped=0
        for name,cls in TABLES.items():
            cols={c.name:c for c in cls.__table__.columns};pk=list(cls.__table__.primary_key.columns)[0].name
            for row in data['tables'][name]:
                if not isinstance(row,dict):fail('Некорректная запись',422)
                if data['version']<3 and name=='items':row={'menu_product_id':None,**row}
                if data['version']<3 and name=='bank_transactions':row={'expense_category':'',**row}
                legacy_person=name=='debts' and 'person_id' not in row and data['version']<4
                if legacy_person:row={**row,'person_id':None}
                legacy_payment=name=='payments' and 'created_at' not in row and data['version']==1
                if legacy_payment:row={**row,'created_at':row.get('occurred_at')}
                if set(row)!=set(cols):fail('Неверные поля '+name,422)
                for k,v in row.items():
                    col=cols[k]
                    if v is None:
                        if not col.nullable:fail('Пустое обязательное поле',422)
                    elif isinstance(col.type,Integer):
                        if type(v)!=int or abs(v)>1_000_000_000_000:fail('Некорректное целое число',422)
                    elif not isinstance(v,str) or len(v)>(getattr(col.type,'length',None) or (5_000_000 if name=='bank_imports' and k=='source_text' else 100000)):fail('Некорректная строка',422)
                existing=s.get(cls,row[pk])
                if existing:
                    existing_data=dump(existing)
                    if legacy_person:row['person_id']=existing_data['person_id']
                    if legacy_payment:row['created_at']=existing_data['created_at']
                    if existing_data!=row:fail('Конфликт ID: копия содержит другую версию записи. Импорт отменён.',409)
                    skipped+=1;continue
                s.add(cls(**row));s.flush();added+=1
        for debt in s.scalars(select(Debt)):
            if not debt.person_id:debt.person_id=ensure_person(s,debt.person).id
        for product in s.scalars(select(Product)):
            if not s.get(Barcode,product.barcode):s.add(Barcode(barcode=product.barcode,name=product.name,category=product.category))
        s.flush()
        validate_ledger(s)
        return {'added':added,'skipped':skipped}
    except (ValueError,KeyError,TypeError,csv.Error) as e:fail('Повреждённый файл: '+str(e)[:150],422)

from .bank_api import register as register_bank_api, validate_bank_ledger
register_bank_api(app)

from .menu_api import register as register_menu_api
register_menu_api(app)

from .category_api import register as register_category_api, resolver as category_resolver, validate as validate_categories
from .people_api import register as register_people_api, ensure_person, display_name as person_display, name_key as person_name_key
from .expense_api import register as register_expense_api
register_category_api(app)
register_people_api(app)
register_expense_api(app)
