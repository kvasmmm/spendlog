#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"
repo_name=${1:-SpendLog}
gh_bin=$(command -v gh)
if [[ -x /usr/bin/gh ]]; then gh_bin=/usr/bin/gh; fi
"$gh_bin" auth status
if [[ ! -f .git/config ]]; then git init -b main; fi
# Explicit source allowlist. Do not stage the statement, .env, exports, or DB.
git add -- .gitignore .env.example README.md VERIFICATION.md Caddyfile compose.yaml compose.https.yaml backend frontend scripts .github
if git ls-files -z | python3 -c 'import sys; from pathlib import PurePath; names=sys.stdin.buffer.read().split(b"\0"); bad=[n for n in names if n and (PurePath(n.decode()).suffix.lower() in (".pdf",".db",".csv",".xlsx",".xls",".pem",".key") or PurePath(n.decode()).name==".env")]; sys.exit(bool(bad))'; then
  if ! git diff --cached --quiet; then git commit -m "Add audited bank import, reconciliation and shared barcode catalog"; fi
else
  echo 'Private financial or credential files are tracked. Remove them from the index before publishing.' >&2
  exit 1
fi
if git remote get-url origin >/dev/null 2>&1; then
  echo 'Origin already exists; inspect it before publishing. No remote was changed.' >&2
  exit 1
fi
"$gh_bin" repo create "$repo_name" --private --description 'Personal mobile-first finance tracker for South Korea' --source=. --remote=origin --push
