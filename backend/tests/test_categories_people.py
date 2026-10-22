import copy
import json
import pytest
from test_ledger import client,post,purchase,dash
from test_bank import preview,confirm,statement
from app import main


def category(client,name='Выходные'):
    r=client.post('/api/categories',json={'name':name});assert r.status_code==200,r.text
    return r.json()

def expenses(client,**params):
    r=client.get('/api/analytics/expenses',params={'anchor':'2026-10-22',**params});assert r.status_code==200,r.text
    return r.json()

def test_category_crud_aliases_preserve_historical_items_and_totals(client):
    c=category(client)
    p=post(client,'/purchases',purchase(total=1000,own=700,items=[{'name':'Кофе','category':c['name'],'unit_price':1000,'quantity':1}])).json()
    r=client.put('/api/categories/'+c['id'],json={'name':'Кафе и отдых','version':1});assert r.status_code==200,r.text
    assert r.json()['aliases']==['Выходные','Кафе и отдых']
    assert client.get('/api/purchases').json()[0]['items'][0]['category']=='Выходные'
    assert dash(client)['categories'][0]['name']=='Кафе и отдых' and dash(client)['expenses']==700
    assert expenses(client,category='Кафе и отдых')['total']==700
    assert expenses(client,category='Выходные')['total']==700
    assert client.delete('/api/categories/'+c['id']+'?version=2').status_code==200
    row=next(r for r in client.get('/api/categories').json() if r['id']==c['id'])
    assert not row['active'] and row['version']==3
    assert dash(client)['expenses']==700
    assert client.post('/api/categories',json={'name':'выходные'}).status_code==409
    assert client.put('/api/categories/'+c['id'],json={'name':row['name'],'active':True,'version':3}).status_code==200
    assert client.put('/api/categories/'+c['id'],json={'name':'stale','version':3}).status_code==409


def test_category_rename_merges_alias_spelling_without_merging_money(client):
    c=category(client,'Кофе')
    assert client.put('/api/categories/'+c['id'],json={'name':'Напитки и кофе','version':1}).status_code==200
    for label in ['Кофе','Напитки и кофе']:
        assert post(client,'/purchases',purchase(total=1000,own=1000,items=[{'name':'Кофе','category':label,'unit_price':1000}])).status_code==200
    report=dash(client)
    assert report['categories']==[{'name':'Напитки и кофе','amount':2000,'limit':0}]
    assert len(expenses(client,category='Напитки и кофе')['rows'])==2
    assert client.post('/api/categories',json={'name':'Напитки И КОФЕ'}).status_code==409


def test_category_named_debt_does_not_create_financial_debt(client,monkeypatch):
    category(client,'Долг')
    batch=preview(client,monkeypatch,statement(['1 2026-10-23 [12:00:00] 40,000 0 10,000 SHOP']))
    assert confirm(client,batch,[{'row_id':batch['rows'][0]['id'],'action':'new','category':'Долг'}]).status_code==200
    assert dash(client)['expenses']==40000
    assert client.get('/api/debts').json()==[]
    assert client.get('/api/people').json()==[]


def test_drilldown_matches_chart_own_share_and_bank_rows(client,monkeypatch):
    post(client,'/purchases',purchase(total=40000,own=20000,merchant='치맥킹',items=[{'name':'Курица','category':'Еда вне дома','unit_price':30000},{'name':'Напитки','category':'Напитки','unit_price':10000}]))
    batch=preview(client,monkeypatch,statement(['1 2026-10-23 [12:00:00] 5,000 0 10,000 CAFE']))
    assert confirm(client,batch,[{'row_id':batch['rows'][0]['id'],'action':'new','category':'Еда вне дома'}]).status_code==200
    post(client,'/payments',{'kind':'income','amount':10000,'occurred_at':'2026-10-23T00:00:00Z'})
    post(client,'/payments',{'kind':'loan_given','amount':3000,'person':'Друг','occurred_at':'2026-10-23T00:00:00Z'})
    for row in dash(client)['categories']:assert expenses(client,category=row['name'])['total']==row['amount']
    for row in dash(client)['merchants']:assert expenses(client,merchant=row['name'])['total']==row['amount']
    detail=expenses(client,merchant='치맥킹')['rows'][0]
    assert (detail['amount'],detail['paid'],detail['own_amount'])==(20000,40000,20000)
    assert expenses(client,category='Еда вне дома')['total']==20000
    assert len(expenses(client)['rows'])==2


