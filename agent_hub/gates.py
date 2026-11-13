"""Safety gates run before a lane's tool call (the PreToolUse hook in `hooks.py`). Pure functions, no state.

  kill    refuses a process kill whose pattern could hit the tower, other lanes or every process of the user
          (`pkill -f "x" -n -u $(whoami)` on BSD reads `-n`, `-u` and the user name as extra patterns and kills
          everything with the user name in its command line), bare names (`pkill node`), `kill -1`, `kill 0`.
  git     refuses sweeping the shared work tree: `git stash`, `reset --hard`, `clean`, `checkout -- .`,
          `restore .`, and a `git commit` without `-- <paths>` that would carry another lane's staged deletions.
          Off inside a linked worktree (a lane with its own checkout may reset it).
  env     refuses reading `.env` files (secrets); `.env.example` is fine.
  binary  refuses OpenCode's `apply_patch` (a text diff) on an existing binary file: one such call once stored
          70 MB in session history. Claude Code's Edit needs a Read first, so it is not guarded.

Commands are read as POSIX shell (Claude Code runs Bash, also on Windows through Git Bash). Quotes, separators,
comments and heredocs are understood: a heredoc body is data (`cat > x.sh <<EOF ... pkill ... EOF` writes a file,
it kills nothing) unless it is fed to a shell (`bash <<EOF`), then it is checked as commands; so is `sh -c "..."`
and `eval "..."`. `taskkill /IM name` is checked like `killall name`.

`check(tool, args, cwd)` returns (gate, message) for a call that must be refused, else None.
"""

from __future__ import annotations

import codecs
import getpass
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- shell lexing

