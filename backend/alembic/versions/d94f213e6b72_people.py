"""People with stable identities; preserve debt name snapshots and balances."""
from alembic import op
import sqlalchemy as sa
import unicodedata
from uuid import uuid4
revision='d94f213e6b72'
down_revision='c83e102d5a61'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('people',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('name',sa.String(200),nullable=False),
        sa.Column('name_key',sa.String(400),nullable=False,unique=True),
        sa.Column('note',sa.Text(),nullable=False,server_default=''),
        sa.Column('version',sa.Integer(),nullable=False))
    bind=op.get_bind()
    if bind.dialect.name=='sqlite':op.execute('ALTER TABLE debts ADD COLUMN person_id VARCHAR(36) REFERENCES people(id)')
    else:
        op.add_column('debts',sa.Column('person_id',sa.String(36),nullable=True))
        op.create_foreign_key('fk_debts_person','debts','people',['person_id'],['id'])
    identities={}
    for name, in bind.execute(sa.text('SELECT DISTINCT person FROM debts')):
        normalized=unicodedata.normalize('NFKC',name).strip().casefold()
        if normalized not in identities:
            id=str(uuid4());identities[normalized]=id
            bind.execute(sa.text("INSERT INTO people(id,name,name_key,note,version) VALUES (:id,:name,:key,'',1)"),{'id':id,'name':name.strip(),'key':normalized})
        bind.execute(sa.text('UPDATE debts SET person_id=:id WHERE person=:name'),{'id':identities[normalized],'name':name})

def downgrade():
    bind=op.get_bind()
    # Keep existing debt snapshots; metadata must be exported before destructive rollback.
    if bind.execute(sa.text('SELECT COUNT(*) FROM people')).scalar():raise RuntimeError('Export/restore before downgrade: people metadata would be lost')
    if bind.dialect.name!='sqlite':op.drop_constraint('fk_debts_person','debts',type_='foreignkey')
    op.drop_column('debts','person_id')
    op.drop_table('people')
