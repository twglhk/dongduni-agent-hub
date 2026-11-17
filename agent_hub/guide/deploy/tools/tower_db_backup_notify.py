import datetime
import json

TARGET = "hub"
KIND = "backup_fail"
STALE_HOURS = 30
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
        ctx["store"].push(TARGET, "infra", KIND, text)
    (sd / _STATE).write_text(json.dumps(state), encoding="utf-8")
    ctx["log"](f"tower_db_backup_notify: {len(texts)} notice(s) to {TARGET}")