def test_bank_quick_share_creates_person_and_twenty_thousand_debt(client,monkeypatch):
    batch=preview(client,monkeypatch,statement(['1 2026-10-23 [12:00:00] 40,000 0 10,000 치맥킹']))
    assert confirm(client,batch).status_code==200
    tx=client.get('/api/bank/transactions').json()[0]
    data=purchase(total=40000,own=20000,merchant='치맥킹',person='Алан',items=[{'name':'치맥킹','category':'Еда вне дома','unit_price':40000}])
    r=post(client,'/bank/transactions/'+tx['id']+'/purchase?version=1',data)
    assert r.status_code==200,r.text
    assert (dash(client)['expenses'],dash(client)['cash_flow'])==(20000,-40000)
    person=client.get('/api/people').json()[0]
    assert person['name']=='Алан' and person['receivable']==20000 and person['payable']==0
    debt=client.get('/api/debts').json()[0]
    assert debt['person_id']==person['id'] and debt['remaining']==20000
    assert len(client.get('/api/payments').json())==1
    assert expenses(client,merchant='치맥킹')['total']==20000


def test_people_rename_preserves_identity_history_and_both_directions(client):
    p=post(client,'/purchases',purchase(person='Алан')).json()
    person=client.get('/api/people').json()[0]
    r=client.put('/api/people/'+person['id'],json={'name':'Алан Н.','note':'Друг','version':1})
    assert r.status_code==200,r.text
    assert client.get('/api/debts').json()[0]['person']=='Алан Н.'
    with main.SessionLocal() as s:assert s.get(main.Debt,p['debt']['id']).person=='Алан'
    # An old spelling still resolves through the debt snapshot to the same person.
    post(client,'/payments',{'kind':'loan_received','amount':5000,'person':'АЛАН','occurred_at':'2026-10-23T00:00:00Z'})
    post(client,'/debts/'+p['debt']['id']+'/settle',{'amount':7000,'occurred_at':'2026-10-24T00:00:00Z'})
    people=client.get('/api/people').json();assert len(people)==1
    assert (people[0]['receivable'],people[0]['payable'])==(13000,5000)
    assert dash(client)['income']==0
    assert client.delete('/api/people/'+person['id']+'?version=2').status_code==409
    assert client.put('/api/people/'+person['id'],json={'name':'stale','version':1}).status_code==409


def test_empty_person_crud_and_case_duplicates(client):
    p=client.post('/api/people',json={'name':'Друг','note':'Знакомый'}).json()
    assert p['receivable']==p['payable']==0 and p['debt_count']==0
    assert client.post('/api/people',json={'name':'ДРУГ'}).status_code==409
    assert client.delete('/api/people/'+p['id']+'?version=1').status_code==200
    assert client.get('/api/people').json()==[]


@pytest.mark.parametrize('format',['json','csv'])
def test_categories_people_restore_preserves_aliases_balances_and_repeat(format,client):
    post(client,'/purchases',purchase(person='Алан'))
    c=category(client,'Встречи')
    assert client.put('/api/categories/'+c['id'],json={'name':'Встречи и отдых','version':1}).status_code==200
    before=dash(client);people=client.get('/api/people').json();raw=client.get('/api/export?format='+format).content
    with main.SessionLocal() as s:
        for cls in reversed(list(main.TABLES.values())):s.query(cls).delete()
        s.commit()
    r=client.post('/api/import?format='+format,content=raw)
    assert r.status_code==200,r.text
    assert dash(client)==before and client.get('/api/people').json()==people
    assert next(r for r in client.get('/api/categories').json() if r['id']==c['id'])['aliases']==['Встречи','Встречи и отдых']
    assert client.post('/api/import?format='+format,content=raw).json()['added']==0


def test_legacy_v3_debts_gain_people_and_repeat_import_stays_safe(client):
    post(client,'/purchases',purchase(person='Алан'))
    data=client.get('/api/export').json();data['version']=3;data['tables'].pop('people')
    for d in data['tables']['debts']:d.pop('person_id')
    with main.SessionLocal() as s:
        for cls in reversed(list(main.TABLES.values())):s.query(cls).delete()
        s.commit()
    r=client.post('/api/import',json=data);assert r.status_code==200,r.text
    assert client.get('/api/people').json()[0]['receivable']==20000
    assert client.post('/api/import',json=data).json()['added']==0


def test_corrupt_category_backup_rolls_back(client):
    data=client.get('/api/export').json()
    data['tables']['products'].append({'id':'new','barcode':'8800000000002','name':'Not committed','category':'Продукты'})
    data['tables']['settings'].append({'key':'categories','value':json.dumps([{'name':'Broken'}])})
    r=client.post('/api/import',json=data)
    assert r.status_code==422,r.text
    assert client.get('/api/products').json()==[]


def test_drilldown_obeys_seoul_financial_month(client):
    post(client,'/purchases',purchase(total=1000,own=1000,at='2026-10-21T14:59:59Z'))
    post(client,'/purchases',purchase(total=2000,own=2000,at='2026-10-21T15:00:00Z'))
    assert expenses(client)['total']==2000
