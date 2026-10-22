import os
os.environ['APP_PASSWORD']='test-password-only-123'
os.environ['COOKIE_SECURE']='false'
import json, uuid, copy, threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from app.models import Base, Purchase, Payment, Debt
import app.main as main

@pytest.fixture
def client(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'test.db'),connect_args={'check_same_thread':False,'timeout':30})
    @event.listens_for(engine,'connect')
    def fk(c,_): c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main,'SessionLocal',sessionmaker(engine,expire_on_commit=False))
    with TestClient(main.app,headers={'X-SpendLog':'1'}) as c:
        assert c.post('/api/login',json={'password':os.environ['APP_PASSWORD']}).status_code==200
        yield c
    engine.dispose()

def post(c,url,data,key=None):return c.post('/api'+url,json=data,headers={'Idempotency-Key':key or str(uuid.uuid4())})
def purchase(total=40000,own=20000,at='2026-10-22T14:45:00+09:00',**kw):
    return {'merchant':'GS25','occurred_at':at,'items':[{'name':'Обед','category':'Рестораны','unit_price':total,'quantity':1}],'receipt_total':total,'own_amount':own,'person':'Друг',**kw}
def dash(c,anchor='2026-10-22'):return c.get('/api/dashboard',params={'anchor':anchor}).json()

def test_split_settlement_does_not_inflate_income(client):
    p=post(client,'/purchases',purchase()).json(); d=p['debt']['id']
    a=dash(client);assert (a['expenses'],a['cash_flow'],a['income'])==(20000,-40000,0)
    r=post(client,f'/debts/{d}/settle',{'amount':15000,'occurred_at':'2026-10-23T00:00:00Z'})
    assert r.status_code==200,r.text
    a=dash(client);assert (a['expenses'],a['cash_flow'],a['income'])==(20000,-25000,0)
    assert client.get('/api/debts').json()[0]['remaining']==5000
    assert post(client,f'/debts/{d}/settle',{'amount':5001,'occurred_at':'2026-10-23T00:00:00Z'}).status_code==422
    assert client.delete(f"/api/purchases/{p['id']}?version=1").status_code==409

def test_idempotency_and_conflicting_reuse(client):
    key=str(uuid.uuid4());data=purchase()
    a=post(client,'/purchases',data,key);b=post(client,'/purchases',data,key)
    assert a.json()==b.json();assert len(client.get('/api/purchases').json())==1
    data['note']='changed'
    assert post(client,'/purchases',data,key).status_code==409
    assert dash(client)['expenses']==20000

def test_concurrent_retry_and_overpayment(client):
    key=str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:post(client,'/purchases',purchase(),key),range(2)))
    assert all(r.status_code==200 for r in results)
    assert results[0].json()['id']==results[1].json()['id']
    debt=results[0].json()['debt']['id']
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:post(client,f'/debts/{debt}/settle',{'amount':15000,'occurred_at':'2026-10-24T00:00:00Z'}),range(2)))
    assert sorted(r.status_code for r in results)==[200,422]
    assert client.get('/api/debts').json()[0]['remaining']==5000

def test_loans_both_directions(client):
    for kind in ('loan_given','loan_received'):
        p=post(client,'/payments',{'kind':kind,'amount':10000,'person':'А','occurred_at':'2026-10-23T00:00:00Z'}).json()
        assert post(client,f"/debts/{p['debt_id']}/settle",{'amount':10000,'occurred_at':'2026-10-24T00:00:00Z'}).status_code==200
    a=dash(client);assert (a['income'],a['expenses'],a['cash_flow'])==(0,0,0)
    assert all(d['remaining']==0 for d in client.get('/api/debts').json())

def test_receipt_validation_integer_only_and_rollback(client):
    bad=purchase();bad['receipt_total']=39999
    assert post(client,'/purchases',bad).status_code==422
    bad=purchase();bad['items'][0]['unit_price']=40000.5
    assert post(client,'/purchases',bad).status_code==422
    bad=purchase();bad['items'][0]['product_id']='not-found'
    assert post(client,'/purchases',bad).status_code==422
    assert client.get('/api/purchases').json()==[]
    assert client.get('/api/merchants').json()==[]
    assert post(client,'/purchases',purchase(own=40001)).status_code==422

