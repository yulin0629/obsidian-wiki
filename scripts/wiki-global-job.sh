#!/usr/bin/env bash
# Global wiki maintenance — SINGLE WRITER of index.md/log.md/hot.md.
# Phase-1 whitelist: derived-file rebuild + read-only lint report. Nothing creative.
set -euo pipefail

# launchd's minimal env may omit USER, which Claude Code needs to read its
# login-keychain OAuth credential (verified: without USER, `claude -p` reports
# "Not logged in"). Guarantee it regardless of what launchd provides.
export USER="${USER:-$(id -un)}"
export LOGNAME="${LOGNAME:-$USER}"

CONFIG="$HOME/.obsidian-wiki/config"
[[ -f "$CONFIG" ]] || { echo "[global-job] no config"; exit 1; }
# shellcheck source=/dev/null
# set -a so config vars are exported: child processes (manifest.py, claude -p)
# resolve WIKI_MACHINE_KEY from the environment, not the legacy fallback.
set -a; source "$CONFIG"; set +a
: "${OBSIDIAN_VAULT_PATH:?}" "${WIKI_MACHINE_KEY:?}"
[[ "$WIKI_MACHINE_KEY" == "mac-mini-m4" ]] || { echo "[global-job] not the owner machine — exiting"; exit 0; }

# mkdir-based lock (macOS has no flock(1)); stale locks broken by PID liveness check.
LOCKDIR="$HOME/.obsidian-wiki/state/global.lock.d"
mkdir -p "$(dirname "$LOCKDIR")"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  LOCKPID=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
  if [[ -n "$LOCKPID" ]] && kill -0 "$LOCKPID" 2>/dev/null; then
    echo "[global-job] another run holds the lock (pid $LOCKPID) — exiting"; exit 0
  fi
  echo "[global-job] removing stale lock"
  rm -rf "$LOCKDIR"; mkdir "$LOCKDIR"
fi
echo $$ > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR"' EXIT

SKILLS_ROOT=$(ls -d "$HOME/.claude/plugins/cache/yician-wiki-tools/wiki-kit"/*/ 2>/dev/null | sort -V | tail -1)
[[ -n "$SKILLS_ROOT" ]] || { echo "[global-job] wiki-kit plugin not installed"; exit 1; }

cd "$OBSIDIAN_VAULT_PATH"
git pull --quiet
mkdir -p _reports

claude -p "You are the wiki's global maintenance job — the ONLY writer of the global derived files. \
Vault: $OBSIDIAN_VAULT_PATH. Do these four things and nothing else: \
(1) Rebuild index.md from the current pages' frontmatter (title/summary/tags per directory), preserving its existing structure. \
(2) Rebuild log.md: regenerate the operations log from 'git log --grep=wiki-ingest --pretty=format:\"%ci %s\"' plus existing non-ingest entries. \
(3) Rebuild hot.md from the template in ${SKILLS_ROOT}wiki-ingest/SKILL.md: Recent Activity = last 3 wiki-ingest commits' summaries, update the frontmatter 'updated' timestamp. \
(4) Run the lint checks described in ${SKILLS_ROOT}wiki-lint/SKILL.md in REPORT-ONLY mode and write findings to _reports/lint-$(date +%F).md — do NOT fix anything. \
Do NOT create/edit/delete any page under concepts/, entities/, synthesis/, projects/, skills/, journal/. Do NOT run git commands that modify state (add/commit/push/checkout) — read-only 'git log' for steps 2-3 is allowed and expected." \
  --setting-sources "" \
  --allowedTools "Read,Grep,Glob,Write,Edit,Bash(git log:*),Bash(python3:*),Bash(date:*)" \
  --max-turns 60 | tail -3

# Guard: only the whitelisted paths may have changed.
CHANGED=$(git status --porcelain | awk '{print $2}')
BAD=$(echo "$CHANGED" | grep -vE '^(index\.md|log\.md|hot\.md|_reports/)' || true)
if [[ -n "$BAD" ]]; then
  echo "[global-job] ABORT: non-whitelisted changes:"; echo "$BAD"
  git checkout -q -- .; exit 1
fi
[[ -z "$CHANGED" ]] && { echo "[global-job] nothing to do"; date +%s > "$HOME/.obsidian-wiki/state/global-heartbeat"; exit 0; }
git add index.md log.md hot.md _reports
printf '%s\n' "wiki-maintain(mac-mini-m4): rebuild derived files + lint report" "" "AI-Agent: Claude Code (scheduled)" | git commit -qF -
git push --quiet || { git pull --quiet && git push --quiet; }
date +%s > "$HOME/.obsidian-wiki/state/global-heartbeat"
echo "[global-job] done: $(git log -1 --format=%h)"
