# Releasing

Releases are built and published by GitHub Actions, not from a laptop. Any machine that can push tags to `twglhk/dongduni-agent-hub` can cut one; no PyPI password or token is needed anywhere.

## How it is wired (set up once, already done)

| piece | where | what it does |
| --- | --- | --- |
| `.github/workflows/ci.yml` | every push to `main`, every PR | unit tests on Ubuntu, macOS and Windows × Python 3.10 and 3.13, then builds the wheel, installs it in a fresh venv and runs `dah init`, `dah engine list`, `dah status`; a `board` job rebuilds the React board (`board-app/`) and fails when the committed `agent_hub/board_next/` differs from a fresh build |
| `.github/workflows/release.yml` | a pushed tag `v*` | runs `ci.yml`, checks the tag equals `agent_hub.__version__`, builds sdist + wheel, publishes to PyPI, creates a GitHub release with both files |
| PyPI Trusted Publisher | pypi.org → `dongduni-agent-hub` → Publishing | trusts owner `twglhk`, repo `dongduni-agent-hub`, workflow `release.yml`, environment `pypi`. This is why no token exists |
| GitHub environment `pypi` | repo Settings → Environments | created by the first release; add required reviewers there if you want a manual approval before each publish |

The package is pure Python (`py3-none-any`): one wheel serves Windows, macOS and Linux.

## Cut a release

