#!/usr/bin/env python3
"""baton — session continuity across compact/clear/restart/pod/model switches.

Single stdlib-only file. Spec: DESIGN.md (v3.2, binding: sec 13). Subcommands:
  pickup (default) status now trap block hook pretool recall

Law of the design (DESIGN.md sec 2): a success signal is not evidence of work,
only an effect is. Every probe here exercises the thing it checks; every
hook path degrades to empty output on failure, never to wrong output, and
never blocks.
"""

import sys
import os
import re
import json
import glob
import time
import fnmatch
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

BATON_HOME = Path(__file__).resolve().parent
TRAPS_PATH = BATON_HOME / "traps.jsonl"

TIER0_MAX_LINES = 40
TIER1_MAX_LINES = 120
INTERRUPTED_MAX_CHARS = 2000 * 4          # ~2k tokens, rough 4-chars/token
PICKUP_HARD_MAX_CHARS = 3500 * 4          # ~3.5k tokens hard ceiling, safety net
META_TRAP_MAX_CHARS = 300

VENV_SEARCH_ROOTS_GLOBAL = [
    # Add glob patterns here for any shared venv directories outside individual
    # project roots (e.g. a pod-wide venvs/ dir used across multiple projects).
    # Empty by default — project-local <root>/venvs/* and <root>/.venv are
    # always scanned regardless of this list.
]

REDACTION_PATTERNS = [
    # Redact credential-bearing URLs before matching individual tokens. A raw
    # tool argument can include user:password@host without a named secret key.
    re.compile(r"(?i)\b(?:https?|ftp|sftp)://[^\s/@:?#]+:[^\s/@?#]+@[^\s/?#]+(?:[^\s]*)?"),
    # Common bare access tokens often appear without 'token=' or 'Bearer'.
    # Do not retain the prefix or partial token in injected session context.
    re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{35,}|gh[pousr]_[A-Za-z0-9]{25,}|hf_[A-Za-z0-9]{24,})\b"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|secret|token|password|passwd)\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.]{6,}[\"']?"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.]{8,}"),
]


# ── Generic safety helpers ──────────────────────────────────────────────────

def eprint(*a, **kw):
    print(*a, file=sys.stderr, **kw)


_SWALLOWED = [0]  # count of exceptions safe() ate this invocation — audited, never shown in-loop


def safe(fn, default=None):
    """Run fn() and swallow any exception, returning default. Used pervasively
    so one broken probe can never take down the whole pickup (DESIGN sec 2:
    degrades to nothing, never blocks). Swallows are COUNTED (DESIGN sec 14):
    invisible in-loop, visible post-hoc via the audit log."""
    try:
        return fn()
    except Exception:
        _SWALLOWED[0] += 1
        return default


# ── Audit trail (DESIGN sec 14) — append-only, size-rotated, never blocks ────

AUDIT_LOG = None  # set after BATON_HOME exists
AUDIT_MAX_BYTES = 5 * 1024 * 1024


def audit_write(rec):
    """One JSONL line per audited event. Failure to audit must never affect
    the session — the whole write is safe()-wrapped, and audit failures are
    themselves detectable post-hoc (a gap in the log IS the signal)."""
    def _w():
        log = BATON_HOME / "logs" / "audit.jsonl"
        log.parent.mkdir(exist_ok=True)
        if log.is_file() and log.stat().st_size > AUDIT_MAX_BYTES:
            os.replace(log, log.parent / "audit.jsonl.1")  # keep exactly one rotation
        rec["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(log, "a") as f:
            f.write(json.dumps(rec) + "\n")
    safe(_w)


def redact(text):
    if not text:
        return text
    for pat in REDACTION_PATTERNS:
        text = pat.sub("<redacted>", text)
    return text


def run(cmd, cwd=None, timeout=6):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception:
        return None


def clamp_lines(text, max_lines, note="truncated"):
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    kept = lines[:max_lines]
    return "\n".join(kept) + f"\n… [{note}: {len(lines) - max_lines} more lines, hard capped]"


def clamp_chars(text, max_chars, note="truncated"):
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n… [{note}: {len(text) - max_chars} more chars, hard capped]"


# ── Project resolution ──────────────────────────────────────────────────────

def find_project_root(cwd):
    r = safe(lambda: run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, timeout=5))
    if r and r[0] == 0 and r[1]:
        return r[1]
    return cwd


def project_name(root):
    return os.path.basename(os.path.normpath(root)) or "unknown"


# ── Generic disk cache for slow-but-important probes ────────────────────────
# DESIGN sec 4: "cached probes always print their age" — a stale value shown
# without its timestamp is exactly the confident lie this system exists to
# kill. Never hide staleness; just don't pay the cost every single hook fire.

CACHE_DIR = BATON_HOME / ".cache"


def cached_probe(name, ttl_seconds, fn):
    cache_file = CACHE_DIR / f"{name}.json"
    now = time.time()
    cached = None
    if cache_file.is_file():
        cached = safe(lambda: json.loads(cache_file.read_text()))
    if cached and (now - cached.get("ts", 0)) < ttl_seconds:
        age = int(now - cached["ts"])
        return cached.get("value"), age
    value = fn()
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps({"ts": now, "value": value}))
    except Exception:
        pass
    return value, 0


# ── Tier 0: git probe ───────────────────────────────────────────────────────

def probe_git(root):
    r = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, timeout=5)
    if not r or r[0] != 0:
        return ["git: not a repo"]
    # One porcelain-v2 call carries branch, upstream, ahead/behind, and dirty
    # state together — cuts what used to be 4 separate git spawns to 2, since
    # subprocess spawn overhead (not git itself) was the real cost here.
    status = safe(lambda: run(["git", "status", "--porcelain=v2", "--branch"], cwd=root, timeout=5))
    stash = safe(lambda: run(["git", "stash", "list"], cwd=root, timeout=5))

    branch_s, sha_s, ahead_behind, dirty_n = "?", "?", "", 0
    if status and status[0] == 0 and status[1]:
        for line in status[1].splitlines():
            if line.startswith("# branch.head "):
                branch_s = line.split(" ", 2)[2]
            elif line.startswith("# branch.oid "):
                oid = line.split(" ", 2)[2]
                sha_s = "no commits yet" if oid.startswith("(") else oid[:8]
            elif line.startswith("# branch.ab "):
                parts = line.split()
                ahead = parts[2].lstrip("+") if len(parts) > 2 else "0"
                behind = parts[3].lstrip("-") if len(parts) > 3 else "0"
                if ahead != "0" or behind != "0":
                    ahead_behind = f", ahead {ahead}/behind {behind}"
            elif not line.startswith("#"):
                dirty_n += 1

    stash_n = len(stash[1].splitlines()) if stash and stash[0] == 0 and stash[1] else 0
    line = f"git: {branch_s}@{sha_s}"
    line += ", clean" if dirty_n == 0 else f", DIRTY ({dirty_n} paths)"
    if stash_n:
        line += f", {stash_n} stash(es)"
    line += ahead_behind
    return [line]


