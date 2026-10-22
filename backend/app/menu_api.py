"""Private restaurant menus and item-level merchant analytics."""
from collections import defaultdict
from datetime import date, datetime
from fastapi import Depends
from sqlalchemy import select
from .models import MenuProduct, Merchant, Item, Purchase, BankTransaction, Payment
from .schemas import MenuProductIn


def register(app):
    from . import main as l

    def view(s, menu):
        latest=s.scalar(select(Item.unit_price).join(Purchase,Item.purchase_id==Purchase.id).where(Item.menu_product_id==menu.id).order_by(Purchase.occurred_at.desc(),Purchase.created_at.desc()).limit(1))
        return {**l.dump(menu),'merchant':s.get(Merchant,menu.merchant_id).name,'last_price':latest}

    @app.get('/api/menu-products',dependencies=[Depends(l.auth)])
    def menu_products(s=Depends(l.db,scope='function')):
        return [view(s,m) for m in s.scalars(select(MenuProduct).order_by(MenuProduct.name))]

    @app.post('/api/menu-products',dependencies=[Depends(l.auth)])
    def create(data:MenuProductIn,s=Depends(l.db,scope='function')):
        merchant=l.merchant(s,data.merchant)
        existing=s.scalar(select(MenuProduct).where(MenuProduct.merchant_id==merchant.id,MenuProduct.name==data.name))
        if existing:
            if existing.price!=data.price or existing.category!=data.category:l.fail('Такое блюдо уже есть. Измените его карточку.',409)
            return view(s,existing)
        menu=MenuProduct(merchant_id=merchant.id,name=data.name,category=data.category,price=data.price)
        s.add(menu);s.flush();return view(s,menu)

    @app.put('/api/menu-products/{id}',dependencies=[Depends(l.auth)])
    def edit(id:str,data:MenuProductIn,s=Depends(l.db,scope='function')):
        menu=l.lock(s,MenuProduct,id);l.check_version(menu,data.version)
        merchant=l.merchant(s,data.merchant)
        if menu.merchant_id!=merchant.id and s.scalar(select(Item.id).where(Item.menu_product_id==id)):
            l.fail('У блюда есть история. Для другого заведения создайте отдельную карточку.',409)
        menu.merchant_id=merchant.id;menu.name=data.name;menu.category=data.category;menu.price=data.price;menu.version+=1
        s.flush();return view(s,menu)

    @app.get('/api/merchants/analytics',dependencies=[Depends(l.auth)])
    def analytics(anchor:date|None=None,s=Depends(l.db,scope='function')):
        start,end=l.period(anchor or datetime.now(l.TZ).date(),l.settings(s).month_start)
        shops={}
        def shop(name):
            if name not in shops:shops[name]={'merchant':name,'own_amount':0,'total':0,'unitemized':0,'purchase_count':0,'items':{}}
            return shops[name]
        def within(value):return start<=datetime.fromisoformat(value).astimezone(l.TZ).date()<end
        for purchase in s.scalars(select(Purchase)):
            if not within(purchase.occurred_at):continue
            data=shop(s.get(Merchant,purchase.merchant_id).name)
            data['own_amount']+=purchase.own_amount;data['total']+=purchase.total;data['purchase_count']+=1
            for item in s.scalars(select(Item).where(Item.purchase_id==purchase.id)):
                key=item.menu_product_id or item.product_id or (item.name,item.category)
                if key not in data['items']:data['items'][key]={'name':item.name,'category':item.category,'quantity':0,'own_amount':0,'total':0}
                row=data['items'][key];row['quantity']+=item.quantity;row['own_amount']+=item.own_amount;row['total']+=item.unit_price*item.quantity
        for tx in s.scalars(select(BankTransaction)):
            payment=s.get(Payment,tx.payment_id)
            if payment.purchase_id or payment.kind!='bank_unclassified' or tx.amount>=0 or not within(tx.occurred_at):continue
            data=shop(tx.merchant or 'Без описания')
            data['unitemized']-=tx.amount;data['own_amount']-=tx.amount;data['total']-=tx.amount
        return {'start':str(start),'end':str(end),'merchants':[{**data,'items':sorted(data['items'].values(),key=lambda r:-r['quantity'])} for data in sorted(shops.values(),key=lambda d:-d['own_amount'])]}
