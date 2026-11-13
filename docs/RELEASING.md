# Releasing

Releases are built and published by GitHub Actions, not from a laptop. Any machine that can push tags to `twglhk/dongduni-agent-hub` can cut one; no PyPI password or token is needed anywhere.

## How it is wired (set up once, already done)

| piece | where | what it does |
| --- | --- | --- |
| `.github/workflows/ci.yml` | every push to `main`, every PR | unit tests on Ubuntu, macOS and Windows × Python 3.10 and 3.13, then builds the wheel, installs it in a fresh venv and runs `dah init`, `dah engine list`, `dah status` |
| `.github/workflows/release.yml` | a pushed tag `v*` | runs `ci.yml`, checks the tag equals `agent_hub.__version__`, builds sdist + wheel, publishes to PyPI, creates a GitHub release with both files |
| PyPI Trusted Publisher | pypi.org → `dongduni-agent-hub` → Publishing | trusts owner `twglhk`, repo `dongduni-agent-hub`, workflow `release.yml`, environment `pypi`. This is why no token exists |
| GitHub environment `pypi` | repo Settings → Environments | created by the first release; add required reviewers there if you want a manual approval before each publish |

The package is pure Python (`py3-none-any`): one wheel serves Windows, macOS and Linux.

## Cut a release

1. Start from an up-to-date `main` with a green CI run (`gh run list --limit 3`).
2. Pick the version ([semver](https://semver.org); while below 1.0, bump the minor for anything that changes commands, files or behaviour people rely on, the patch for fixes).
3. Set it in `agent_hub/__init__.py` (`__version__ = "X.Y.Z"`). That is the only place; `pyproject.toml` reads it. Add the release's section under "Release notes" below.
4. Run the tests: `python3 -m unittest discover -s tests -t .` must print `OK`.
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
