# AWS

Run the tower and the agents on a server when the team should not depend on one person's laptop.

## Machine

- EC2 (or Lightsail), Ubuntu or Amazon Linux, 2 vCPU / 4–8 GB is enough for a hub, infra and two team lanes. The agents do the heavy lifting remotely; the box mostly runs git, tests and builds.
- Install the agent CLIs you use and log them in once over SSH (`opencode auth login`, `claude`, `codex login`). Put API keys in the environment of the service, not in the repo.
- Install the `tower` command (`pipx install git+https://github.com/twglhk/agent-hub`), clone your workspace, then `tower init` as in the setup playbook (`tower guide`).
- Run it as a service, in the workspace:

```ini
# /etc/systemd/system/tower.service
[Unit]
Description=agent-hub
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/workspace
EnvironmentFile=/home/ubuntu/.tower.env
ExecStart=/home/ubuntu/.local/bin/tower run
Restart=always

[Install]
WantedBy=multi-user.target
```

## Reaching the board

Pick one:

1. **Cloudflare Tunnel + Access** on the instance: same as [cloudflare.md](cloudflare.md). No inbound ports open at all. Simplest and safest.
2. **Tailscale** on the instance: same as [tailscale.md](tailscale.md).
3. **ALB + OIDC (Cognito or your IdP)**: put an Application Load Balancer in front, with an `authenticate-oidc` / `authenticate-cognito` rule before forwarding to the instance. The ALB adds `x-amzn-oidc-identity` (the user's subject id). Run a local reverse proxy (nginx, Caddy) on the instance that forwards to `127.0.0.1:8787`, and set:

   ```json
   "auth": {"mode": "header", "header": "x-amzn-oidc-identity"}
   ```

   then pin people with `tower member add ana --ident <their subject id>`. Keep the instance's security group open only to the ALB.
4. **No public endpoint**: `aws ssm start-session --target <id> --document-name AWS-StartPortForwardingSession --parameters portNumber=8787,localPortNumber=8787` and open `http://127.0.0.1:8787` locally (laptop only, not phones).

## Backups

All tower state is one SQLite file. Copy it safely while running and ship it to S3 (or R2 with `--endpoint-url`):

```sh
sqlite3 .tower/state/tower.db ".backup /tmp/tower.db" && aws s3 cp /tmp/tower.db s3://<bucket>/tower/$(date +%F).db
```
