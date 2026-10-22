"""Managed category names/aliases. Renames never rewrite historical Item snapshots."""
import json
import unicodedata
from uuid import uuid4,uuid5,NAMESPACE_URL
from fastapi import Depends
from sqlalchemy import select
from .models import Setting,Item,Product,Barcode,MenuProduct,BankTransaction,Rule
from .schemas import CategoryIn

DEFAULTS=['Еда вне дома','Магазины у дома','Продукты','Обязательные траты','Страховка','Связь и интернет','Подписки','Здоровье и клиники','Транспорт','Жильё','PC Café','Покупки','Услуги','Готовая еда','Рестораны','Напитки','Молочные продукты','Снеки','Здоровье','Другое']
RESERVED='Банк: без категории'

def key(value):return unicodedata.normalize('NFKC',value).strip().casefold()

def definitions(s):
    saved=s.get(Setting,'categories')
    data=json.loads(saved.value) if saved else [{'id':str(uuid5(NAMESPACE_URL,'spendlog-category:'+key(name))),'name':name,'aliases':[name],'active':True,'version':1} for name in DEFAULTS]
    known={key(alias):row for row in data for alias in row['aliases']}
    observed=set()
    for column in (Item.category,Product.category,Barcode.category,MenuProduct.category,BankTransaction.expense_category,Rule.category):
        observed.update(v for v in s.scalars(select(column).distinct()) if v)
    for name in sorted(observed):
        normalized=key(name)
        if normalized in known:
            if name not in known[normalized]['aliases']:known[normalized]['aliases'].append(name)
        elif name!=RESERVED:
            row={'id':str(uuid5(NAMESPACE_URL,'spendlog-category:'+normalized)),'name':name,'aliases':[name],'active':True,'version':1}
            data.append(row);known[normalized]=row
    return data

def resolver(s):
    if 'category_names' not in s.info:
        s.info['category_names']={key(alias):row['name'] for row in definitions(s) for alias in row['aliases']}
    mapping=s.info['category_names']
    return lambda name:mapping.get(key(name),name)

def save(s,data):
    from . import main as l
    encoded=json.dumps(data,ensure_ascii=False)
    if len(encoded)>90000:l.fail('Справочник категорий слишком большой',422)
    s.merge(Setting(key='categories',value=encoded));s.info.pop('category_names',None)

def validate(s):
    from . import main as l
    saved=s.get(Setting,'categories')
    if not saved:return
    data=json.loads(saved.value)
    if not isinstance(data,list):l.fail('Некорректный справочник категорий',422)
    names={};ids=set()
    for row in data:
        if not isinstance(row,dict) or set(row)!={'id','name','aliases','active','version'}:l.fail('Некорректная категория',422)
        CategoryIn.model_validate({k:row[k] for k in ('name','active','version')})
        if not isinstance(row['id'],str) or not row['id'] or len(row['id'])>36 or row['id'] in ids or not isinstance(row['aliases'],list) or row['name'] not in row['aliases']:l.fail('Некорректный ID/псевдоним категории',422)
        if type(row['version']) is not int or row['version']<1:l.fail('Некорректная версия категории',422)
        ids.add(row['id'])
        for alias in row['aliases']:
            if not isinstance(alias,str) or not alias.strip() or len(alias)>100:l.fail('Некорректное имя категории',422)
            normalized=key(alias)
            if normalized in names and names[normalized]!=row['id']:l.fail('Конфликт имён категорий',422)
            names[normalized]=row['id']

def register(app):
    from . import main as l
    @app.get('/api/categories',dependencies=[Depends(l.auth)])
    def categories(s=Depends(l.db,scope='function')):return definitions(s)

    @app.post('/api/categories',dependencies=[Depends(l.auth)])
    def create(data:CategoryIn,s=Depends(l.db,scope='function')):
        rows=definitions(s)
        if key(data.name)==key(RESERVED) or any(key(data.name)==key(alias) for r in rows for alias in r['aliases']):l.fail('Название уже используется. Удалённую категорию можно восстановить.',409)
        row={'id':str(uuid4()),'name':data.name,'aliases':[data.name],'active':True,'version':1}
        rows.append(row);save(s,rows);return row

    @app.put('/api/categories/{id}',dependencies=[Depends(l.auth)])
    def edit(id:str,data:CategoryIn,s=Depends(l.db,scope='function')):
        rows=definitions(s);row=next((r for r in rows if r['id']==id),None)
        if not row:l.fail('Категория не найдена',404)
        if row['version']!=data.version:l.fail('Категория уже изменена. Обновите список.',409)
        if key(data.name)==key(RESERVED) or any(r['id']!=id and any(key(a)==key(data.name) for a in r['aliases']) for r in rows):l.fail('Название уже используется',409)
        row['name']=data.name;row['active']=data.active;row['version']+=1
        if data.name not in row['aliases']:row['aliases'].append(data.name)
        save(s,rows);return row

    @app.delete('/api/categories/{id}',dependencies=[Depends(l.auth)])
    def remove(id:str,version:int,s=Depends(l.db,scope='function')):
        rows=definitions(s);row=next((r for r in rows if r['id']==id),None)
        if not row:l.fail('Категория не найдена',404)
        if row['version']!=version:l.fail('Категория уже изменена. Обновите список.',409)
        row['active']=False;row['version']+=1;save(s,rows);return {'ok':True}
