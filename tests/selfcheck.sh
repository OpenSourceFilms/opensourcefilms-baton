#!/bin/bash
# BATON self-check — the mechanical acceptance floor (DESIGN §14).
# Runs standalone and as the pre-commit gate. Exit 0 = safe to commit baton.py.
# Checks EFFECTS (valid JSON out, exit 0, budget respected), never just rc.
# No set -e: every check runs; failures accumulate.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BATON="$REPO_ROOT/baton.py"
PY=python3
FAIL=0

say()  { echo "selfcheck: $*"; }
bad()  { echo "selfcheck FAIL: $*"; FAIL=1; }

# 1. Compiles at all
$PY -m py_compile "$BATON" 2>/dev/null || bad "py_compile failed"

# 2. hook: valid JSON + exit 0 on garbage / empty / real-shaped stdin
for input in 'garbage!!!' '' '{"session_id":"sc","transcript_path":"/nonexistent","cwd":"/tmp","hook_event_name":"SessionStart","source":"startup"}'; do
  out=$(printf '%s' "$input" | $PY "$BATON" hook 2>/dev/null); rc=$?
  [ $rc -eq 0 ] || bad "hook exit $rc on stdin=[${input:0:20}]"
  printf '%s' "$out" | $PY -c "import json,sys; json.load(sys.stdin)" 2>/dev/null \
    || bad "hook emitted invalid JSON on stdin=[${input:0:20}]"
done

# 3. pretool: same resilience
for input in 'garbage!!!' '' '{"session_id":"sc","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"ls"}}'; do
  out=$(printf '%s' "$input" | $PY "$BATON" pretool 2>/dev/null); rc=$?
  [ $rc -eq 0 ] || bad "pretool exit $rc on stdin=[${input:0:20}]"
  printf '%s' "$out" | $PY -c "import json,sys; json.load(sys.stdin)" 2>/dev/null \
    || bad "pretool emitted invalid JSON on stdin=[${input:0:20}]"
done

# 4. pickup budget: additionalContext must respect the hard cap (+ JSON overhead margin)
chars=$(printf '{"cwd":"%s"}' "$REPO_ROOT" | $PY "$BATON" hook 2>/dev/null | \
  $PY -c "import json,sys; print(len(json.load(sys.stdin)['hookSpecificOutput'].get('additionalContext','')))" 2>/dev/null)
if [ -n "$chars" ]; then
  [ "$chars" -le 14500 ] || bad "pickup context $chars chars exceeds hard budget"
else
  bad "could not measure pickup size"
fi

# 5. gross-latency floor (catches hangs/blocking regressions, not perf tuning — bar is generous)
t0=$(date +%s%N 2>/dev/null)
printf '{}' | $PY "$BATON" hook >/dev/null 2>&1
t1=$(date +%s%N 2>/dev/null)
if [ -n "$t0" ] && [ -n "$t1" ]; then
  ms=$(( (t1 - t0) / 1000000 ))
  [ "$ms" -le 5000 ] || bad "hook took ${ms}ms (>5s — something is blocking)"
fi

# 6. secret hygiene (T-INFRA-04): no live key material in tracked files
if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  hits=$(git -C "$REPO_ROOT" grep -lE 'sk-[a-f0-9]{28,}|scp-live-[a-f0-9]{10,}' -- . 2>/dev/null)
  [ -z "$hits" ] || bad "tracked files contain key-shaped strings: $hits"
fi

# 7. audit path writes (DESIGN §14): the hook fire in check 4/5 must have appended a record
if [ -f "$REPO_ROOT/logs/audit.jsonl" ]; then
  tail -1 "$REPO_ROOT/logs/audit.jsonl" | $PY -c "import json,sys; json.load(sys.stdin)" 2>/dev/null \
    || bad "audit.jsonl last line is not valid JSON"
else
  bad "audit.jsonl missing after hook fires — audit path broken"
fi