def test_financial_month_uses_seoul(client):
    # Oct 21 23:59:59 and Oct 22 00:00:00 Seoul.
    post(client,'/purchases',purchase(total=100,own=100,at='2026-10-21T14:59:59Z'))
    post(client,'/purchases',purchase(total=200,own=200,at='2026-10-21T15:00:00Z'))
    a=dash(client);assert (a['expenses'],a['previous_expenses'])==(200,100)
    assert a['start']=='2026-10-22' and a['end']=='2026-11-21'
    assert dash(client,'2026-10-21')['expenses']==100
    assert client.put('/api/settings',json={'month_start':1,'monthly_limit':0}).status_code==200
    assert dash(client)['expenses']==300

def test_edit_delete_atomic_and_stale_version(client):
    p=post(client,'/purchases',purchase()).json()
    data=purchase(total=10000,own=10000,version=1)
    r=client.put('/api/purchases/'+p['id'],json=data)
    assert r.status_code==200,r.text
    assert r.json()['version']==2
    assert dash(client)['expenses']==10000
    assert len(client.get('/api/payments').json())==1
    assert client.get('/api/debts').json()==[]
    assert client.put('/api/purchases/'+p['id'],json=data).status_code==409
    assert client.delete('/api/purchases/'+p['id']+'?version=2').status_code==200
    assert dash(client)['expenses']==0 and dash(client)['cash_flow']==0

def test_price_history_and_snapshot_categories(client):
    pr=client.post('/api/products',json={'barcode':'8801234567890','name':'Йогурт','category':'Молочные продукты'}).json()
    for shop,price,day in [('GS25',1500,22),('CU',1700,23)]:
        data=purchase(total=price,own=price,at=f'2026-10-{day}T00:00:00Z',merchant=shop)
        data['items']=[{'product_id':pr['id'],'name':'Йогурт','category':'Молочные продукты','unit_price':price,'quantity':1}]
        assert post(client,'/purchases',data).status_code==200
    client.put('/api/products/'+pr['id'],json={'barcode':pr['barcode'],'name':'Другое имя','category':'Другое'})
    history=client.get('/api/products/'+pr['id']+'/prices').json()
    assert [x['unit_price'] for x in history]==[1500,1700]
    assert client.get('/api/products').json()[0]['last_price']==1700
    assert dash(client)['categories'][0]['name']=='Молочные продукты'

def test_exact_allocation_and_zero_price(client):
    data=purchase(total=3,own=2,items=[{'name':str(i),'category':str(i),'unit_price':1,'quantity':1} for i in range(3)])
    p=post(client,'/purchases',data).json()
    assert sum(i['own_amount'] for i in p['items'])==2
    assert sum(x['amount'] for x in dash(client)['categories'])==2
    assert post(client,'/purchases',purchase(total=0,own=0)).status_code==200
    assert dash(client)['cash_flow']==-3

def test_reimbursement_not_income(client):
    p=post(client,'/purchases',purchase(total=50000,own=0,debt_kind='reimbursement',person='DGIST')).json()
    assert post(client,'/debts/'+p['debt']['id']+'/settle',{'amount':50000,'occurred_at':'2026-10-24T00:00:00Z'}).status_code==200
    a=dash(client);assert (a['income'],a['expenses'],a['cash_flow'])==(0,0,0)

def test_settlement_edit_delete_and_date_check(client):
    d=post(client,'/purchases',purchase()).json()['debt']['id']
    assert post(client,f'/debts/{d}/settle',{'amount':1,'occurred_at':'2026-10-20T00:00:00Z'}).status_code==422
    p=post(client,f'/debts/{d}/settle',{'amount':10000,'occurred_at':'2026-10-24T00:00:00Z'}).json()
    r=client.put(f"/api/settlements/{p['id']}?version=1",json={'amount':5000,'occurred_at':'2026-10-24T00:00:00Z'})
    assert r.status_code==200,r.text
    assert client.get('/api/debts').json()[0]['remaining']==15000
    assert client.delete(f"/api/payments/{p['id']}?version=2").status_code==200
    assert client.get('/api/debts').json()[0]['remaining']==20000

