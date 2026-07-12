# Per-Machine Wiki Auto-Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each machine automatically ingests its own machine-local AI session history into the shared wiki vault on a schedule; a single owner machine (M4) rebuilds the global derived files — with zero cross-machine git conflicts by design.

**Architecture:** Per-machine manifest shards (`.manifest.<machine-key>.json`, write-your-own-shard-only discipline copied from the machines registry), ingest skills stripped of all writes to the three global hot-spot files (`index.md`/`log.md`/`hot.md` — rebuilt only by the M4 global job), `claude -p` headless runtime (Keychain OAuth verified 2026-07-08), git push identity pinned via the SSH host alias `github.com-yulin` (gh active-account proven unreliable). Ingest job scripts own all git operations deterministically; the LLM only does ingest content work.

**Tech Stack:** Python 3 stdlib (`manifest.py`), bash + launchd (jobs), `claude -p` (LLM runtime), git over SSH.

## Global Constraints

- Fork repo: `~/github/obsidian-wiki` (branch off `main`; wiki-kit plugin currently **1.2.3**, lives in `.skills/` with `.skills/.claude-plugin/plugin.json`).
- Vault: `~/Documents/obsidian-wiki-vault`, remote `git@github.com-yulin:yulin0629/obsidian-wiki-vault.git`, `pull.rebase=true` (already set on MBP; other machines get it in rollout).
- Machine keys follow `~/.config/yician/machines/` convention: `macbook-pro`, `mac-mini-m4`, `mac-mini-m2`.
- Headless invocations: `claude -p` **without `--bare`** (breaks OAuth), **with `--setting-sources ""`** (safety), explicit `--allowedTools`.
- Ingest skills may still **read** `index.md`; they must no longer **write** `index.md`, `log.md`, or `hot.md`.
- `wiki-rebuild` and `wiki-dedup` stay manual-only; nothing in this plan schedules them.
- Commit format per git-commit SSOT (Conventional Commits + AI-Agent/AI-Session-IDs trailer). Plugin functional changes and the `plugin.json` version bump land **in the same commit** (versioning rule). Proposed bump: **1.2.3 → 2.0.0** (breaking: manifest schema v2 + skill behavior change) — **confirm semver with user before Task 5 commit**.
- Do not push the fork or the vault without explicit user authorization at execution time.

---

### Task 1: manifest.py v2 — per-machine shard support

**Files:**
- Modify: `~/github/obsidian-wiki/.skills/llm-wiki/scripts/manifest.py`
- Test: bash smoke test in a temp vault (repo has no pytest harness for this script)

**Interfaces:**
- Consumes: existing `canonical()`, `load_manifest()`, CLI subcommands `normalize`/`delta`.
- Produces: `machine_key() -> str|None`, `manifest_path(vault)` now shard-aware, new CLI subcommands `path` (print resolved manifest path) and `init` (materialize seeded shard). Task 2's skill text and Task 3's job script both call `manifest.py path` / `manifest.py init`.

- [ ] **Step 1: Create feature branch**

```bash
cd ~/github/obsidian-wiki && git checkout -b yician/per-machine-manifest main
```

- [ ] **Step 2: Add machine-key resolution + shard path (edit manifest.py)**

Replace the existing `manifest_path` function (lines 35–36) with:

```python
def machine_key() -> str | None:
    """Resolve this machine's shard key: $WIKI_MACHINE_KEY, else the
    WIKI_MACHINE_KEY= line in ~/.obsidian-wiki/config, else None (legacy mode)."""
    key = os.environ.get("WIKI_MACHINE_KEY", "").strip()
    if key:
        return key
    cfg = os.path.expanduser("~/.obsidian-wiki/config")
    if os.path.exists(cfg):
        with open(cfg) as f:
            for line in f:
                line = line.strip()
                if line.startswith("WIKI_MACHINE_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return None


def legacy_manifest_path(vault: str) -> str:
    return os.path.join(canonical(vault), ".manifest.json")


def manifest_path(vault: str) -> str:
    """Shard path when a machine key is configured; legacy single file otherwise."""
    key = machine_key()
    if key:
        return os.path.join(canonical(vault), f".manifest.{key}.json")
    return legacy_manifest_path(vault)
```