# ── Tier 0: venv drift probe ────────────────────────────────────────────────

def _parse_pyvenv_cfg(path):
    d = {}
    try:
        for line in Path(path).read_text(errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    except Exception:
        pass
    return d


VENV_PROBE_CACHE_FILE = "venvs.json"   # under CACHE_DIR
VENV_PROBE_TTL = 900                   # seconds; venv interpreters change only on intentional rebuilds


def _venv_probe_cache_load():
    f = CACHE_DIR / VENV_PROBE_CACHE_FILE
    if not f.is_file():
        return {}
    return safe(lambda: json.loads(f.read_text()), {}) or {}


def _venv_probe_cache_write(cache):
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        tmp = CACHE_DIR / f"{VENV_PROBE_CACHE_FILE}.tmp"
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, CACHE_DIR / VENV_PROBE_CACHE_FILE)
    except Exception:
        pass


def probe_venvs(root):
    """GAMEPLAN Phase 2b S4: a real `python --version` subprocess spawn per venv is the true
    op (DESIGN sec 2), but its result changes only when pyvenv.cfg itself changes (a venv
    rebuild) — safe to cache keyed on that file's mtime, short-circuiting the respawn on every
    other warm hook fire. Age is always shown when any entry is served from cache (DESIGN sec 4:
    never hide staleness)."""
    candidates = []
    for pat in [os.path.join(root, "venvs", "*"), os.path.join(root, ".venv")]:
        candidates.extend(glob.glob(pat))
    for pat in VENV_SEARCH_ROOTS_GLOBAL:
        candidates.extend(glob.glob(pat))

    cache = _venv_probe_cache_load()
    now = time.time()
    checked = 0
    drifted = []
    seen = set()
    cache_hits = 0
    oldest_hit_age = 0
    cache_dirty = False
    for venv_dir in candidates:
        if venv_dir in seen or not os.path.isdir(venv_dir):
            continue
        seen.add(venv_dir)
        cfg = os.path.join(venv_dir, "pyvenv.cfg")
        if not os.path.isfile(cfg):
            continue
        checked += 1
        recorded = _parse_pyvenv_cfg(cfg).get("version", "")
        cfg_mtime = safe(lambda: os.path.getmtime(cfg), 0)
        entry = cache.get(venv_dir)
        if entry and entry.get("pyvenv_mtime") == cfg_mtime and (now - entry.get("ts", 0)) < VENV_PROBE_TTL:
            actual = entry.get("actual", "")
            age = int(now - entry["ts"])
            cache_hits += 1
            oldest_hit_age = max(oldest_hit_age, age)
        else:
            py = os.path.join(venv_dir, "bin", "python3")
            if not os.path.exists(py):
                py = os.path.join(venv_dir, "bin", "python")
            r = safe(lambda: run([py, "--version"], timeout=5))
            actual = ""
            if r and r[0] == 0:
                actual = (r[1] or r[2]).replace("Python ", "").strip()
            cache[venv_dir] = {"pyvenv_mtime": cfg_mtime, "actual": actual, "ts": now}
            cache_dirty = True
        if not actual:
            drifted.append(f"{os.path.basename(venv_dir)}: interpreter missing/broken")
            continue
        rec_mm = ".".join(recorded.split(".")[:2])
        act_mm = ".".join(actual.split(".")[:2])
        if rec_mm and act_mm and rec_mm != act_mm:
            drifted.append(f"{os.path.basename(venv_dir)}: recorded {recorded} but live interpreter is {actual} — DRIFT")

    if cache_dirty:
        safe(lambda: _venv_probe_cache_write({k: v for k, v in cache.items() if k in seen}))

    if checked == 0:
        return []
    age_note = f" ({cache_hits}/{checked} cached, oldest {oldest_hit_age}s ago)" if cache_hits else ""
    if drifted:
        return [f"venvs: {checked} checked, {len(drifted)} DRIFTED:{age_note}"] + [f"  - {d}" for d in drifted]
    return [f"venvs: {checked} checked, all match pyvenv.cfg{age_note}"]


# ── Tier 0: BATON.md path linter ─────────────────────────────────────────────
# Feedback 2026-07-17 (a consuming session): stale absolute paths in BATON.md
# are the biggest live failure mode — an injection asserted a path under the
# wrong project root and it was relayed as fact until a tool call failed
# against it. DESIGN sec 2: machine-observable facts must not be trusted as
# prose. ls-verify every path-shaped span mechanically, same as git/venvs.

_PATH_SPAN_RE = re.compile(r"`([^`]{1,200})`")


def extract_candidate_paths(text):
    """Path-shaped backtick spans worth ls-verifying: single-token (no spaces
    — excludes shell commands like `cp a b`), absolute/home-relative (starts
    with /, ./, or ~/), multi-segment (excludes slash-commands like `/clear`,
    which have exactly one `/`), and free of glob metacharacters (a `*.json`
    pattern isn't a literal path to check).

    Uses its own backtick regex rather than extract_backtick_commands: markdown
    soft-wraps long lines, so a backtick span can contain an embedded newline.
    Excluding \\n from the content class (as extract_backtick_commands does,
    correctly, for its own NOW-parsing use) makes the regex mis-pair that
    span's closing backtick with a *later* same-line backtick, silently
    swallowing the next, unwrapped path span too. Here we allow the newline
    and whitespace-normalize the span instead, so a wrapped multi-word command
    still correctly fails the single-token check rather than corrupting the
    next span's extraction."""
    candidates = []
    for raw in _PATH_SPAN_RE.findall(text or ""):
        p = " ".join(raw.split())
        if not p or " " in p:
            continue
        if not (p.startswith("/") or p.startswith("./") or p.startswith("~/")):
            continue
        if any(ch in p for ch in "*?[]"):
            continue
        if p.count("/") < 2:
            continue
        candidates.append(p)
    return candidates


