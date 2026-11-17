# Caddy + oauth2-proxy (Google or any OIDC)

For a server with a domain that is not on Cloudflare and not on a tailnet: Caddy terminates TLS (certificates are automatic), [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) signs people in with Google or any OpenID Connect provider (Okta, Entra ID, Keycloak, Auth0 ...) and tells the board who they are in a header. Everything runs on one machine and only Caddy listens on a public port.

```
browser ─443─▶ Caddy ─▶ oauth2-proxy 127.0.0.1:4180 ─▶ board 127.0.0.1:8787
                 │            sign-in, session cookie,        auth.mode = header
                 │            allowed e-mail domains          header = X-Forwarded-Email
                 └ drops any X-Forwarded-* identity header the client sent
```

Needs: a DNS name pointing at the machine (`board.example.com`), ports 80 and 443 open (80 is for the certificate challenge), and an OAuth client at your identity provider.

## 1. OAuth client

At the provider, create a **web application** client with the redirect URI

```
https://board.example.com/oauth2/callback
```

- Google: Cloud console → APIs & Services → Credentials → OAuth client ID → Web application. No JavaScript origin needed.
- Other OIDC provider: note the issuer URL (the one that serves `/.well-known/openid-configuration`).

Keep the client id and secret out of the repo. Generate the cookie secret yourself:

```sh
python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

## 2. oauth2-proxy

Install the release binary for your platform from the oauth2-proxy project (or your package manager) to `/usr/local/bin/oauth2-proxy`.

Secrets in an environment file only root and the service can read:

```sh
# /etc/oauth2-proxy/env   (chmod 640, owner root, group oauth2-proxy)
OAUTH2_PROXY_CLIENT_ID=<CLIENT_ID>
OAUTH2_PROXY_CLIENT_SECRET=<CLIENT_SECRET>
OAUTH2_PROXY_COOKIE_SECRET=<COOKIE_SECRET>
```

Everything else in `/etc/oauth2-proxy/oauth2-proxy.cfg`:

```toml
provider = "google"                       # or: provider = "oidc" and oidc_issuer_url = "https://<ISSUER>"
http_address = "127.0.0.1:4180"
upstreams = ["http://127.0.0.1:8787/"]
redirect_url = "https://board.example.com/oauth2/callback"

email_domains = ["example.com"]           # who may sign in; "*" lets in any account the provider has
reverse_proxy = true                      # it sits behind Caddy: use the forwarded headers
trusted_proxy_ips = ["127.0.0.1/32", "::1/128"]   # believe X-Forwarded-* only from Caddy on this machine
pass_user_headers = true                  # send X-Forwarded-Email / -User to the board
skip_provider_button = true               # go straight to the provider, no "Sign in with ..." page

cookie_name = "_board_auth"
cookie_secure = true                      # the cookie only travels over https
cookie_expire = "168h"                    # one week; phones stay signed in
```

```ini
# /etc/systemd/system/oauth2-proxy.service
[Unit]
Description=oauth2-proxy
After=network-online.target

[Service]
User=oauth2-proxy
EnvironmentFile=/etc/oauth2-proxy/env
ExecStart=/usr/local/bin/oauth2-proxy --config=/etc/oauth2-proxy/oauth2-proxy.cfg
Restart=always

[Install]
WantedBy=multi-user.target
```

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin oauth2-proxy
sudo systemctl daemon-reload && sudo systemctl enable --now oauth2-proxy.service
```

## 3. Caddy

`/etc/caddy/Caddyfile`:

```
board.example.com {
	encode gzip
	reverse_proxy 127.0.0.1:4180 {
		header_up -X-Forwarded-Email
		header_up -X-Forwarded-User
		header_up -X-Forwarded-Preferred-Username
		header_up -X-Forwarded-Groups
	}
}
```

**The four `header_up -...` lines are the security of this setup.** The board believes whatever the header says. If a client could send its own `X-Forwarded-Email: owner@example.com` and it reached the board unchanged, they would be signed in as that person. oauth2-proxy overwrites the headers for requests it has authenticated, but removing them at the edge means a client-supplied value never gets that far, whatever the proxy's version or settings do. Keep the list in step with every identity header you make the board read.

```sh
sudo systemctl reload caddy
```

## 4. The board

In `.tower/tower.json`:

```json
"web": {"host": "127.0.0.1", "port": 8787, "public_url": "https://board.example.com"},
"auth": {"mode": "header", "header": "X-Forwarded-Email"}
```

Restart the tower (`dah restart`, or `sudo systemctl restart agent-hub.service`). Optionally pin people to names and roles: `dah member add ana --role owner --ident ana@example.com`. Once any member has an ident, only those members get in, even if oauth2-proxy lets the whole domain through.

As in the [Cloudflare guide](cloudflare.md), the tower trusts the identity header only on connections from loopback, which is where oauth2-proxy connects from. Keep the board on `127.0.0.1` and never bind it to `0.0.0.0` in header mode.

## Check

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://board.example.com/api/state       # 302 to the provider (or 401): no session
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forwarded-Email: x@example.com' https://board.example.com/api/state   # same answer: a forged header changes nothing
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forwarded-Email: x@example.com' http://127.0.0.1:8787/api/state        # 200 on the machine itself (if no member allow-list excludes it): that is why the board must never be reachable from outside
```

Then open the URL in a browser, sign in, and the board loads. Firewall: only 22 (from where you need it), 80 and 443 inbound; 4180 and 8787 stay on loopback.

## Notes

- Sessions are cookies on the board's domain; `cookie_expire` sets how long a phone stays signed in. Change `cookie_secret` to sign everyone out.
- `email_domains` is a coarse gate. Use `dah member add --ident` (above) for an exact list.
- Push notifications that link to items need `public_url`.
- Logout: `https://board.example.com/oauth2/sign_out`.
