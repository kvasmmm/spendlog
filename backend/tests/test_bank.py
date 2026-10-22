"""Synthetic, anonymised examples derived from the supplied PDF's actual layout."""
import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from app.bank_parser import BankRow, StatementError, parse_text, duplicate_status
from app.models import BankImport, BankTransaction, Payment
from app import bank_api, main
from test_ledger import client, post, purchase, dash

HEADER='''거래내역조회
고객명 : TEST 조회계좌번호 : 999-00-000001-1 조회기간 : 2026-09-01 ~ 2026-11-30 현재잔액 : 7,000 원
NO 거래일시 거래종류 찾으신금액 맡기신금액 거래후잔액 비고 메모 거래점
'''
ROWS=[
 '1 2026-10-23 [12:00:00] 1,000 0 7,000 GS25',
 '2 2026-10-22 [12:00:00] 2,000 0 8,000 CU',
 '3 2026-10-21 [12:00:00] 0 10,000 10,000 TEST TRANSFER',
]

def statement(rows=ROWS):return HEADER+'\n'.join(rows)

def preview(client,monkeypatch,text=None,raw=b'%PDF-test'):
    monkeypatch.setattr(bank_api,'extract_pdf',lambda _:text or statement())
    r=client.post('/api/bank/imports/preview',content=raw)
    assert r.status_code==200,r.text
    return r.json()

def decisions(batch):
    return [{'row_id':r['id'],'action':'repeat' if r['status']=='repeat' else 'new',
             'transaction_id':r['candidates'][0]['id'] if r['status']=='repeat' else None} for r in batch['rows']]

def confirm(client,batch,choices=None,key=None):
    return post(client,'/bank/imports/'+batch['id']+'/confirm',{'rows':choices or decisions(batch)},key)


def test_parser_validates_balances_direction_and_order():
    data=parse_text(statement())
    assert [r.amount for r in data['rows']]==[-1000,-2000,10000]
    assert data['rows'][0].occurred_at=='2026-10-23T03:00:00+00:00'
    assert parse_text(statement(list(reversed(ROWS))))['semantic_hash']==data['semantic_hash']
    for bad in [statement().replace('7,000 GS25','7,001 GS25'),statement(ROWS[:1]+ROWS[2:]),statement().replace('1,000 0 7,000','1,000 5 7,000'),statement().replace('[12:00:00]','12:00:00',1)]:
        with pytest.raises(StatementError):parse_text(bad)


def test_same_amount_time_shop_is_not_duplicate_without_balance_and_context():
    row=BankRow(1,'2026-10-23T03:00:00+00:00',-1000,7000,'GS25','')
    existing=[{'id':'a','fingerprint':row.fingerprint(),'occurred_at':row.occurred_at,'amount':-1000,'balance':7000}]
    assert duplicate_status(row,[row],existing)[0]=='ambiguous'
    other=BankRow(2,row.occurred_at,-1000,8000,'GS25','')
    assert duplicate_status(other,[row,other],existing)[0]=='ambiguous'


def test_same_file_twice_and_changed_order_do_not_change_totals(client,monkeypatch):
    batch=preview(client,monkeypatch)
    before=dash(client);assert before['cash_flow']==0
    r=confirm(client,batch);assert r.status_code==200,r.text
    after=dash(client);assert (after['expenses'],after['income'],after['cash_flow'])==(3000,0,-3000)
    again=preview(client,monkeypatch,statement(list(reversed(ROWS))),raw=b'%PDF-other-order')
    assert again['id']==batch['id'] and again['confirmed_at']
    assert confirm(client,again).status_code==200
    assert dash(client)==after
    assert len(client.get('/api/bank/transactions').json())==3


def test_overlapping_periods_add_only_new_movements(client,monkeypatch):
    first=preview(client,monkeypatch);assert confirm(client,first).status_code==200
    extended=['1 2026-10-24 [12:00:00] 500 0 6,500 NEW SHOP']+[
        str(int(r.split()[0])+1)+' '+r.split(' ',1)[1] for r in ROWS]
    second=preview(client,monkeypatch,statement(extended))
    assert [r['status'] for r in second['rows']]==['new','repeat','repeat','repeat']
    r=confirm(client,second);assert r.status_code==200,r.text
    assert r.json()['added']==1 and r.json()['repeated']==3
    assert dash(client)['expenses']==3500


def test_two_real_identical_purchases_are_preserved(client,monkeypatch):
    rows=['1 2026-10-23 [12:00:00] 1,000 0 7,000 GS25',
          '2 2026-10-23 [12:00:00] 1,000 0 8,000 GS25']
    batch=preview(client,monkeypatch,statement(rows))
    assert confirm(client,batch).status_code==200
    assert len(client.get('/api/bank/transactions').json())==2
    assert dash(client)['expenses']==2000


