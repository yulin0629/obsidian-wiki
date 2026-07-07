#!/usr/bin/env python3
"""
vault_health.py - Obsidian Wiki Health Check (mechanical checks only)

Adapted from obsidian-second-brain's scripts/vault_health.py (MIT license).
That version audits a personal second-brain vault (duplicates, stale tasks,
templates, empty folders, ...). This version is scoped down to only the three
mechanical checks the wiki-lint skill's Check 1/2/3/3a currently do by hand:

- Orphaned pages (zero incoming wikilinks)
- Broken wikilinks ([[target]] that resolves to no page or vault asset)
- Missing/incomplete required frontmatter, and missing `summary` (soft warning)

Everything else in wiki-lint (contradictions, provenance drift, tag cohesion,
consolidate actions, ...) needs LLM judgment and stays in the skill markdown.

Pure stdlib, no dependencies.

Usage:
    python3 scripts/vault_health.py --path <vault>
    python3 scripts/vault_health.py --path <vault> --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Vault-root files that are infrastructure, not content pages. They are still
# loaded (see load_vault) so their outgoing wikilinks participate in the
# broken-link check, but they are excluded from orphan/frontmatter checks and
# from the orphan check's link-target pool.
# Keep in sync with the "Skip vault-root system files" case in
# .skills/hooks/wiki-validate-frontmatter.sh.
SYSTEM_FILES = {"index.md", "log.md", "hot.md", "_insights.md"}

# System files whose [[...]] text is narrative (append-only journal entries
# quoting link syntax, error messages, etc.), not curated links. Their
# outgoing links are NOT fed to the broken-link check, unlike the other
# SYSTEM_FILES (index/hot/_insights are curated pointers where a dangling
# reference is a real defect).
NARRATIVE_SYSTEM_FILES = {"log.md"}
# Folders excluded from the note scan (staging areas, non-page content, exports).
# Keep in sync with the "Skip staging/system directories" case in
# .skills/hooks/wiki-validate-frontmatter.sh and with .skills/wiki-lint/SKILL.md.
EXCLUDE_DIRS = {
    "_meta", "_raw", "_staging", "wiki-export", ".obsidian", ".git", "_archive", "_archives", ".trash",
}
# Folders excluded from the full-file asset index (link-resolution target list).
# Narrower than EXCLUDE_DIRS: files under _meta/ (e.g. *.base dashboards) are
# legitimate wikilink targets even though they aren't scanned as content pages.
ASSET_EXCLUDE_DIRS = {".obsidian", ".git"}
# Orphan check is skipped for these top-level folders: journal/ is a dated
# sequence (like second-brain's Daily) where incoming links aren't expected.
ORPHAN_SKIP_FOLDERS = {"journal"}

# Keep in sync with the `for field in ...` list in
# .skills/hooks/wiki-validate-frontmatter.sh.
REQUIRED_FIELDS = ["title", "category", "tags", "sources", "created", "updated"]
MAX_SUMMARY_LEN = 200

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
ALIAS_RE = re.compile(r"^aliases:\s*\n((?:\s+-\s+.+\n?)+)", re.MULTILINE)
ALIAS_ITEM_RE = re.compile(r"^\s+-\s+(.+)$", re.MULTILINE)
CODE_FENCE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# Built from code points so em/en dash stay unambiguous in source.
_EM_DASH, _EN_DASH = "—", "–"


def _strip_code(text: str) -> str:
    """Remove fenced code blocks and inline code before scanning for wikilinks,
    so shell/example snippets like `[[ -z "$VAR" ]]` are never treated as
    real links."""
    return INLINE_CODE_RE.sub("", CODE_FENCE_BLOCK_RE.sub("", text))


def _normalize_dashes(s: str) -> str:
    """Convert em-dash/en-dash to a regular hyphen so link targets resolve
    regardless of which dash style the filename or the link text used."""
    return s.replace(_EM_DASH, "-").replace(_EN_DASH, "-")


def parse_aliases(frontmatter: str) -> list[str]:
    block = ALIAS_RE.search(frontmatter)
    if not block:
        return []
    return [m.strip().strip('"\'').lower() for m in ALIAS_ITEM_RE.findall(block.group(1))]


def _is_excluded(rel_parts: tuple[str, ...]) -> bool:
    return any(p in EXCLUDE_DIRS for p in rel_parts)


def index_vault_files(vault: Path) -> set[str]:
    """Lowercased relative paths and bare filenames of every non-excluded vault
    file (including non-.md assets like .base/.canvas), so links to those
    resolve instead of being flagged broken."""
    files: set[str] = set()
    for f in vault.rglob("*"):
        rel_parts = f.relative_to(vault).parts
        if any(p in ASSET_EXCLUDE_DIRS for p in rel_parts):
            continue
        if not f.is_file():
            continue
        files.add(f.relative_to(vault).as_posix().lower())
        files.add(f.name.lower())
    return files


def load_vault(vault: Path) -> dict:
    """Load every content page, plus a link-source-only entry (marked
    "system": True) for each vault-root SYSTEM_FILES file so its outgoing
    wikilinks still feed the broken-link check. System entries are excluded
    from orphan/frontmatter checks and from the orphan check's link-target
    pool by every caller that filters on note["system"]."""
    notes: dict[str, dict] = {}
    for md in vault.rglob("*.md"):
        rel_path = md.relative_to(vault)
        rel_parts = rel_path.parts
        is_system = len(rel_parts) == 1 and rel_parts[0] in SYSTEM_FILES
        if _is_excluded(rel_parts):
            continue
        rel = rel_path.as_posix()
        # utf-8-sig strips a leading BOM so FRONTMATTER_RE (anchored at ^)
        # still matches pages saved with a byte-order mark.
        content = md.read_text(encoding="utf-8-sig", errors="replace")
        fm_match = FRONTMATTER_RE.match(content)
        frontmatter = fm_match.group(1) if fm_match else ""
        if is_system and rel_parts[0] in NARRATIVE_SYSTEM_FILES:
            links = []  # narrative journal: [[...]] mentions are quotes, not links
        else:
            links = [l.strip().rstrip("\\") for l in LINK_RE.findall(_strip_code(content))]
        notes[rel] = {
            "rel": rel,
            "stem": md.stem,
            "frontmatter": frontmatter,
            "has_frontmatter": bool(fm_match),
            "links": links,
            "aliases": parse_aliases(frontmatter),
            "system": is_system,
        }
    return notes


def _frontmatter_field_present(frontmatter: str, field: str) -> bool:
    return re.search(rf"^{re.escape(field)}:", frontmatter, re.MULTILINE) is not None


def _summary_value(frontmatter: str) -> str | None:
    m = re.search(r"^summary:\s*(.*)$", frontmatter, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip('"\'')


def check_frontmatter(notes: dict) -> list[dict]:
    issues = []
    for rel, note in sorted(notes.items()):
        if note["system"]:
            continue
        if not note["has_frontmatter"]:
            issues.append({
                "type": "no_frontmatter",
                "severity": "warning",
                "message": f"Missing frontmatter block: {rel}",
                "files": [rel],
            })
            continue
        missing = [f for f in REQUIRED_FIELDS if not _frontmatter_field_present(note["frontmatter"], f)]
        if missing:
            issues.append({
                "type": "missing_frontmatter_fields",
                "severity": "warning",
                "message": f"{rel} — missing: {', '.join(missing)}",
                "files": [rel],
            })

        summary = _summary_value(note["frontmatter"])
        if summary is None:
            issues.append({
                "type": "missing_summary",
                "severity": "info",
                "message": f"No summary field: {rel}",
                "files": [rel],
            })
        elif len(summary) > MAX_SUMMARY_LEN:
            issues.append({
                "type": "missing_summary",
                "severity": "info",
                "message": f"Summary exceeds {MAX_SUMMARY_LEN} chars ({len(summary)}): {rel}",
                "files": [rel],
            })
    return issues


def check_orphans(notes: dict) -> list[dict]:
    all_links = set()
    for note in notes.values():
        for link in note["links"]:
            lk = link.lower()
            if lk.endswith(".md"):
                lk = lk[:-3]
            # Links may be written as bare "page-name" or path-qualified
            # "category/page-name" — index both the full target and its last
            # path component so either link style resolves.
            all_links.add(lk)
            all_links.add(lk.rsplit("/", 1)[-1])
            all_links.add(lk.replace(" ", "-"))
            all_links.add(_normalize_dashes(lk))

    alias_set = set()
    for note in notes.values():
        for alias in note["aliases"]:
            alias_set.add(alias.lower())

    issues = []
    for rel, note in sorted(notes.items()):
        if note["system"]:
            continue
        top_folder = rel.split("/")[0] if "/" in rel else ""
        if top_folder in ORPHAN_SKIP_FOLDERS:
            continue
        stem_lower = note["stem"].lower()
        stem_norm = stem_lower.replace("-", " ").replace("_", " ")
        stem_dash_norm = _normalize_dashes(stem_lower)
        linked = (
            stem_lower in all_links
            or stem_norm in all_links
            or stem_dash_norm in all_links
            or any(alias in all_links or alias in alias_set for alias in note["aliases"])
        )
        if not linked:
            issues.append({
                "type": "orphan",
                "severity": "info",
                "message": f"No incoming links: {rel}",
                "files": [rel],
            })
    return issues


def check_broken_links(notes: dict, vault: Path) -> list[dict]:
    all_stems = {note["stem"].lower(): rel for rel, note in notes.items()}
    all_files = index_vault_files(vault)
    all_stems_dash_norm = {
        _normalize_dashes(note["stem"]).lower(): rel for rel, note in notes.items()
    }
    all_aliases: dict[str, str] = {}
    for rel, note in notes.items():
        for alias in note["aliases"]:
            all_aliases[alias.lower()] = rel

    issues = []
    for rel, note in sorted(notes.items()):
        for link in note["links"]:
            # Wikilink targets carry no extension; taking the last path
            # component with rsplit (not Path.stem) avoids truncating dotted
            # titles like "release v2.4 notes" at the first dot.
            link_name = link.rsplit("/", 1)[-1]
            if link_name.lower().endswith(".md"):
                link_name = link_name[:-3]
            link_stem = link_name.lower()
            link_norm = link_stem.replace("-", " ").replace("_", " ")
            link_dash_norm = _normalize_dashes(link_stem)
            resolved = (
                link_stem in all_stems
                or link_norm in all_stems
                or link_stem in all_aliases
                or link_norm in all_aliases
                or link_dash_norm in all_stems_dash_norm
                or link.lower() in all_files
                or link_name.lower() in all_files
            )
            if not resolved:
                issues.append({
                    "type": "broken_link",
                    "severity": "warning",
                    "message": f"[[{link}]] — broken link in {rel}",
                    "files": [rel],
                })
    return issues


def run_health_check(vault: Path) -> dict:
    print(f"Scanning vault: {vault}", file=sys.stderr)
    notes = load_vault(vault)
    total_notes = sum(1 for note in notes.values() if not note["system"])
    print(f"Found {total_notes} pages", file=sys.stderr)

    checks = [
        ("orphans", check_orphans(notes)),
        ("broken_links", check_broken_links(notes, vault)),
        ("frontmatter", check_frontmatter(notes)),
    ]

    all_issues = []
    counts = {}
    for label, issues in checks:
        counts[label] = len(issues)
        all_issues.extend(issues)

    return {
        "vault": str(vault),
        "total_notes": total_notes,
        "total_issues": len(all_issues),
        "counts": counts,
        "issues": all_issues,
    }


def print_report(result: dict) -> None:
    print("=" * 60)
    print("  VAULT HEALTH REPORT")
    print("=" * 60)
    print(f"  Pages scanned: {result['total_notes']}")
    print(f"  Issues found:  {result['total_issues']}")
    print()

    if result["total_issues"] == 0:
        print("Vault is clean. No issues found.")
        return

    for label, count in result["counts"].items():
        if count > 0:
            print(f"  {label}: {count}")

    by_type = defaultdict(list)
    for issue in result["issues"]:
        by_type[issue["type"]].append(issue)

    for issue_type, issues in by_type.items():
        print(f"\n{issue_type} ({len(issues)})")
        print("-" * 50)
        for issue in issues[:10]:
            print(f"  {issue['message']}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", required=True, help="path to the Obsidian vault")
    parser.add_argument("--json", action="store_true", help="output as JSON")
    args = parser.parse_args(argv)

    vault = Path(args.path).expanduser().resolve()
    if not vault.exists():
        print(f"Vault not found: {vault}", file=sys.stderr)
        return 1

    result = run_health_check(vault)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
