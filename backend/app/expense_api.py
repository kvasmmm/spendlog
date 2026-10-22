"""Drill-down using the same own-share and Seoul-period rules as dashboard."""
from datetime import date,datetime
from fastapi import Depends
from sqlalchemy import select
from .models import Purchase,Item,Merchant,Payment,BankTransaction
from .category_api import resolver

def register(app):
    from . import main as l
    @app.get('/api/analytics/expenses',dependencies=[Depends(l.auth)])
    def expenses(anchor:date|None=None,category:str|None=None,merchant:str|None=None,s=Depends(l.db,scope='function')):
        start,end=l.period(anchor or datetime.now(l.TZ).date(),l.settings(s).month_start)
        resolve=resolver(s);rows=[]
        def within(stamp):return start<=datetime.fromisoformat(stamp).astimezone(l.TZ).date()<end
        for p in s.scalars(select(Purchase)):
            name=s.get(Merchant,p.merchant_id).name
            if not within(p.occurred_at) or merchant is not None and merchant!=name:continue
            items=[{**l.dump(i),'category_label':resolve(i.category)} for i in s.scalars(select(Item).where(Item.purchase_id==p.id)) if category is None or resolve(i.category)==resolve(category)]
            if not items:continue
            tx=s.scalar(select(BankTransaction).join(Payment,BankTransaction.payment_id==Payment.id).where(Payment.purchase_id==p.id))
            rows.append({'id':p.id,'source':'purchase','merchant':name,'occurred_at':p.occurred_at,'amount':sum(i['own_amount'] for i in items),'paid':p.total,'own_amount':p.own_amount,'purchase':l.purchase_view(s,p),'bank':l.dump(tx) if tx else None,'items':items})
        for tx in s.scalars(select(BankTransaction)):
            payment=s.get(Payment,tx.payment_id)
            name=tx.merchant or 'Банковская операция';label=resolve(tx.expense_category or 'Банк: без категории')
            if payment.purchase_id or payment.kind!='bank_unclassified' or payment.amount>=0 or not within(tx.occurred_at):continue
            if category is not None and resolve(category)!=label or merchant is not None and merchant!=name:continue
            rows.append({'id':tx.id,'source':'bank','merchant':name,'occurred_at':tx.occurred_at,'amount':-payment.amount,'paid':-payment.amount,'own_amount':-payment.amount,'purchase':None,'bank':l.dump(tx),'items':[]})
        rows.sort(key=lambda r:r['occurred_at'],reverse=True)
        return {'start':str(start),'end':str(end),'category':resolve(category) if category is not None else None,'merchant':merchant,'total':sum(r['amount'] for r in rows),'rows':rows}
