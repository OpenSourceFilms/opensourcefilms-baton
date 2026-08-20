# BATON on a Runpod pod

BATON is general-purpose Claude Code tooling — a single stdlib-only Python file with no
dependency on a GPU, CUDA, or any particular cloud provider. It does not need Runpod and this
is not a GPU-software doc.

It's covered here because Runpod pods happen to have exactly the properties that make session
continuity hard, and that BATON was built to handle correctly:

- a **persistent/network volume** plus an **ephemeral container root** that's wiped on rebuild,
  so "where you put a file" has real consequences;
- **pod restarts and fresh pods on the same volume**, which is the normal discontinuity BATON's
  `SessionStart` pickup exists to smooth over;
- **per-model/per-project GPU venvs**, which is exactly what `.baton/probes.json`'s `gpu_python`
  field is for; and
- long-running jobs and "it succeeded" claims that need to be checked by effect, not by exit
  code — the same law of effects BATON's probes follow (see `DESIGN.md` §2).

Everything below is a Runpod-flavored walkthrough of the install/usage steps already documented
in `README.md`. It doesn't repeat or change anything there — read `README.md` first if you
haven't.

## 1. Where things end up: `BATON_HOME`

This matters more than it looks like it should, so it's worth stating up front.

`baton.py` resolves its own real (symlink-followed) location at import time and calls that
`BATON_HOME`. Three things live next to `baton.py` at that resolved location, all created
automatically on first use:

| Path (relative to `BATON_HOME`) | What it is | Scope |
|---|---|---|
| `traps.jsonl` | the negative-knowledge store | shared across every project this `baton.py` is used from, filtered per-project at render time via each trap's `project` field |
| `.cache/` | probe result cache (GPU, venvs, custom probes) | machine-local, regenerable, gitignored |
| `logs/audit.jsonl` | append-only self-audit trail | machine-local, regenerable, gitignored |

Everything **project-specific** — `BATON.md` and the optional `.baton/probes.json` — lives at
the project's own root (`git rev-parse --show-toplevel`, or the current directory if it isn't a
git repo), wherever that project happens to be checked out.

The practical consequence: **wherever you put the real `baton.py` file (not a symlink to it) is
where its shared state lives.** Put it on the ephemeral container root and `traps.jsonl` and the
audit log vanish on the next rebuild even though your projects on the network volume are fine.
Put it on the persistent volume and that state survives pod restarts and fresh pods alike.

## 2. Installing on a persistent volume

Runpod pods typically mount a network/persistent volume (commonly `/workspace`, but check your
template — some name it differently) alongside an ephemeral container root that does not
survive a rebuild. Clone BATON onto the persistent volume, not into `/root`, `/tmp`, or anywhere
else that's only container-root storage:

```bash
git clone https://github.com/OpenSourceFilms/opensourcefilms-baton.git /workspace/tools/baton
```

Then symlink it onto `PATH`, same as the README's install step, just pointed at the persistent
copy:

```bash
ln -s /workspace/tools/baton/baton.py /usr/local/bin/baton
```

One Runpod-specific wrinkle: on several templates `/usr/local/bin` itself is on the ephemeral
container root, so the symlink you just made will disappear on rebuild even though the target
file is safe on the volume. Two ways to handle that, pick whichever matches your template:

- Check whether your shell init file (`~/.bashrc`, `~/.profile`) is itself on the persistent
  volume (some templates persist `/root`, some don't). If it is, add the persistent bin directly
  to `PATH` there instead of relying on a symlink:
  ```bash
  echo 'export PATH="/workspace/tools/baton:$PATH"' >> ~/.bashrc
  ```
  (this works because `baton.py` has a `#!/usr/bin/env python3` shebang and is executable)
- Or just re-run the `ln -s` once after a rebuild — it's a single command and costs nothing if
  you forget until you need `baton` and it's missing.

Either way, verify what actually persisted before relying on it — don't assume:

```bash
readlink -f "$(command -v baton)"   # should resolve onto your persistent volume
```

## 3. Using BATON with a project on the volume

No change from the normal workflow — BATON finds the project root via `git rev-parse
--show-toplevel`, so as long as your project is a git repo living on the volume (which it
should be, for the same rebuild-survival reason as BATON itself), just seed its task file:

```bash
cp /workspace/tools/baton/TEMPLATE.md /workspace/your-project/BATON.md
```

and fill in `MISSION`/`NOW`. Everything after that — hooks firing, traps being pushed, `baton
status` being generated — works identically to any other machine.

## 4. Claude Code hooks

Wire the two hooks exactly as shown in `README.md`'s Install section (step 3) — nothing about
the hook JSON changes for Runpod. Put it in the project's `.claude/settings.json` if you want it
project-scoped, or `~/.claude/settings.json` if that file's location is itself on the persistent
volume and you want it to apply to every project on the pod without repeating the config.

## 5. Pointing GPU checks at the right venv

Runpod pods used for model work commonly have several per-model virtual environments rather than
one system Python with everything installed. `baton status`'s GPU probe defaults to
`sys.executable` (whatever `python3` invoked `baton.py`), which is frequently *not* the venv that
actually has a working, correctly-built `torch` for that GPU's architecture.

Point it at the right interpreter per project with `.baton/probes.json` at the project root:

```json
{
  "gpu_python": "/workspace/your-project/venvs/model-a/bin/python"
}
```

`baton.py`'s own GPU probe (`_probe_gpu_uncached`) runs a real op — `torch.randn(4,4).to('cuda')`,
never a presence-check — through whichever interpreter is configured, and reports one of:

- `GPU: real op OK (torch.randn(4,4).to('cuda'), gpu_python=...)`
- `GPU: torch not importable from <label> — GPU state UNKNOWN, not known-bad` if the configured
  interpreter doesn't have torch
- `GPU: probe INCONCLUSIVE — <label>'s torch build lacks this GPU's arch` if torch imports but
  the build doesn't match the GPU (the exact failure mode Runpod's mixed-architecture fleet
  produces most often — e.g. a `cu124` build on a Blackwell card)
