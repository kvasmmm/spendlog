from typing import Annotated, Literal
from datetime import datetime, timezone
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
Money = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
Positive = Annotated[int, Field(strict=True, ge=1, le=1_000_000_000)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
class Timed(Model):
    occurred_at: datetime
    @field_validator('occurred_at')
    @classmethod
    def zoned(cls,v):
        if v.tzinfo is None: raise ValueError('Укажите часовой пояс')
        return v.astimezone(timezone.utc)
class ProductIn(Model):
    barcode: Annotated[str, Field(min_length=1,max_length=64,pattern=r'^[0-9A-Za-z-]+$')]
    name: Name
    category: Name
class ItemIn(Model):
    product_id: str | None = None
    menu_product_id: str | None = None
    name: Name
    category: Name
    unit_price: Money
    quantity: Annotated[int, Field(strict=True,ge=1,le=10000)] = 1
class PurchaseIn(Timed):
    merchant: Name
    note: Annotated[str, Field(max_length=2000)] = ''
    items: Annotated[list[ItemIn], Field(min_length=1,max_length=200)]
    receipt_total: Money
    own_amount: Money
    person: str = ''
    debt_kind: Literal['friend','reimbursement'] = 'friend'
    version: int | None = None
    @model_validator(mode='after')
    def amounts(self):
        if any(i.product_id and i.menu_product_id for i in self.items):raise ValueError('Позиция не может быть одновременно товаром и блюдом меню')
        total=sum(i.unit_price*i.quantity for i in self.items)
        if total != self.receipt_total: raise ValueError('Сумма позиций не совпадает с чеком')
        if self.own_amount>total: raise ValueError('Моя доля превышает сумму')
        if total>self.own_amount and not self.person.strip(): raise ValueError('Укажите должника или организацию')
        if len(self.person)>200: raise ValueError('Слишком длинное имя')
        return self
class PaymentIn(Timed):
    kind: Literal['income','loan_given','loan_received']
    amount: Positive
    note: Annotated[str,Field(max_length=2000)] = ''
    person: str = ''
    version: int | None = None
    @model_validator(mode='after')
    def person_required(self):
        if self.kind!='income' and not self.person.strip(): raise ValueError('Укажите человека')
        if len(self.person)>200: raise ValueError('Слишком длинное имя')
        return self
class SettlementIn(Timed):
    amount: Positive
    note: Annotated[str,Field(max_length=2000)] = ''
class SettingsIn(Model):
    month_start: Annotated[int,Field(strict=True,ge=1,le=28)] = 22
    monthly_limit: Money = 650000
    category_limits: dict[str, Money] = {}
class RuleIn(Model):
    match: Name
    category: Name

class BankDecision(Model):
    row_id: str
    action: Literal['new','repeat','link']
    transaction_id: str | None = None
    purchase_id: str | None = None
    classification: Literal['bank_unclassified','bank_transfer','income'] = 'bank_unclassified'
    category: Annotated[str,Field(max_length=100)] = ''

class BankConfirm(Model):
    rows: Annotated[list[BankDecision], Field(min_length=1,max_length=10000)]

class BankLink(Model):
    purchase_id: str
    version: Annotated[int, Field(strict=True,ge=1)]

class MenuProductIn(Model):
    merchant: Name
    name: Name
    category: Annotated[str,Field(min_length=1,max_length=100)] = 'Еда вне дома'
    price: Money
    version: int | None = None

class BankClassification(Model):
    kind: Literal['bank_unclassified','bank_transfer','income']
    version: Annotated[int, Field(strict=True,ge=1)]

    category: Annotated[str,Field(max_length=100)] | None = None

class CategoryIn(Model):
    name: Annotated[str,Field(min_length=1,max_length=100)]
    active: Annotated[bool,Field(strict=True)] = True
    version: Annotated[int,Field(strict=True,ge=1)] | None = None

class PersonIn(Model):
    name: Name
    note: Annotated[str,Field(max_length=2000)] = ''
    version: Annotated[int,Field(strict=True,ge=1)] | None = None
