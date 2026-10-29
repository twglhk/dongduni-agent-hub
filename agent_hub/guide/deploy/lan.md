# Same network (Wi-Fi / LAN)

The quickest way to open the board on a phone: no account, no domain, no tunnel. It only works while the phone is on the same network as the machine, and it is plain HTTP, so use it on a network you trust (home, office), never on public Wi-Fi.

1. Sign people in with tokens, since anyone on the network can reach the port:

   ```sh
   tower member add me --role owner --token     # prints a token once; keep it private
   ```

2. Listen on the network instead of loopback, in `.tower/tower.json`:

   ```json
   "web": {"host": "0.0.0.0", "port": 8787, "public_url": "http://<machine-ip>:8787"},
   "auth": {"mode": "token"}
   ```

   The machine's address: `ipconfig getifaddr en0` (macOS) or `hostname -I` (Linux).

3. Restart the tower (`tower stop && tower start`), open `http://<machine-ip>:8787` on the phone, paste the token once. Add it to the home screen; it opens like an app.

4. Check it from the phone, or from another machine: `curl -s -o /dev/null -w "%{http_code}\n" http://<machine-ip>:8787/api/state` prints `401` (signed out, as it should be). A firewall prompt on macOS needs "Allow" for Python.

Never use `"auth": {"mode": "local"}` with host `0.0.0.0`: local mode makes everyone who reaches the port the owner. Outside the network, use [Tailscale](tailscale.md) (simplest) or [Cloudflare](cloudflare.md).

One board shows every workspace on the machine (`tower workspaces`): switch from its title. Workspaces in `local` mode stay hidden behind a token-mode board.