def test_payment_edit_and_delete(client):
    data={'kind':'loan_received','amount':1000,'person':'A','occurred_at':'2026-10-22T00:00:00Z'}
    p=post(client,'/payments',data).json()
    r=client.put('/api/payments/'+p['id'],json={**data,'version':1,'kind':'income','amount':2000})
    assert r.status_code==200,r.text
    assert client.get('/api/debts').json()==[]
    assert dash(client)['income']==2000
    assert client.delete('/api/payments/'+p['id']+'?version=2').status_code==200
    assert dash(client)['income']==0

@pytest.mark.parametrize('format',['json','csv'])
def test_export_restore_repeat_and_preserve_idempotency(client,format):
    key=str(uuid.uuid4());p=post(client,'/purchases',purchase(),key).json()
    post(client,'/debts/'+p['debt']['id']+'/settle',{'amount':5000,'occurred_at':'2026-10-24T00:00:00Z'})
    before=dash(client);raw=client.get('/api/export?format='+format).content
    with main.SessionLocal() as s:
        for cls in reversed(list(main.TABLES.values())):s.query(cls).delete()
        s.commit()
    r=client.post('/api/import?format='+format,content=raw)
    assert r.status_code==200,r.text
    assert dash(client)==before
    assert client.post('/api/import?format='+format,content=raw).json()['added']==0
    assert post(client,'/purchases',purchase(),key).json()['id']==p['id']
    assert len(client.get('/api/purchases').json())==1

def test_import_conflict_and_tampering_are_atomic(client):
    post(client,'/purchases',purchase())
    data=client.get('/api/export').json()
    data['tables']['products'].append({'id':str(uuid.uuid4()),'barcode':'8801','name':'Never committed','category':'Other'})
    data['tables']['purchases'][0]['total']=100
    r=client.post('/api/import',json=data)
    assert r.status_code==409,r.text
    assert client.get('/api/products').json()==[]
    assert dash(client)['expenses']==20000

def test_import_invalid_ledger(client):
    data=client.get('/api/export').json()
    data['tables']['payments']=[{'id':str(uuid.uuid4()),'purchase_id':None,'debt_id':None,'kind':'income','amount':-10,'occurred_at':'2026-10-22T00:00:00+00:00','note':'','version':1}]
    assert client.post('/api/import',json=data).status_code==422
    assert client.get('/api/payments').json()==[]

def test_auth_csrf_and_logout(client):
    assert client.post('/api/settings',headers={'X-SpendLog':''},json={}).status_code==403
    assert client.post('/api/logout').status_code==200
    assert client.get('/api/purchases').status_code==401
    assert client.get('/api/export').status_code==401

@pytest.mark.parametrize('anchor,start,end',[
    ('2026-01-01','2025-12-22','2026-01-22'),
    ('2028-02-29','2028-02-22','2028-03-22'),
    ('2026-12-22','2026-12-22','2027-01-22'),
])
def test_period_year_and_leap_boundaries(anchor,start,end):
    from datetime import date
    assert tuple(str(x) for x in main.period(date.fromisoformat(anchor),22))==(start,end)

def test_allocation_preserves_integer_totals():
    import random
    r=random.Random(22)
    for _ in range(1000):
        weights=[r.randrange(0,100000) for _ in range(r.randrange(1,20))]
        amount=r.randrange(sum(weights)+1)
        allocated=main.allocate(amount,weights)
        assert sum(allocated)==amount
        assert all(type(a) is int and 0<=a<=w for a,w in zip(allocated,weights))

@pytest.mark.parametrize('payload',[[],None,{'format':'spendlog','version':1,'currency':'KRW','tables':[]}])
def test_malformed_snapshot_returns_validation_error(client,payload):
    assert client.post('/api/import',content=json.dumps(payload),headers={'Content-Type':'application/json'}).status_code==422