SEPS = ";&|\n()`"
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
PREFIX = {"sudo", "command", "exec", "nohup", "time", "env", "nice", "timeout"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
MAX_DEPTH = 3  # nested `bash -c` / heredoc-to-shell levels that are still checked


@dataclass
class Cmd:
    words: list[str] = field(default_factory=list)
    heredocs: list[str] = field(default_factory=list)


def _word_at(s: str, i: int) -> tuple[str, int]:
    """A shell word starting at i (quotes removed), and the index after it."""
    out = ""
    n = len(s)
    while i < n and s[i] not in " \t" and s[i] not in SEPS and s[i] not in "<>":
        c = s[i]
        if c == "'":
            j = s.find("'", i + 1)
            j = n if j < 0 else j
            out += s[i + 1:j]
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    j += 1
                out += s[j]
                j += 1
            i = j + 1
        elif c == "\\" and i + 1 < n:
            out += s[i + 1]
            i += 2
        else:
            out += c
            i += 1
    return out, i


def parse(cmd: str) -> list[Cmd]:
    """Quote-aware split into simple commands. Separators: ; & | newline ( ) backtick. Heredoc bodies are attached
    to the command that reads them, never lexed as commands."""
    cmds: list[Cmd] = [Cmd()]
    pending: list[tuple[str, bool, Cmd]] = []  # (delimiter, strip leading tabs, owner) waiting for the next newline
    cur, has = "", False

    def flush() -> None:
        nonlocal cur, has
        if has:
            cmds[-1].words.append(cur)
        cur, has = "", False

    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if c == "'":
            j = cmd.find("'", i + 1)
            end = n if j < 0 else j
            cur += cmd[i + 1:end]
            has = True
            i = end
        elif c == '"':
            j = i + 1
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\" and j + 1 < n:
                    j += 1
                cur += cmd[j]
                j += 1
            has = True
            i = j
        elif c == "\\" and i + 1 < n:
            i += 1
            if cmd[i] != "\n":  # backslash-newline continues the line
                cur += cmd[i]
                has = True
        elif c == "#" and not has:  # a comment runs to the end of the line
            while i + 1 < n and cmd[i + 1] != "\n":
                i += 1
        elif cmd.startswith("<<<", i):  # a here-string: its word is data, not a heredoc
            cur += "<<<"
            has = True
            i += 3
            continue
        elif cmd.startswith("<<", i):
            flush()
            i += 2
            strip = i < n and cmd[i] == "-"
            i += 1 if strip else 0
            while i < n and cmd[i] in " \t":
                i += 1
            delim, i = _word_at(cmd, i)
            if delim:
                pending.append((delim, strip, cmds[-1]))
            continue
        elif c in " \t":
            flush()
        elif c in SEPS:
            flush()
            cmds.append(Cmd())
            if c == "\n" and pending:
                i = _read_bodies(cmd, i + 1, pending)
                pending = []
                continue
        else:
            cur += c
            has = True
        i += 1
    flush()
    for delim, strip, owner in pending:  # `<<EOF` with no newline after it: the body is empty
        owner.heredocs.append("")
    return [c for c in cmds if c.words or c.heredocs]


def _read_bodies(cmd: str, i: int, pending: list[tuple[str, bool, Cmd]]) -> int:
    """Consume the heredoc bodies that start at i, in order; return the index after the last delimiter line."""
    lines = cmd[i:].split("\n")
    k = 0
    pos = i
    for delim, strip, owner in pending:
        body: list[str] = []
        while k < len(lines):
            line = lines[k]
            k += 1
            pos += len(line) + 1
            if (line.lstrip("\t") if strip else line) == delim:
                break
            body.append(line)
        owner.heredocs.append("\n".join(body))
    return min(pos, len(cmd))


def commands(cmd: str) -> list[list[str]]:
    return [c.words for c in parse(cmd) if c.words]


def _head(words: list[str]) -> tuple[str, list[str]]:
    """The program name (prefixes such as sudo and VAR=x skipped, path dropped) and its arguments."""
    k = 0
    while k < len(words) and (words[k] in PREFIX or _ASSIGN.match(words[k])):
        k += 1
    name = (words[k] if k < len(words) else "").replace("\\", "/").split("/")[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name, words[k + 1:]


def inner_scripts(c: Cmd) -> list[str]:
    """Text that this command runs as shell: `sh -c "..."`, `eval ...`, a heredoc fed to a shell."""
    name, args = _head(c.words)
    out: list[str] = []
    if name in SHELLS:
        out += c.heredocs
        for j, a in enumerate(args):
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a) and j + 1 < len(args):
                out.append(args[j + 1])
                break
    elif name == "eval":
        out.append(" ".join(args))
    return out


def _walk(cmd: str, depth: int = 0):
    """Every simple command in cmd, including those a shell runs from -c, eval or a heredoc."""
    for c in parse(cmd):
        if c.words:
            yield c
        if depth < MAX_DEPTH:
            for script in inner_scripts(c):
                yield from _walk(script, depth + 1)


# ---------------------------------------------------------------- kill gate

PKILL_ARG_OPTS = set("FGgJMNPstUu")  # BSD pkill/pgrep options that take a value (`man pkill`)
# Words a kill pattern must never contain: agent-hub's own processes, the agents it runs, and the user (the user
# name and home are in almost every command line).
SHARED = ["agent_hub", "agent-hub", "dah", "tower", "opencode", "claude", "codex", "tmux", "cloudflared", "caffeinate",
          "whoami", "$user", "${user}", "$home", "${home}", "~/", "%username%", "%userprofile%"]
BARE = {"node", "bun", "deno", "python", "python3", "py", "pythonw", "zsh", "bash", "sh", "login", "uv", "uvx",
        "npm", "npx", "git", "cmd", "powershell", "pwsh", "conhost", "explorer"}


def shared_pattern(pattern: str, user: str, home: str) -> str | None:
    p = pattern.lower()
    if p.endswith(".exe"):
        p = p[:-4]
    if p in BARE:
        return f'bare name "{pattern}" matches every lane\'s helpers'
    if len(re.sub(r"[\s.*^$]", "", p)) < 3:
        return f'pattern "{pattern}" is too short'
    q = p.replace("\\", "/")
    for w in [*SHARED, user.lower(), home.lower().replace("\\", "/")]:
        if w and w in q:
            return f'pattern "{pattern}" contains "{w}"'
    return None


def pkill_problems(args: list[str], user: str, home: str) -> list[str]:
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or a == "-":
            break
        if re.fullmatch(r"-[A-Za-z]+", a) and a[-1] in PKILL_ARG_OPTS:
            i += 1  # its value is the next word
        i += 1
    rest = args[i:]
    if not rest:
        return ["pkill with no pattern kills everything its -u/-g/-t filter matches"]
    out: list[str] = []
    late = [a for a in rest[1:] if a.startswith("-")]
    if late:
        out.append(f"option(s) {' '.join(late)} come after the pattern; BSD pkill reads them as extra patterns. "
                   "Put every option before the pattern")
    out += [w for w in (shared_pattern(p, user, home) for p in rest) if w]
    return out


def killall_problems(args: list[str], user: str, home: str) -> list[str]:
    names: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-u", "-t"):
            i += 1  # a user or tty, not a process name
        elif a == "-c":
            i += 1
            names.append(args[i] if i < len(args) else "")
        elif not a.startswith("-"):
            names.append(a)
        i += 1
    if not names:
        return ["killall with no process name kills everything its -u/-t filter matches"]
    return [w for w in (shared_pattern(n, user, home) for n in names) if w]


def taskkill_problems(args: list[str], user: str, home: str) -> list[str]:
    names = [args[j + 1] for j, a in enumerate(args[:-1]) if a.lower() in ("/im", "-im")]
    return [w for w in (shared_pattern(n, user, home) for n in names) if w]


def kill_problems(args: list[str]) -> list[str]:
    pids = args[1:] if args and args[0].startswith("-") and args[0] != "--" else args
    if any(a in ("-1", "0") for a in pids):
        return ["kill -1 / kill 0 signals every process you own or your whole group"]
    return []


def kill_gate(cmd: str, user: str | None = None, home: str | None = None) -> list[str]:
    user = user if user is not None else _user()
    home = home if home is not None else str(Path.home())
    out: list[str] = []
    for c in _walk(cmd):
        name, args = _head(c.words)
        if name == "pkill":
            out += pkill_problems(args, user, home)
        elif name == "killall":
            out += killall_problems(args, user, home)
        elif name == "kill":
            out += kill_problems(args)
        elif name == "taskkill":
            out += taskkill_problems(args, user, home)
    return out


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no login name (some containers): only the fixed words apply
        return ""


KILL_MESSAGE = ("Kill gate refused this command. Nothing ran.\n{problems}\n"
                "The tower, the board and every lane share this machine. Kill by a PID you started (`$!`, a pidfile), "
                "or `pkill -n -f \"<specific script path>\"` with every option before one specific pattern.")

# ---------------------------------------------------------------- git sweep gate

_GIT_DENY = [
    (re.compile(r"^stash\b"), "git stash"),
    (re.compile(r"^reset\b.*--hard"), "git reset --hard"),
    (re.compile(r"^checkout(\s+-\S+)*(\s+--)?\s+(\.|:/)$"), "git checkout ."),
    (re.compile(r"^restore\b.*\s(\.|:/)$"), "git restore ."),
    (re.compile(r"^clean\b"), "git clean"),
]


def linked_worktree(path: str) -> bool:
    """True inside a `git worktree add` checkout (its `.git` is a file): that tree belongs to one lane."""
    try:
        p = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    for d in (p, *p.parents):
        g = d / ".git"
        try:
            if g.is_dir():
                return False
            if g.is_file():
                text = g.read_text(encoding="utf-8", errors="replace").replace("\\", "/")
                return text.startswith("gitdir:") and "/worktrees/" in text
        except OSError:
            return False
    return False


def _staged_removals(cwd: str, all_tracked: bool) -> list[str]:
    """Paths the next commit would delete or rename away: staged D/R, plus unstaged deletions with `commit -a`."""
    found: list[str] = []
    runs = [["diff", "--cached", "--name-status", "-M"]] + ([["diff", "--name-status", "-M"]] if all_tracked else [])
    for args in runs:
        try:
            r = subprocess.run(["git", *args], cwd=cwd or None, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=20)
        except (OSError, subprocess.SubprocessError):
            return found
        for line in r.stdout.splitlines():
            f = line.split("\t")
            if f and f[0][:1] in ("D", "R") and len(f) > 1:
                found.append(f[1])
    return found


def commit_removal_problems(words: list[str], cwd: str) -> list[str]:
    """`git commit` with no `-- <paths>` commits the whole index, so another lane's staged deletions ride along."""
    if "--" in words:
        return []
    flags = [w for w in words if w.startswith("-") and not w.startswith("--")]
    all_tracked = "--all" in words or any("a" in f[1:] for f in flags if not f.startswith("-m"))
    gone = _staged_removals(cwd, all_tracked)
    if not gone:
        return []
    shown = ", ".join(gone[:4]) + (f" (+{len(gone) - 4} more)" if len(gone) > 4 else "")
    return [f"git commit without `-- <paths>` would commit staged deletions or renames: {shown}"]


def git_sweep_problems(cmd: str, cwd: str = "") -> list[str]:
    out: list[str] = []
    for c in _walk(cmd):
        name, rest = _head(c.words)
        if name != "git":
            continue
        where = cwd
        j = 0  # git's global options (-C dir, -c k=v, --no-pager ...)
        while j < len(rest) and rest[j].startswith("-"):
            if rest[j] == "-C" and j + 1 < len(rest):
                where = os.path.join(cwd or ".", rest[j + 1])
            j += 2 if rest[j] in ("-C", "-c") else 1
        if where and linked_worktree(where):
            continue
        sub = " ".join(rest[j:])
        if rest[j:j + 1] == ["commit"]:
            out += commit_removal_problems(rest[j + 1:], where)
        out += [f"{label} sweeps the shared work tree" for rx, label in _GIT_DENY if rx.search(sub)]
    return out


GIT_MESSAGE = ("Git sweep gate refused this command. Nothing ran.\n{problems}\n"
               "Every lane works in this tree. For a clean tree use `git worktree add <dir outside the repo> HEAD`; "
               "commit only your own files with `git commit -m \"...\" -- <paths>`.")

# ---------------------------------------------------------------- .env read gate

_ENV_NAME = re.compile(r"(^|/)\.env(\.[^/]+)?$")


def env_problems(path: str) -> list[str]:
    base = path.replace("\\", "/")
    if _ENV_NAME.search(base) and not base.endswith(".env.example"):
        return [f"reading {path} is denied (secrets)"]
    return []


# ---------------------------------------------------------------- binary patch guard (OpenCode apply_patch)

CHUNK_BYTES = 64 * 1024
_HEADER = re.compile(r"^\*\*\* (?:Update File|Delete File|Add File|Move to): (.+)$")


def patch_paths(patch_text: str, directory: str) -> list[str]:
    seen: dict[str, None] = {}
    for line in re.split(r"\r?\n", patch_text):
        m = _HEADER.match(line)
        if m and m.group(1).strip():
            seen[os.path.normpath(os.path.join(directory, m.group(1).strip()))] = None
    return list(seen)


def is_binary_file(path: str) -> bool:
    """Streams the file in chunks: True on any NUL byte or invalid UTF-8 (a character split across chunks is fine)."""
    p = Path(path)
    try:
        if not p.is_file():
            return False
    except OSError:
        return False
    dec = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        with p.open("rb") as f:
            while True:
                chunk = f.read(CHUNK_BYTES)
                if not chunk:
                    break
                if b"\0" in chunk:
                    return True
                dec.decode(chunk, final=False)
        dec.decode(b"", final=True)
        return False
    except UnicodeDecodeError:
        return True
    except OSError:
        return False


def binary_problems(args: dict[str, Any], directory: str) -> list[str]:
    text = args.get("patchText")
    if not isinstance(text, str):
        return []
    return [f"- existing binary file: {p}" for p in patch_paths(text, directory) if is_binary_file(p)]


BINARY_MESSAGE = ("Binary patch guard refused apply_patch. Nothing was changed.\n{problems}\n"
                  "apply_patch is a text diff tool. Handle binary files with binary-aware tools (cp, mv, rm, a script).")

# ---------------------------------------------------------------- one entry for the hook

BASH_TOOLS = ("Bash", "bash")
READ_TOOLS = ("Read", "read")


def check(tool: str, args: dict[str, Any], cwd: str, user: str | None = None,
          home: str | None = None) -> tuple[str, str] | None:
    """(gate, message) when the call must be refused, else None. Tool names are Claude Code's (`Bash`, `Read`) or
    OpenCode's (`bash`, `read`, `apply_patch`)."""
    if not isinstance(args, dict):
        return None
    if tool in BASH_TOOLS:
        cmd = args.get("command")
        if isinstance(cmd, str):
            if p := kill_gate(cmd, user, home):
                return "kill", KILL_MESSAGE.format(problems="\n".join(f"- {x}" for x in p))
            if p := git_sweep_problems(cmd, cwd):
                return "git", GIT_MESSAGE.format(problems="\n".join(f"- {x}" for x in p))
        return None
    if tool == "apply_patch":
        if p := binary_problems(args, cwd):
            return "binary", BINARY_MESSAGE.format(problems="\n".join(p))
        return None
    if tool in READ_TOOLS:
        fp = args.get("file_path") or args.get("filePath")
        if isinstance(fp, str) and (p := env_problems(fp)):
            return "env", "Read refused. " + "; ".join(p)
    return None
