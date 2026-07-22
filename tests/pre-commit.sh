#!/bin/bash
# Pre-commit gate for the baton repo (install: cp tests/pre-commit.sh .git/hooks/pre-commit).
# Two mechanical invariants, both from live incidents:
#  - baton.py must pass selfcheck (a broken baton.py is uncommittable)
#  - no key-shaped strings in ANY staged content (T-INFRA-04)

staged=$(git diff --cached --name-only)
repo_root=$(git rev-parse --show-toplevel)

if printf '%s\n' "$staged" | grep -q 'baton\.py'; then
  bash "$repo_root/tests/selfcheck.sh" || {
    echo "pre-commit: baton.py failed selfcheck — commit blocked"; exit 1; }
fi

if git diff --cached | grep -qE 'sk-[a-f0-9]{28,}|scp-live-[a-f0-9]{10,}'; then
  echo "pre-commit: staged diff contains key-shaped strings (T-INFRA-04) — commit blocked"
  exit 1
fi
exit 0
