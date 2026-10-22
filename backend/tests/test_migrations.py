import os, subprocess, sys
from pathlib import Path
from sqlalchemy import create_engine, inspect

def test_migration_upgrade_check_downgrade(tmp_path):
    root=Path(__file__).resolve().parents[1]
    url='sqlite:///'+str(tmp_path/'migration.db')
    env={**os.environ,'DATABASE_URL':url}
    def run(*args):
        result=subprocess.run([sys.executable,'-m','alembic',*args],cwd=root,env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stdout+result.stderr
    run('upgrade','head')
    engine=create_engine(url)
    assert {'purchases','items','payments','debts','products','merchants','settings','rules','sessions','request_keys'}<=set(inspect(engine).get_table_names())
    engine.dispose()
    run('check')
    run('downgrade','base')
    run('upgrade','head')

def test_upgrade_existing_ledger_preserves_data_and_seeds_shared_barcodes(tmp_path):
    root=Path(__file__).resolve().parents[1]
    url='sqlite:///'+str(tmp_path/'existing.db')
    env={**os.environ,'DATABASE_URL':url}
    def run(*args):
        r=subprocess.run([sys.executable,'-m','alembic',*args],cwd=root,env=env,capture_output=True,text=True)
        assert r.returncode==0,r.stdout+r.stderr
    run('upgrade','da27abfcf7ff')
    import sqlite3
    with sqlite3.connect(tmp_path/'existing.db') as db:
        db.execute("INSERT INTO products VALUES ('product-1','8800000000001','Saved name','Food')")
        db.execute("INSERT INTO payments(id,kind,amount,occurred_at,note,version) VALUES ('payment-1','income',1000,'2026-10-22T00:00:00+00:00','',1)")
    run('upgrade','head');run('check')
    with sqlite3.connect(tmp_path/'existing.db') as db:
        assert db.execute('SELECT barcode,name FROM barcodes').fetchone()==('8800000000001','Saved name')
        assert db.execute('SELECT amount,created_at FROM payments').fetchone()==(1000,'2026-10-22T00:00:00+00:00')
        assert db.execute('SELECT COUNT(*) FROM products').fetchone()[0]==1

def test_menu_migration_preserves_existing_item_foreign_keys(tmp_path):
    import sqlite3
    root=Path(__file__).resolve().parents[1]
    path=tmp_path/'populated-v2.db';env={**os.environ,'DATABASE_URL':'sqlite:///'+str(path)}
    def run(*args):
        result=subprocess.run([sys.executable,'-m','alembic',*args],cwd=root,env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stdout+result.stderr
    run('upgrade','b72f981a2c40')
    with sqlite3.connect(path) as s:
        s.execute('PRAGMA foreign_keys=ON')
        s.execute("INSERT INTO merchants VALUES ('shop','Old shop')")
        s.execute("INSERT INTO products VALUES ('food','8800000000001','Original food','Food')")
        s.execute("INSERT INTO purchases VALUES ('receipt','shop','2026-10-22T00:00:00+00:00','2026-10-22T00:00:00+00:00','',3000,3000,1)")
        s.execute("INSERT INTO items VALUES ('item','receipt','food','Original food','Food',1000,3,3000)")
        s.execute("INSERT INTO payments(id,purchase_id,kind,amount,occurred_at,created_at,note,version) VALUES ('pay','receipt','purchase',-3000,'2026-10-22T00:00:00+00:00','2026-10-22T00:00:00+00:00','',1)")
    run('upgrade','head');run('check')
    with sqlite3.connect(path) as s:
        assert s.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert s.execute('SELECT id,product_id,menu_product_id,quantity,unit_price FROM items').fetchone()==('item','food',None,3,1000)
        assert s.execute('SELECT amount FROM payments').fetchone()[0]==-3000

def test_people_migration_groups_old_names_without_changing_debts(tmp_path):
    import sqlite3
    root=Path(__file__).resolve().parents[1];path=tmp_path/'old-debts.db'
    env={**os.environ,'DATABASE_URL':'sqlite:///'+str(path)}
    def run(*args):
        result=subprocess.run([sys.executable,'-m','alembic',*args],cwd=root,env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stdout+result.stderr
    run('upgrade','c83e102d5a61')
    with sqlite3.connect(path) as s:
        s.execute("INSERT INTO debts(id,person,direction,kind,principal,occurred_at) VALUES ('d1','Друг','receivable','loan',20000,'2026-10-22T00:00:00+00:00')")
        s.execute("INSERT INTO debts(id,person,direction,kind,principal,occurred_at) VALUES ('d2','ДРУГ','payable','loan',5000,'2026-10-22T00:00:00+00:00')")
    run('upgrade','head');run('check')
    with sqlite3.connect(path) as s:
        assert s.execute('SELECT COUNT(*) FROM people').fetchone()[0]==1
        assert s.execute('SELECT COUNT(DISTINCT person_id) FROM debts').fetchone()[0]==1
        assert s.execute('SELECT person,principal FROM debts ORDER BY id').fetchall()==[('Друг',20000),('ДРУГ',5000)]
        assert s.execute('PRAGMA foreign_key_check').fetchall()==[]
