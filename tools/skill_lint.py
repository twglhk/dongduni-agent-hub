#!/usr/bin/env python3
"""Static lint for OpenCode skills and oh-my-opencode path rules.

Idea from Claude Code Game Studios `skill-test static` (MIT, commit 05ad7c8);
the checks are opencode's: https://opencode.ai/docs/skills/

    python3 tools/skill_lint.py [repo_root]

Skills are found by globbing `.opencode/skills/*/` and `.agents/skills/*/` (never a list, so a new
skill can't slip past). Per skill:
  FAIL  no SKILL.md; frontmatter missing or not YAML; `name` missing, not
        ^[a-z0-9]+(-[a-z0-9]+)*$, over 64 chars, or not the folder name;
        `description` missing or not 1-1024 chars; description cut short by
        an unquoted ` #` (YAML reads the rest as a comment); a relative
        markdown link in SKILL.md or references/*.md that points nowhere.
  WARN  frontmatter keys opencode ignores; SKILL.md body over 150 lines or a
        reference over 300 lines (size budget).
Rules in `.claude/rules/*.md` (oh-my-opencode rules): FAIL without `paths`/`globs`,
WARN when a pattern matches no file in the repo.

A file that can't be read is NOT ASSESSED, never OK. Exit 0 only when every
item is OK or WARN and at least one skill was scanned.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
KNOWN_KEYS = {"name", "description", "license", "compatibility", "metadata"}
SKILL_DIRS = (".opencode/skills", ".agents/skills", ".claude/skills")  # `.agents/skills` is where `dah engine add` puts an engine's skills
BODY_BUDGET = 150
REF_BUDGET = 300


def split_frontmatter(text: str) -> tuple[str, str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    return text[4:end], text[end + 4 :].lstrip("\n")


def raw_description(front: str) -> str | None:
    for line in front.splitlines():
        if line.startswith("description:"):
            return line[len("description:") :].strip()
    return None


def broken_links(md: Path) -> list[str]:
    out: list[str] = []
    # Examples inside fenced blocks and `inline code` are not links.
    text = re.sub(r"```.*?```", "", md.read_text(encoding="utf-8"), flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    for target in LINK_RE.findall(text):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        path = target.split("#", 1)[0]
        if path and not (md.parent / path).exists():
            out.append(f"{md.name} -> {target}")
    return out


def lint_skill(folder: Path) -> tuple[str, list[str]]:
    skill = folder / "SKILL.md"
    if not skill.is_file():
        return "FAIL", ["no SKILL.md"]
    try:
        text = skill.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        return "NOT ASSESSED", [f"SKILL.md unreadable: {err}"]
    fails: list[str] = []
    warns: list[str] = []
    parts = split_frontmatter(text)
    if parts is None:
        return "FAIL", ["no frontmatter block (--- … ---)"]
    front, body = parts
    try:
        meta = yaml.safe_load(front)
    except yaml.YAMLError as err:
        return "FAIL", [f"frontmatter is not YAML: {err}".splitlines()[0]]
    if not isinstance(meta, dict):
        return "FAIL", ["frontmatter is not a mapping"]

    name = meta.get("name")
    if not isinstance(name, str) or not name:
        fails.append("name missing")
    else:
        if not NAME_RE.match(name) or len(name) > 64:
            fails.append(f"name {name!r} breaks ^[a-z0-9]+(-[a-z0-9]+)*$ or 64 chars")
        if name != folder.name:
            fails.append(f"name {name!r} != folder {folder.name!r}")

    desc = meta.get("description")
    if not isinstance(desc, str) or not desc.strip():
        fails.append("description missing")
    else:
        if len(desc) > 1024:
            fails.append(f"description {len(desc)} chars > 1024")
        raw = raw_description(front)
        if raw and raw[0] not in "\"'|>" and " #" in raw:
            fails.append("description cut at unquoted ' #' (YAML comment); quote it")

    extra = sorted(set(meta) - KNOWN_KEYS)
    if extra:
        warns.append(f"keys opencode ignores: {', '.join(extra)}")

    body_lines = body.count("\n")
    if body_lines > BODY_BUDGET:
        warns.append(f"SKILL.md body {body_lines} lines > {BODY_BUDGET}")

    docs = [skill, *sorted((folder / "references").glob("*.md"))]
    for doc in docs[1:]:
        n = doc.read_text(encoding="utf-8").count("\n")
        if n > REF_BUDGET:
            warns.append(f"references/{doc.name} {n} lines > {REF_BUDGET}")
    for doc in docs:
        fails.extend(f"broken link {b}" for b in broken_links(doc))

    if fails:
        return "FAIL", fails + warns
    return ("WARN", warns) if warns else ("OK", [])


def lint_rule(rule: Path, root: Path) -> tuple[str, list[str]]:
    parts = split_frontmatter(rule.read_text(encoding="utf-8"))
    if parts is None:
        return "FAIL", ["no frontmatter block"]
    try:
        meta = yaml.safe_load(parts[0]) or {}
    except yaml.YAMLError as err:
        return "FAIL", [f"frontmatter is not YAML: {err}".splitlines()[0]]
    patterns = meta.get("paths") or meta.get("globs")
    if isinstance(patterns, str):
        patterns = [patterns]
    if not patterns:
        return "FAIL", ["no paths/globs, so it never injects"]
    empty = [p for p in patterns if not any(root.glob(p))]
    return ("WARN", [f"matches no file: {p}" for p in empty]) if empty else ("OK", [])


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    skills = sorted(p for d in SKILL_DIRS for p in (root / d).glob("*/") if p.is_dir())
    rules = sorted((root / ".claude/rules").glob("*.md"))
    if not skills:
        print(f"NOT ASSESSED: no skill folders under {' or '.join(str(root / d) for d in SKILL_DIRS)}")
        return 2
    results = [(f"skill {p.name}", *lint_skill(p)) for p in skills]
    results += [(f"rule {p.name}", *lint_rule(p, root)) for p in rules]
    for label, verdict, notes in results:
        print(f"{verdict:<12} {label}")
        for note in notes:
            print(f"             - {note}")
    counts = {v: sum(1 for _, r, _ in results if r == v) for v in ("OK", "WARN", "FAIL", "NOT ASSESSED")}
    print(f"scanned {len(skills)} skills, {len(rules)} rules: " + ", ".join(f"{v} {n}" for v, n in counts.items()))
    return 1 if counts["FAIL"] or counts["NOT ASSESSED"] else 0


if __name__ == "__main__":
    sys.exit(main())
