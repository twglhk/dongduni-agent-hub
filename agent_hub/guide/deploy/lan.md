# Same network (Wi-Fi / LAN)

The quickest way to open the board on a phone: no account, no domain, no tunnel. It only works while the phone is on the same network as the machine, and it is plain HTTP, so use it on a network you trust (home, office), never on public Wi-Fi.

1. Sign people in with tokens, since anyone on the network can reach the port:

   ```sh
   dah member add me --role owner --token     # prints a token once; keep it private
   ```

2. Listen on the network instead of loopback, in `.tower/tower.json`:

   ```json
   "web": {"host": "0.0.0.0", "port": 8787, "public_url": "http://<machine-ip>:8787"},
   "auth": {"mode": "token"}
   ```

   The machine's address: `ipconfig getifaddr en0` (macOS) or `hostname -I` (Linux).

3. Restart the tower (`dah restart`), open `http://<machine-ip>:8787` on the phone, paste the token once. Add it to the home screen; it opens like an app.

4. Check it from the phone, or from another machine: `curl -s -o /dev/null -w "%{http_code}\n" http://<machine-ip>:8787/api/state` prints `401` (signed out, as it should be). A firewall prompt on macOS needs "Allow" for Python.

Never use `"auth": {"mode": "local"}` with host `0.0.0.0`: local mode makes everyone who reaches the port the owner, and the tower refuses to start with it unless `auth.allow_exposed` is `true`. Outside the network, use [Tailscale](tailscale.md) (simplest) or [Cloudflare](cloudflare.md).

One board shows every workspace on the machine (`dah workspaces`): switch from its title. Workspaces in `local` mode stay hidden behind a token-mode board, and a header-mode workspace is only served by a board in header mode with the same header name.

The board answers only requests whose `Host` it knows: `localhost` and any IP address on any port (so `ssh -L 9000:127.0.0.1:8787` and `docker -p 9000:8787` work), the host of `web.public_url`, the `web.host` name, and every name in `web.allowed_hosts`; anything else gets `421`. To reach the board by a LAN name such as `box.local` or a MagicDNS name, list it: `"web": {"allowed_hosts": ["box.local", "box.tail1234.ts.net"]}` (bare host names, no scheme or port). A POST must also come from the board's own origin: the `Origin` is the `Host`, the origin of `web.public_url`, or an allowed host.

Behind a reverse proxy the proxy must pass the original `Host` (nginx: `proxy_set_header Host $host;`; the default `$proxy_host` sends the upstream address and the board then refuses the name). Set `web.public_url` to the address people type.