# 8. command probes (GAMEPLAN S2): cache-hit-instant / cache-miss-detached-worker,
# garbage entries degrade to one honest line, legacy forms still parse.
PROBE_TESTDIR=$(mktemp -d)
git init -q "$PROBE_TESTDIR"
mkdir -p "$PROBE_TESTDIR/.baton"
cat > "$PROBE_TESTDIR/.baton/probes.json" <<'EOF'
{"probes": [
  {"name": "sc_echo", "command": "echo baton_selfcheck_probe_ok", "ttl_seconds": 60, "timeout": 5},
  {"name": "sc_garbage", "command": "this_binary_does_not_exist_selfcheck"},
  {"name": "sc_nocmd"}
]}
EOF
out1=$(cd "$PROBE_TESTDIR" && $PY "$BATON" status 2>/dev/null); rc=$?
[ $rc -eq 0 ] || bad "probes status (first fire) exit $rc"
echo "$out1" | grep -q "sc_echo: launched in background" || bad "probes: real command did not report cache-miss launch"
echo "$out1" | grep -q "sc_nocmd: no command configured" || bad "probes: missing-command entry did not degrade to an honest line"
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.5
  out2=$(cd "$PROBE_TESTDIR" && $PY "$BATON" status 2>/dev/null)
  echo "$out2" | grep -q "sc_echo:.*baton_selfcheck_probe_ok" && break
done
echo "$out2" | grep -q "sc_echo:.*baton_selfcheck_probe_ok" || bad "probes: real command never landed a cached value (worker didn't complete)"
echo "$out2" | grep -q "sc_garbage:" || bad "probes: garbage command produced no line at all"
echo "$out2" | grep -qi "sc_garbage:.*(no output\|not found\|error" || bad "probes: garbage command did not degrade to an honest line"
# legacy bare-list form (no 'probes' key) must still parse without error
echo '[{"name":"j","artifact":"nope.txt"}]' > "$PROBE_TESTDIR/.baton/probes.json"
out3=$(cd "$PROBE_TESTDIR" && $PY "$BATON" status 2>/dev/null); rc=$?
[ $rc -eq 0 ] || bad "probes: legacy list form broke status (exit $rc)"
echo "$out3" | grep -q "job j: MISSING" || bad "probes: legacy list form no longer parses jobs"
# cleanup: don't leave this run's probe cache entries lying around
for h in $($PY -c "import hashlib; [print('probe-'+hashlib.sha256(x.encode()).hexdigest()[:8]) for x in ('sc_echo:echo baton_selfcheck_probe_ok','sc_garbage:this_binary_does_not_exist_selfcheck')]"); do
  rm -f "$REPO_ROOT/.cache/${h}.json" "$REPO_ROOT/.cache/${h}.json.tmp"
done
rm -rf "$PROBE_TESTDIR"

# 9. volatile-fact freshness (GAMEPLAN S9): stale VOLATILE line warns; a today-dated line
# and a BATON.md with zero VOLATILE lines both produce no volatile-related output at all
# (invisible when healthy, same bar as every other probe here).
VOL_TESTDIR=$(mktemp -d)
git init -q "$VOL_TESTDIR"
STALE_DATE=$(date -d "-3 days" +%Y-%m-%d 2>/dev/null || date -v-3d +%Y-%m-%d)
TODAY_DATE=$(date +%Y-%m-%d)

printf '## NOW\nVOLATILE (%s): 5K to eyeball = old_export_dir/\n' "$STALE_DATE" > "$VOL_TESTDIR/BATON.md"
out=$(cd "$VOL_TESTDIR" && $PY "$BATON" status 2>/dev/null); rc=$?
[ $rc -eq 0 ] || bad "volatile probe (stale) exit $rc"
echo "$out" | grep -q "WARNING: stale volatile fact (3d old): 5K to eyeball = old_export_dir/" \
  || bad "volatile probe: stale VOLATILE line did not warn"

printf '## NOW\nVOLATILE (%s): 5K to eyeball = new_export_dir/\n' "$TODAY_DATE" > "$VOL_TESTDIR/BATON.md"
out=$(cd "$VOL_TESTDIR" && $PY "$BATON" status 2>/dev/null); rc=$?
[ $rc -eq 0 ] || bad "volatile probe (fresh) exit $rc"
echo "$out" | grep -q "stale volatile fact" && bad "volatile probe: today-dated line produced a warning"

printf '## NOW\nno volatile facts here\n' > "$VOL_TESTDIR/BATON.md"
out=$(cd "$VOL_TESTDIR" && $PY "$BATON" status 2>/dev/null); rc=$?
[ $rc -eq 0 ] || bad "volatile probe (no VOLATILE lines) exit $rc"
echo "$out" | grep -qi "volatile" && bad "volatile probe: zero VOLATILE lines still produced volatile-related output"
rm -rf "$VOL_TESTDIR"

if [ "$FAIL" -eq 0 ]; then say "ALL PASS"; else say "FAILURES ABOVE"; fi
exit $FAIL