def probe_paths(root):
    baton_md = os.path.join(root, "BATON.md")
    if not os.path.isfile(baton_md):
        return []
    text = safe(lambda: Path(baton_md).read_text(errors="replace"), "")
    if not text:
        return []
    candidates = sorted(set(extract_candidate_paths(text)))
    if not candidates:
        return []
    missing = [p for p in candidates if not safe(lambda p=p: os.path.exists(os.path.expanduser(p)), False)]
    if not missing:
        return [f"paths: {len(candidates)} checked, all exist"]
    shown = missing[:10]
    lines = [f"paths: {len(candidates)} checked, {len(missing)} MISSING"]
    lines += [f"  WARNING: stale path in BATON.md: {p}" for p in shown]
    if len(missing) > 10:
        lines.append(f"  … {len(missing) - 10} more missing paths, see BATON.md")
    return lines


# ── Tier 0: BATON.md volatile-fact freshness (GAMEPLAN Phase 2b S9) ─────────
# Pointer-style NOW/IN-FLIGHT lines churn far faster than MISSION/TRAPS and go
# stale within days — a live NOW line named a file a later same-project memory
# showed was superseded. DESIGN's "enforcement not
# goodwill" (sec 2, sec 14.1) rules out mandating updates at session-close;
# instead a project opts a fact into `VOLATILE (YYYY-MM-DD): <fact>` and this
# probe ages it mechanically. Pure text-parse + date diff, no subprocess — a
# real op isn't required when there's no I/O to fake (DESIGN sec 2 is about
# not faking effects, not about mandating a syscall that doesn't exist here).

_VOLATILE_RE = re.compile(r"VOLATILE\s*\((\d{4}-\d{2}-\d{2})\)\s*:\s*(.+)")
VOLATILE_DEFAULT_THRESHOLD_DAYS = 2


def probe_volatile(root):
    baton_md = os.path.join(root, "BATON.md")
    if not os.path.isfile(baton_md):
        return []
    text = safe(lambda: Path(baton_md).read_text(errors="replace"), "")
    if not text:
        return []
    cfg = safe(lambda: read_probes_config(root), {}) or {}
    threshold = cfg.get("volatile_threshold_days", VOLATILE_DEFAULT_THRESHOLD_DAYS)
    today = datetime.now().date()
    lines = []
    for raw_date, fact in _VOLATILE_RE.findall(text):
        d = safe(lambda raw_date=raw_date: datetime.strptime(raw_date, "%Y-%m-%d").date())
        if d is None:
            continue
        age = (today - d).days
        if age > threshold:
            lines.append(f"WARNING: stale volatile fact ({age}d old): {fact.strip()}")
    return lines


# ── Tier 0: GPU real-op probe ───────────────────────────────────────────────
# Measured: torch import alone costs ~1.8s in this pod's system python3 —
# enough on its own to blow the "<1s warm" hook budget. Real op stays real
# (never torch.cuda.is_available() — DESIGN sec 2 names that exact lie), it
# just doesn't re-pay the import cost on every single hook fire.

GPU_PROBE_TTL = 300  # seconds


def _probe_gpu_uncached(python_exe, label, timeout=15):
    code = "import torch; x=torch.randn(4,4).to('cuda'); print('OK', float(x.sum()))"
    r = safe(lambda: subprocess.run([python_exe, "-c", code], capture_output=True, text=True, timeout=timeout))
    if r is None:
        return "GPU: probe crashed/timed out"
    if r.returncode == 0 and "OK" in (r.stdout or ""):
        return f"GPU: real op OK (torch.randn(4,4).to('cuda'), {label})"
    combined = (r.stderr or r.stdout or "").strip()
    if "No module named 'torch'" in combined:
        return (f"GPU: torch not importable from {label} — GPU state UNKNOWN, not known-bad "
                "(set gpu_python in .baton/probes.json to a venv python with a working torch)")
    # Arch/kernel mismatch = the PROBE's torch build can't drive this GPU; says nothing about
    # the GPU itself. Reporting it as a GPU failure is a Tier-0 confident lie (found live:
    # system torch 2.4.1+cu124 lacks sm_120 kernels on a Blackwell pod, 2026-07-15).
    mismatch_sigs = ("no kernel image", "sm_", "not compatible with the current PyTorch",
                     "CUDA capability", "too old for this")
    if any(s in combined for s in mismatch_sigs):
        first_err = combined.splitlines()[-1] if combined else ""
        return (f"GPU: probe INCONCLUSIVE — {label}'s torch build lacks this GPU's arch "
                f"({redact(first_err)[:100]}); GPU NOT known bad — set gpu_python in .baton/probes.json")
    first_err = combined.splitlines()[-1] if combined else "unknown error"
    return f"GPU: FAILED real op ({label}) — {redact(first_err)[:150]}"


def read_probes_config(root):
    """<root>/.baton/probes.json — either the legacy bare list of job entries, or an object
    {"gpu_python": "/abs/path/to/python", "jobs": [ ...same entries... ],
     "probes": [{"name": str, "command": str, "ttl_seconds": int (optional), "timeout": int (optional)}]}."""
    cfg_path = os.path.join(root, ".baton", "probes.json")
    if not os.path.isfile(cfg_path):
        return {}
    cfg = safe(lambda: json.loads(Path(cfg_path).read_text()))
    if isinstance(cfg, list):
        return {"jobs": cfg}
    if isinstance(cfg, dict):
        return cfg
    return {"_invalid": True}


GPU_VENV_PROBE_TTL = 3600      # heavy venv result changes rarely; don't re-pay a ~30s cold import
GPU_VENV_PENDING_GRACE = 120   # respawn a background probe only if the last one looks dead


def _gpu_cache_write(key, value, pending=False):
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        tmp = CACHE_DIR / f"{key}.json.tmp"
        tmp.write_text(json.dumps({"ts": time.time(), "value": value, "pending": pending}))
        os.replace(tmp, CACHE_DIR / f"{key}.json")
    except Exception:
        pass


