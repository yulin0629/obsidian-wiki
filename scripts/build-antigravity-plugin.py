#!/usr/bin/env python3
"""Flatten .skills/<name>/SKILL.md into .agy-plugin/skills/<name>.md for Antigravity CLI.

Antigravity only supports single-file markdown skills (frontmatter + body) under
a plugin's skills/ directory -- it has no concept of a skill directory with
bundled references/scripts. This script regenerates .agy-plugin/ from .skills/
so the two never drift; do not hand-edit files under .agy-plugin/.
"""
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / ".skills"
OUT = REPO / ".agy-plugin"
EXCLUDE = {"skill-creator", "impl-validator"}

NOTE_TEMPLATE = """

> **Note (Antigravity conversion):** this skill originally included supporting \
files under `{dirs}` alongside its `SKILL.md`. Antigravity does not support \
bundled skill assets, so those files are not included here. See \
`{name}/` in the obsidian-wiki repo's `.skills/` directory for the full skill \
with its supporting files.
"""


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    skills_out = OUT / "skills"
    skills_out.mkdir(parents=True)

    (OUT / "plugin.json").write_text(
        '{\n  "name": "wiki-kit",\n  "version": "1.0.0"\n}\n'
    )

    converted = []
    for skill_dir in sorted(SRC.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name in EXCLUDE:
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        body = skill_md.read_text()
        extra_dirs = sorted(
            p.name + "/"
            for p in skill_dir.iterdir()
            if p.is_dir() and p.name != "agents"
        )
        if extra_dirs:
            body = body.rstrip("\n") + "\n" + NOTE_TEMPLATE.format(
                dirs=", ".join(extra_dirs), name=skill_dir.name
            )
        (skills_out / f"{skill_dir.name}.md").write_text(body)
        converted.append(skill_dir.name)

    print(f"Converted {len(converted)} skills into {skills_out}")


if __name__ == "__main__":
    sys.exit(main())
