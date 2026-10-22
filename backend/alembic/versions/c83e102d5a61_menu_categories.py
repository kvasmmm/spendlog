"""Reusable restaurant menus and bank expense categories; preserve old items."""
from alembic import op
import sqlalchemy as sa
revision='c83e102d5a61'
down_revision='b72f981a2c40'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('menu_products',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('merchant_id',sa.String(36),sa.ForeignKey('merchants.id'),nullable=False),
        sa.Column('name',sa.String(200),nullable=False),
        sa.Column('category',sa.String(100),nullable=False),
        sa.Column('price',sa.Integer(),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.UniqueConstraint('merchant_id','name',name='uq_menu_merchant_name'),
        sa.CheckConstraint('price >= 0 AND price <= 1000000000'))
    if op.get_bind().dialect.name=='sqlite':
        # Native ADD COLUMN avoids rebuilding items/purchases with live FK links.
        op.execute('ALTER TABLE items ADD COLUMN menu_product_id VARCHAR(36) REFERENCES menu_products(id)')
    else:
        op.add_column('items',sa.Column('menu_product_id',sa.String(36),nullable=True))
        op.create_foreign_key('fk_items_menu_product','items','menu_products',['menu_product_id'],['id'])
    op.add_column('bank_transactions',sa.Column('expense_category',sa.String(100),nullable=False,server_default=''))

def downgrade():
    bind=op.get_bind()
    if bind.execute(sa.text('SELECT COUNT(*) FROM menu_products')).scalar() or bind.execute(sa.text("SELECT COUNT(*) FROM bank_transactions WHERE expense_category != ''")).scalar():
        raise RuntimeError('Export/restore before downgrade: menu/category data would be lost')
    if bind.dialect.name!='sqlite':op.drop_constraint('fk_items_menu_product','items',type_='foreignkey')
    op.drop_column('items','menu_product_id')
    op.drop_column('bank_transactions','expense_category')
    op.drop_table('menu_products')
