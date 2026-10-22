"""Bank provenance, explicit confirmation and reversible purchase reconciliation."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePath
from urllib.parse import unquote
from fastapi import Depends, Header, Request
from sqlalchemy import select
from .models import BankImport, BankImportRow, BankTransaction, Payment, Purchase, Merchant
from .schemas import BankConfirm, BankLink, BankClassification, PurchaseIn
from .bank_parser import BankRow, StatementError, extract_pdf, parse_text, duplicate_status


def scoped_transactions(s, batch):
    return s.scalars(select(BankTransaction).where(BankTransaction.bank == batch.bank,
                                                  BankTransaction.account_key == batch.account_key)).all()


def classification(l, kind, amount):
    if kind == 'income' and amount < 0:
        l.fail('Списание не может быть доходом', 422)
    return kind


def purchase_candidates(s, row, window=600):
    stamp = datetime.fromisoformat(row.occurred_at)
    candidates = []
    if row.amount >= 0:
        return candidates
    for p in s.scalars(select(Purchase).where(Purchase.total == -row.amount)):
        seconds = abs((datetime.fromisoformat(p.occurred_at) - stamp).total_seconds())
        if window is not None and seconds > window:
            continue
        payment = s.scalar(select(Payment).where(Payment.purchase_id == p.id))
        if not payment or s.scalar(select(BankTransaction.id).where(BankTransaction.payment_id == payment.id)):
            continue
        name = s.get(Merchant, p.merchant_id).name
        a, b = (re.sub(r'[^\w]', '', v.casefold()) for v in (name, row.merchant))
        match = bool(a and b and (a == b or (min(len(a), len(b)) >= 3 and (a in b or b in a))))
        candidates.append({'id':p.id, 'merchant':name, 'total':p.total, 'own_amount':p.own_amount,
                           'occurred_at':p.occurred_at, 'created_at':p.created_at,
                           'seconds_apart':int(seconds), 'merchant_match':match})
    return sorted(candidates, key=lambda p:(not p['merchant_match'], p['seconds_apart']))


def link_purchase(l, s, transaction, purchase_id):
    bank_payment = l.lock(s, Payment, transaction.payment_id)
    if bank_payment.purchase_id:
        l.fail('Сначала отмените текущую связь', 409)
    purchase = l.lock(s, Purchase, purchase_id)
    if transaction.amount >= 0 or purchase.total != -transaction.amount:
        l.fail('Сумма и направление не соответствуют покупке', 422)
    manual = s.scalar(select(Payment).where(Payment.purchase_id == purchase.id))
    if not manual or s.scalar(select(BankTransaction.id).where(BankTransaction.payment_id == manual.id)):
        l.fail('Покупка уже связана или не имеет платежа', 409)
    # Preserve the existing manual payment and its creation timestamp; the bank
    # keeps its own source time. Only the actual movement time comes from bank.
    transaction.payment_id = manual.id
    transaction.version += 1
    manual.occurred_at = transaction.occurred_at
    manual.version += 1
    purchase.version += 1  # invalidate forms opened before reconciliation
    s.flush()
    s.delete(bank_payment)
    s.flush()


def transaction_view(l, s, tx):
    payment = s.get(Payment, tx.payment_id)
    return {**l.dump(tx), 'kind':payment.kind, 'purchase_id':payment.purchase_id,
            'payment_created_at':payment.created_at,
            'purchase_candidates':purchase_candidates(s, BankRow(0,tx.occurred_at,tx.amount,tx.balance,tx.merchant,''),window=None)
                if not payment.purchase_id else []}


def preview_view(l, s, batch):
    result = []
    for row in s.scalars(select(BankImportRow).where(BankImportRow.import_id == batch.id).order_by(BankImportRow.number)):
        data = BankRow(**json.loads(row.data))
        candidates = [s.get(BankTransaction, id) for id in json.loads(row.candidates)]
        result.append({**l.dump(row), 'data':data.data(),
                       'candidates':[transaction_view(l,s,t) for t in candidates if t],
                       'purchase_candidates':purchase_candidates(s,data) if not batch.confirmed_at else []})
    return {**{k:v for k,v in l.dump(batch).items() if k != 'source_text'}, 'rows':result}


def validate_bank_ledger(s):
    from . import main as l
    for tx in s.scalars(select(BankTransaction)):
        payment = s.get(Payment, tx.payment_id)
        if not payment or payment.amount != tx.amount or payment.occurred_at != tx.occurred_at or payment.debt_id:
            l.fail('Нарушена связь банковского движения',422)
        if tx.bank!='im' or tx.precision!='second' or not tx.amount or tx.version<1:
            l.fail('Некорректные банковские данные',422)
        canonical = datetime.fromisoformat(tx.occurred_at)
        if canonical.utcoffset().total_seconds()!=0 or canonical.isoformat()!=tx.occurred_at:
            l.fail('Некорректное время банка',422)
        if payment.purchase_id and payment.kind!='purchase':l.fail('Некорректная сверка',422)
        if not payment.purchase_id and payment.kind not in ('bank_unclassified','bank_transfer','income'):
            l.fail('Некорректный тип банковского движения',422)
        source_rows = s.scalars(select(BankImportRow).where(BankImportRow.transaction_id==tx.id)).all()
        if not source_rows:l.fail('Нет исходной строки банка',422)
        for source in source_rows:
            batch=s.get(BankImport,source.import_id)
            data=BankRow(**json.loads(source.data))
            if not batch or not batch.confirmed_at or batch.bank!=tx.bank or batch.account_key!=tx.account_key:
                l.fail('Некорректное происхождение банковского движения',422)
            # Explicit repeat decisions can acknowledge corrected descriptions or
            # balances; immutable original transaction is never silently changed.
            if source.decision=='repeat' and (data.amount!=tx.amount or data.occurred_at!=tx.occurred_at or data.precision!=tx.precision):
                l.fail('Неверно подтверждённый банковский повтор',422)
            if source.decision!='repeat' and data.fingerprint()!=tx.fingerprint:
                l.fail('Банковская строка отличается от операции',422)
    for batch in s.scalars(select(BankImport)):
        rows=s.scalars(select(BankImportRow).where(BankImportRow.import_id==batch.id)).all()
        if not rows:l.fail('Пустая история банковского импорта',422)
        for source in rows:
            data=BankRow(**json.loads(source.data))
            if source.number!=data.number or source.status not in ('new','repeat','ambiguous'):
                l.fail('Некорректная строка импорта',422)
            if batch.confirmed_at and (not source.transaction_id or source.decision not in ('new','repeat','link')):
                l.fail('Незавершённая строка подтверждённого импорта',422)
            if not batch.confirmed_at and (source.transaction_id or source.decision):l.fail('Изменённый предпросмотр',422)


def register(app):
    from . import main as l

    @app.post('/api/bank/imports/preview', dependencies=[Depends(l.auth)])
    async def preview(request:Request, s=Depends(l.db,scope='function')):
        raw=await request.body()
        try:
            text=extract_pdf(raw)
            parsed=parse_text(text)
        except (StatementError,ValueError,UnicodeError) as e:
            l.fail(str(e),422)
        existing=s.scalar(select(BankImport).where(BankImport.semantic_hash==parsed['semantic_hash']))
        if existing:
            if not existing.confirmed_at:
                known=[l.dump(t) for t in scoped_transactions(s,existing)]
                for saved in s.scalars(select(BankImportRow).where(BankImportRow.import_id==existing.id)):
                    status,candidates=duplicate_status(BankRow(**json.loads(saved.data)),parsed['rows'],known)
                    saved.status=status;saved.candidates=json.dumps(candidates)
                s.flush()
            return preview_view(l,s,existing)
        rows=parsed.pop('rows')
        batch=BankImport(**parsed,filename=PurePath(unquote(request.headers.get('x-filename','statement.pdf'))).name[:200],
                         file_hash=hashlib.sha256(raw).hexdigest(),source_text=text,created_at=l.now())
        s.add(batch);s.flush()
        known=[l.dump(t) for t in scoped_transactions(s,batch)]
        for data in rows:
            status,candidates=duplicate_status(data,rows,known)
            s.add(BankImportRow(import_id=batch.id,number=data.number,data=json.dumps(data.data(),ensure_ascii=False),
                                status=status,candidates=json.dumps(candidates)))
        s.flush()
        return preview_view(l,s,batch)

    @app.get('/api/bank/imports', dependencies=[Depends(l.auth)])
    def history(s=Depends(l.db,scope='function')):
        return [{k:v for k,v in l.dump(batch).items() if k!='source_text'}
                for batch in s.scalars(select(BankImport).order_by(BankImport.created_at.desc()))]

    @app.get('/api/bank/imports/{id}', dependencies=[Depends(l.auth)])
    def get_import(id:str,s=Depends(l.db,scope='function')):
        return preview_view(l,s,l.lock(s,BankImport,id))

    @app.post('/api/bank/imports/{id}/confirm', dependencies=[Depends(l.auth)])
    def confirm(id:str,data:BankConfirm,idempotency_key:str=Header(),s=Depends(l.db,scope='function')):
        fp,old=l.replay(s,idempotency_key,{'bank_import':id,**data.model_dump(mode='json')})
        if old:return old
        batch=l.lock(s,BankImport,id)
        if batch.confirmed_at:return {'id':batch.id,'confirmed_at':batch.confirmed_at,'already_confirmed':True}
        rows=s.scalars(select(BankImportRow).where(BankImportRow.import_id==id)).all()
        decisions={d.row_id:d for d in data.rows}
        if len(decisions)!=len(data.rows) or set(decisions)!={r.id for r in rows}:
            l.fail('Нужно решить каждую строку ровно один раз',422)
        known=[l.dump(t) for t in scoped_transactions(s,batch)]
        source_data=[BankRow(**json.loads(r.data)) for r in rows]
        # Validate the whole preview against current state BEFORE any writes.
        for row,source in zip(rows,source_data):
            status,candidates=duplicate_status(source,source_data,known)
            if status!=row.status or set(candidates)!=set(json.loads(row.candidates)):
                l.fail('Другой импорт изменил данные. Загрузите PDF заново для нового предпросмотра.',409)
        added=0;repeated=0;linked=0
        used_repeats=set()
        for row,source in zip(rows,source_data):
            decision=decisions[row.id]
            if decision.action=='repeat':
                if not decision.transaction_id or decision.transaction_id not in json.loads(row.candidates):
                    l.fail('Выберите существующую банковскую операцию',422)
                if decision.transaction_id in used_repeats:l.fail('Две строки нельзя пропустить как одну операцию',422)
                used_repeats.add(decision.transaction_id)
                tx=l.lock(s,BankTransaction,decision.transaction_id)
                if tx.bank!=batch.bank or tx.account_key!=batch.account_key:l.fail('Другой банковский счёт',422)
                if tx.amount!=source.amount or tx.occurred_at!=source.occurred_at or tx.precision!=source.precision:
                    l.fail('Изменены сумма, направление или время. Нельзя пропустить строку как повтор; требуется отдельное исправление.',422)
                repeated+=1
            else:
                kind=classification(l,decision.classification,source.amount)
                p=Payment(kind=kind,amount=source.amount,occurred_at=source.occurred_at,note=source.merchant)
                s.add(p);s.flush()
                tx=BankTransaction(bank=batch.bank,account_key=batch.account_key,bank_id=None,
                    fingerprint=source.fingerprint(),occurred_at=source.occurred_at,precision=source.precision,
                    amount=source.amount,balance=source.balance,merchant=source.merchant,expense_category=decision.category,payment_id=p.id,created_at=l.now())
                s.add(tx);s.flush();added+=1
                if decision.action=='link':
                    if not decision.purchase_id:l.fail('Выберите покупку',422)
                    link_purchase(l,s,tx,decision.purchase_id);linked+=1
            row.transaction_id=tx.id;row.decision=decision.action
        batch.confirmed_at=l.now();s.flush()
        validate_bank_ledger(s)
        return l.remember(s,idempotency_key,fp,{'id':id,'confirmed_at':batch.confirmed_at,'added':added,'repeated':repeated,'linked':linked})

    @app.get('/api/bank/transactions', dependencies=[Depends(l.auth)])
    def transactions(s=Depends(l.db,scope='function')):
        return [transaction_view(l,s,tx) for tx in s.scalars(select(BankTransaction).order_by(BankTransaction.occurred_at.desc()))]

    @app.post('/api/bank/transactions/{id}/purchase',dependencies=[Depends(l.auth)])
    def itemize(id:str,data:PurchaseIn,version:int,idempotency_key:str=Header(),s=Depends(l.db,scope='function')):
        fp,old=l.replay(s,idempotency_key,{'bank_purchase':id,'version':version,'purchase':data.model_dump(mode='json')})
        if old:return old
        tx=l.lock(s,BankTransaction,id);l.check_version(tx,version)
        payment=s.get(Payment,tx.payment_id)
        if payment.purchase_id:l.fail('У операции уже есть чек. Откройте его для редактирования.',409)
        if tx.amount>=0 or data.receipt_total!=-tx.amount:l.fail('Сумма позиций должна совпадать с банковским списанием',422)
        result=l.write_purchase(s,data)
        link_purchase(l,s,tx,result['id'])
        return l.remember(s,idempotency_key,fp,l.purchase_view(s,s.get(Purchase,result['id'])))

    @app.post('/api/bank/transactions/{id}/link', dependencies=[Depends(l.auth)])
    def link(id:str,data:BankLink,s=Depends(l.db,scope='function')):
        tx=l.lock(s,BankTransaction,id);l.check_version(tx,data.version)
        link_purchase(l,s,tx,data.purchase_id)
        return transaction_view(l,s,tx)

    @app.post('/api/bank/transactions/{id}/unlink', dependencies=[Depends(l.auth)])
    def unlink(id:str,version:int,s=Depends(l.db,scope='function')):
        tx=l.lock(s,BankTransaction,id);l.check_version(tx,version)
        p=l.lock(s,Payment,tx.payment_id)
        if not p.purchase_id:l.fail('Операция не связана с покупкой',409)
        purchase=l.lock(s,Purchase,p.purchase_id)
        p.occurred_at=purchase.occurred_at;p.version+=1;purchase.version+=1
        new=Payment(kind='bank_unclassified',amount=tx.amount,occurred_at=tx.occurred_at,note=tx.merchant)
        s.add(new);s.flush();tx.payment_id=new.id;tx.version+=1;s.flush()
        return transaction_view(l,s,tx)

    @app.put('/api/bank/transactions/{id}/classification', dependencies=[Depends(l.auth)])
    def classify(id:str,data:BankClassification,s=Depends(l.db,scope='function')):
        tx=l.lock(s,BankTransaction,id);l.check_version(tx,data.version)
        p=l.lock(s,Payment,tx.payment_id)
        if p.purchase_id:l.fail('Категории связанной покупки меняются через чек',409)
        if data.category is not None:tx.expense_category=data.category
        p.kind=classification(l,data.kind,tx.amount);p.version+=1;tx.version+=1;s.flush()
        return transaction_view(l,s,tx)
