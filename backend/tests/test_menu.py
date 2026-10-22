import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from app import main
from test_ledger import client,post,purchase,dash
from test_bank import preview,confirm,statement

SHOP='김밥천국(DGIST점'

def menu(client,name='Тонкатсу',price=6000,merchant=SHOP):
    r=client.post('/api/menu-products',json={'merchant':merchant,'name':name,'price':price,'category':'Еда вне дома'})
    assert r.status_code==200,r.text
    return r.json()

def food(card,quantity=1):
    return {'product_id':None,'menu_product_id':card['id'],'name':card['name'],'category':card['category'],'unit_price':card['price'],'quantity':quantity}

def bank(client,monkeypatch,amount=40000,shop=SHOP):
    batch=preview(client,monkeypatch,statement([f'1 2026-10-23 [12:00:00] {amount:,} 0 10,000 {shop}']))
    r=confirm(client,batch);assert r.status_code==200,r.text
    return client.get('/api/bank/transactions').json()[0]


def test_menu_cards_and_item_level_merchant_report(client,monkeypatch):
    pork=menu(client);chicken=menu(client,'Чикенмайо',7000)
    data=purchase(total=60000,own=60000,merchant=SHOP,items=[food(pork,3),food(chicken,6)])
    assert post(client,'/purchases',data).status_code==200
    bank(client,monkeypatch,40000)
    report=client.get('/api/merchants/analytics?anchor=2026-10-22').json()['merchants'][0]
    assert report['merchant']==SHOP and report['own_amount']==100000 and report['unitemized']==40000
    items={i['name']:i for i in report['items']}
    assert items['Тонкатсу']['quantity']==3 and items['Чикенмайо']['quantity']==6
    assert items['Тонкатсу']['own_amount']==18000 and items['Чикенмайо']['own_amount']==42000
    r=client.put('/api/menu-products/'+pork['id'],json={'merchant':SHOP,'name':'Новое имя','category':'Другое','price':9000,'version':1})
    assert r.status_code==200,r.text
    assert {i['name'] for i in client.get('/api/purchases').json()[0]['items']}=={'Тонкатсу','Чикенмайо'}
    assert client.get('/api/menu-products').json()[0]['last_price'] in (6000,7000)
    assert dash(client)['expenses']==100000


def test_bank_itemization_is_atomic_idempotent_and_keeps_single_movement(client,monkeypatch):
    card=menu(client,price=10000);tx=bank(client,monkeypatch)
    data=purchase(total=40000,own=20000,merchant=SHOP,at='2026-10-23T12:02:00+09:00',items=[food(card,4)])
    key=str(uuid.uuid4());path='/bank/transactions/'+tx['id']+'/purchase?version='+str(tx['version'])
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:post(client,path,data,key),range(2)))
    assert all(r.status_code==200 for r in results),[r.text for r in results]
    assert results[0].json()['id']==results[1].json()['id']
    a=dash(client);assert (a['expenses'],a['cash_flow'],a['cash_out'])==(20000,-40000,40000)
    assert len(client.get('/api/payments').json())==1
    assert client.get('/api/debts').json()[0]['remaining']==20000
    assert client.get('/api/bank/transactions').json()[0]['occurred_at']=='2026-10-23T03:00:00+00:00'
    assert client.get('/api/purchases').json()[0]['occurred_at']=='2026-10-23T03:02:00+00:00'
    changed={**data,'note':'changed'}
    assert post(client,path,changed,key).status_code==409


def test_invalid_bank_itemization_rolls_back_receipt_and_keeps_bank(client,monkeypatch):
    wrong=menu(client,price=10000,merchant='Other shop');tx=bank(client,monkeypatch)
    data=purchase(total=40000,own=40000,merchant=SHOP,items=[food(wrong,4)])
    r=post(client,'/bank/transactions/'+tx['id']+'/purchase?version=1',data)
    assert r.status_code==422,r.text
    assert client.get('/api/purchases').json()==[]
    assert len(client.get('/api/payments').json())==1 and dash(client)['expenses']==40000


def test_editing_linked_items_preserves_bank_payment_and_total(client,monkeypatch):
    card=menu(client,price=10000);tx=bank(client,monkeypatch)
    data=purchase(total=40000,own=40000,merchant=SHOP,items=[food(card,4)])
    p=post(client,'/bank/transactions/'+tx['id']+'/purchase?version=1',data).json()
    before=client.get('/api/payments').json()[0]
    edited={**data,'version':p['version'],'items':[{**food(card,2),'unit_price':20000}]}
    r=client.put('/api/purchases/'+p['id'],json=edited)
    assert r.status_code==200,r.text
    after=client.get('/api/payments').json()[0]
    assert before['id']==after['id'] and before['created_at']==after['created_at'] and before['occurred_at']==after['occurred_at']
    assert len(client.get('/api/payments').json())==1 and dash(client)['cash_flow']==-40000
    bad={**edited,'version':r.json()['version'],'items':[{**food(card,2),'unit_price':15000}],'receipt_total':30000,'own_amount':30000}
    assert client.put('/api/purchases/'+p['id'],json=bad).status_code==422
    assert dash(client)['expenses']==40000
    assert client.post('/api/import',content=client.get('/api/export').content).json()['added']==0