def test_corrected_bank_description_requires_review_and_does_not_overwrite(client,monkeypatch):
    first=preview(client,monkeypatch);assert confirm(client,first).status_code==200
    second=preview(client,monkeypatch,statement().replace('GS25','GS25 CORRECTED'))
    assert second['rows'][0]['status']=='ambiguous'
    choices=decisions(second)
    choices[0]={'row_id':second['rows'][0]['id'],'action':'repeat','transaction_id':second['rows'][0]['candidates'][0]['id']}
    assert confirm(client,second,choices).status_code==200
    assert dash(client)['expenses']==3000
    assert client.get('/api/bank/transactions').json()[0]['merchant']=='GS25'


def test_error_mid_import_rolls_back_every_movement(client,monkeypatch):
    batch=preview(client,monkeypatch);choices=decisions(batch)
    choices[-1]['action']='repeat';choices[-1]['transaction_id']='missing'
    result=confirm(client,batch,choices)
    assert result.status_code==422,result.text
    assert client.get('/api/payments').json()==[]
    assert client.get('/api/bank/transactions').json()==[]
    assert not client.get('/api/bank/imports/'+batch['id']).json()['confirmed_at']


def test_concurrent_confirmation_and_stale_preview(client,monkeypatch):
    batch=preview(client,monkeypatch);key=str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:confirm(client,batch,key=key),range(2)))
    assert all(r.status_code==200 for r in results)
    assert results[0].json()==results[1].json()
    assert len(client.get('/api/bank/transactions').json())==3


def test_preview_refresh_after_another_import(client,monkeypatch):
    first=preview(client,monkeypatch)
    second=preview(client,monkeypatch,statement().replace('GS25','GS25 CORRECTED'))
    assert confirm(client,first).status_code==200
    assert confirm(client,second).status_code==409
    second=preview(client,monkeypatch,statement().replace('GS25','GS25 CORRECTED'))
    assert second['rows'][0]['status']=='ambiguous'


def test_link_unlink_retains_source_and_own_share_and_distinct_times(client,monkeypatch):
    data=purchase(total=1000,own=500,at='2026-10-23T12:02:00+09:00')
    p=post(client,'/purchases',data).json()
    original_payment=client.get('/api/payments').json()[0]
    batch=preview(client,monkeypatch);choices=decisions(batch)
    assert batch['rows'][0]['purchase_candidates'][0]['id']==p['id']
    choices[0].update(action='link',purchase_id=p['id'])
    r=confirm(client,batch,choices);assert r.status_code==200,r.text
    a=dash(client);assert (a['expenses'],a['cash_flow'])==(2500,-3000)
    movements=client.get('/api/payments').json();assert len(movements)==3
    linked=next(m for m in movements if m['purchase_id']==p['id'])
    assert linked['id']==original_payment['id'] and linked['created_at']==original_payment['created_at']
    assert linked['occurred_at']=='2026-10-23T03:00:00+00:00'
    assert client.get('/api/purchases').json()[0]['occurred_at']=='2026-10-23T03:02:00+00:00'
    assert client.delete('/api/purchases/'+p['id']+'?version=2').status_code==409
    tx=next(t for t in client.get('/api/bank/transactions').json() if t['purchase_id']==p['id'])
    r=client.post('/api/bank/transactions/'+tx['id']+'/unlink',params={'version':tx['version']})
    assert r.status_code==200,r.text
    assert dash(client)['expenses']==3500
    tx=r.json()
    r=client.post('/api/bank/transactions/'+tx['id']+'/link',json={'purchase_id':p['id'],'version':tx['version']})
    assert r.status_code==200,r.text
    assert dash(client)['expenses']==2500
    snapshot=client.get('/api/export').content
    assert client.post('/api/import',content=snapshot).json()['added']==0


@pytest.mark.parametrize('format',['json','csv'])
def test_bank_backup_roundtrip_and_restart(client,monkeypatch,format):
    batch=preview(client,monkeypatch);assert confirm(client,batch).status_code==200
    before=dash(client);raw=client.get('/api/export?format='+format).content
    main.SessionLocal.kw['bind'].dispose()  # new connections, same persisted database
    assert dash(client)==before
    with main.SessionLocal() as s:
        for cls in reversed(list(main.TABLES.values())):s.query(cls).delete()
        s.commit()
    r=client.post('/api/import?format='+format,content=raw)
    assert r.status_code==200,r.text
    assert dash(client)==before
    assert client.post('/api/import?format='+format,content=raw).json()['added']==0


def test_shared_barcodes_have_no_personal_prices(client):
    with main.SessionLocal() as s:
        s.add(main.Barcode(barcode='8800000000001',name='Shared food',category='Продукты'));s.commit()
    shared=client.get('/api/barcodes/8800000000001').json()
    assert set(shared)=={'barcode','name','category'}
    assert client.get('/api/products').json()==[]
    a=client.post('/api/products',json=shared);b=client.post('/api/products',json=shared)
    assert a.status_code==b.status_code==200 and a.json()['id']==b.json()['id']
    client.put('/api/products/'+a.json()['id'],json={**shared,'name':'Personal name'})
    assert client.get('/api/barcodes/8800000000001').json()['name']=='Shared food'


