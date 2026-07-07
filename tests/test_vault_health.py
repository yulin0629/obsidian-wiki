from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".skills" / "wiki-lint" / "scripts"))

import vault_health  # noqa: E402


FULL_FRONTMATTER = """---
title: {title}
category: concept
tags:
  - concept
sources:
  - https://example.com
created: 2026-01-01T00:00:00+08:00
updated: 2026-01-01T00:00:00+08:00
summary: A short summary.
---

# {title}
"""


class VaultHealthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "concepts").mkdir()
        (self.vault / "journal").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, rel: str, content: str) -> Path:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _run(self) -> dict:
        return vault_health.run_health_check(self.vault)

    # --- frontmatter checks ---

    def test_complete_frontmatter_has_no_issues(self) -> None:
        self._write("concepts/foo.md", FULL_FRONTMATTER.format(title="Foo"))
        result = self._run()
        types = {i["type"] for i in result["issues"]}
        self.assertNotIn("no_frontmatter", types)
        self.assertNotIn("missing_frontmatter_fields", types)
        self.assertNotIn("missing_summary", types)

    def test_missing_sources_is_warning(self) -> None:
        content = FULL_FRONTMATTER.format(title="Foo").replace(
            "sources:\n  - https://example.com\n", ""
        )
        self._write("concepts/foo.md", content)
        result = self._run()
        issue = next(i for i in result["issues"] if i["type"] == "missing_frontmatter_fields")
        self.assertEqual(issue["severity"], "warning")
        self.assertIn("sources", issue["message"])

    def test_missing_summary_is_info(self) -> None:
        content = FULL_FRONTMATTER.format(title="Foo").replace("summary: A short summary.\n", "")
        self._write("concepts/foo.md", content)
        result = self._run()
        issue = next(i for i in result["issues"] if i["type"] == "missing_summary")
        self.assertEqual(issue["severity"], "info")

    def test_missing_frontmatter_block(self) -> None:
        self._write("concepts/foo.md", "# Foo\n\nNo frontmatter here.\n")
        result = self._run()
        issue = next(i for i in result["issues"] if i["type"] == "no_frontmatter")
        self.assertEqual(issue["severity"], "warning")

    # --- broken link checks ---

    def test_broken_link_detected(self) -> None:
        self._write("concepts/foo.md", FULL_FRONTMATTER.format(title="Foo") + "\n[[nonexistent-page]]\n")
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(len(broken), 1)
        self.assertIn("nonexistent-page", broken[0]["message"])

    def test_code_fence_link_not_flagged(self) -> None:
        content = FULL_FRONTMATTER.format(title="Foo") + "\n```\n[[fake-link]]\n```\n"
        self._write("concepts/foo.md", content)
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(len(broken), 0)

    def test_inline_code_link_not_flagged(self) -> None:
        content = FULL_FRONTMATTER.format(title="Foo") + "\nUse `[[ -z \"$VAR\" ]]` in shell.\n"
        self._write("concepts/foo.md", content)
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(len(broken), 0)

    def test_asset_link_not_flagged(self) -> None:
        self._write("_meta/dashboard.base", "{}")
        # _meta is excluded from the note index, but its files are still
        # indexed as vault assets so links to them resolve.
        content = FULL_FRONTMATTER.format(title="Foo") + "\n[[dashboard.base]]\n"
        self._write("concepts/foo.md", content)
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(len(broken), 0)

    def test_dotted_title_link_resolves(self) -> None:
        self._write("concepts/release v2.4 notes.md", FULL_FRONTMATTER.format(title="Release v2.4 Notes"))
        content = FULL_FRONTMATTER.format(title="Foo") + "\n[[release v2.4 notes]]\n"
        self._write("concepts/foo.md", content)
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(len(broken), 0)

    def test_alias_link_resolves(self) -> None:
        aliased = FULL_FRONTMATTER.format(title="Bar").replace(
            "category: concept\n", "category: concept\naliases:\n  - Baz Alias\n"
        )
        self._write("concepts/bar.md", aliased)
        content = FULL_FRONTMATTER.format(title="Foo") + "\n[[Baz Alias]]\n"
        self._write("concepts/foo.md", content)
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(len(broken), 0)

    def test_category_qualified_link_resolves(self) -> None:
        self._write("concepts/bar.md", FULL_FRONTMATTER.format(title="Bar"))
        content = FULL_FRONTMATTER.format(title="Foo") + "\n[[concepts/bar]]\n"
        self._write("concepts/foo.md", content)
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(len(broken), 0)

    # --- orphan checks ---

    def test_orphan_detected(self) -> None:
        self._write("concepts/lonely.md", FULL_FRONTMATTER.format(title="Lonely"))
        result = self._run()
        orphans = [i for i in result["issues"] if i["type"] == "orphan"]
        self.assertEqual([i["files"][0] for i in orphans], ["concepts/lonely.md"])

    def test_linked_page_not_orphan(self) -> None:
        self._write("concepts/bar.md", FULL_FRONTMATTER.format(title="Bar"))
        content = FULL_FRONTMATTER.format(title="Foo") + "\n[[bar]]\n"
        self._write("concepts/foo.md", content)
        result = self._run()
        orphan_files = {i["files"][0] for i in result["issues"] if i["type"] == "orphan"}
        self.assertNotIn("concepts/bar.md", orphan_files)

    def test_journal_pages_skip_orphan_check(self) -> None:
        self._write("journal/2026-01-01.md", FULL_FRONTMATTER.format(title="Daily"))
        result = self._run()
        orphan_files = {i["files"][0] for i in result["issues"] if i["type"] == "orphan"}
        self.assertNotIn("journal/2026-01-01.md", orphan_files)

    def test_system_files_not_scanned(self) -> None:
        self._write("index.md", "---\ntitle: Wiki Index\n---\n")
        self._write("log.md", "---\ntitle: Wiki Log\n---\n")
        result = self._run()
        self.assertEqual(result["total_notes"], 0)

    # --- dash-normalization consistency (orphan vs. broken-link) ---

    def test_em_dash_link_resolves_and_does_not_orphan(self) -> None:
        self._write("concepts/my-page.md", FULL_FRONTMATTER.format(title="My Page"))
        content = FULL_FRONTMATTER.format(title="Foo") + "\n[[my—page]]\n"  # em-dash
        self._write("concepts/foo.md", content)
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(broken, [])
        orphan_files = {i["files"][0] for i in result["issues"] if i["type"] == "orphan"}
        self.assertNotIn("concepts/my-page.md", orphan_files)

    # --- BOM handling ---

    def test_bom_prefixed_page_not_flagged_no_frontmatter(self) -> None:
        p = self.vault / "concepts" / "foo.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(FULL_FRONTMATTER.format(title="Foo"), encoding="utf-8-sig")
        result = self._run()
        types = {i["type"] for i in result["issues"]}
        self.assertNotIn("no_frontmatter", types)

    # --- system files as link sources ---

    def test_system_file_broken_link_is_reported(self) -> None:
        self._write("hot.md", "---\ntitle: Hot\n---\n\n[[nonexistent-page]]\n")
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(len(broken), 1)
        self.assertIn("hot.md", broken[0]["files"][0])

    def test_system_file_still_excluded_from_frontmatter_and_orphan_checks(self) -> None:
        self._write("hot.md", "no frontmatter, no links here.\n")
        result = self._run()
        flagged_files = {f for i in result["issues"] for f in i["files"]}
        self.assertNotIn("hot.md", flagged_files)

    def test_log_md_narrative_links_are_not_scanned(self) -> None:
        # log.md is an append-only narrative journal: [[...]] text in its
        # entries quotes link syntax rather than asserting curated links, so
        # it must not feed the broken-link check (unlike hot.md/index.md).
        self._write("log.md", "---\ntitle: Log\n---\n- [T] LINT found [[nonexistent-page]] mention\n")
        result = self._run()
        broken = [i for i in result["issues"] if i["type"] == "broken_link"]
        self.assertEqual(broken, [])


