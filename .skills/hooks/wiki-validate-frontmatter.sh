#!/usr/bin/env bash
# =============================================================================
# wiki-validate-frontmatter.sh - Warn on missing wiki page frontmatter
# =============================================================================
# Fires as a Claude Code PostToolUse hook after Write/Edit. Inspects the
# written/edited file and warns (non-blocking) if it looks like a wiki page
# but is missing required frontmatter fields per CLAUDE.md ("Every wiki page
# has required frontmatter: title, category, tags, sources, created, updated").
#
# Scope:
#   - Only inspects .md files inside OBSIDIAN_VAULT_PATH
#   - Skips staging/system paths: _raw/, _staging/, _meta/, .obsidian/, .git/,
#     .trash/, _archive(s)/, wiki-export/
#   - Skips vault-root system files: index.md, log.md, hot.md, _insights.md
#
# Exit codes:
#   0 = pass or not applicable (silent)
#   2 = warnings found; stderr is fed back to Claude so it can fix the page
#       (the write itself is not reverted)
#
# Defensive: any unexpected condition (missing jq, unparseable payload, vault
# not configured, file outside vault) exits 0 silently so this never
# interferes with non-wiki projects.
# =============================================================================

INPUT=$(cat)

command -v jq >/dev/null 2>&1 || exit 0

FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[[ -z "$FILE" ]] && exit 0
[[ "$FILE" == *.md ]] || exit 0
[[ -f "$FILE" ]] || exit 0

CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // ""' 2>/dev/null)

# shellcheck source=lib-vault-resolve.sh
source "$(dirname "$0")/lib-vault-resolve.sh"
VAULT=$(resolve_vault_path "$CWD")
[[ -z "$VAULT" ]] && exit 0

case "$FILE" in
  "$VAULT"/*) ;;
  *) exit 0 ;;
esac

# Skip staging/system directories
case "$FILE" in
  */_raw/*|*/_staging/*|*/_meta/*|*/.obsidian/*|*/.git/*|*/.trash/*|*/_archive/*|*/_archives/*|*/wiki-export/*)
    exit 0 ;;
esac

# Skip vault-root system files
BASENAME=$(basename "$FILE")
if [[ "$(dirname "$FILE")" == "$VAULT" ]]; then
  case "$BASENAME" in
    index.md|log.md|hot.md|_insights.md) exit 0 ;;
  esac
fi

WARNINGS=()

# Single pass over the file: first line, total '---' delimiter count, and the
# frontmatter body (lines between the first and second delimiter), joined
# with a record-separator control char (\x1e) so they can be split back out
# in bash without re-reading the file.
AWK_OUT=$(LC_ALL=C awk '
  NR==1 { gsub(/^\357\273\277/, "") }
  { if (NR==1) first=$0 }
  /^---$/ { c++; next }
  c==1 { fm = fm $0 "\n" }
  END { printf "%s\x1e%d\x1e%s", first, c, fm }
' "$FILE" 2>/dev/null)

FIRST_LINE="${AWK_OUT%%$'\x1e'*}"
REST="${AWK_OUT#*$'\x1e'}"
DELIMITER_COUNT="${REST%%$'\x1e'*}"
FRONTMATTER="${REST#*$'\x1e'}"
DELIMITER_COUNT=${DELIMITER_COUNT:-0}

if [[ "$FIRST_LINE" != "---" ]]; then
  WARNINGS+=("- no frontmatter block detected (file does not start with '---')")
else
  if [[ "$DELIMITER_COUNT" -lt 2 ]]; then
    WARNINGS+=("- missing closing '---' delimiter")
  fi

  if [[ "$FRONTMATTER" == *$'\t'* ]]; then
    WARNINGS+=("- frontmatter contains tab characters (YAML requires spaces)")
  fi

  # bash 3.2 (macOS default) has no associative arrays, so track presence as
  # a space-delimited string instead.
  FOUND_FIELDS=" "
  while IFS= read -r line; do
    [[ "$line" =~ ^([A-Za-z0-9_-]+): ]] && FOUND_FIELDS="$FOUND_FIELDS${BASH_REMATCH[1]} "
  done <<< "$FRONTMATTER"

  field_present() {
    case "$FOUND_FIELDS" in
      *" $1 "*) return 0 ;;
      *) return 1 ;;
    esac
  }

  for field in title category tags sources created updated; do
    field_present "$field" || WARNINGS+=("- missing '$field:'")
  done
  field_present "summary" || WARNINGS+=("- missing 'summary:' (optional, but recommended)")
fi

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
  {
    echo "Wiki frontmatter check: $BASENAME"
    printf '%s\n' "${WARNINGS[@]}"
    echo "Required frontmatter: title, category, tags, sources, created, updated (see AGENTS.md Vault Structure)"
  } >&2
  exit 2
fi

exit 0