- `GPU: FAILED real op (<label>) — <redacted error>` only for a genuine failure distinct from
  the above two

The first heavy import against a configured venv (~30s on a cold FUSE-backed volume) runs in a
detached background worker and caches for an hour; `baton status` never blocks waiting for it —
you'll see `GPU: real-op probe launched in background (..., cold venv ~30s) — result next fire`
on the first call and the real result on the next.

`baton.py`'s own venv-drift check (a separate probe from the GPU one) auto-scans
`<project>/venvs/*` and `<project>/.venv` for interpreter drift against each `pyvenv.cfg` with no
config needed — useful on a pod with several per-model venvs under one project.

## 6. Across a pod restart, or a fresh pod on the same volume

Nothing BATON-specific needs to happen. On the next session start:

- `baton.py`, `traps.jsonl`, `.cache/`, and `logs/audit.jsonl` are exactly as they were, as long
  as `BATON_HOME` (§1) is on the persistent volume.
- `BATON.md` and `.baton/probes.json` are exactly as they were, as long as the project itself is
  on the persistent volume.
- The GPU probe cache and venv-drift cache are stale-but-safe: `baton status` always shows a
  probe's age, so a value from before the restart is visibly old rather than silently trusted,
  and the GPU real-op probe will simply re-run on the next TTL expiry (or immediately, since a
  fresh pod's underlying hardware may differ from the one that produced the cached value — the
  probe doesn't know that, it just re-checks on its normal schedule).
- What does *not* survive: anything you left on the ephemeral container root — a `PATH` symlink
  placed outside the persisted shell init (§2), or a `baton.py` clone left in `/root` instead of
  the volume. If `command -v baton` comes up empty after a fresh pod, that's what happened;
  re-run the one-line `ln -s`/`PATH` step from §2.

A brand-new pod attached to the same volume is functionally the same case as a restart, from
BATON's point of view — it has no concept of "which pod," only "what's on disk right now."

## 7. What lives with the project vs. what's machine state

| Lives with the project (on the volume, project's own repo) | Machine/BATON-install state (also on the volume, but not project-specific) |
|---|---|
| `BATON.md` | `traps.jsonl` (tracked in the BATON repo itself — it's meant to persist and is not gitignored) |
| `.baton/probes.json` | `.cache/*.json` (gitignored — regenerable probe cache) |
| the project's own `.claude/settings.json` hook wiring | `logs/audit.jsonl` (gitignored — regenerable audit trail, rotates at 5MB) |

Whether to commit a given project's `BATON.md` into that project's own git history is that
project's call (many will want to — it's a small, intent-only file). `.baton/probes.json` is
also reasonable to commit if it doesn't contain machine-specific absolute paths you'd rather not
hardcode; if your venv paths differ between pods, keep it uncommitted or template it instead.

## 8. Degrade behavior when the GPU or a configured venv is absent

Confirmed by reading `baton.py` directly (`probe_gpu` / `_probe_gpu_uncached`, `probe_venvs`),
not assumed:

- **No GPU / no torch anywhere reachable**: the probe reports `GPU: torch not importable from
  system python3 — GPU state UNKNOWN, not known-bad` and a pointer to configure `gpu_python`. It
  does not error, exit non-zero, or block `baton status`/`pickup` — it's one line in the output.
- **`gpu_python` configured but the path no longer exists** (e.g. a venv was deleted, or
  `probes.json` was copied from another pod): `probe_gpu` falls back to system `python3` and
  appends `(configured gpu_python missing: <path> — fell back to system python3)` to whatever
  that fallback probe reports. Still no error, no block.
- **A configured venv's torch build doesn't match the GPU's architecture**: reported as
  `INCONCLUSIVE`, explicitly distinguished in the code from a real GPU failure, because it's a
  statement about the probe's torch build, not the GPU.
- **A venv referenced by drift-checking (`<project>/venvs/*`, `<project>/.venv`) no longer
  exists**: simply not scanned — `probe_venvs` only walks directories that exist on disk; a
  stale reference in nothing tracks failures for paths that never existed in the first place. If
  a venv directory exists but its interpreter binary is gone, that's reported as one line —
  `<name>: interpreter missing/broken` — again without blocking anything else.

This matches the design principle stated in both `README.md` and `DESIGN.md`: BATON is meant to
degrade to a plain, honest line of output, never to an exception or a blocked session. Running
this repo's validation in a plain CPU-only environment with no `torch` installed at all produces
exactly the first case above — see the Testing section below for the actual output.

## 9. Running the test suite after install

Same as the README, run from wherever you cloned BATON (the persistent-volume path from §2):

```bash
bash /workspace/tools/baton/tests/selfcheck.sh
```

It's self-contained — it resolves its own repo root relative to itself, spins up temporary git
repos under `mktemp -d` for its probe/venv-drift checks, and cleans up after itself. No project
setup or GPU required to pass it; it's exercising BATON's own mechanics, not your pod's hardware.

Optionally install it as this repo's own pre-commit gate if you plan to modify `baton.py`
locally: `cp tests/pre-commit.sh .git/hooks/pre-commit` (per-clone — `.git/hooks/` isn't tracked
by git, so re-run this after any fresh clone, same as the README already notes).
