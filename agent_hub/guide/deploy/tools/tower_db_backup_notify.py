"""Tell the hub once when the tower.db backup fails or stops running. An example `on_tick` extension.

Copy it to `<workspace>/.tower/ext/tower_db_backup_notify.py` (the tower loads every file in that folder; the
extension contract is in `dah guide extend`). It reads the status file that tower_db_backup.py writes next to the
database (`.tower/state/tower-db-backup.json`) and, each tick:

- a failure newer than the last success that was not told yet -> one message to the hub;
- no successful backup for STALE_HOURS (the timer did not run) -> one message for that last success.

What was told is kept in `.tower/state/tower_db_backup_notify.json`, so a tower restart never repeats a notice.
A later successful backup needs no message. Delete this file to stop the notices.

Not on the stable extension list: `ctx["store"].push(...)` (a message to a lane). The stable parts are `on_tick`,
`ctx["cfg"]`, `ctx["log"]` and `ctx["now"]`; `Store` methods beyond reading may change in a release, so check this
file after an update. If you would rather not depend on it, replace the push with a call to your own channel
(a webhook, mail) and keep the rest.
"""
import datetime
import json

TARGET = "hub"           # the lane that should hear about it: the id of your hub lane in tower.json
KIND = "backup_fail"
STALE_HOURS = 30         # a daily timer plus some slack
_STATUS = "tower-db-backup.json"
_STATE = "tower_db_backup_notify.json"


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _when(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def notices(status, state, now):
    """(texts, new state) for one tick. Pure: no files, no clock."""
    state = dict(state)
    ok, fail = status.get("last_ok", 0), status.get("last_fail", 0)
    texts = []
    if fail > ok and fail > state.get("told_fail", 0):
        texts.append(f"tower.db backup failed ({_when(fail)}, {status.get('fail_why', '?')}): "
                     f"{str(status.get('error', ''))[:300]}. Run the backup script by hand to see the error, "
                     "and check its timer with `systemctl list-timers`.")
        state["told_fail"] = fail
    if ok and now - ok > STALE_HOURS * 3600 and ok > state.get("told_stale", 0):
        texts.append(f"No tower.db backup has succeeded for over {STALE_HOURS} hours (last success {_when(ok)}): "
                     "check that the backup timer is enabled and running.")
        state["told_stale"] = ok
    return texts, state


def on_tick(ctx):
    sd = ctx["cfg"].state_dir
    status = _read(sd / _STATUS)
    if not status:
        return
    now = ctx.get("now") or datetime.datetime.now().timestamp()
    texts, state = notices(status, _read(sd / _STATE), now)
    if not texts:
        return
    for text in texts:
        ctx["store"].push(TARGET, "infra", KIND, text)  # non-stable API, see the note at the top
    (sd / _STATE).write_text(json.dumps(state), encoding="utf-8")
    ctx["log"](f"tower_db_backup_notify: {len(texts)} notice(s) to {TARGET}")