1. Start from an up-to-date `main` with a green CI run (`gh run list --limit 3`).
2. Pick the version ([semver](https://semver.org); while below 1.0, bump the minor for anything that changes commands, files or behaviour people rely on, the patch for fixes).
3. Set it in `agent_hub/__init__.py` (`__version__ = "X.Y.Z"`). That is the only place; `pyproject.toml` reads it. Add the release's section under "Release notes" below.
4. Run the tests: `python3 -m unittest discover -s tests -t .` must print `OK`. If the board changed, rebuild it (`cd board-app && npm ci && npm run build`) and commit `agent_hub/board_next/` with its source: the wheel ships the committed build, never a build made at release time.
5. Commit and tag:

   ```sh
   git commit -am "Release X.Y.Z"
   git push origin main
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

6. Watch it: `gh run watch $(gh run list --workflow release.yml --limit 1 --json databaseId -q '.[0].databaseId') --exit-status` (or the Actions tab).
7. Check what users get:

   ```sh
   python3 -m venv /tmp/dah-check && /tmp/dah-check/bin/pip install --no-cache-dir dongduni-agent-hub==X.Y.Z
   /tmp/dah-check/bin/dah -h && /tmp/dah-check/bin/dah guide | head -3
   ```

   PyPI can take a minute to serve a new version. The release page is `https://github.com/twglhk/dongduni-agent-hub/releases/tag/vX.Y.Z`; edit its generated notes if users need to know something (a renamed command, a manual step after upgrading). Users get the release with `dah update`; anything it cannot do by itself (a new setting to choose) belongs in those notes.

## When something fails

- **Tag does not match the version**: the `build` job stops before publishing. Delete the tag (`git push --delete origin vX.Y.Z && git tag -d vX.Y.Z`), fix `__version__`, tag again.
- **Tests fail in the release run**: nothing was published. Fix on `main`, delete and re-push the tag as above.
- **PyPI rejects the upload "file already exists"**: that version was published before. PyPI never accepts the same version twice, even after a delete; bump to the next patch version.
- **PyPI says the publisher is not trusted**: the Trusted Publisher entry no longer matches (repo renamed or moved, workflow file renamed, environment name changed). Fix it on pypi.org under the project's Publishing settings, then re-run the failed job (`gh run rerun <id> --failed`).
- **A bad release is out**: publish a fixed patch version. Optionally *yank* the bad one on pypi.org (Manage → Releases → Options → Yank): installs that pin it still work, new installs skip it.

## Moving or renaming the repo

Update, in one commit: the URLs in `pyproject.toml` (`[project.urls]`), `REPO` in `agent_hub/install.py`, the links in `README.md` and the setup guide; then update the Trusted Publisher on pypi.org before the next tag.

## Release notes

Newest first. Paste the section of the version being tagged into the GitHub release page; it is also what `dah update` users need to read.

### 0.10.0

A larger release: board server hardening, a process library with verification, a React board, and a set of opt-in team features. Everything new is off by default except where the Upgrade section says otherwise.

**Upgrade** (read before updating a running install)

- **`auth.mode` `local` on an exposed board is refused.** If `auth.mode` is `local` (the default) and `web.host` is not loopback, or `web.public_url` or `tunnel` is set, the tower no longer starts: the config load fails with an error that names the keys. Either switch to `header` or `token` (`dah guide`), or accept the risk with `"auth": {"mode": "local", "allow_exposed": true}`. Before this release such a setup started silently; a board with `public_url` only for notification links is the usual case.
- **The board answers 421 to an unknown Host.** The board server checks the `Host` header against its own address (loopback names and IP addresses on any port, `web.host`, the host of `web.public_url`, the names in `web.allowed_hosts`). A request through a reverse proxy or tunnel under another name gets `421 unknown Host`. Set `web.public_url` to the address people open (and `web.allowed_hosts` for further names). Cross-site POSTs are refused too.
- **`dah update` converts pattern libraries into the process library.** An engine whose `library` is a pattern-library table gets a `processes/` folder: a full process (`processes/full/<id>.md`, one step per table row) and one sub-process per row (`processes/sub/<id>/process.md` with its `patterns/`). The old table is kept as `pattern-library.md.bak`; `Line: #3 ...` headers still work through the old `#` column; a second run changes nothing. `lanes/hub.md` and `PROTOCOL.md` are refreshed when untouched; an edited file gets a `.new` beside it. `dah guide engines` has the steps. Database: columns and tables are only added (`items.memo_id`, `member_prefs`, `overlaps`, upload tables ...), so a downgrade to 0.9.x still opens the database.
- **Attachments are on by default.** The hub chat has a paperclip with the `local` provider: files go to `uploads/<date>/<request>/` in the workspace, at most 25 MB per file, 10 files per request and 2 GB in all. Set `"uploads": {"provider": "off"}` to remove the paperclip and the upload routes.
- **Members see less.** With `requester` or `person_hubs` on, a member gets another person's tasks, reports and infra memos only as a placeholder ("someone else's work"), and `/f/` serves a member only files attached to a card, report or request they can see. The owner, and installs with both keys off, see what they saw before. More file names are blocked for everyone (`*.env`, `client_secret*`, `rclone.conf`, `credentials*`, SSH keys).
- Typed `tower.json` readers: a number written as text that is not a number, or a string where a boolean is expected, is now a config error instead of being guessed. A broken `workspaces.json` is kept as `.bak` rather than overwritten.

**Security fixes**

- Board server: unknown `Host` refused (421), cross-site POST refused, served files sandboxed with frame and CSP headers, request bodies bounded, owner-only settings.
- Server-side visibility: cards, decided history, memos, chat, card files, sessions and attachments are filtered by who is asking (`visibility.py`), not only in the browser; another member's person hub is hidden.
- `auth.mode` `local` on an exposed board needs `auth.allow_exposed`.
- Store: every connection is closed, read-then-write methods run in transactions, state files are private, column names are whitelisted.
- Secrets: the Claude usage token goes to `curl` on standard input, not on the command line; notify sends through `curl` only after a TLS error and with the url and body on standard input; lane environment stays out of launcher scripts; Claude sign-in codes and tokens never reach a command line, log, database or answer.
- Done gate and scratch: a private, user-owned root; the gate worktree is checked to belong to this repo; verdict files kept until handled; race-safe lock takeover.
- Kill gate sees through prefix options, `xargs` and regex patterns; hooks run as `python -m agent_hub.hooks` so a reinstall cannot block open sessions.
- Stop escalates to a forced kill, and a lane pid is signalled only while it is still the process the tower started; the tower claims its pid file with `O_EXCL` under a lock and no longer inherits `TOWER_LANE`.
- Uploads: stdlib SigV4 signer, per-file and per-request caps, files shown only to members who may see the request.

**New, opt-in (keys in `tower.json`)**

- `requester` (false): tasks record who asked; cards default to that person.
- `fair_turns` (false): waiting tasks go round by requester.
- `person_hubs` (false, or `{"max": 3, "auto": false, "domains": []}`): one always-on hub per board member.
- `claude_accounts` (false): each member signs in to their own Claude account from the board; needs `auth.mode` `header` or `token`.
- `rule_watch` (off): `{"files": [...], "delay_minutes": 10, "cards_per_day": 3}`, one card to everyone when a shared rule file changes in a commit.
- `overlap_check` (false): queued work on a shared rule file that meets another person's open work asks both people first; needs `rule_watch.files`.
- `uploads` (`local`): chat attachments with `local`, `drive` (rclone) or `s3` storage; see Upgrade.
- `schedules` (none): a task or hub request per day or week.
- `max_instances` (1, also per lane and on `infra`): parallel sessions per lane, each in its own worktree and branch when `worktree` is on; paths are a file lock.
- `branch_sync`, `branch_sync_seconds`, `branch_sync_push` (false/0/false): worktree lanes follow the current branch.
- `leftover_sweep` (false, Linux): stops processes of closed lane sessions.
- `context_limit_k` (off; a number or a table by model): a long session is asked to hand off.
- `scratch_root`, `scratch_grace` (user cache dir, 600 s), `done_gate`, `usage_percent` (off): as in 0.9.3, now documented in the README table.
- `auth.allow_exposed` (false): see Upgrade.
- `web.allowed_hosts` (none): further host names the board answers to.
- `process_verification` (false; needs `require_line`): a full process that is not verified yet is checked after every sub-process; see Process library.

**Process library and engines**

- Processes live in one library, `processes/`: sub-processes (a process card and its pattern cards) and full processes (sub-processes in order, with model and effort). A lane lists the full processes it uses under `## Processes used`. A brief says `Line: <full> / <sub> · stage 2 · make`. `dah engine add` writes this layout; rows that name the same process card share one sub-process.
- Process verification (`process_verification`): a full process goes draft → confirmed → verified. Until it is verified, every task of one run shares a `--deliverable KEY`, a lane ends each sub-process with `dah check` (a card to the requester: next / fix and redo), and the next sub-process waits for that answer; a fix task is queued on "fix and redo". After the last one the hub records it with `dah process verify FULL --card N` in `processes/verified.json`; a changed card drops the process back to confirmed. `dah process status` shows where runs are.
- Automatic conversion of pattern libraries by `dah update` (see Upgrade); a conversion that stopped half way resumes on the next run. `Line: new <type> · prepare` for a row the library lacks; repo-path cards; `.claude/skills` is the default skills folder.
- The Engine tab is the library (`GET /api/processes`, `GET /api/processes/doc?path=P`); it no longer lists lanes. `/api/engines*` and `/old/` are unchanged.
- Hub answers to a request a person wrote reach that person as a review card (a second reply revises the open card).

**Board**

- A React board at `/` (the old board stays at `/old/`); strings in English, Korean, Japanese, Chinese and Spanish.
- Hub chat shows requests and answers in full and has attachments with previews and progress; the "last reply" strip is gone and the request box floats over the page.
- Lanes tab: queue strip (now and waiting work with the reason, per-person counts), session sheet (`GET /api/sessions/<key>`), sessions and branches per lane, machine disks, a settings sheet per member (`member_prefs`), my Claude account sheet, "Add a lane" asks the sender's hub.
- History tab: a Work feed of what each lane and session did, filtered per viewer.
- Board items can recommend one option (`--recommend`, shown first with a badge); server-side filtering of cards, work, files and sessions per viewer.

**CLI**

- New: `dah check`, `dah process status|verify|check`, `dah restart`, `dah prefs show|set|reset`, `dah paths`, `dah overlap CARD go|after|merge`, `dah memo cancel`, `dah memo bump`, `dah memo paths`, `dah memo reply --covers N`, `dah task bump`, `dah infra --model`, `dah infra --effort`, `dah infra --paths`, `dah infra --alone`, `dah task --request`, `dah task --for`, `dah item add --to`, `dah item add --recommend`, `dah item add --request`; `dah member list` shows each member's hub and `dah guide -h` lists every guide.
- `dah init` / `dah update`: `--force` keeps an edited file as `.bak`; `dah restart` waits only for a service manager.
- Real model and effort per lane session; `dah task` and the shipped `ENGINE-audit` skill are checked against the parser by a test that now covers the README, guides and docs.

**Internals**

- `cli.py` is the package `cli/` and `runtimes.py` the package `runtimes/` (the `Adapter`, `Headless`, `Host` contract in `agent_hub/guide/EXTEND.md` is unchanged); `engines.py`, `processes.py`, `line.py`, `libmigrate.py`, `visibility.py`, `storage/`, `rulewatch.py`, `overlap.py`, `accounthold.py`, `claudelogin.py`, `leftovers.py`, `tower_db_backup.py`, `shot.py` and others are new modules. Decision routing moved into Store; edits to `tower.json` validate before writing and replace the file atomically. Comments and docstrings that restate the code were removed.
- CI rebuilds the board app and requires the committed `agent_hub/board_next/` to match; `skill_lint` has its own frontmatter parser (no PyYAML).
- Messages of the overlap check and the rule watch exist in all five languages; `notify.desktop` accepts JSON `true` as well as `"1"`.

**Docs**

- `docs/internals.md` (engine, store, config, CLI and the other modules), `docs/attachments.md`, `docs/board-api.md`, a README table of every `tower.json` key, a README paragraph on `auth.allow_exposed` and the Host check, guides for a Linux server (systemd), Caddy + oauth2-proxy, Orca, the tower.db backup tool with a systemd timer, and a safe update and reinstall checklist.

### 0.9.3

A patch release: the six candidates from `docs/audit/0.9.3-candidates.md`, ported as neutral code with tests.

**New**

- **Gates.** A PreToolUse hook for Claude lanes (`agent_hub/hooks.py`, `gates.py`): kill gate, git sweep gate (no `stash`, `reset --hard`, `clean`), `.env` read gate; denials are logged as `GateDeny`. `DAH_GATES=off` turns them off for one lane. OpenCode: `python agent_hub/hooks.py opencode-plugin` prints a plugin for its binary-patch guard (untested under OpenCode). The gates read POSIX shell syntax.
- **Subagents and skills.** `explore`, `librarian`, `looker`, `general` in `.claude/agents/`; skills `humanizer`, `media-reading`, `review-board`, `project-audit`; concurrent-session rules in PROTOCOL.md.
- **Done gate** (`done_gate` in `tower.json`): `dah report done` returns at once; the gate runs on one persistent worktree outside the project, one at a time, and the tick delivers pass or refusal. Plus `dah scratch` and a sweep of worktrees and temp dirs dah made.
- **Machine card and footer strip** on the board: load, CPU, memory, swap graded against this machine, wait reasons for queued tasks, and an optional Claude usage percent (`"usage_percent": "claude"`, off by default, undocumented endpoint, "unknown" on failure).
- **Urgent tell** `dah tell --urgent LANE "..."`, injected at the lane's next tool call, and a push for Claude API errors (login expired, billing, server) once per kind per 30 minutes.

**Upgrade**

`dah update` refreshes PROTOCOL.md (untouched copies) and adds the starter files. Hooks are merged into Claude lane settings when the tower opens a lane.

### 0.9.2

A patch release: PROTOCOL takes the project-neutral working rules from Auto Arkham's own tower (docs/audit/0.9.1-starter.md, "Compared with Auto Arkham").

**New** (PROTOCOL.md)

- Hard rules: a denied tool call is not retried; a hook's refusal reason is read and the call fixed; subagents get the full rules in their brief and their results are checked file by file.
- Lane close-out: delete temp dirs and worktrees before `wrapped`; retry a locked commit or rejected push once; never force-push, rebase or amend.
- "Shared checkout": reread before writing, stage own hunks, stop only your own PIDs, heavy jobs one at a time.
- "When to post an item": what is and is not an item, approvals kept apart, evidence on the item, revise open items in place.

**Upgrade**

`dah update` swaps an untouched PROTOCOL.md; an edited one gets `PROTOCOL.md.new` beside it.

### 0.9.1

A patch release: the starter fix list from `docs/audit/0.9.0-starter.md` section 5.

**New**

- **`CLAUDE.md` and `AGENTS.md`.** `dah init` and `dah update` add a short marked block (`<!-- dah:start -->`) to both files: the tower runs lane sessions, a plain session reads `.tower/lanes/$TOWER_LANE.md` and `.tower/PROTOCOL.md` only when `TOWER_LANE` is set, and requests go through `dah`. An existing file is only appended to; the block is refreshed in place; a `CLAUDE.md` that imports `@AGENTS.md` is left alone. `dah uninstall` removes the block.
- **Skills where Claude Code reads them.** On a `claude` lane the research and audit skills (and engine skills) go to `.claude/skills/`; other runtimes keep `.agents/skills/`.
- **Instructions.** Hub: brief fields (`model:`, `effort:`, `paths:`, `after:`, `--deliverable`), when to use research and audit, `dah ratings`/`dah blame` at review, close the item after a decision, no live `ask` on Claude Code, one `dah` command at a time. PROTOCOL: permissions block, lane close-out (done/blocked/note, own-files commit, handoff shape), engine flow. Team lane: blocked/note/handoff pointers.
- **Model fallback.** `dah init` probes `fable` once on a claude lane and writes `opus` when the agent refuses it.
- "Roam" removed from the setup guide's hint list.

**Upgrade**

`dah update` refreshes the lane files (your edits are kept; a changed file gets a `.new` beside it) and adds the block and skills.

### 0.9.0

A minor release below 1.0. No breaking changes.

**New**

- **Model and effort guide.** `guide/MODELS.md` (`dah guide models`): the current Claude lineup (Fable, Opus, Sonnet, Haiku), effort levels, and the rule: start from the model's own default, raise where a task earns it, make the task small before making the model strong. The engine templates' pattern libraries ship with these defaults (`sonnet/high`).
- **Validation.** `fable` joins the ranked models; `stagemodel.problems()` flags a mistyped model alias or an effort outside `low|medium|high|xhigh|max`. `dah task` now warns (it does not refuse unless `require_model_lines` is set) when a brief names no model or effort, or a bad one.
- **Two required lanes.** `dah engine add` creates `NAME-research` (a referenced note against tier-1 sources on the research-note process card, with a library row; Opus medium) and `NAME-audit` (reads raw evidence for why a pattern happened and sends the hub cards with predicted effects, then scores them; Fable high) with their skills. `dah init` creates plain `research` and `audit` lanes and skills. On Claude lanes the lane's `model` is preset to `opus` and `fable`; change it where Fable is not configured.

**Upgrade**

`dah update` leaves existing lanes alone. To get the two lanes in an existing workspace: `dah lane add research --runtime claude` and `dah lane add audit --runtime claude` then `dah install` (it writes the lane files and skills from the new templates), or `dah engine add NAME --from blank`.

### 0.8.0

A minor release below 1.0, with breaking changes. `agent_hub/__init__.py` is the only place the version is set; `pyproject.toml` reads it.

**New**

- **Engines.** A set of lanes with one method: a pattern library, skills holding process cards and pattern cards, a review lane. `dah engine list|show|add NAME --from blank|animation|marketing`, `dah lane add --engine`, `dah guide engines`. An install that declares none runs one implicit engine, `default`. Engines sharing a skills folder each list only the skills their lanes name (or `skill_names`); a skill two engines name shows once as shared.
- **Process cards.** A brief starts with `Line: #3 Type · stage 2 · … · make|prepare|fix`; `dah stage N` names the stage in progress; `require_line` refuses a brief whose card is not confirmed. Model and effort per stage come from the library's `model · effort` column.
- **Ratings.** `dah deliverable`, `dah blame`, `dah ratings`: first-pass rate per stage and process, token cost per attempt and per adopted deliverable. A `dah tell` starting with `fix:` counts as a send-back (PROTOCOL, "Sending work back").
- **Task ledger.** Model, effort, tokens and turns per task, a retro line on `wrapped`, `dah stats`, `dah retros`; optional usage cap per engine (`tokens-day:N` or an extension's `caps`).
- **Board.** Engine tab (switcher, pipelines with ratings, skills, tools, experiments, tokens of the last seven days, weekly snapshot and what changed), per-lane sheets, a request box on every tab, five-language strings.
- **Scheduler.** `paths:` conflicts between tasks, head-of-line skip, `light_lanes` and `heavy_paths`, `max_instances` parallel sessions per lane (`docs#2`), linked tasks, memory guard (`memory_limit_mb`).

**Breaking**

- **Goals are gone.** `dah goal`, the Goals section, the goal prompts and `dah task --goal` (ignored with a notice) are removed; `/api/goals*` answers 410 for this release and is dropped in the next; `on_event` kind `goal` stops firing. Goal rows in an old database stay there, unread. Anyone who lives on goals: send the work as requests, or stay on 0.7.x.
- **The Infra tab is gone.** Humans reach infra only through the hub (`dah infra "..." --approved "request #N"`); `to: infra` from the board answers 409. A workspace whose `lanes/hub.md` was edited gets the new hub template as `hub.md.new`; until it is merged the hub does not forward infra work. `dah update` prints this and asks infra to merge.

**Compatible**

- `tower.json`: new optional keys only (`engines`, `light_lanes`, `max_instances`, `heavy_paths`, `require_line`, `memory_limit_mb`); an old file loads unchanged and `dah update` rewrites nothing in it beyond the existing `--safe` fix.
- Database: columns are added, none dropped, so a downgrade to 0.7.x still opens it.
- Template texts: `templates/shipped.json` carries every release's text, so `dah update` swaps untouched copies and writes `.new` beside edited ones (tested from a 0.7.0 workspace in `tests/test_update_070.py`).

**Upgrade**

```sh
dah update --dry-run     # what would be upgraded, which package files were changed in place
dah update               # upgrade, migrate each workspace, restart the tower
```

Check before tagging: full suite `OK`; `grep -rniE "godot|blender|hero|boss" agent_hub/` shows no game words in source; the CI matrix is unchanged.
