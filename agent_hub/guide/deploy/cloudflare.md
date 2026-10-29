# Cloudflare Tunnel + Access

The board stays on `127.0.0.1`; `cloudflared` makes an outbound tunnel to Cloudflare, and Cloudflare Access lets in only the people you list (email one-time code, Google, GitHub, Okta …). Needs a domain on Cloudflare. Free for small teams.

## 1. Tunnel

```sh
brew install cloudflared            # or your OS package
cloudflared tunnel login            # opens a browser; pick the zone
cloudflared tunnel create board     # writes ~/.cloudflared/<id>.json (a secret: never commit it)
cloudflared tunnel route dns board board.example.com
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: board
credentials-file: /Users/you/.cloudflared/<id>.json
ingress:
  - hostname: board.example.com
    service: http://127.0.0.1:8787
  - service: http_status:404
```

Let the tower keep it alive (it restarts the command if it exits) in `.tower/tower.json`:

```json
"tunnel": "cloudflared tunnel run board"
```

## 2. Access

Zero Trust dashboard → Access → Applications → Add → Self-hosted:

- Application domain: `board.example.com`
- Policy: Allow, Include → Emails (or an email domain, or your IdP group)
- Session duration: as long as you like (a month is comfortable on phones)

Access adds `Cf-Access-Authenticated-User-Email` to every request it lets through. Tell the tower to trust it:

```json
"web": {"host": "127.0.0.1", "port": 8787, "public_url": "https://board.example.com"},
"auth": {"mode": "header", "header": "Cf-Access-Authenticated-User-Email"}
```

Optionally pin people to names and roles: `dah member add ana --role owner --ident ana@example.com`. Once any member has an ident, only those members get in.

Restart the tower (`dah stop && dah start`) after changing `web` or `auth`.

## Notes

- The tower only trusts the header from loopback, which is where `cloudflared` connects from. Do not bind the board to `0.0.0.0` in header mode.
- Push notifications that link to items need `public_url`.
