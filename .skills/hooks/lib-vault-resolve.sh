# shellcheck shell=bash
# =============================================================================
# lib-vault-resolve.sh - Shared vault path resolution for Claude Code hooks
# =============================================================================
# Sourced by wiki-validate-frontmatter.sh and wiki-session-context.sh.
# Implements the Config Resolution Protocol (see llm-wiki/SKILL.md):
#   1. OBSIDIAN_VAULT_PATH env var, if already exported
#   2. Walk up from the caller's cwd to $HOME looking for a .env that sets
#      OBSIDIAN_VAULT_PATH (supports per-project vault overrides)
#   3. ~/.obsidian-wiki/config
#
# Config/.env files are parsed with grep+cut+tr, never sourced, so an
# untrusted .env cannot execute arbitrary shell in the hook process.
# =============================================================================

# _vault_resolve_extract FILE
# Prints the value of OBSIDIAN_VAULT_PATH= from FILE (quotes stripped), or
# nothing if not present.
_vault_resolve_extract() {
  grep -m1 '^OBSIDIAN_VAULT_PATH=' "$1" 2>/dev/null | cut -d= -f2- | tr -d "\"'"
}

# _vault_resolve_walk_up CWD
# Walks from CWD up to $HOME (inclusive) looking for a .env with
# OBSIDIAN_VAULT_PATH set. Prints the resolved path and returns 0 on match.
_vault_resolve_walk_up() {
  local dir="$1" home="$HOME" val
  [[ -n "$dir" ]] || return 1
  while true; do
    if [[ -f "$dir/.env" ]]; then
      val=$(_vault_resolve_extract "$dir/.env")
      if [[ -n "$val" ]]; then
        printf '%s' "$val"
        return 0
      fi
    fi
    [[ "$dir" == "$home" || "$dir" == "/" ]] && break
    dir=$(dirname "$dir")
  done
  return 1
}

# resolve_vault_path [CWD]
# Prints the resolved OBSIDIAN_VAULT_PATH, or nothing if unresolvable.
resolve_vault_path() {
  local cwd="${1:-}" val

  if [[ -n "${OBSIDIAN_VAULT_PATH:-}" ]]; then
    printf '%s' "$OBSIDIAN_VAULT_PATH"
    return 0
  fi

  if val=$(_vault_resolve_walk_up "$cwd"); then
    printf '%s' "$val"
    return 0
  fi

  local config="$HOME/.obsidian-wiki/config"
  if [[ -f "$config" ]]; then
    val=$(_vault_resolve_extract "$config")
    if [[ -n "$val" ]]; then
      printf '%s' "$val"
      return 0
    fi
  fi

  return 1
}