- [ ] **Step 3: Make load_manifest seed the shard from the legacy manifest**

Replace the existing `load_manifest` (lines 39–44) with:

```python
def load_manifest(vault: str) -> dict:
    mp = manifest_path(vault)
    if os.path.exists(mp):
        with open(mp) as f:
            return json.load(f)
    key = machine_key()
    legacy = legacy_manifest_path(vault)
    if key and os.path.exists(legacy):
        # First run on this machine after the shard split: seed wholesale from
        # the legacy manifest. Entries for files that only exist on other
        # machines are inert for delta (their paths never match locally).
        with open(legacy) as f:
            m = json.load(f)
        m["version"] = 2
        m["machine"] = key
        return m
    return {"version": 2 if key else 1, "machine": key, "sources": {}, "projects": {}, "stats": {}}
```

- [ ] **Step 4: Add `path` and `init` subcommands**

Add before `main()`:

```python
def cmd_path(args: argparse.Namespace) -> int:
    print(manifest_path(args.vault))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Materialize the shard file (seeding from legacy if present). Idempotent."""
    mp = manifest_path(args.vault)
    if os.path.exists(mp):
        print(f"exists {mp}")
        return 0
    m = load_manifest(args.vault)
    with open(mp, "w") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    print(f"wrote {mp} (machine={m.get('machine')}, {len(m.get('sources', {}))} seeded sources)")
    return 0
```

And register in `main()` after the `delta` parser:

```python
    pp = sub.add_parser("path", help="print the resolved manifest path (shard-aware)")
    pp.add_argument("vault")
    pp.set_defaults(func=cmd_path)

    i = sub.add_parser("init", help="materialize this machine's manifest shard (idempotent)")
    i.add_argument("vault")
    i.set_defaults(func=cmd_init)
```

- [ ] **Step 5: Smoke test in a temp vault**

```bash
T=$(mktemp -d)
echo '{"version":1,"sources":{"/tmp/nonexistent.jsonl":{"ingested_at":"2026-01-01T00:00:00Z"}},"claude":{"last_ingested":"x"}}' > "$T/.manifest.json"
P=~/github/obsidian-wiki/.skills/llm-wiki/scripts/manifest.py
# legacy mode (no key): path resolves to legacy file
WIKI_MACHINE_KEY= python3 $P path "$T"
# shard mode: path, init seeds from legacy, second init is a no-op
WIKI_MACHINE_KEY=testbox python3 $P path "$T"
WIKI_MACHINE_KEY=testbox python3 $P init "$T"
WIKI_MACHINE_KEY=testbox python3 $P init "$T"
python3 -c "import json;d=json.load(open('$T/.manifest.testbox.json'));assert d['version']==2 and d['machine']=='testbox' and '/tmp/nonexistent.jsonl' in d['sources'];print('seed OK')"
# delta against the shard still works
WIKI_MACHINE_KEY=testbox python3 $P delta "$T" --scan "$T/*.jsonl"
rm -rf "$T"
```
Expected: first `path` prints `.../.manifest.json`; second prints `.../.manifest.testbox.json`; `init` prints `wrote ... 1 seeded sources` then `exists ...`; `seed OK`; delta prints `# 0 new, 0 modified, 1 known`.

- [ ] **Step 6: Commit**

```bash
cd ~/github/obsidian-wiki
git add .skills/llm-wiki/scripts/manifest.py
printf '%s\n' 'feat(manifest): per-machine shard support with legacy seeding' '' 'AI-Agent: Claude Code' 'AI-Session-IDs: a3a7f2a4-178a-4d15-aed1-8b7d02fe1ab3' | git commit -F -
```

---

### Task 2: Strip global-file writes from the 7 ingest skills

