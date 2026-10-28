# Tailscale

If everyone who uses the board is on your tailnet (phones included), this is the shortest path: no domain, no public URL.

```sh
tailscale serve --bg 8787
tailscale serve status        # shows https://<machine>.<tailnet>.ts.net
```

Tailscale Serve proxies to `127.0.0.1:8787` and adds `Tailscale-User-Login` (the person's login, e.g. an email) to each request:

```json
"web": {"host": "127.0.0.1", "port": 8787, "public_url": "https://<machine>.<tailnet>.ts.net"},
"auth": {"mode": "header", "header": "Tailscale-User-Login"}
```

Pin people if you want names and roles: `tower member add ana --ident ana@example.com --role owner`.

Restart the tower after changing `web` or `auth`. Use your tailnet ACLs to decide which devices may reach the machine.

Do not use `tailscale funnel` for the board: Funnel is public and sends no identity header.
