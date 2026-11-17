# Linux server

Run the tower and its lane sessions on an always-on Linux machine (a cloud VM, a rented box, a spare PC) so the team does not depend on one person's laptop. This guide is about the machine and the services. How people reach the board is a separate choice: [cloudflare.md](cloudflare.md), [tailscale.md](tailscale.md), [aws.md](aws.md).

Written for systemd distributions (Ubuntu, Debian, Fedora, Amazon Linux). Commands are examples: show them to the person before running anything with `sudo`.

## 1. Install `dah` as a fixed copy

Install a normal, non-editable copy and treat it as read-only:

```sh
uv tool install dongduni-agent-hub        # or: pipx install dongduni-agent-hub
which dah                                  # e.g. /home/<user>/.local/bin/dah
```

Never use `pip install -e` or `uv tool install -e` on a server that is running: replacing or deleting the source folder under a live tower breaks the board and every open agent session (see [Safe update](#6-safe-update-and-reinstall)).

Log each agent CLI in once over SSH (`claude`, `opencode auth login`, `codex login`), clone the workspace, then `dah init` as the setup playbook says (`dah guide`).

## 2. The tower as a systemd service

```ini
# /etc/systemd/system/agent-hub.service
[Unit]
Description=agent-hub tower
After=network-online.target
Wants=network-online.target

[Service]
User=<user>
WorkingDirectory=/home/<user>/workspace
Environment=HOME=/home/<user>
Environment=PATH=/home/<user>/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/<user>/.local/bin/dah run
Restart=always
RestartSec=5
KillMode=process

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now agent-hub.service
```

Two lines matter more than the rest:

- **`KillMode=process`**. systemd's default, `control-group`, kills every process in the service's cgroup when the unit stops or restarts. Lane sessions started by the tower live in that cgroup, so a plain `systemctl restart` would end every running agent session in the middle of its work. With `KillMode=process` only the tower itself is stopped and the sessions keep running; the new tower finds them again. Without it, restart only when no lane is working.
- **`Restart=always`**: the tower comes back after a crash or a reboot.

Environment variables the agents need (API keys, `GH_TOKEN`) go in a file with mode `600` and `EnvironmentFile=/home/<user>/.tower.env`, never in the repo.

### PATH

A service does not read your shell profile. Its `PATH` is whatever the unit says, so tools that work in your SSH session can be "not found" for lane sessions. Common cases:

- Tools installed as snaps live in `/snap/bin`, which a service `PATH` usually lacks. Either add `/snap/bin` to `Environment=PATH=` or link the binary: `sudo ln -s /snap/bin/<tool> /usr/local/bin/<tool>`.
- `/usr/local/bin` must be in the `PATH` line if you link tools there.
- `dah` and agent CLIs installed with `uv tool`/`pipx`/`npm -g --prefix ~/.local` need `/home/<user>/.local/bin`.

After editing the unit: `sudo systemctl daemon-reload && sudo systemctl restart agent-hub.service`. A unit file that changed on disk but was not reloaded shows a warning in `systemctl cat`.

## 3. Lane windows with Orca (optional, headless server)

If lanes run in terminal windows people can watch (`"terminal": "orca"` in `tower.json`), the Orca runtime has to run on the server without anyone logged in.

1. Keep your user's services running without a login session:

   ```sh
   sudo loginctl enable-linger <user>
   ```

2. Run the Orca runtime as a systemd **user** unit. The Linux CLI is called `orca-ide`; link it as `orca` so tools that look for `orca` find it:

   ```sh
   sudo ln -s "$(command -v orca-ide)" /usr/local/bin/orca
   ```

   ```ini
   # ~/.config/systemd/user/orca-serve.service
   [Unit]
   Description=Orca server
   StartLimitIntervalSec=900
   StartLimitBurst=5

   [Service]
   Type=simple
   ExecStart=/usr/bin/orca-ide serve --supervise --no-pairing --port 6768
   Environment=PATH=/home/<user>/.local/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin
   Restart=on-failure
   RestartSec=10
   KillMode=process

   [Install]
   WantedBy=default.target
   ```

   ```sh
   systemctl --user daemon-reload
   systemctl --user enable --now orca-serve.service
   ```

   Check the flags with `orca-ide serve --help` for your Orca version: they differ between releases. Treat the port as a remote control for every terminal on the machine, not as a web page.

   - Orca's documentation lists `--bind <address>` (the listening interface) and says `serve` accepts clients from localhost, LAN and Tailscale addresses unless `--allow-public` is added. Some releases (for example 1.4.224) listened on `0.0.0.0` and ignored any bind option, so do not assume your version binds loopback: check with `ss -ltn | grep 6768` after the start.
   - The unit above uses `--no-pairing`, as Orca's own `serve service install` does. This guide cannot say what authentication remains with that flag: Orca's documentation does not state it. Assume none.
   - So: add `--bind 127.0.0.1` where your version has it (or your tailnet address), block the port in the host firewall and the cloud security group (only SSH and what you really serve may be open), and re-check with `ss` after every Orca update.

   Over SSH, `systemctl --user` needs `XDG_RUNTIME_DIR=/run/user/$(id -u)` if the session has none.

3. **Register the workspace in Orca.** Orca only opens lane windows in folders it knows as a repository. Without this, opening a lane fails with `selector_not_found`:

   ```sh
   orca-ide repo add --path /home/<user>/workspace --kind git
   ```

   If you later move the workspace, remove the old registration first and add the new path.

4. Set `"terminal": "orca"` in `.tower/tower.json` and restart the tower. `dah status` prints where to watch the lanes.

Newer agent-hub versions also detect a window whose process has exited (some Orca releases keep answering `ok` for a finished tab). If lanes look alive in `dah status` but their window is dead on an older version, update agent-hub.

## 4. Swap

A burst of builds plus several agent sessions can run a small machine out of memory, and the kernel's OOM killer then picks a victim at random, often an agent session. A little swap turns that into slowness instead of lost work:

```sh
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf
sudo sysctl --system
swapon --show        # the file, 4G
```

`vm.swappiness=10` makes the kernel prefer RAM and use swap only under pressure. Swap is a safety net, not capacity: if it is in use all the time, the machine is too small.

## 5. Sizing

The agents' model work happens remotely; the machine runs the CLIs, git, tests and builds. As a rule of thumb to start from, then measure:

| load | starting size |
| --- | --- |
| hub + infra + 1-2 team lanes | 2 vCPU, 4-8 GB |
| 3-5 sessions at once, a test suite per session | 4 vCPU, 16 GB |

CPU is what grows with concurrent sessions: expect about one vCPU per two to three sessions that are busy at the same time, more if each session runs a heavy test suite. Watch a week of CPU and memory graphs (or `top`, `vmstat 5`) before resizing. On burstable cloud instances, check how the provider charges once the CPU credits run out; a steady average CPU above roughly 60% is the usual sign to move to a larger or non-burstable size. Cap concurrent infra sessions (`"max_instances"` on the infra lane) before buying more CPU.

## 6. Safe update and reinstall

Reinstalling the package under a live tower is the riskiest routine thing on a server. The infra lane's instructions carry the same checklist ("Updating or reinstalling on a server"). The short version:

1. Back up `tower.db` first (the backup script in [tower-db-backup.md](tower-db-backup.md) with `--why restart`, or `sqlite3 .tower/state/tower.db ".backup /tmp/tower-before-update.db"`). Stop if it fails.
2. Do it when no team lane is working (`dah status` shows none `busy`).
3. Note the version that runs now (`dah --version`, or `uv tool list`) so you can go back: `uv tool install --force dongduni-agent-hub==<old>`.
4. Install a fixed copy, never an editable one. Then `dah update` (or reinstall) and restart: `sudo systemctl restart agent-hub.service`.
5. Check: the board answers (`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/api/state` on the machine: `200` in `local` mode, `401` in token or header mode without sign-in or the identity header; through your public URL with sign-in, `200`), and the hub can run a command (ask it for the tower version in the board chat).

Failure mode to know: an agent session that was opened before the update has the path of the old `hooks.py` file written into its hook configuration. If an update or reinstall deletes that file, the hook exits with an error on every tool call and the agent cannot run anything ("PreToolUse ... hook error"). Newer versions run the hooks as a module (`python -m agent_hub.hooks`), which survives reinstalls; sessions opened by an older version keep the old path until they are renewed.

Optional team policy: require a human approval (a board item) before any reinstall or restart.

## 7. Troubleshooting

| symptom | look at |
| --- | --- |
| tower does not start or keeps restarting | `journalctl -u agent-hub.service -n 100 --no-pager`; try `dah run` by hand as that user in the workspace |
| a lane stays `closed` | `.tower/state/logs/<lane>.err` (not logged in, unknown flag, missing model) |
| tool "not found" only inside lane sessions | the service `PATH` ([PATH](#path)) |
| Orca window does not open, `selector_not_found` | the workspace is not registered: `orca-ide repo add --path ... --kind git` |
| Orca not answering | `systemctl --user status orca-serve.service`, `journalctl --user -u orca-serve.service`, and that lingering is on (`loginctl show-user <user> -p Linger`) |
| see what a lane's window shows | `orca-ide terminal read --terminal <handle>` (the handle is in `dah status` or `orca-ide terminal list`) |
| every command in a session fails with "PreToolUse ... hook error" | the hook file it points to is gone, usually after a reinstall ([Safe update](#6-safe-update-and-reinstall)); renew the session, update agent-hub |
| lanes died at every tower restart | `KillMode=process` missing in the unit |
| out-of-memory kills (`journalctl -k \| grep -i oom`) | swap ([Swap](#4-swap)) and the number of concurrent sessions |

## Backups

All tower state is one SQLite file, `.tower/state/tower.db`. `dah guide tower-db-backup` ships a daily backup script (rclone, S3 or a folder) with systemd timer, restore steps and a failure notice for the hub: [tower-db-backup.md](tower-db-backup.md).