def probe_gpu(root):
    cfg = safe(lambda: read_probes_config(root), {}) or {}
    python_exe, label = sys.executable, "system python3"
    missing_note = ""
    gp = cfg.get("gpu_python")
    if gp:
        if os.path.isfile(gp) and os.access(gp, os.X_OK):
            python_exe, label = gp, f"gpu_python={gp}"
        else:
            missing_note = f" (configured gpu_python missing: {gp} — fell back to system python3)"
    if python_exe == sys.executable:
        value, age = cached_probe("gpu", GPU_PROBE_TTL, lambda: _probe_gpu_uncached(python_exe, label))
        return f"{value}{missing_note} (cached {age}s ago)" if age > 0 else f"{value}{missing_note}"
    # Configured venv probe: a cold heavy-torch import measured ~33s (FUSE I/O) — must NEVER
    # block a session-start hook. Cache hit → instant; miss → detached background worker
    # writes the cache; this fire reports honestly that the answer is pending.
    key = "gpu-" + hashlib.sha256(python_exe.encode()).hexdigest()[:8]
    cache_file = CACHE_DIR / f"{key}.json"
    cached = safe(lambda: json.loads(cache_file.read_text())) if cache_file.is_file() else None
    now = time.time()
    if cached:
        age = int(now - cached.get("ts", 0))
        if cached.get("pending"):
            if age < GPU_VENV_PENDING_GRACE:
                return f"GPU: real-op probe running in background ({label}, started {age}s ago)"
        elif age < GPU_VENV_PROBE_TTL:
            return f"{cached.get('value')} (cached {age}s ago)"
    _gpu_cache_write(key, "probe launched", pending=True)
    safe(lambda: subprocess.Popen(
        [sys.executable, os.path.realpath(__file__), "_gpuworker", python_exe, label, key],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    return f"GPU: real-op probe launched in background ({label}, cold venv ~30s) — result next fire"


def cmd_gpuworker(args):
    """Hidden: detached worker for heavy-venv GPU probes. argv: python_exe label key."""
    python_exe, label, key = args[0], args[1], args[2]
    value = _probe_gpu_uncached(python_exe, label, timeout=90)
    _gpu_cache_write(key, value, pending=False)


# ── Tier 0: pluggable per-project command probes ────────────────────────────
# GAMEPLAN Phase 2b S2. Same pattern as the gpu_python venv probe above: a
# configured command may be arbitrarily slow and must never block a
# session-start hook. Cache hit -> instant; miss -> detached background
# worker writes the cache; this fire reports honestly that the answer is
# pending. Reuses _gpu_cache_write — its cache format ({ts, value, pending})
# is already generic, not GPU-specific.

PROBE_DEFAULT_TTL = 300      # seconds, mirrors GPU_PROBE_TTL
PROBE_DEFAULT_TIMEOUT = 10   # seconds, per-command subprocess timeout
PROBE_PENDING_GRACE = 30     # seconds; commands are expected far lighter than a cold venv import
PROBE_OUTPUT_MAX_CHARS = 150


def _probe_command_uncached(command, timeout):
    r = safe(lambda: subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout))
    if r is None:
        return "probe crashed/timed out"
    out = (r.stdout or r.stderr or "").strip()
    first_line = out.splitlines()[0] if out else f"(no output, exit {r.returncode})"
    return redact(first_line)[:PROBE_OUTPUT_MAX_CHARS]


def cmd_probeworker(args):
    """Hidden: detached worker for command probes. argv: name key command timeout."""
    name, key, command, timeout_s = args[0], args[1], args[2], args[3]
    value = _probe_command_uncached(command, float(timeout_s))
    _gpu_cache_write(key, value, pending=False)


def probe_command_probes(root):
    """Reads <root>/.baton/probes.json 'probes' entries:
    {"name": str, "command": str, "ttl_seconds": int (optional), "timeout": int (optional)}.
    Same cache-hit-instant / cache-miss-detached-worker pattern as probe_gpu.
    Never blocks; a garbage entry yields one honest line, never a crash (DESIGN sec 2)."""
    cfg = safe(lambda: read_probes_config(root), {}) or {}
    entries = cfg.get("probes", [])
    if not isinstance(entries, list):
        return ["probes: .baton/probes.json 'probes' is not a list"]
    out = []
    now = time.time()
    for e in entries[:20]:
        if not isinstance(e, dict):
            out.append("probe ?: malformed entry")
            continue
        name = e.get("name", "?")
        command = e.get("command")
        if not command or not isinstance(command, str):
            out.append(f"probe {name}: no command configured")
            continue
        ttl = e.get("ttl_seconds", PROBE_DEFAULT_TTL)
        timeout_s = e.get("timeout", PROBE_DEFAULT_TIMEOUT)
        key = "probe-" + hashlib.sha256(f"{name}:{command}".encode()).hexdigest()[:8]
        cache_file = CACHE_DIR / f"{key}.json"
        cached = safe(lambda: json.loads(cache_file.read_text())) if cache_file.is_file() else None
        if cached:
            age = int(now - cached.get("ts", 0))
            if cached.get("pending"):
                if age < PROBE_PENDING_GRACE:
                    out.append(f"probe {name}: running in background (started {age}s ago)")
                    continue
            elif age < ttl:
                out.append(f"probe {name}: {cached.get('value')} (cached {age}s ago)")
                continue
        _gpu_cache_write(key, "probe launched", pending=True)
        safe(lambda: subprocess.Popen(
            [sys.executable, os.path.realpath(__file__), "_probeworker", name, key, command, str(timeout_s)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        out.append(f"probe {name}: launched in background — result next fire")
    return out


# ── Tier 0: pluggable per-project artifact/job probes ───────────────────────

def probe_project_jobs(root):
    """Reads <root>/.baton/probes.json if present. Each entry:
    {"name": str, "artifact": path, "newer_than": path (optional)}.
    Reports presence/mtime — never an exit code (DESIGN sec 2)."""
    cfg = read_probes_config(root)
    if cfg.get("_invalid"):
        return ["jobs: .baton/probes.json unreadable/invalid JSON"]
    entries = cfg.get("jobs", [])
    if not isinstance(entries, list):
        return ["jobs: .baton/probes.json 'jobs' is not a list"]
    out = []
    for e in entries[:20]:
        name = e.get("name", "?")
        artifact = e.get("artifact", "")
        path = artifact if os.path.isabs(artifact) else os.path.join(root, artifact)
        if not os.path.exists(path):
            out.append(f"job {name}: MISSING ({artifact})")
            continue
        size = os.path.getsize(path)
        mtime = os.path.getmtime(path)
        age_s = time.time() - mtime
        status = "empty" if size == 0 else "present"
        newer_note = ""
        newer_than = e.get("newer_than")
        if newer_than:
            ref = newer_than if os.path.isabs(newer_than) else os.path.join(root, newer_than)
            if os.path.exists(ref) and mtime < os.path.getmtime(ref):
                newer_note = " — STALE vs " + newer_than
        out.append(f"job {name}: {status}, {int(age_s)}s old{newer_note}")
    return out


def render_status(root):
    """GAMEPLAN Phase 2b S4: the probes are independent I/O — git and (uncached) venv
    spawns dominate wall-clock, so run them concurrently instead of summing their costs.
    Output order is fixed by .result() call order below, not by finish order — identical to
    the old sequential output. path-linting probe added post-bake-in feedback, 2026-07-17.
    Volatile-fact freshness probe (S9) added same day; it's pure text-parse (no real I/O to
    parallelize) but stays in the pool for output-order consistency with the other probes."""
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_git = ex.submit(safe, lambda: probe_git(root), [])
        f_venvs = ex.submit(safe, lambda: probe_venvs(root), [])
        f_gpu = ex.submit(safe, lambda: probe_gpu(root))
        f_jobs = ex.submit(safe, lambda: probe_project_jobs(root), [])
        f_probes = ex.submit(safe, lambda: probe_command_probes(root), [])
        f_paths = ex.submit(safe, lambda: probe_paths(root), [])
        f_volatile = ex.submit(safe, lambda: probe_volatile(root), [])
        lines = []
        lines += f_git.result()
        lines += f_venvs.result()
        gpu_line = f_gpu.result()
        if gpu_line:
            lines.append(gpu_line)
        lines += f_jobs.result()
        lines += f_probes.result()
        lines += f_paths.result()
        lines += f_volatile.result()
    if not lines:
        lines = ["status: no probes produced output (non-git dir, no venvs, no GPU, no .baton/probes.json)"]
    body = "\n".join(lines)
    return clamp_lines(body, TIER0_MAX_LINES)


# ── Tier 1: BATON.md ────────────────────────────────────────────────────────

TIER1_SECTIONS = ["MISSION", "NOW", "MODEL", "IN-FLIGHT", "BLOCKED", "OPEN"]


def parse_baton_md_sections(text):
    sections = {}
    current = None
    buf = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            header = m.group(1).strip().upper()
            key = header.split("(")[0].split("/")[0].strip()
            current = key
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def extract_backtick_commands(text):
    return re.findall(r"`([^`\n]{4,})`", text or "")


def mark_now_suspect(now_text, events):
    """Mechanical NOW-suspect marking (DESIGN sec 4): if a backtick command
    named in NOW shows up as an EXECUTED Bash tool_use, flag NOW as suspect.

    A tool_use block existing is not evidence it ran (DESIGN sec 2, the law
    of effects, applied here too — caught empirically: a rejected/blocked
    tool call still leaves a tool_use block in the transcript even though it
    never executed). Only count a candidate if its tool_use has a matching
    tool_result that is not an error and not a user rejection."""
    if not now_text or not events:
        return now_text
    commands = extract_backtick_commands(now_text)
    if not commands:
        return now_text

    candidate_calls = {}  # tool_use_id -> (cmd, ts)
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for block in (ev.get("message", {}) or {}).get("content", []) or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Bash":
                continue
            cmd = (block.get("input", {}) or {}).get("command", "")
            if cmd and any(c.strip() and c.strip() in cmd for c in commands):
                candidate_calls[block.get("id")] = (cmd, ev.get("timestamp", "?"))

    if not candidate_calls:
        return now_text

    for ev in events:
        if ev.get("type") != "user":
            continue
        content = (ev.get("message", {}) or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = block.get("tool_use_id")
            if tid not in candidate_calls:
                continue
            if block.get("is_error"):
                continue
            c = block.get("content")
            if isinstance(c, list):
                c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
            if c and "doesn't want to proceed with this tool use" in str(c):
                continue  # rejected, not executed
            _, ts = candidate_calls[tid]
            return f"⚠ SUSPECT (a command from NOW was executed at {ts}, unverified — check yourself)\n\n{now_text}"
    return now_text


def render_tier1(root, events):
    baton_md = os.path.join(root, "BATON.md")
    if not os.path.isfile(baton_md):
        return ("(no BATON.md — this project has no intent layer. If work here spans sessions, "
                "seed one from baton's TEMPLATE.md)")
    text = safe(lambda: Path(baton_md).read_text(errors="replace"), "")
    if not text:
        return ""
    sections = parse_baton_md_sections(text)
    out_parts = []
    for key in TIER1_SECTIONS:
        if key not in sections or not sections[key]:
            continue
        content = sections[key]
        if key == "NOW":
            content = mark_now_suspect(content, events)
        out_parts.append(f"## {key}\n{content}")
    body = "\n\n".join(out_parts)
    return clamp_lines(body, TIER1_MAX_LINES)


# ── Transcript adapter (ONE function tree — everything else calls into this) ─

def load_transcript_events(path):
    """Defensive line-by-line JSONL parse. Bad lines are skipped, never raised.
    Format is internal/unstable (DESIGN sec 5) — degrade to empty, never wrong."""
    events = []
    if not path or not os.path.isfile(path):
        return events
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return events


def previous_session_transcript(current_path):
    """Most recent OTHER .jsonl in the same project transcript dir
    (DESIGN sec 8b, VERIFIED)."""
    if not current_path:
        return None
    d = os.path.dirname(current_path)
    try:
        candidates = [
            p for p in glob.glob(os.path.join(d, "*.jsonl"))
            if os.path.abspath(p) != os.path.abspath(current_path)
        ]
    except Exception:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda p: safe(lambda: os.path.getmtime(p), 0), reverse=True)
    return candidates[0]


def resolve_relevant_transcript(current_path):
    """Startup/clear hand us a near-empty transcript; resume/compact hand us
    the full one. Rather than trust the (partly UNVERIFIED) `source` field,
    just check: does the current file have real content? If not, fall back
    to the most recent sibling."""
    events = load_transcript_events(current_path)
    if len(events) >= 4:
        return current_path, events
    prev = previous_session_transcript(current_path)
    if prev:
        return prev, load_transcript_events(prev)
    return current_path, events


def build_interrupted_state(events):
    """Mechanical INTERRUPTED EXECUTION STATE (DESIGN sec 5). Never raw
    transcript re-injection (CCRM: 7.1x more likely to fail — sec 5, v2 was
    wrong about this).

    Death-point detection: a tool_use with no matching tool_result is NOT by
    itself evidence of a cap-death — long healthy sessions can have orphaned
    tool calls from unrelated causes (verified empirically against a real
    project transcript: two Read calls from day 1 never got a tool_result, yet
    the same session continued cleanly for 3 more days). The only reliable
    mechanical signal is: the LAST event in the whole transcript is an
    assistant message that ISSUED a tool call and nothing followed it at
    all — there was no room for a tool_result to ever arrive."""
    if not events:
        return None

    last_edit_path = None
    last_tool_use = None
    last_error = None

    for ev in events:
        t = ev.get("type")
        ts = ev.get("timestamp")
        if t == "assistant":
            for block in (ev.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name", "?")
                tin = block.get("input", {}) or {}
                last_tool_use = (name, tin, ts)
                if name in ("Edit", "Write", "NotebookEdit") and tin.get("file_path"):
                    last_edit_path = tin.get("file_path")
        elif t == "user":
            content = (ev.get("message", {}) or {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    if block.get("is_error"):
                        c = block.get("content")
                        if isinstance(c, list):
                            c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
                        last_error = str(c)[:300] if c else None

    died_during = None
    last_real_event = next((ev for ev in reversed(events) if ev.get("type") in ("assistant", "user")), None)
    if last_real_event and last_real_event.get("type") == "assistant":
        blocks = (last_real_event.get("message", {}) or {}).get("content", []) or []
        dying_calls = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        if dying_calls:
            b = dying_calls[-1]
            tin = b.get("input", {}) or {}
            detail = tin.get("file_path") or tin.get("command") or ""
            died_during = f"{b.get('name','?')}({redact(str(detail))[:150]}) at {last_real_event.get('timestamp','?')}"

    last_action = None
    if last_tool_use:
        name, tin, ts = last_tool_use
        detail = tin.get("file_path") or tin.get("command") or ""
        last_action = f"{name}({redact(str(detail))[:150]})"
        if last_error:
            last_action += f" → {redact(last_error)[:200]}"

    if not died_during and not last_action:
        return None

    return {
        "active_file": last_edit_path,
        "last_action": last_action,
        "died_during": died_during,
    }


def render_interrupted_state(state):
    """Feedback 2026-07-17 (a consuming session): this block used to render
    whenever ANY tool call existed anywhere in the transcript, showing the
    last call (possibly ending in an error) alongside a "ended cleanly" note
    computed from an unrelated signal (died_during) — self-contradictory, and
    it fired on effectively every session. Emit this block ONLY on a genuine
    mechanical death signal (died_during set); otherwise there is nothing to
    orient around, so say nothing (DESIGN "invisible when healthy")."""
    if not state or not state.get("died_during"):
        return ""
    lines = ["[STATE: INTERRUPTED EXECUTION]"]
    if state.get("active_file"):
        lines.append(f"  Active file:  {state['active_file']}")
    if state.get("last_action"):
        lines.append(f"  Last action:  {state['last_action']}")
    lines.append(f"  Died during:  {state['died_during']}   ← no matching tool_result; likely a cap/crash death")
    lines.append("  Note:         previous attempt was interrupted mid-action. Verify current state before resuming.")
    return clamp_chars("\n".join(lines), INTERRUPTED_MAX_CHARS)


# ── Traps (Tier 1b) ─────────────────────────────────────────────────────────

RETIRED_STATUSES = {"superseded", "retired", "resolved", "closed"}


def load_traps(path=TRAPS_PATH):
    traps = []
    if not os.path.isfile(path):
        return traps
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    traps.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return traps


def _trap_applies_to_project(trap, project):
    p = trap.get("project", "*")
    return p == "*" or p == project


def _trap_active(trap):
    return trap.get("status", "active") not in RETIRED_STATUSES


def render_meta_traps(traps, project):
    metas = [t for t in traps if t.get("class") == "meta" and _trap_applies_to_project(t, project) and _trap_active(t)]
    if not metas:
        return ""
    lines = ["## TRAPS (meta — always apply)"]
    for t in metas:
        line = f"- [{t.get('id','?')}] {t.get('what','')} — {t.get('rule', t.get('why',''))}"
        if len(line) > META_TRAP_MAX_CHARS:
            cut = line.rfind(" ", 0, META_TRAP_MAX_CHARS)
            if cut <= 0:
                cut = META_TRAP_MAX_CHARS
            line = line[:cut].rstrip() + f" […truncated, see {t.get('id','?')} in traps.jsonl]"
        lines.append(line)
    return "\n".join(lines)


def match_path_traps(traps, project, tool_name, tool_input):
    matches = []
    path_val = tool_input.get("file_path", "") or tool_input.get("path", "") or ""
    cmd_val = tool_input.get("command", "") or ""
    for t in traps:
        if t.get("class") == "meta":
            continue
        if not _trap_applies_to_project(t, project) or not _trap_active(t):
            continue
        triggers = t.get("triggers", {}) or {}
        hit = False
        if path_val:
            for pat in triggers.get("paths", []):
                if fnmatch.fnmatch(path_val, pat):
                    hit = True
                    break
        if not hit and cmd_val:
            for pat in triggers.get("commands", []):
                if fnmatch.fnmatch(cmd_val, pat):
                    hit = True
                    break
        if hit:
            matches.append(t)
    return matches


def _seen_traps_file(session_id):
    return f"/tmp/baton_seen_traps__{session_id}.json"


def load_seen(session_id):
    p = _seen_traps_file(session_id)
    if not os.path.isfile(p):
        return set()
    try:
        return set(json.loads(Path(p).read_text()))
    except Exception:
        return set()


def mark_seen(session_id, ids):
    p = _seen_traps_file(session_id)
    seen = load_seen(session_id)
    seen |= set(ids)
    try:
        Path(p).write_text(json.dumps(sorted(seen)))
    except Exception:
        pass


# ── Assembly: pickup ─────────────────────────────────────────────────────────

def _load_events_and_render_tier1(root, transcript_path):
    _, events = resolve_relevant_transcript(transcript_path) if transcript_path else (None, [])
    tier1 = safe(lambda: render_tier1(root, events), "")
    interrupted = safe(lambda: render_interrupted_state(build_interrupted_state(events)), "")
    return tier1, interrupted


def build_pickup(root, transcript_path):
    """GAMEPLAN Phase 2b S4: Tier-0 probes (dominated by git/venv, ~1s on a heavy project) and
    transcript loading + Tier-1 rendering are independent — run concurrently rather than paying
    both costs in sequence."""
    project = project_name(root)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_tier0 = ex.submit(safe, lambda: render_status(root), "status: probe failure")
        f_rest = ex.submit(_load_events_and_render_tier1, root, transcript_path)
        tier0 = f_tier0.result()
        tier1, interrupted = f_rest.result()
    traps = safe(lambda: load_traps(), [])
    meta_traps = safe(lambda: render_meta_traps(traps, project), "")

    parts = [
        "Precedence: Tier 0 (observed reality) > BATON.md (intent) > compaction summary > everything else.",
        f"## TIER 0 — REALITY ({project})\n{tier0}",
    ]
    if tier1:
        parts.append(tier1)
    if interrupted:
        parts.append(interrupted)
    if meta_traps:
        parts.append(meta_traps)

    text = "\n\n".join(p for p in parts if p)
    return clamp_chars(text, PICKUP_HARD_MAX_CHARS)


# ── Subcommands ──────────────────────────────────────────────────────────────

def cmd_status(args):
    root = find_project_root(os.getcwd())
    print(render_status(root))


def cmd_now(args):
    root = find_project_root(os.getcwd())
    baton_md = os.path.join(root, "BATON.md")
    if not os.path.isfile(baton_md):
        print("(no BATON.md in this project)")
        return
    text = Path(baton_md).read_text(errors="replace")
    sections = parse_baton_md_sections(text)
    now = sections.get("NOW", "(no NOW section)")
    events = []
    transcripts = sorted(
        glob.glob("/workspace/.claude/projects/*/*.jsonl"),
        key=lambda p: safe(lambda: os.path.getmtime(p), 0), reverse=True,
    )
    if transcripts:
        events = load_transcript_events(transcripts[0])
    print(mark_now_suspect(now, events))


def cmd_pickup(args):
    root = find_project_root(os.getcwd())
    transcript = args[0] if args else None
    if not transcript:
        candidates = sorted(
            glob.glob("/workspace/.claude/projects/*/*.jsonl"),
            key=lambda p: safe(lambda: os.path.getmtime(p), 0), reverse=True,
        )
        transcript = candidates[0] if candidates else None
    print(build_pickup(root, transcript))


def cmd_hook(args):
    """SessionStart. MUST always emit valid JSON and exit 0 — this is a hard
    acceptance criterion (GAMEPLAN 1.3): garbage input, missing BATON.md, or
    a non-git dir must never break the session."""
    context = ""
    t0 = time.time()
    payload, root = {}, None
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        cwd = payload.get("cwd") or os.getcwd()
        transcript_path = payload.get("transcript_path")
        root = find_project_root(cwd)
        context = build_pickup(root, transcript_path)
    except Exception as e:
        context = f"(baton pickup unavailable this session: {type(e).__name__})"
    audit_write({
        "kind": "hook",
        "session_id": payload.get("session_id", "unknown"),
        "source": payload.get("source", "unknown"),
        "project": safe(lambda: project_name(root), "?") if root else "?",
        "chars": len(context),
        "truncated": "hard capped" in context,
        "suspect": "SUSPECT" in context,
        "no_baton_md": "no BATON.md" in context,
        "degraded": context.startswith("(baton pickup unavailable"),
        "swallowed": _SWALLOWED[0],
        "ms": int((time.time() - t0) * 1000),
    })
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
    print(json.dumps(out))
    sys.exit(0)


def cmd_pretool(args):
    """PreToolUse. Non-blocking, additionalContext only — permissionDecision
    is always 'allow'. MUST always emit valid JSON and exit 0."""
    context = ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        cwd = payload.get("cwd") or os.getcwd()
        session_id = payload.get("session_id", "unknown")
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {}) or {}
        root = find_project_root(cwd)
        project = project_name(root)

        traps = load_traps()
        matches = match_path_traps(traps, project, tool_name, tool_input)
        seen = load_seen(session_id)
        new_matches = [t for t in matches if t.get("id") not in seen]

        if new_matches:
            lines = []
            for t in new_matches[:5]:
                lines.append(f"[TRAP {t.get('id','?')}] {t.get('what','')} — {t.get('why','')} Rule: {t.get('rule','')}")
            context = redact("\n".join(lines))
            mark_seen(session_id, [t.get("id") for t in new_matches])
            audit_write({
                "kind": "pretool_fire",
                "session_id": session_id,
                "project": project,
                "tool": tool_name,
                "traps": [t.get("id") for t in new_matches],
                "swallowed": _SWALLOWED[0],
            })
    except Exception:
        context = ""
        audit_write({"kind": "pretool_error", "swallowed": _SWALLOWED[0]})

    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}
    if context:
        out["hookSpecificOutput"]["additionalContext"] = context
    print(json.dumps(out))
    sys.exit(0)


def cmd_audit(args):
    """Bake-in audit digest: what did BATON actually do, and did anything degrade
    silently. Reads logs/audit.jsonl(+.1). Usage: baton audit [days] (default 7)."""
    days = 7
    if args:
        days = int(safe(lambda: float(args[0]), 7) or 7)
    cutoff = time.time() - days * 86400
    records = []
    for name in ("audit.jsonl.1", "audit.jsonl"):
        p = BATON_HOME / "logs" / name
        if not p.is_file():
            continue
        for line in p.read_text(errors="replace").splitlines():
            rec = safe(lambda: json.loads(line))
            if not rec:
                continue
            ts = safe(lambda: datetime.fromisoformat(rec["ts"]).timestamp(), 0)
            if ts >= cutoff:
                records.append(rec)
    if not records:
        print(f"(no audit records in the last {days}d — if hooks have fired in that window, "
              "the audit path itself is broken: that absence is the finding)")
        return
    hooks = [r for r in records if r.get("kind") == "hook"]
    fires = [r for r in records if r.get("kind") == "pretool_fire"]
    errors = [r for r in records if r.get("kind") == "pretool_error"]
    print(f"BATON AUDIT — last {days}d, {len(records)} records "
          f"({records[0].get('ts','?')} → {records[-1].get('ts','?')})")
    if hooks:
        by_proj = {}
        for r in hooks:
            by_proj.setdefault(r.get("project", "?"), []).append(r)
        print(f"\npickups: {len(hooks)} across {len(by_proj)} project(s)")
        for proj, rs in sorted(by_proj.items()):
            chars = [r.get("chars", 0) for r in rs]
            ms = [r.get("ms", 0) for r in rs]
            flags = []
            for label, key in (("truncated", "truncated"), ("suspect", "suspect"),
                               ("no-BATON.md", "no_baton_md"), ("DEGRADED", "degraded")):
                n = sum(1 for r in rs if r.get(key))
                if n:
                    flags.append(f"{label}×{n}")
            print(f"  {proj}: {len(rs)} fires, chars avg {sum(chars)//len(chars)} max {max(chars)} "
                  f"(budget {PICKUP_HARD_MAX_CHARS}), ms avg {sum(ms)//len(ms)} max {max(ms)}"
                  + (" — " + ", ".join(flags) if flags else ""))
        sw = [r.get("swallowed", 0) for r in hooks]
        if any(sw):
            print(f"  swallowed-exception counts per pickup: max {max(sw)}, "
                  f"nonzero in {sum(1 for s in sw if s)}/{len(sw)} fires — "
                  "(probes failing quietly; worth a look, not an alarm)")
        else:
            print("  swallowed exceptions: none — no probe degraded silently")
    if fires:
        by_trap = {}
        for r in fires:
            for tid in r.get("traps", []):
                by_trap[tid] = by_trap.get(tid, 0) + 1
        print(f"\ntrap pushes: {len(fires)} events — " +
              ", ".join(f"{k}×{v}" for k, v in sorted(by_trap.items())))
        noisy = [k for k, v in by_trap.items() if v > 3 * max(1, len(set(r.get('session_id') for r in fires)))]
        if noisy:
            print(f"  NOISY (fires ≫ sessions — dedup may be broken or trigger too broad): {noisy}")
    else:
        print("\ntrap pushes: none")
    if errors:
        print(f"\npretool ERRORS: {len(errors)} — pretool's own try block failed; investigate")


def _next_trap_id(traps, project):
    prefix = f"T-{project.upper()}-"
    nums = []
    for t in traps:
        tid = t.get("id", "")
        if tid.startswith(prefix):
            try:
                nums.append(int(tid[len(prefix):]))
            except Exception:
                pass
    n = (max(nums) + 1) if nums else 1
    return f"{prefix}{n:02d}"


def cmd_trap(args):
    import argparse
    p = argparse.ArgumentParser(prog="baton trap")
    p.add_argument("--what", required=True)
    p.add_argument("--why", required=True)
    p.add_argument("--rule", default="")
    p.add_argument("--paths", default="", help="comma-separated glob patterns")
    p.add_argument("--commands", default="", help="comma-separated glob patterns")
    p.add_argument("--class", dest="klass", default="path", choices=["path", "meta"])
    p.add_argument("--project", default=None, help="defaults to current project; use '*' for global")
    ns = p.parse_args(args)

    root = find_project_root(os.getcwd())
    project = ns.project or project_name(root)
    traps = load_traps()
    tid = _next_trap_id(traps, project if project != "*" else "GLOBAL")

    record = {
        "id": tid,
        "project": project,
        "source": "baton trap (manual)",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "active",
        "class": ns.klass,
        "triggers": {
            "paths": [s.strip() for s in ns.paths.split(",") if s.strip()],
            "commands": [s.strip() for s in ns.commands.split(",") if s.strip()],
        },
        "what": ns.what,
        "why": ns.why,
        "rule": ns.rule,
    }
    with open(TRAPS_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"trap recorded: {tid}")


def cmd_block(args):
    if not args:
        eprint('usage: baton block "<description>"')
        sys.exit(1)
    text = " ".join(args)
    root = find_project_root(os.getcwd())
    baton_md = os.path.join(root, "BATON.md")
    if not os.path.isfile(baton_md):
        eprint(f"no BATON.md at {baton_md}")
        sys.exit(1)
    content = Path(baton_md).read_text(errors="replace")
    bullet = f"- {text}"
    m = re.search(r"(## BLOCKED[^\n]*\n)", content)
    if m:
        insert_at = m.end()
        new_content = content[:insert_at] + bullet + "\n" + content[insert_at:]
    else:
        new_content = content.rstrip() + f"\n\n## BLOCKED\n\n{bullet}\n"
    Path(baton_md).write_text(new_content)
    print(f"added to BLOCKED in {baton_md}")


def cmd_recall(args):
    if not args:
        eprint("usage: baton recall <query>")
        sys.exit(1)
    query = " ".join(args)
    root = find_project_root(os.getcwd())
    if not os.path.isdir(os.path.join(root, ".rtfm")):
        print("(no index)")
        return
    rtfm_bin = "rtfm"
    which = safe(lambda: run(["which", "rtfm"], timeout=3))
    if not which or which[0] != 0:
        alt = "/workspace/venvs/rtfm/bin/rtfm"
        if os.path.exists(alt):
            rtfm_bin = alt
    r = safe(lambda: run([rtfm_bin, "search", query, "--format", "prompt"], cwd=root, timeout=20))
    if not r:
        print("(recall failed — rtfm not runnable)")
        return
    rc, out, err = r
    print(out if rc == 0 and out else f"(recall failed: {err[:200]})")


SUBCOMMANDS = {
    "pickup": cmd_pickup,
    "status": cmd_status,
    "now": cmd_now,
    "trap": cmd_trap,
    "block": cmd_block,
    "hook": cmd_hook,
    "pretool": cmd_pretool,
    "recall": cmd_recall,
    "audit": cmd_audit,
    "_gpuworker": cmd_gpuworker,
    "_probeworker": cmd_probeworker,
}


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] not in SUBCOMMANDS:
        cmd = "pickup"
        rest = argv
    else:
        cmd = argv[0]
        rest = argv[1:]
    SUBCOMMANDS[cmd](rest)


if __name__ == "__main__":
    main()
