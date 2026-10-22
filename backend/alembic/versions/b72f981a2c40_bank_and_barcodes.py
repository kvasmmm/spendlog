"""Shared barcode metadata and auditable bank imports, preserving the ledger."""
from alembic import op
import sqlalchemy as sa

revision = 'b72f981a2c40'
down_revision = 'da27abfcf7ff'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('barcodes',
        sa.Column('barcode', sa.String(64), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('category', sa.String(100), nullable=False))
    op.execute('INSERT INTO barcodes (barcode, name, category) SELECT barcode, name, category FROM products')
    op.add_column('payments', sa.Column('created_at', sa.String(32), nullable=False, server_default=''))
    # Historical creation times are unknown; retain their recorded movement time.
    op.execute('UPDATE payments SET created_at = occurred_at')
    op.create_table('bank_imports',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('bank', sa.String(30), nullable=False),
        sa.Column('account_key', sa.String(64), nullable=False),
        sa.Column('account_label', sa.String(100), nullable=False),
        sa.Column('filename', sa.String(200), nullable=False),
        sa.Column('file_hash', sa.String(64), nullable=False),
        sa.Column('semantic_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('parser_version', sa.String(50), nullable=False),
        sa.Column('period_start', sa.String(10), nullable=False),
        sa.Column('period_end', sa.String(10), nullable=False),
        sa.Column('source_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.String(32), nullable=False),
        sa.Column('confirmed_at', sa.String(32), nullable=True))
    op.create_index('ix_bank_imports_account_key', 'bank_imports', ['account_key'])
    op.create_table('bank_transactions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('bank', sa.String(30), nullable=False),
        sa.Column('account_key', sa.String(64), nullable=False),
        sa.Column('bank_id', sa.String(200), nullable=True),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('occurred_at', sa.String(32), nullable=False),
        sa.Column('precision', sa.String(20), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('balance', sa.Integer(), nullable=False),
        sa.Column('merchant', sa.String(200), nullable=False),
        sa.Column('payment_id', sa.String(36), sa.ForeignKey('payments.id'), nullable=False, unique=True),
        sa.Column('created_at', sa.String(32), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.UniqueConstraint('bank','account_key','bank_id',name='uq_bank_transaction_id'))
    for column in ('account_key', 'fingerprint', 'occurred_at'):
        op.create_index('ix_bank_transactions_' + column, 'bank_transactions', [column])
    op.create_table('bank_import_rows',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('import_id', sa.String(36), sa.ForeignKey('bank_imports.id'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('data', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('candidates', sa.Text(), nullable=False),
        sa.Column('decision', sa.String(20), nullable=True),
        sa.Column('transaction_id', sa.String(36), sa.ForeignKey('bank_transactions.id'), nullable=True),
        sa.UniqueConstraint('import_id','number',name='uq_bank_import_row_number'))
    op.create_index('ix_bank_import_rows_import_id', 'bank_import_rows', ['import_id'])

def downgrade():
    # Downgrading with imported payments would discard their provenance. Refuse it.
    connection = op.get_bind()
    if connection.execute(sa.text('SELECT COUNT(*) FROM bank_imports')).scalar():
        raise RuntimeError('Export/restore before downgrade: bank import history must not be discarded')
    op.drop_table('bank_import_rows')
    op.drop_table('bank_transactions')
    op.drop_table('bank_imports')
    op.drop_column('payments', 'created_at')
    op.drop_table('barcodes')
