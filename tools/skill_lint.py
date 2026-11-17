#!/usr/bin/env python3
"""Static lint for OpenCode skills and oh-my-opencode path rules.

Idea from Claude Code Game Studios `skill-test static` (MIT, commit 05ad7c8);
the checks are opencode's: https://opencode.ai/docs/skills/

    python3 tools/skill_lint.py [repo_root]

Standard library only. It lives in `tools/` of the agent-hub repository and is not part of the installed
package: run it from a clone. Skills are found by globbing `.opencode/skills/*/`, `.agents/skills/*/` and
`.claude/skills/*/` (never a list, so a new skill can't slip past). Per skill:
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

import json
import re
import sys
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
KNOWN_KEYS = {"name", "description", "license", "compatibility", "metadata"}
SKILL_DIRS = (".opencode/skills", ".agents/skills", ".claude/skills")  # `.agents/skills` is where `dah engine add` puts an engine's skills
BODY_BUDGET = 150
REF_BUDGET = 300


class FrontmatterError(ValueError):
    pass


INT_RE = re.compile(r"[-+]?(0|[1-9][0-9_]*)$")
FLOAT_RE = re.compile(r"[-+]?([0-9][0-9_]*)?\.[0-9]+([eE][-+]?[0-9]+)?$")
KEY_RE = re.compile(r"""("[^"]*"|'[^']*'|[^\s:#'"\[\]{},&*!|>%@`-][^:]*?)\s*:(?:\s+(.*))?$""")


def plain_scalar(text: str) -> Any:
    low = text.lower()
    if low in ("", "~", "null"):
        return None
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if INT_RE.match(text):
        return int(text.replace("_", ""))
    if FLOAT_RE.match(text):
        return float(text.replace("_", ""))
    return text


def quoted_scalar(text: str) -> str:
    quote = text[0]
    i = 1
    while i < len(text):
        if quote == "'" and text.startswith("''", i):
            i += 2
            continue
        if quote == '"' and text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            break
        i += 1
    else:
        raise FrontmatterError("unterminated quoted string")
    rest = text[i + 1 :].strip()
    if rest and not rest.startswith("#"):
        raise FrontmatterError("text after a closing quote")
    inner = text[1:i]
    if quote == "'":
        return inner.replace("''", "'")
    try:
        return json.loads('"' + inner + '"')
    except ValueError:
        raise FrontmatterError("unsupported escape in a double-quoted string")


def block_scalar(style: str, block: list[str]) -> str:
    body = list(block)
    while body and not body[-1].strip():
        body.pop()
    indent = min((len(ln) - len(ln.lstrip()) for ln in body if ln.strip()), default=0)
    text = "\n".join(ln[indent:] for ln in body)
    if style.startswith("|"):
        return text
    return "\n".join(" ".join(para.split("\n")) for para in text.split("\n\n"))


def value_of(rest: str, block: list[str]) -> Any:
    if rest[:1] in ("|", ">"):
        return block_scalar(rest, block)
    if not rest:
        body = [ln for ln in block if ln.strip() and not ln.lstrip().startswith("#")]
        if not body:
            return None
        if body[0].lstrip().startswith("- "):
            return [plain_scalar(ln.lstrip()[2:].strip()) for ln in body if ln.lstrip().startswith("- ")]
        indent = len(body[0]) - len(body[0].lstrip())
        return parse_mapping([ln[indent:] if ln.strip() else "" for ln in block])
    if rest[0] in ("'", '"'):
        return quoted_scalar(" ".join([rest, *(ln.strip() for ln in block if ln.strip())]))
    text = " ".join([rest, *(ln.strip() for ln in block if ln.strip())])
    text = re.split(r"\s#", text, maxsplit=1)[0].strip()
    if text[:1] in ("@", "`", "%", "*", "&"):
        raise FrontmatterError(f"a value cannot start with {text[0]!r}; quote it")
    if ": " in text or text.endswith(":"):
        raise FrontmatterError("mapping values are not allowed here (a colon and a space inside an unquoted value; quote it)")
    if text.startswith("[") and text.endswith("]"):
        return [plain_scalar(x.strip().strip("'\"")) for x in text[1:-1].split(",") if x.strip()]
    return plain_scalar(text)


def parse_mapping(lines: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip() or ln.lstrip().startswith("#"):
            i += 1
            continue
        if ln[0] in " \t":
            raise FrontmatterError(f"unexpected indentation: {ln.strip()[:40]!r}")
        m = KEY_RE.match(ln.rstrip())
        if not m:
            raise FrontmatterError(f"not a `key: value` line: {ln.strip()[:40]!r}")
        key = m.group(1).strip("'\"")
        i += 1
        block: list[str] = []
        while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
            block.append(lines[i])
            i += 1
        out[key] = value_of((m.group(2) or "").strip(), block)
    return out


def parse_frontmatter(front: str) -> Any:
    return parse_mapping(front.splitlines())


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
        meta = parse_frontmatter(front)
    except FrontmatterError as err:
        return "FAIL", [f"frontmatter is not YAML: {err}"]

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
        meta = parse_frontmatter(parts[0])
    except FrontmatterError as err:
        return "FAIL", [f"frontmatter is not YAML: {err}"]
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