class DriftGuardTest(unittest.TestCase):
    """Guards EXCLUDE_DIRS/SYSTEM_FILES/REQUIRED_FIELDS in vault_health.py
    against silently drifting out of sync with the equivalent lists hardcoded
    in .skills/hooks/wiki-validate-frontmatter.sh."""

    def _hook_source(self) -> str:
        hook_path = (
            Path(__file__).resolve().parent.parent
            / ".skills" / "hooks" / "wiki-validate-frontmatter.sh"
        )
        return hook_path.read_text(encoding="utf-8")

    def test_exclude_dirs_match_hook_skip_list(self) -> None:
        source = self._hook_source()
        # Directory names appear as "*/name/*" inside the hook's skip case.
        hook_dirs = set(re.findall(r"\*/([A-Za-z0-9_.-]+)/\*", source))
        self.assertTrue(hook_dirs, "could not extract any skip dirs from hook script")
        self.assertEqual(hook_dirs, vault_health.EXCLUDE_DIRS)

    def test_system_files_match_hook_skip_list(self) -> None:
        source = self._hook_source()
        m = re.search(r"case \"\$BASENAME\" in\s*\n\s*([\w.|]+)\)\s*exit 0", source)
        self.assertIsNotNone(m, "could not extract system-file skip list from hook script")
        hook_files = set(m.group(1).split("|"))
        self.assertEqual(hook_files, vault_health.SYSTEM_FILES)

    def test_required_fields_match_hook_field_list(self) -> None:
        source = self._hook_source()
        m = re.search(r"for field in ([\w\s]+?); do", source)
        self.assertIsNotNone(m, "could not extract required-field list from hook script")
        hook_fields = m.group(1).split()
        self.assertEqual(hook_fields, vault_health.REQUIRED_FIELDS)


if __name__ == "__main__":
    unittest.main()