**Files (all under `~/github/obsidian-wiki/.skills/`):**
- Modify: `claude-history-ingest/SKILL.md` (three-file section at lines ~416–424), `codex-history-ingest/SKILL.md` (~197–205), `copilot-history-ingest/SKILL.md` (~324–332), `hermes-history-ingest/SKILL.md` (~188–196), `openclaw-history-ingest/SKILL.md` (~206–214), `pi-history-ingest/SKILL.md` (~255–263), `wiki-ingest/SKILL.md` (log.md at ~374–377, hot.md at ~379–380)
- Modify: `llm-wiki/SKILL.md` (the `.manifest.json` documentation section)
- Test: acceptance greps (Step 4)

**Interfaces:**
- Consumes: Task 1's `manifest.py path` subcommand (referenced in new skill text).
- Produces: ingest skills that write only content pages + journal + own shard. Task 4's global job becomes the sole writer of `index.md`/`log.md`/`hot.md`.

- [ ] **Step 1: Replace the three-file write section in each of the 6 history-ingest skills**

In each skill, locate the section that begins `Update `index.md` and `log.md`` (exact line anchors above; re-grep before editing since line numbers may drift: `grep -n 'Update .index.md. and .log.md' <skill>/SKILL.md`). Delete from that line through the end of the `**\`hot.md\`**` paragraph, and replace with:

```markdown
Do **not** write `index.md`, `log.md`, or `hot.md`. These global derived files are rebuilt exclusively by the scheduled global maintenance job (single-writer, runs on the designated owner machine). Concurrent ingest on multiple machines writing these files is the primary merge-conflict hot spot this rule eliminates. Your log line's information is carried by the git commit message instead (the ingest job script composes it).
```

- [ ] **Step 2: Same replacement in wiki-ingest/SKILL.md**

Delete the `**\`log.md\`** — Append an entry:` block and the `**\`hot.md\`** — ...` paragraph (anchors ~374–380); insert the same replacement text. Keep the hot.md **template** that lives further down in the file (other skills reference it for manual runs) but retitle its heading from a write instruction to `### hot.md template (used by the global maintenance job)`.

- [ ] **Step 3: Redirect manifest references + journal filename**

In each of the 7 skills, find the early step `Read `.manifest.json` at the vault root` (anchors: claude L26, codex L26, copilot L29, hermes L26, openclaw L26, pi L22, wiki-ingest L25) and replace the sentence with:

```markdown
Read this machine's manifest shard — resolve its path with `python3 "<skill-base-dir>/../llm-wiki/scripts/manifest.py" path "$OBSIDIAN_VAULT_PATH"` (returns `.manifest.<machine>.json` when `WIKI_MACHINE_KEY` is configured, the legacy `.manifest.json` otherwise). All manifest reads AND writes in this skill target that resolved path only — never another machine's shard.
```

In the journal-entry instructions of the 6 history-ingest skills, change the journal filename pattern from `journal/<date>-<harness>-history.md` to `journal/<date>-<harness>-history-$WIKI_MACHINE_KEY.md` (fallback: no suffix when the key is unset) — two machines ingesting the same harness on the same day must not collide on the same journal path.

- [ ] **Step 4: Acceptance greps**

```bash
cd ~/github/obsidian-wiki/.skills
# No skill instructs writing/updating the three global files anymore:
grep -rn "Update .index.md. and .log.md\|log.md.*Append an entry\|Update \*\*Recent Activity\*\*" \
  */SKILL.md && echo "FAIL: write instruction remains" || echo "PASS: no global-file writes"
# Every ingest skill references the shard resolution:
for s in claude codex copilot hermes openclaw pi; do
  grep -q "manifest.py\" path" ${s}-history-ingest/SKILL.md || echo "MISSING shard ref: $s"
done; grep -q "manifest.py\" path" wiki-ingest/SKILL.md || echo "MISSING shard ref: wiki-ingest"
echo "grep pass done"
```
Expected: `PASS: no global-file writes`, no `MISSING` lines, `grep pass done`.

- [ ] **Step 5: Commit**

