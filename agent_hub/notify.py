"""Push a short note to humans when something lands on the board for them.

tower.json `notify` keys (values may be `env:NAME`):
  ntfy_topic     topic on ntfy (phone app), with optional ntfy_server (default https://ntfy.sh)
  slack_webhook  Slack incoming-webhook URL
  desktop        "1" = macOS/Linux desktop notification on the tower machine
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.request
from typing import Any, Callable

from .config import Config, secret


def _post(url: str, body: dict[str, Any], log: Callable[[str], None]) -> None:
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"content-type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return
    except Exception as e:  # some Python builds ship without CA certs; the system curl usually has them
        if not shutil.which("curl"):
            log(f"notify failed: {e}")
            return
    p = subprocess.run(["curl", "-fsS", "-m", "10", "-H", "content-type: application/json", "-d", data.decode(), url],
                       capture_output=True, text=True)
    if p.returncode:
        log(f"notify failed: {p.stderr.strip()}")


def _desktop(title: str, text: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(text)} with title {json.dumps(title)}"],
                       capture_output=True)
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", title, text], capture_output=True)


def notifier(cfg: Config, log: Callable[[str], None] = print) -> Callable[[list[dict[str, Any]]], None]:
    n = {k: secret(v) for k, v in cfg.notify.items()}

    def push(items: list[dict[str, Any]]) -> None:
        if not items:
            return
        first = items[0]
        title = first["title"] if first.get("alert") else f"{cfg.name}: {len(items)} for you"
        text = "\n".join(f"#{it['id']} {it['title']}" for it in items[:5])
        link = f"{cfg.public_url.rstrip('/')}/#item-{first['id']}" if cfg.public_url and not first.get("alert") else ""
        if n.get("ntfy_topic"):
            body: dict[str, Any] = {"topic": n["ntfy_topic"], "title": title, "message": text}
            if link:
                body["click"] = link
            _post(n.get("ntfy_server") or "https://ntfy.sh/", body, log)
        if n.get("slack_webhook"):
            _post(n["slack_webhook"], {"text": f"*{title}*\n{text}" + (f"\n<{link}|Open the board>" if link else "")}, log)
        if n.get("desktop") == "1":
            _desktop(title, text)

    return push
