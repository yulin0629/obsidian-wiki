#!/usr/bin/env bash
# Per-machine wiki ingest job. Owns all git ops; claude -p does ingest only.
set -euo pipefail

CONFIG="$HOME/.obsidian-wiki/config"
[[ -f "$CONFIG" ]] || { echo "[ingest-job] no config"; exit 1; }
# shellcheck source=/dev/null
source "$CONFIG"
: "${OBSIDIAN_VAULT_PATH:?}" "${WIKI_MACHINE_KEY:?}" "${WIKI_INGEST_HARNESSES:?}"

# mkdir-based lock (macOS has no flock(1)); stale locks broken by PID liveness check.
LOCKDIR="$HOME/.obsidian-wiki/state/ingest-$WIKI_MACHINE_KEY.lock.d"
mkdir -p "$(dirname "$LOCKDIR")"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  LOCKPID=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
  if [[ -n "$LOCKPID" ]] && kill -0 "$LOCKPID" 2>/dev/null; then
    echo "[ingest-job] another run holds the lock (pid $LOCKPID) — exiting"; exit 0
  fi
  echo "[ingest-job] removing stale lock"
  rm -rf "$LOCKDIR"; mkdir "$LOCKDIR"
fi
echo $$ > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR"' EXIT

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