def test_bank_categories_and_item_categories_take_precedence(client,monkeypatch):
    tx=bank(client,monkeypatch)
    r=client.put('/api/bank/transactions/'+tx['id']+'/classification',json={'kind':'bank_unclassified','category':'Обязательные траты','version':1})
    assert r.status_code==200,r.text
    assert dash(client)['categories'][0]['name']=='Обязательные траты'
    card=menu(client,price=10000)
    p=post(client,'/bank/transactions/'+tx['id']+'/purchase?version=2',purchase(total=40000,own=40000,merchant=SHOP,items=[food(card,4)]))
    assert p.status_code==200,p.text
    assert dash(client)['categories'][0]['name']=='Еда вне дома'
    assert dash(client)['expenses']==40000


@pytest.mark.parametrize('format',['json','csv'])
def test_menu_and_categories_backup_roundtrip(client,monkeypatch,format):
    card=menu(client,price=10000);tx=bank(client,monkeypatch)
    assert post(client,'/bank/transactions/'+tx['id']+'/purchase?version=1',purchase(total=40000,own=20000,merchant=SHOP,items=[food(card,4)])).status_code==200
    raw=client.get('/api/export?format='+format).content;before=dash(client)
    with main.SessionLocal() as s:
        for cls in reversed(list(main.TABLES.values())):s.query(cls).delete()
        s.commit()
    r=client.post('/api/import?format='+format,content=raw)
    assert r.status_code==200,r.text
    assert dash(client)==before
    assert client.get('/api/menu-products').json()[0]['name']=='Тонкатсу'
    assert client.post('/api/import?format='+format,content=raw).json()['added']==0


def test_v2_snapshot_without_new_columns_still_restores(client,monkeypatch):
    bank(client,monkeypatch)
    raw=client.get('/api/export').json();raw['version']=2;raw['tables'].pop('menu_products')
    for i in raw['tables']['items']:i.pop('menu_product_id')
    for tx in raw['tables']['bank_transactions']:tx.pop('expense_category')
    assert client.post('/api/import',json=raw).json()['added']==0


def test_menu_price_validation_and_historical_store_protection(client):
    card=menu(client)
    assert post(client,'/purchases',purchase(total=6000,own=6000,merchant=SHOP,items=[food(card)])).status_code==200
    r=client.put('/api/menu-products/'+card['id'],json={'merchant':'Elsewhere','name':card['name'],'category':card['category'],'price':6000,'version':1})
    assert r.status_code==409
    assert client.post('/api/menu-products',json={'merchant':SHOP,'name':'Bad','price':6.5}).status_code==422

def test_import_category_is_preserved_and_repeat_cannot_reclassify(client,monkeypatch):
    batch=preview(client,monkeypatch,statement(['1 2026-10-23 [12:00:00] 40,000 0 10,000 INSURANCE']))
    decision=[{'row_id':batch['rows'][0]['id'],'action':'new','category':'Страховка'}]
    assert confirm(client,batch,decision).status_code==200
    assert dash(client)['categories'][0]['name']=='Страховка'
    tx=client.get('/api/bank/transactions').json()[0]
    raw=client.get('/api/export').content
    assert client.post('/api/import',content=raw).json()['added']==0
    # Different description is ambiguous, acknowledged as the same movement.
    second=preview(client,monkeypatch,statement(['1 2026-10-23 [12:00:00] 40,000 0 10,000 INSURANCE NEW']))
    decision=[{'row_id':second['rows'][0]['id'],'action':'repeat','transaction_id':tx['id'],'category':'Другое'}]
    assert confirm(client,second,decision).status_code==200
    assert dash(client)['categories'][0]['name']=='Страховка'


def test_linked_item_edit_with_settled_debt_is_rejected_atomically(client,monkeypatch):
    card=menu(client,price=10000);tx=bank(client,monkeypatch)
    data=purchase(total=40000,own=20000,merchant=SHOP,items=[food(card,4)])
    p=post(client,'/bank/transactions/'+tx['id']+'/purchase?version=1',data).json()
    assert post(client,'/debts/'+p['debt']['id']+'/settle',{'amount':10000,'occurred_at':'2026-10-24T00:00:00Z'}).status_code==200
    before=dash(client)
    r=client.put('/api/purchases/'+p['id'],json={**data,'version':p['version'],'own_amount':40000})
    assert r.status_code==409
    assert dash(client)==before
    assert client.get('/api/debts').json()[0]['remaining']==10000

def test_changing_own_share_of_linked_receipt_updates_only_debt_and_expenses(client,monkeypatch):
    card=menu(client,price=10000);tx=bank(client,monkeypatch)
    data=purchase(total=40000,own=20000,merchant=SHOP,items=[food(card,4)])
    created=post(client,'/bank/transactions/'+tx['id']+'/purchase?version=1',data).json()
    payment=client.get('/api/payments').json()[0]
    r=client.put('/api/purchases/'+created['id'],json={**data,'own_amount':10000,'version':created['version']})
    assert r.status_code==200,r.text
    assert client.get('/api/debts').json()[0]['remaining']==30000
    after=client.get('/api/payments').json()[0]
    assert after['id']==payment['id'] and after['amount']==payment['amount'] and after['occurred_at']==payment['occurred_at']
    assert dash(client)['expenses']==10000 and dash(client)['cash_flow']==-40000
    assert client.post('/api/import',content=client.get('/api/export').content).json()['added']==0
