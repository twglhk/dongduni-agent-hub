# Chat attachments

Opt-out feature of the board: a paperclip in the hub chat. Code: `agent_hub/uploads.py` (the flow), `uploadroutes.py` (HTTP), `storage/` (providers), `visibility.py` (who may see a file). Board: `board-app/src/lib/uploads.ts`, `lib/attach.ts`, `components/chat/Tray.tsx`, `components/AttachStrip.tsx`.

## Flow

1. `POST /api/uploads/start` with `{text, files: [{name, type, size}]}`. The server checks count, size and type, picks every path, stores the request held (`hold:<hub>`, so no lane takes it) and answers one instruction per file.
2. The browser sends the bytes the way the provider says: `chunks` (POST up to 4 MB at a time to `/api/uploads/chunk?id=&n=&offset=`; a 409 carries `received`, the offset to continue from; an empty chunk asks), `put` (one presigned PUT) or `multipart` (presigned parts from `/api/uploads/parts`, ETags handed back at finish).
3. `POST /api/uploads/finish` checks every file with the provider and releases the request to the hub with the text below. A file that is short or missing is named as a failure; the rest still goes.
4. `uploads.sweep` (run from `/api/state`, at most once a minute per workspace) releases a held request nobody finished once two hours pass since its last chunk (`HOLD_MAX`, store clock).

The hub reads, in the language of `lang` (en or ko):

```
see the picture

1 attachment(s) (saved in the workspace under /path/to/project, viewable on the board):
- uploads/2026-11-17/12/1-shot.png · image/png · 1.2 MB (original name: shot.png)
```

## The storage interface

`agent_hub/storage/base.py`. A provider is a `Storage` subclass built by `storage.make(settings, home, workdir)`; a provider whose bytes pass through the board (`local`, `drive`) extends `Receiver`, which keeps a `.part` file and appends chunks in order.

| method | meaning |
| --- | --- |
| `check()` | raise `StorageError` with a message a person can act on (rclone missing, no credentials) |
| `prepare(slot)` | before the browser is told anything: create a multipart upload, make the folder |
| `begin(slot)` | what the browser does: `chunks`, `put` or `multipart` with the facts it needs |
| `more(slot, numbers)` | presigned part URLs (S3 only) |
| `receive(slot, offset, data)` | append a chunk, in order, never past the announced size (`Receiver`) |
| `settle(slot, report)` | make the file final and check it: `""`, `"cut"`, `"size"` or `"store"` |
| `discard(slot)` | drop what an unfinished upload left |
| `locator(key)` | how the request text names the file |
| `view(key)` | a board address for the stored file, or `None` |
| `open(key, range)` | stream the file through the board (`drive`, `s3`; not `local`, which `/f/` serves) |
| `used_bytes()` | what is already stored, for `max_total_mb` (`local` walks the folder, `drive` asks `rclone size`); `live_usage` marks a provider whose bytes are checked again on every chunk |
| `connect_host()` | an external host the page must be allowed to connect to (`s3`) |

A `Slot` is `(rid, n, key, type, size, upload_id, part_size)`. Its `key` is always `uploads/<YYYY-MM-DD>/<request>/<n>-<name>.<ext>`: the server alone builds it, from the request number, a sanitised ASCII stem and an extension taken from the type it decided (not from the browser's name).

## Providers

**local** (default). The browser POSTs chunks to the board; the file ends at `<workspace>/uploads/<date>/<request>/<n>-<name>.<ext>`, where lanes read it by path (the stored request text names only the relative key; the absolute workspace folder is added only to the text a session is sent, `uploads.for_session`, which matters for lanes in their own worktree). The board shows it through `/f/`, so its rules apply: dot paths and secrets are refused, `Range` works, scripts cannot run (`sandbox`, `nosniff`). Not a symlink anywhere on the path, or the upload is refused. The day folder gets a `.gitignore` containing `*` so attachments never reach git; nothing in the project's own `.gitignore` changes, and a project that already has its own `uploads/` folder is not affected.

**drive** (`remote`, `root`). rclone cannot give a browser a resumable session, so bytes go browser to board (chunks, staged under `.tower/state/uploads/staging/`) and the board runs `rclone copyto` at finish, then `rclone lsjson` to check the size. The board shows files by running `rclone cat --offset --count`, so video seeks. rclone is called with an argument list (never a shell) through `procs.command`; if it is missing, or the remote is unknown, starting an upload answers with that message and the words still reach the hub. rclone keeps its own credentials in its own config.

**s3** (`bucket`, `region`, `prefix`). The browser uploads straight to the bucket: one presigned PUT with the content type and length signed (so the browser cannot change them), or, over 100 MB, a multipart upload with presigned parts. Viewing goes through the board (streamed with `Range`), because the page's content security policy only allows the board's own origin; only the bucket host is added to `connect-src` for the upload. Credentials: environment, the shared credentials file, or an instance role (IMDSv2), read when needed and never logged, never in `tower.json`, never in argv. `credential_process` and SSO profiles are not read. The bucket needs a CORS rule, for example:

```json
[{"AllowedOrigins": ["https://board.example.com"], "AllowedMethods": ["PUT"], "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"]}]
```

`s3sign.py` is a stdlib SigV4 signer; `tests/test_s3sign.py` checks it against the worked examples in AWS's S3 SigV4 documentation.

## Defaults and why

`local`, 25 MB per file, 10 files per request, 2 GB in the folder. A single-user install needs nothing set up and cannot be surprised by an external account; the caps bound what a member of an exposed board can put on the disk. `off` removes the paperclip and the routes (they answer 409).

## Security decisions

- The browser sends names, types and sizes; the server alone chooses path, stored type and extension. An allow-list of types (images, video, audio, PDF, plain text, markdown, CSV, JSON): no HTML, SVG, scripts or archives.
- No `..`, absolute path, backslash or symlink is ever accepted; a viewed key is reduced to its canonical `uploads/<date>/<request>/<n>-<stem>.<ext>` tail by a strict pattern before anything touches the disk or a remote.
- Every new POST goes through the existing `x-tower`, same-origin and Host rules. Chunks are capped at 4 MB, in order, and never past the announced size.
- A member sees an attachment only if a request or card that names it is visible to them (`visibility.upload_allowed`); a file nothing names is the owner's alone. Held requests are nobody's.
- An upload belongs to its sender: another member cannot add a chunk to it or finish it.
- Secrets are not in the page state, logs or the request text. The drive provider only ever puts the remote name and path in the text.

## Not carried over

Picking the same file again to resume a cut upload after a restart of the browser; per-kind size limits; dropping files on the chat box; result files under `generated/`; thumbnails made by the storage service. Each can be added without changing the interface.