```bash
cd ~/github/obsidian-wiki
git add .skills/*history-ingest/SKILL.md .skills/wiki-ingest/SKILL.md .skills/llm-wiki/SKILL.md
printf '%s\n' 'refactor(skills): ingest writes own manifest shard only; global derived files moved to maintenance job' '' 'AI-Agent: Claude Code' 'AI-Session-IDs: a3a7f2a4-178a-4d15-aed1-8b7d02fe1ab3' | git commit -F -
```

---

### Task 3: Per-machine ingest job (script + launchd plist)

**Files:**
- Create: `~/github/obsidian-wiki/scripts/wiki-ingest-job.sh` (executable)
- Create: `~/github/obsidian-wiki/scripts/com.obsidian-wiki.ingest.plist` (template; installed per machine in Task 6)
- Test: manual foreground run (Step 3)

**Interfaces:**
- Consumes: `manifest.py init/path` (Task 1); skills stripped per Task 2; vault config `~/.obsidian-wiki/config` gains two machine-local keys: `WIKI_MACHINE_KEY="<key>"`, `WIKI_INGEST_HARNESSES="claude,codex"` (comma list; per machine).
- Produces: scheduled per-machine ingest. Commit message format `wiki-ingest(<harnesses>,<machine>): <summary>` that Task 4's log rebuild parses.

- [ ] **Step 1: Write the job script**

`~/github/obsidian-wiki/scripts/wiki-ingest-job.sh`:

```bash
#!/usr/bin/env bash
# Per-machine wiki ingest job. Owns all git ops; claude -p does ingest only.
set -euo pipefail

CONFIG="$HOME/.obsidian-wiki/config"
[[ -f "$CONFIG" ]] || { echo "[ingest-job] no config"; exit 1; }
# shellcheck source=/dev/null
source "$CONFIG"
: "${OBSIDIAN_VAULT_PATH:?}" "${WIKI_MACHINE_KEY:?}" "${WIKI_INGEST_HARNESSES:?}"

LOCK="$HOME/.obsidian-wiki/state/ingest-$WIKI_MACHINE_KEY.lock"
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
flock -n 9 || { echo "[ingest-job] another run holds the lock — exiting"; exit 0; }

# Newest installed wiki-kit plugin version dir (skills root for headless runs).
SKILLS_ROOT=$(ls -d "$HOME/.claude/plugins/cache/yician-wiki-tools/wiki-kit"/*/ 2>/dev/null | sort -V | tail -1)
[[ -n "$SKILLS_ROOT" ]] || { echo "[ingest-job] wiki-kit plugin not installed"; exit 1; }

cd "$OBSIDIAN_VAULT_PATH"
git pull --quiet                     # pull.rebase=true is repo config
python3 "${SKILLS_ROOT}llm-wiki/scripts/manifest.py" init "$OBSIDIAN_VAULT_PATH"

IFS=',' read -ra HARNESSES <<< "$WIKI_INGEST_HARNESSES"
for H in "${HARNESSES[@]}"; do
  SKILL="${SKILLS_ROOT}${H}-history-ingest/SKILL.md"
  [[ -f "$SKILL" ]] || { echo "[ingest-job] skip $H (no skill at $SKILL)"; continue; }
  echo "[ingest-job] ingesting $H"
  claude -p "Read and follow the skill instructions at $SKILL in append mode (delta only). \
The vault is $OBSIDIAN_VAULT_PATH. WIKI_MACHINE_KEY=$WIKI_MACHINE_KEY. \
Do NOT run any git commands; do NOT write index.md, log.md, or hot.md. \
When done, output one line starting with SUMMARY: describing counts." \
    --setting-sources "" \
    --allowedTools "Read,Grep,Glob,Write,Edit,Bash(python3:*),Bash(ls:*),Bash(wc:*)" \
    --max-turns 80 \
    | tee "$HOME/.obsidian-wiki/state/ingest-$WIKI_MACHINE_KEY-$H.out" | tail -3
done

if [[ -z "$(git status --porcelain)" ]]; then
  echo "[ingest-job] nothing new — done"; exit 0
fi
git add concepts entities projects synthesis skills journal _raw ".manifest.${WIKI_MACHINE_KEY}.json" 2>/dev/null || true
# Leak guard: never commit the global derived files from an ingest job.
if git diff --cached --name-only | grep -qE '^(index|log|hot)\.md$|^\.manifest\.json$'; then
  echo "[ingest-job] ABORT: staged a global derived file — investigate"; git reset -q; exit 1
fi
SUMMARY=$(grep -h '^SUMMARY:' "$HOME/.obsidian-wiki/state/ingest-$WIKI_MACHINE_KEY-"*.out 2>/dev/null | head -3 | tr '\n' ' ' || true)
printf '%s\n' \
  "wiki-ingest(${WIKI_INGEST_HARNESSES},${WIKI_MACHINE_KEY}): scheduled delta ingest" \
  "" "${SUMMARY:-no summary captured}" "" \
  "AI-Agent: Claude Code (scheduled)" | git commit -qF -
git push --quiet || { git pull --quiet && git push --quiet; } \
  || { echo "[ingest-job] push failed twice — leaving commit local, alert"; exit 1; }
echo "[ingest-job] done: $(git log -1 --format=%h)"
```

