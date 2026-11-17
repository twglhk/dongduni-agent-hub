# Backing up tower.db

All tower state (board items, requests, tasks, ledger, messages) is one SQLite file, `.tower/state/tower.db`. It is not in git. Keep a copy somewhere that is not the machine, because a disk failure or a bad reinstall would otherwise take the board's history with it.

agent-hub ships a standard-library script for this, `tower_db_backup.py`, plus an optional notice for the hub when a backup fails. It is a standalone script, not a `dah` command: copy it into your workspace and run it from a timer.

## What the script does

1. Takes a consistent snapshot with SQLite's online backup API. That is safe while the tower runs (one snapshot, WAL included); do not just `cp` the file.
2. Switches the copy to a single-file journal and runs `PRAGMA integrity_check`. A copy that fails is never uploaded.
3. Uploads it as `<dest>/YYYY-MM-DD/tower-HHMM.db` (local time) with `rclone`, the `aws` CLI or a plain folder copy.
4. Compares the uploaded size with the local size.
5. Deletes date folders older than `--keep-days` (default 30). Folders whose name is not a date are never touched.
6. Writes `.tower/state/tower-db-backup.json` (`last_ok`, `last_fail`, `error`, ...) and exits `0` on success, `1` on failure (`2` for a usage error).

It reads no credentials: `rclone` and `aws` use their own configuration or the service's environment. There is no default destination; you must give one.

## Install

Find the shipped copy and put it into the workspace (so git keeps it and a package update does not replace it):

```sh
SRC="$(python3 -c 'import agent_hub, pathlib; print(pathlib.Path(agent_hub.__file__).parent / "guide/deploy/tools")')"
mkdir -p .tower/tools .tower/ext
cp "$SRC/tower_db_backup.py" .tower/tools/
```

If `python3` cannot import `agent_hub` (a tool install lives in its own environment), `dah guide` prints the path of a guide file in its first line; the `tools/` folder is next to it, in the package's `guide/deploy/` folder.

Try it by hand first. Pick the destination that fits:

```sh
# an rclone remote (Google Drive, Dropbox, B2, SFTP ...), set up with `rclone config`
python3 .tower/tools/tower_db_backup.py --dest "myremote:backups/tower" --why manual
# S3 or compatible; for R2 / MinIO add the endpoint
python3 .tower/tools/tower_db_backup.py --dest s3://<bucket>/tower --aws-arg=--endpoint-url=https://<endpoint>
# a mounted disk
python3 .tower/tools/tower_db_backup.py --dest /mnt/backups/tower
```

The destination can also come from the environment (`TOWER_BACKUP_DEST`, `TOWER_BACKUP_KEEP_DAYS`, `TOWER_BACKUP_METHOD`), which keeps it out of the unit file. Extra tool options: `--rclone-arg` and `--aws-arg`, repeatable (for example `--rclone-arg=--drive-use-trash=true` to make deletions go to the trash, `--aws-arg=--profile --aws-arg=backup`). Run it from the workspace folder or pass `--workspace`.

## Daily, with systemd

```ini
# /etc/systemd/system/tower-db-backup.service
[Unit]
Description=Back up tower.db
After=network-online.target

[Service]
Type=oneshot
User=<user>
WorkingDirectory=/home/<user>/workspace
Environment=PATH=/home/<user>/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=TOWER_BACKUP_DEST=<remote:path or s3://bucket/prefix>
ExecStart=/usr/bin/python3 /home/<user>/workspace/.tower/tools/tower_db_backup.py --why daily
Nice=10
TimeoutStartSec=15min
```

```ini
# /etc/systemd/system/tower-db-backup.timer
[Unit]
Description=Daily tower.db backup

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now tower-db-backup.timer
sudo systemctl start tower-db-backup.service      # one run now
systemctl list-timers tower-db-backup.timer
journalctl -u tower-db-backup.service -n 20 --no-pager
```

`Persistent=true` runs a backup that was missed while the machine was off right after it boots. The service PATH is short: if `rclone` or `aws` is installed somewhere else (for example `/snap/bin`), add it to `PATH=` or pass `--rclone-bin` / `--aws-bin` with the full path. For a per-user setup, put the same two files in `~/.config/systemd/user/`, use `systemctl --user ...` and `loginctl enable-linger <user>`.

Run it once more right before a reinstall or update (`--why restart`); a non-zero exit means stop.

## Tell the hub when it fails

`tower_db_backup_notify.py` is a small extension (`dah guide extend`) that reads the status file and posts one message to the hub when a backup fails, or when none has succeeded for 30 hours, and never repeats a notice after a tower restart.

```sh
cp "$SRC/tower_db_backup_notify.py" .tower/ext/
```

Read the note at the top of the file: it sends the message with `ctx["store"].push(...)`, which is not on the extension contract's stable list (`dah guide extend`, "What stays stable"), so look at it again after upgrading agent-hub. Set `TARGET` to your hub lane's id if it is not `hub`.

## Restore

Restoring replaces the board's whole history with the backup's: anything written after the backup is gone. Do it with the tower stopped, and tell the people who use the board.

1. Pick the file: `rclone lsf -R "<dest>"`, `aws s3 ls --recursive s3://<bucket>/tower/` or `ls -R <folder>`; names are `<date>/tower-<HHMM>.db`.
2. Stop the tower: `dah stop`, or `sudo systemctl stop agent-hub.service` (a stop leaves lane sessions running if the unit has `KillMode=process`; see [linux-server.md](linux-server.md)).
3. Move the current files aside, do not delete them:

   ```sh
   cd <workspace>/.tower/state
   mv tower.db tower.db.before-restore
   mv tower.db-wal tower.db-wal.before-restore 2>/dev/null; mv tower.db-shm tower.db-shm.before-restore 2>/dev/null
   ```

4. Download the backup to `tower.db` (`rclone copyto "<dest>/<date>/tower-<HHMM>.db" tower.db`, `aws s3 cp s3://<bucket>/tower/<date>/tower-<HHMM>.db tower.db`, or `cp`).
5. Check it: `python3 -c "import sqlite3; print(sqlite3.connect('tower.db').execute('pragma integrity_check').fetchone())"` must print `('ok',)`.
6. Start the tower (`dah start`, or `sudo systemctl start agent-hub.service`), then check that the board answers `200` and `dah status` shows your lanes.
7. Delete the `.before-restore` files only after a person has looked at the board.

To test your backups without touching the live board, do steps 4 and 5 into a scratch folder and open the file with `sqlite3`.
