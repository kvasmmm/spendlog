import os
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, event, String, Integer, ForeignKey, Text, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

class Base(DeclarativeBase): pass
def uid(): return str(uuid4())
class Barcode(Base):
    __tablename__ = 'barcodes'
    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
class Product(Base):
    __tablename__ = 'products'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    barcode: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
class Merchant(Base):
    __tablename__ = 'merchants'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200), unique=True)
class MenuProduct(Base):
    __tablename__ = 'menu_products'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey('merchants.id'))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    price: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('merchant_id','name',name='uq_menu_merchant_name'), CheckConstraint('price >= 0 AND price <= 1000000000'))
class Purchase(Base):
    __tablename__ = 'purchases'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey('merchants.id'))
    occurred_at: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default='')
    total: Mapped[int] = mapped_column(Integer)
    own_amount: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint('total >= 0 AND own_amount >= 0 AND own_amount <= total'),)
class Item(Base):
    __tablename__ = 'items'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    purchase_id: Mapped[str] = mapped_column(ForeignKey('purchases.id', ondelete='CASCADE'), index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey('products.id'), nullable=True)
    menu_product_id: Mapped[str | None] = mapped_column(ForeignKey('menu_products.id'), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    unit_price: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    own_amount: Mapped[int] = mapped_column(Integer)
    __table_args__ = (CheckConstraint('unit_price >= 0 AND quantity > 0 AND own_amount >= 0 AND own_amount <= unit_price * quantity'),)
class Payment(Base):
    __tablename__ = 'payments'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    purchase_id: Mapped[str | None] = mapped_column(ForeignKey('purchases.id', ondelete='CASCADE'), unique=True, nullable=True)
    debt_id: Mapped[str | None] = mapped_column(ForeignKey('debts.id', ondelete='CASCADE'), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    amount: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[str] = mapped_column(String(32), default=lambda: datetime.now(timezone.utc).isoformat(), server_default='')
    note: Mapped[str] = mapped_column(Text, default='')
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint('amount != 0'),)
class Person(Base):
    __tablename__ = 'people'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(400), unique=True)
    note: Mapped[str] = mapped_column(Text, default='', server_default='')
    version: Mapped[int] = mapped_column(Integer, default=1)
class Debt(Base):
    __tablename__ = 'debts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    purchase_id: Mapped[str | None] = mapped_column(ForeignKey('purchases.id', ondelete='CASCADE'), unique=True, nullable=True)
    person: Mapped[str] = mapped_column(String(200))
    person_id: Mapped[str | None] = mapped_column(ForeignKey('people.id'), nullable=True)
    direction: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(30))
    principal: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[str] = mapped_column(String(32))
    __table_args__ = (CheckConstraint('principal > 0'),)
class Setting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
class Rule(Base):
    __tablename__ = 'rules'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    match: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
class Session(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[str] = mapped_column(String(32))
class RequestKey(Base):
    __tablename__ = 'request_keys'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[str] = mapped_column(Text)

class BankImport(Base):
    __tablename__ = 'bank_imports'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    bank: Mapped[str] = mapped_column(String(30))
    account_key: Mapped[str] = mapped_column(String(64), index=True)
    account_label: Mapped[str] = mapped_column(String(100))
    filename: Mapped[str] = mapped_column(String(200))
    file_hash: Mapped[str] = mapped_column(String(64))
    semantic_hash: Mapped[str] = mapped_column(String(64), unique=True)
    parser_version: Mapped[str] = mapped_column(String(50))
    period_start: Mapped[str] = mapped_column(String(10))
    period_end: Mapped[str] = mapped_column(String(10))
    source_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))
    confirmed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

class BankTransaction(Base):
    __tablename__ = 'bank_transactions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    bank: Mapped[str] = mapped_column(String(30))
    account_key: Mapped[str] = mapped_column(String(64), index=True)
    expense_category: Mapped[str] = mapped_column(String(100),default='',server_default='')
    bank_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    occurred_at: Mapped[str] = mapped_column(String(32), index=True)
    precision: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)
    balance: Mapped[int] = mapped_column(Integer)
    merchant: Mapped[str] = mapped_column(String(200))
    payment_id: Mapped[str] = mapped_column(ForeignKey('payments.id'), unique=True)
    created_at: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (UniqueConstraint('bank','account_key','bank_id', name='uq_bank_transaction_id'),)

class BankImportRow(Base):
    __tablename__ = 'bank_import_rows'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    import_id: Mapped[str] = mapped_column(ForeignKey('bank_imports.id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    data: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    candidates: Mapped[str] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey('bank_transactions.id'), nullable=True)

    __table_args__ = (UniqueConstraint('import_id','number', name='uq_bank_import_row_number'),)

url = os.getenv('DATABASE_URL', 'sqlite:///./spendlog.db')
engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 30} if url.startswith('sqlite') else {})
if url.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def pragmas(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=30000')
SessionLocal = sessionmaker(engine, expire_on_commit=False)
