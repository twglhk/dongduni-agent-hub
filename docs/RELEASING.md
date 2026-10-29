# Releasing

Releases are built and published by GitHub Actions, not from a laptop. Any machine that can push tags to `twglhk/dongduni-agent-hub` can cut one; no PyPI password or token is needed anywhere.

## How it is wired (set up once, already done)

| piece | where | what it does |
| --- | --- | --- |
| `.github/workflows/ci.yml` | every push to `main`, every PR | unit tests on Ubuntu, macOS and Windows × Python 3.10 and 3.13, then builds the wheel, installs it in a fresh venv and runs `dah init`, `dah goal add`, `dah status` |
| `.github/workflows/release.yml` | a pushed tag `v*` | runs `ci.yml`, checks the tag equals `agent_hub.__version__`, builds sdist + wheel, publishes to PyPI, creates a GitHub release with both files |
| PyPI Trusted Publisher | pypi.org → `dongduni-agent-hub` → Publishing | trusts owner `twglhk`, repo `dongduni-agent-hub`, workflow `release.yml`, environment `pypi`. This is why no token exists |
| GitHub environment `pypi` | repo Settings → Environments | created by the first release; add required reviewers there if you want a manual approval before each publish |

The package is pure Python (`py3-none-any`): one wheel serves Windows, macOS and Linux.

## Cut a release

1. Start from an up-to-date `main` with a green CI run (`gh run list --limit 3`).
2. Pick the version ([semver](https://semver.org); while below 1.0, bump the minor for anything that changes commands, files or behaviour people rely on, the patch for fixes).
3. Set it in `agent_hub/__init__.py` (`__version__ = "X.Y.Z"`). That is the only place; `pyproject.toml` reads it.
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

   PyPI can take a minute to serve a new version. The release page is `https://github.com/twglhk/dongduni-agent-hub/releases/tag/vX.Y.Z`; edit its generated notes if users need to know something (a renamed command, a manual step after upgrading).

## When something fails

- **Tag does not match the version**: the `build` job stops before publishing. Delete the tag (`git push --delete origin vX.Y.Z && git tag -d vX.Y.Z`), fix `__version__`, tag again.
- **Tests fail in the release run**: nothing was published. Fix on `main`, delete and re-push the tag as above.
- **PyPI rejects the upload "file already exists"**: that version was published before. PyPI never accepts the same version twice, even after a delete; bump to the next patch version.
- **PyPI says the publisher is not trusted**: the Trusted Publisher entry no longer matches (repo renamed or moved, workflow file renamed, environment name changed). Fix it on pypi.org under the project's Publishing settings, then re-run the failed job (`gh run rerun <id> --failed`).
- **A bad release is out**: publish a fixed patch version. Optionally *yank* the bad one on pypi.org (Manage → Releases → Options → Yank): installs that pin it still work, new installs skip it.

## Moving or renaming the repo

Update, in one commit: the URLs in `pyproject.toml` (`[project.urls]`), `REPO` in `agent_hub/install.py`, the links in `README.md` and the setup guide; then update the Trusted Publisher on pypi.org before the next tag.