Then: `chmod +x ~/github/obsidian-wiki/scripts/wiki-ingest-job.sh`

- [ ] **Step 2: Write the plist template**

`~/github/obsidian-wiki/scripts/com.obsidian-wiki.ingest.plist` (install path per machine: `~/Library/LaunchAgents/`; `__HOME__` substituted at install):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.obsidian-wiki.ingest</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>__HOME__/github/obsidian-wiki/scripts/wiki-ingest-job.sh</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer>
  </dict>
  <key>StandardOutPath</key><string>/tmp/obsidian-wiki-ingest.log</string>
  <key>StandardErrorPath</key><string>/tmp/obsidian-wiki-ingest.err</string>
</dict></plist>
```

Machines stagger `Hour`/`Minute` at install (Task 6): MBP 08:30, M2 08:45, M4 09:00 — M4 last so its 09:30 global job (Task 4) sees the morning's ingests.

- [ ] **Step 3: Manual foreground validation on MBP**

```bash
WIKI_MACHINE_KEY=macbook-pro bash ~/github/obsidian-wiki/scripts/wiki-ingest-job.sh
```
Expected: lock acquired, pull OK, `manifest.py init` writes `.manifest.macbook-pro.json`, one `claude -p` run per configured harness, either `nothing new — done` or a commit `wiki-ingest(...)` that is pushed. Verify `git log -1` on the vault and that `git status` is clean. **Note:** requires `WIKI_MACHINE_KEY` and `WIKI_INGEST_HARNESSES` added to `~/.obsidian-wiki/config` first (Task 6 Step 1 — do that step for MBP before this validation).

- [ ] **Step 4: Commit**

```bash
cd ~/github/obsidian-wiki
git add scripts/wiki-ingest-job.sh scripts/com.obsidian-wiki.ingest.plist
printf '%s\n' 'feat(jobs): per-machine scheduled ingest job (flock, git-owning, claude -p runtime)' '' 'AI-Agent: Claude Code' 'AI-Session-IDs: a3a7f2a4-178a-4d15-aed1-8b7d02fe1ab3' | git commit -F -
```

---

### Task 4: M4 global maintenance job (phase-1 whitelist)

**Files:**
- Create: `~/github/obsidian-wiki/scripts/wiki-global-job.sh` (executable)
- Create: `~/github/obsidian-wiki/scripts/com.obsidian-wiki.global.plist` (M4 only)
- Test: manual foreground run (on M4, during rollout — Task 6)

**Interfaces:**
- Consumes: ingest commits with the `wiki-ingest(...)` message prefix (Task 3); hot.md template retained in wiki-ingest/SKILL.md (Task 2).
- Produces: rebuilt `index.md`/`log.md`/`hot.md` + a lint report at `_reports/lint-<date>.md`. Phase 1 whitelist ONLY — no synthesize/cross-link/dedup (those come later, branch-gated; rebuild stays manual forever).

- [ ] **Step 1: Write the job script**

`~/github/obsidian-wiki/scripts/wiki-global-job.sh`:

```bash
#!/usr/bin/env bash
# Global wiki maintenance — SINGLE WRITER of index.md/log.md/hot.md.
# Phase-1 whitelist: derived-file rebuild + read-only lint report. Nothing creative.
set -euo pipefail

