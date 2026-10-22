"""Run inside backend container; SQLite Online Backup includes WAL safely."""
import os, sqlite3
from pathlib import Path
from datetime import datetime, timezone
url=os.getenv('DATABASE_URL','sqlite:////data/spendlog.db')
if not url.startswith('sqlite:///'):raise SystemExit('Use pg_dump for PostgreSQL.')
source=url.removeprefix('sqlite:///')
folder=Path('/data/backups');folder.mkdir(exist_ok=True)
target=folder/('spendlog-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.db')
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:src.backup(dst)
print(target)