def test_legacy_backup_still_imports(client):
    post(client,'/purchases',purchase(total=1000,own=1000))
    snapshot=client.get('/api/export').json();snapshot['version']=1
    for name in ('barcodes','bank_imports','bank_transactions','bank_import_rows'):snapshot['tables'].pop(name)
    for payment in snapshot['tables']['payments']:payment.pop('created_at')
    assert client.post('/api/import',json=snapshot).json()['added']==0

def test_incoming_classification_transfer_and_versions(client,monkeypatch):
    batch=preview(client,monkeypatch);assert confirm(client,batch).status_code==200
    incoming=next(t for t in client.get('/api/bank/transactions').json() if t['amount']>0)
    assert dash(client,'2026-09-22')['income']==0
    r=client.put('/api/bank/transactions/'+incoming['id']+'/classification',json={'kind':'income','version':incoming['version']})
    assert r.status_code==200,r.text
    assert dash(client,'2026-09-22')['income']==10000
    assert client.put('/api/bank/transactions/'+incoming['id']+'/classification',json={'kind':'income','version':1}).status_code==409
    outgoing=next(t for t in client.get('/api/bank/transactions').json() if t['amount']==-1000)
    before=dash(client)['cash_flow']
    r=client.put('/api/bank/transactions/'+outgoing['id']+'/classification',json={'kind':'bank_transfer','version':outgoing['version']})
    assert r.status_code==200,r.text
    assert dash(client)['expenses']==2000 and dash(client)['cash_flow']==before
    assert client.delete('/api/payments/'+outgoing['payment_id']+'?version=2').status_code==409


def test_accounts_do_not_share_duplicates(client,monkeypatch):
    a=preview(client,monkeypatch);assert confirm(client,a).status_code==200
    b=preview(client,monkeypatch,statement().replace('999-00-000001-1','999-00-000002-2'))
    assert all(r['status']=='new' for r in b['rows'])
    assert confirm(client,b).status_code==200
    assert dash(client)['expenses']==6000


def test_link_across_financial_boundary_uses_bank_time_for_cash(client,monkeypatch):
    p=post(client,'/purchases',purchase(total=1000,own=600,at='2026-10-22T00:02:00+09:00')).json()
    batch=preview(client,monkeypatch,statement(['1 2026-10-21 [23:59:00] 1,000 0 7,000 GS25']))
    choices=decisions(batch);choices[0].update(action='link',purchase_id=p['id'])
    assert confirm(client,batch,choices).status_code==200
    assert dash(client)['expenses']==600 and dash(client)['cash_flow']==0
    assert dash(client,'2026-09-22')['expenses']==0 and dash(client,'2026-09-22')['cash_flow']==-1000


def test_ambiguous_link_does_not_auto_choose_purchase(client,monkeypatch):
    for _ in range(2):post(client,'/purchases',purchase(total=1000,own=1000,at='2026-10-23T12:02:00+09:00'))
    batch=preview(client,monkeypatch)
    assert len(batch['rows'][0]['purchase_candidates'])==2
    assert client.get('/api/bank/transactions').json()==[]
    assert all(p['version']==1 for p in client.get('/api/purchases').json())


def test_link_conflict_mid_import_is_atomic(client,monkeypatch):
    p=post(client,'/purchases',purchase(total=1000,own=1000,at='2026-10-23T12:02:00+09:00')).json()
    batch=preview(client,monkeypatch);choices=decisions(batch)
    choices[-1].update(action='link',purchase_id=p['id']) # incoming cannot pay for purchase
    r=confirm(client,batch,choices)
    assert r.status_code==422,r.text
    assert len(client.get('/api/payments').json())==1
    assert client.get('/api/bank/transactions').json()==[]
    assert dash(client)['expenses']==1000

def test_changed_money_cannot_be_silently_skipped_as_repeat(client,monkeypatch):
    first=preview(client,monkeypatch);assert confirm(client,first).status_code==200
    # Corrected first amount with matching corrected balance, maintaining chain.
    second=preview(client,monkeypatch,statement().replace('1,000 0 7,000','1,100 0 6,900'))
    choices=decisions(second)
    row=second['rows'][0];assert row['status']=='ambiguous'
    choices[0]={'row_id':row['id'],'action':'repeat','transaction_id':row['candidates'][0]['id']}
    assert confirm(client,second,choices).status_code==422
    assert dash(client)['expenses']==3000

def test_reversed_export_with_renumbering_is_idempotent(client,monkeypatch):
    first=preview(client,monkeypatch);assert confirm(client,first).status_code==200
    reversed_rows=[str(i)+' '+row.split(' ',1)[1] for i,row in enumerate(reversed(ROWS),1)]
    second=preview(client,monkeypatch,statement(reversed_rows))
    assert second['id']==first['id'] and second['confirmed_at']
    assert dash(client)['expenses']==3000