CONFIG="$HOME/.obsidian-wiki/config"; source "$CONFIG"
: "${OBSIDIAN_VAULT_PATH:?}" "${WIKI_MACHINE_KEY:?}"
[[ "$WIKI_MACHINE_KEY" == "mac-mini-m4" ]] || { echo "[global-job] not the owner machine — exiting"; exit 0; }

LOCK="$HOME/.obsidian-wiki/state/global.lock"; mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"; flock -n 9 || { echo "[global-job] locked — exiting"; exit 0; }

SKILLS_ROOT=$(ls -d "$HOME/.claude/plugins/cache/yician-wiki-tools/wiki-kit"/*/ 2>/dev/null | sort -V | tail -1)
cd "$OBSIDIAN_VAULT_PATH"; git pull --quiet
mkdir -p _reports

claude -p "You are the wiki's global maintenance job — the ONLY writer of the global derived files. \
Vault: $OBSIDIAN_VAULT_PATH. Do these four things and nothing else: \
(1) Rebuild index.md from the current pages' frontmatter (title/summary/tags per directory), preserving its existing structure. \
(2) Rebuild log.md: regenerate the operations log from 'git log --grep=wiki-ingest --pretty=format:\"%ci %s\"' plus existing non-ingest entries. \
(3) Rebuild hot.md from the template in ${SKILLS_ROOT}wiki-ingest/SKILL.md: Recent Activity = last 3 wiki-ingest commits' summaries, update the frontmatter 'updated' timestamp. \
(4) Run the lint checks described in ${SKILLS_ROOT}wiki-lint/SKILL.md in REPORT-ONLY mode and write findings to _reports/lint-$(date +%F).md — do NOT fix anything. \
Do NOT create/edit/delete any page under concepts/, entities/, synthesis/, projects/, skills/, journal/. Do NOT run git commands." \
  --setting-sources "" \
  --allowedTools "Read,Grep,Glob,Write,Edit,Bash(git log:*),Bash(python3:*),Bash(date:*)" \
  --max-turns 60 | tail -3

# Guard: only the whitelisted paths may have changed.
CHANGED=$(git status --porcelain | awk '{print $2}')
BAD=$(echo "$CHANGED" | grep -vE '^(index\.md|log\.md|hot\.md|_reports/)' || true)
if [[ -n "$BAD" ]]; then
  echo "[global-job] ABORT: non-whitelisted changes:"; echo "$BAD"; git checkout -q -- .; exit 1
fi
[[ -z "$CHANGED" ]] && { echo "[global-job] nothing to do"; date +%s > "$HOME/.obsidian-wiki/state/global-heartbeat"; exit 0; }
git add index.md log.md hot.md _reports
printf '%s\n' "wiki-maintain(mac-mini-m4): rebuild derived files + lint report" "" "AI-Agent: Claude Code (scheduled)" | git commit -qF -
git push --quiet || { git pull --quiet && git push --quiet; }
date +%s > "$HOME/.obsidian-wiki/state/global-heartbeat"
echo "[global-job] done: $(git log -1 --format=%h)"
```

`chmod +x ~/github/obsidian-wiki/scripts/wiki-global-job.sh`

- [ ] **Step 2: Write the M4 plist**

`~/github/obsidian-wiki/scripts/com.obsidian-wiki.global.plist` — same shape as Task 3's plist with `Label` `com.obsidian-wiki.global`, script path `wiki-global-job.sh`, `Hour 9 / Minute 30`, log paths `/tmp/obsidian-wiki-global.{log,err}`.

- [ ] **Step 3: Commit**

```bash
cd ~/github/obsidian-wiki
git add scripts/wiki-global-job.sh scripts/com.obsidian-wiki.global.plist
printf '%s\n' 'feat(jobs): M4 global maintenance job — single writer of derived files, phase-1 whitelist' '' 'AI-Agent: Claude Code' 'AI-Session-IDs: a3a7f2a4-178a-4d15-aed1-8b7d02fe1ab3' | git commit -F -
```

---

### Task 5: Plugin version bump + retire the dead daily-update detector

**Files:**
- Modify: `~/github/obsidian-wiki/.skills/.claude-plugin/plugin.json` (version `1.2.3` → **2.0.0**, pending user semver confirmation)
- Delete: `~/github/obsidian-wiki/scripts/daily-update.sh`, `~/github/obsidian-wiki/scripts/com.obsidian-wiki.daily-update.plist` (schema-broken detector: reads a `last_updated` field that no longer exists, so `stale_count` is always 0; superseded by Tasks 3–4)
- Modify: `~/github/obsidian-wiki/.skills/daily-update/SKILL.md` — if it references the deleted script for setup mode, replace the setup section with a pointer to `wiki-ingest-job.sh`/plist install (grep first: `grep -rn "daily-update.sh" ~/github/obsidian-wiki/.skills/`)
- Test: `git grep daily-update.sh` returns only historical docs (Step 2)

**Interfaces:**
- Consumes: all functional changes from Tasks 1–4 (this is the same-release bump).
- Produces: wiki-kit 2.0.0 ready for `plugin marketplace` re-install on each machine (Task 6).

- [ ] **Step 1: Bump version + delete dead detector**

```bash
cd ~/github/obsidian-wiki
python3 - <<'EOF'
import json
p = ".skills/.claude-plugin/plugin.json"
d = json.load(open(p))
d["version"] = "2.0.0"
json.dump(d, open(p, "w"), indent=2); open(p, "a").write("\n")
print("bumped to", d["version"])
EOF
git rm -q scripts/daily-update.sh scripts/com.obsidian-wiki.daily-update.plist
```

- [ ] **Step 2: Verify no live references to the deleted script**

```bash
cd ~/github/obsidian-wiki && git grep -l "daily-update.sh" -- . | grep -v docs/ && echo "live refs remain — fix .skills/daily-update/SKILL.md per Files note" || echo "clean"
```
Expected: `clean` (after fixing any hit in `.skills/daily-update/SKILL.md`).

- [ ] **Step 3: Commit (single commit: bump + retirement, per versioning rule)**

```bash
cd ~/github/obsidian-wiki
git add .skills/.claude-plugin/plugin.json .skills/daily-update/SKILL.md 2>/dev/null
printf '%s\n' 'feat(wiki-kit)!: 2.0.0 — per-machine manifest shards, single-writer derived files, scheduled jobs' '' 'BREAKING CHANGE: manifest schema v2 (per-machine shards); ingest skills no longer write index/log/hot.' '' 'AI-Agent: Claude Code' 'AI-Session-IDs: a3a7f2a4-178a-4d15-aed1-8b7d02fe1ab3' | git commit -F -
```

---

### Task 6: Rollout (ordered; per machine)

**Files:**
- Modify per machine: `~/.obsidian-wiki/config` (add `WIKI_MACHINE_KEY`, `WIKI_INGEST_HARNESSES`), `~/Library/LaunchAgents/com.obsidian-wiki.ingest.plist` (from template), vault git remote + `pull.rebase`
- M4 additionally: `com.obsidian-wiki.global.plist`
- Test: one real launchd-triggered run per machine (Step 5)

**Interfaces:**
- Consumes: everything above, merged to fork `main` and plugin 2.0.0 published to the marketplace both machines install from.
- Produces: the running system.

**Ordering is load-bearing** (old-plugin machines writing the legacy manifest while new-plugin machines write shards = two systems fighting): finish Steps 1–3 on ALL machines before ANY machine loads a launchd job.

- [ ] **Step 1: Merge + publish (fork)** — merge `yician/per-machine-manifest` to `main`, push (user authorization required), then refresh the plugin on this machine per the `wiki-skill-manager` skill's re-sync flow; verify `claude plugin` shows wiki-kit 2.0.0 on Claude Code and Codex.

- [ ] **Step 2: MBP setup (this machine)** — append to `~/.obsidian-wiki/config`:
  ```bash
  printf '%s\n' 'WIKI_MACHINE_KEY="macbook-pro"' 'WIKI_INGEST_HARNESSES="claude,codex"' >> ~/.obsidian-wiki/config
  ```
  Vault remote/pull.rebase already done (2026-07-08). Run Task 3 Step 3's manual validation.

- [ ] **Step 3: M4 + M2 setup (via machine-tasks handoff)** — file one agent-handoff task per machine with: vault `git remote set-url origin git@github.com-yulin:yulin0629/obsidian-wiki-vault.git` + `git config --local pull.rebase true`; plugin update to 2.0.0; config keys (`mac-mini-m4` with its harness list incl. `hermes`/`pi` if present; `mac-mini-m2` likewise); manual foreground job run to validate.

- [ ] **Step 4: Install launchd jobs (each machine, after Step 1–3 done everywhere)**
  ```bash
  sed "s|__HOME__|$HOME|g" ~/github/obsidian-wiki/scripts/com.obsidian-wiki.ingest.plist > ~/Library/LaunchAgents/com.obsidian-wiki.ingest.plist
  # Stagger: MBP keep 08:30; M2 edit to 08:45; M4 to 09:00 (+ install the global plist, 09:30)
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.obsidian-wiki.ingest.plist
  launchctl list | grep obsidian-wiki
  ```

- [ ] **Step 5: Real-launchd validation** — trigger once without waiting for the calendar: `launchctl kickstart -k gui/$(id -u)/com.obsidian-wiki.ingest`; then check `/tmp/obsidian-wiki-ingest.log` for a clean run and the vault for a pushed `wiki-ingest(...)` commit. This closes the two remaining unverified assumptions: Keychain OAuth **under real launchd** (only simulated so far) and SSH push write-access (only read was proven).

- [ ] **Step 6: Post-rollout watch** — for the first week, check `_reports/lint-*.md` and the vault git log for conflicts or leak-guard aborts. After a clean week, design phase 2 (synthesize/cross-link on a branch, dedup candidates) as a separate plan.

---

## Self-Review

**1. Spec coverage:** Design decisions → tasks: manifest shards + seeding (T1), skill three-file strip + shard refs + journal collision fix (T2), per-machine ingest job with flock/leak-guard/push-retry (T3), M4 single-writer global job with whitelist guard (T4), dead detector retirement + version bump (T5), ordered rollout + real-launchd/SSH-write validation (T6). Deferred by design: synthesize/cross-link/dedup scheduling (phase 2), rebuild (manual forever). No gaps against the decision note (`basic-memory: wiki-跨機自動整理架構-2026-07-定稿`).

**2. Placeholder scan:** All commands and code are concrete. Two intentionally deferred concretenesses, both gated not vague: the semver value awaits user confirmation (constraint stated, value proposed); M4/M2 harness lists say "incl. hermes/pi if present" because which harnesses have session data on those machines is verified at handoff time by the executing agent (a runtime check, not a design hole).

**3. Type consistency:** `manifest.py path|init` (T1) match their uses in T2 skill text and T3/T4 scripts. `WIKI_MACHINE_KEY`/`WIKI_INGEST_HARNESSES` names are identical across config, scripts, and skill text. Commit prefix `wiki-ingest(` in T3 matches T4's `git log --grep=wiki-ingest`. Shard filename `.manifest.${WIKI_MACHINE_KEY}.json` (T3 script) matches T1's `manifest_path()` output.

**Known risks (accepted):** (a) `claude -p` inside launchd's env — simulated OK,真 launchd 首跑是 T6 S5 的驗收點; (b) LLM-rebuilt index.md fidelity — mitigated by git revertability + whitelist guard; (c) `--allowedTools` list may be too narrow for some ingest edge case — the job tee's full output to state files for diagnosis.
