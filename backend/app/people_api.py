"""Stable people identities, independently of debt name snapshots."""
import unicodedata
from fastapi import Depends
from sqlalchemy import select
from .models import Person,Debt
from .schemas import PersonIn

def name_key(name):return unicodedata.normalize('NFKC',name).strip().casefold()
def ensure_person(s,name):
    person=s.scalar(select(Person).where(Person.name_key==name_key(name)))
    if not person:
        aliases={d.person_id for d in s.scalars(select(Debt).where(Debt.person_id.is_not(None))) if name_key(d.person)==name_key(name)}
        if len(aliases)==1:person=s.get(Person,next(iter(aliases)))
        elif len(aliases)>1:
            from . import main as l
            l.fail('Имя встречается у разных людей. Выберите текущее имя из карточки.',409)
    if not person:
        person=Person(name=name.strip(),name_key=name_key(name));s.add(person);s.flush()
    return person

def display_name(s,debt):
    person=s.get(Person,debt.person_id) if debt.person_id else None
    return person.name if person else debt.person

def register(app):
    from . import main as l
    def view(s,person):
        debts=[l.debt_view(s,d) for d in s.scalars(select(Debt).where(Debt.person_id==person.id))]
        return {**l.dump(person),'receivable':sum(d['remaining'] for d in debts if d['direction']=='receivable'),'payable':sum(d['remaining'] for d in debts if d['direction']=='payable'),'debt_count':len(debts)}
    @app.get('/api/people',dependencies=[Depends(l.auth)])
    def people(s=Depends(l.db,scope='function')):
        return [view(s,p) for p in s.scalars(select(Person).order_by(Person.name))]
    @app.post('/api/people',dependencies=[Depends(l.auth)])
    def create(data:PersonIn,s=Depends(l.db,scope='function')):
        if s.scalar(select(Person.id).where(Person.name_key==name_key(data.name))):l.fail('Такой человек уже есть',409)
        p=Person(name=data.name,name_key=name_key(data.name),note=data.note);s.add(p);s.flush();return view(s,p)
    @app.put('/api/people/{id}',dependencies=[Depends(l.auth)])
    def edit(id:str,data:PersonIn,s=Depends(l.db,scope='function')):
        p=l.lock(s,Person,id);l.check_version(p,data.version)
        existing=s.scalar(select(Person.id).where(Person.name_key==name_key(data.name),Person.id!=id))
        if existing:l.fail('Такой человек уже есть',409)
        p.name=data.name;p.name_key=name_key(data.name);p.note=data.note;p.version+=1;s.flush();return view(s,p)
    @app.delete('/api/people/{id}',dependencies=[Depends(l.auth)])
    def delete(id:str,version:int,s=Depends(l.db,scope='function')):
        p=l.lock(s,Person,id);l.check_version(p,version)
        if s.scalar(select(Debt.id).where(Debt.person_id==id)):l.fail('Есть история долгов. Человек сохраняется вместе с ней.',409)
        s.delete(p);return {'ok':True}
