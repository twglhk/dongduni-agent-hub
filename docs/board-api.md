# Board web server

`agent_hub/web.py` runs in a thread inside `dah run`. It binds `127.0.0.1` by default; expose it through a proxy that authenticates people (Cloudflare Access, Tailscale, an AWS ALB with OIDC, ...) or use token sign-in.

## Routes

| route | what |
| --- | --- |
| `GET /` | the React board (`board_next/`, built from `board-app/`) or the workspace's `.tower/board/index.html`; `/?builtin` always opens the built-in one; `/assets/<file>` |
| `GET /old/` | the single-file board (`board/index.html`) that came before the React app |
| `GET /api/state` | lanes, each lane's queue (`queues`), open items, tasks, reports, memos, counts; a member gets only what `visibility.py` allows |
| `GET /api/history?kind=K&offset=N` | `requests`, `decided`, `work` (also `reports`, `closed`, `memos`), 20 per page |
| `GET /api/items/<id>` | one item |
| `GET /api/lanes/<id>` | one lane: skills, process and stage, fixing, model, effort, tokens |
| `GET /api/engines` | every engine: id, label, lanes, library rows |
| `GET /api/engines/<id>/map` | lanes with skills, pipelines with ratings, skills, tools and experiments from `engine.json`, 7-day usage, what changed since last week's snapshot (ETag; 304 when unchanged) |
| `GET /api/engines/<id>/doc?path=P` | `{path, text}` of a card or skill document the map names (nothing else is served) |
| `GET /api/processes` | the process library (`processes.view`): processes with sub-processes, stages and skills, who uses what, ratings (ETag) |
| `GET /api/processes/doc?path=P` | `{path, text}` of a document the library names |
| `GET /api/workspaces` | every workspace registered on this machine, for the board's switcher |
| `GET /api/machine` | load, CPU, memory, swap graded g/a/r (`machine.py`), why queued tasks wait, Claude usage percent when `usage_percent` is set |
| `GET /f/<path>` | an attachment under the project (dot paths and secrets are refused; Range supported) |
| `POST /api/items/<id>/decide` | `{verdict, choice \| choices[], note}` |
| `GET\|POST /api/me/prefs` | the signed-in member's own board settings (`prefs.py`); POST `{prefs: {...}}` saves, `{reset: true}` clears |
| `GET /api/me/claude` | the viewer's Claude account `{state: none\|pending\|connected\|expired, url?, email?, plan?, expires?, ended?}`; 404 unless `claude_accounts` is set (`accountroutes.py`) |
| `POST /api/me/claude` | `{action: start\|code\|cancel\|disconnect, code?}`; the code goes straight to `claude auth login`, never stored or logged |
| `POST /api/memo` | `{text, to: hub}`; `to: infra` answers 409 (ask the hub) |
| `POST /api/uploads/start` | `{text, files: [{name, type, size}]}` returns an upload id and, per file, a mode: `chunks` (`chunk` size), `put` (`url`, `headers`) or `multipart` (`part_size`, `parts`). The request is held until finish; the server fixes every path, type and size (`uploads.py`) |
| `POST /api/uploads/chunk?id=&n=&offset=` | raw bytes of a `chunks` file, at most 4 MB, returns `{received}`; 409 `{received}` = out of order |
| `POST /api/uploads/parts` | `{id, n, parts: [1..]}` returns `{urls: {part: url}}` for one multipart S3 file |
| `POST /api/uploads/finish` | `{id, results?: [{n, etags?}]}` returns `{sent, failed, total}`; the request goes to the hub |
| `GET /api/uploads/view?key=K` | `{url, type, thumb?}` for an attachment path a request names (members: only their own) |
| `GET /api/uploads/file?key=K` | an attachment streamed through the board (drive, s3; Range supported) |
| `* /api/goals...` | 410 Gone (goals were removed in 0.8.0) |
| `POST /api/settings` | `{max_lanes: 1..10}` or `{lane, sessions: 1..5}` (a team lane's or infra's `max_instances`; hubs stay at one session each); written to `tower.json`, the tower picks it up |
| `POST /api/login {token}`, `POST /api/logout` | with `auth.mode = token` |
| `GET /x/<path>` | the workspace's `.tower/board/` files (`extra.js`, `extra.css`, images, ...) |
| `GET /api/sessions/<key>` | what a lane session (`docs`, `docs#2`, `infra#2`) holds or last held, and its newest report |
| `GET\|POST /api/x/<name>` | routes from the workspace's `.tower/ext/*.py` (see `agent_hub/guide/EXTEND.md`) |

Other registered workspaces are served under `/w/<slug>/` with the same routes, so one exposed board reaches every project on the machine. Each workspace keeps its own auth; a `local` workspace is only served next to another `local` one (local mode trusts whoever reaches it). One machine card and one Claude login serve every workspace.

## Request checks

- Every request must carry a `Host` the board knows (loopback with its port, the `public_url` host, or the bind address); anything else gets 421 (DNS rebinding).
- Every POST must carry `x-tower: 1` and, when the browser says where it came from (`Origin`, `Sec-Fetch-Site`), come from the board's own origin.
- With `auth.mode = header`, identity headers are trusted only from the local proxy. If the member list is non-empty, a person on it is the only one let in.
- `MAX_BODY` caps the request body; board posts are text only.
- `GET`s whose answer rarely changes carry an ETag and answer 304 when the browser already has it.
- An extension route's bug is its own 500, never the board's.
