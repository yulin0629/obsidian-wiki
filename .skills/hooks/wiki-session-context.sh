#!/usr/bin/env bash
# =============================================================================
# wiki-session-context.sh - Inject recent wiki activity at session start
# =============================================================================
# Fires as a Claude Code SessionStart hook. Prints hot.md (the ~500-word
# semantic snapshot of recent activity) and the tail of log.md to stdout,
# which Claude Code injects into context for SessionStart hooks.
#
# Defensive: any unexpected condition (vault not configured, files missing)
# exits 0 with no output, so this never interferes with non-wiki sessions.
# Fast by design: only cat/head/tail, no parsing.
# =============================================================================

set -uo pipefail

# shellcheck source=lib-vault-resolve.sh
source "$(dirname "$0")/lib-vault-resolve.sh"
VAULT=$(resolve_vault_path "$PWD")
[[ -z "$VAULT" ]] && exit 0

[[ -d "$VAULT" ]] || exit 0

HOT="$VAULT/hot.md"
LOG="$VAULT/log.md"

[[ -f "$HOT" || -f "$LOG" ]] || exit 0

echo "## Obsidian wiki context (auto-injected from $VAULT)"

if [[ -f "$HOT" ]]; then
  head -c 6000 "$HOT" 2>/dev/null
  echo
fi

if [[ -f "$LOG" ]]; then
  echo "Recent wiki activity (log.md tail):"
  tail -n 5 "$LOG" 2>/dev/null
fi

exit 0
