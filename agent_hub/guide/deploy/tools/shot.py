#!/usr/bin/env python3
# /// script
# dependencies = ["playwright"]
# ///
from __future__ import annotations

import argparse
import sys

INSTALL_HINT = ("shot.py needs Playwright and a Chromium for it, and neither comes with agent-hub:\n"
                "  python3 -m pip install playwright && python3 -m playwright install chromium\n"
                "or run it through uv, which installs Playwright itself: uv run shot.py URL OUT.png\n"
                "On a server also install a font for your language (Debian/Ubuntu CJK: fonts-noto-cjk).")
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2}
DESKTOP = {"viewport": {"width": 1280, "height": 800}}


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Headless Chromium screenshot of a page, for example the board.")
    p.add_argument("url")
    p.add_argument("out", help="PNG file to write")
    p.add_argument("--phone", action="store_true", help="390x844 at 2x instead of 1280x800")
    p.add_argument("--full", action="store_true", help="the whole page, not just the viewport")
    p.add_argument("--wait", type=int, default=1500, metavar="MS", help="milliseconds to wait after the page loaded")
    p.add_argument("--locale", default="en-US", help="browser language, e.g. ko-KR")
    p.add_argument("--header", action="append", default=[], metavar="NAME=VALUE",
                   help="extra request header, e.g. the identity header of a board with auth.mode header (repeatable)")
    return p.parse_args(argv)


def headers_of(raw: list[str]) -> dict[str, str]:
    out = {}
    for item in raw:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise SystemExit(f"--header wants NAME=VALUE, got {item!r}")
        out[name.strip()] = value
    return out


def shoot(a: argparse.Namespace, headers: dict[str, str]) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        raise SystemExit(2)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            ctx = browser.new_context(locale=a.locale, extra_http_headers=headers or None, **(PHONE if a.phone else DESKTOP))
            page = ctx.new_page()
            page.goto(a.url, wait_until="networkidle")
            page.wait_for_timeout(a.wait)
            page.screenshot(path=a.out, full_page=a.full)
        finally:
            browser.close()
    print(a.out)


def main(argv: list[str] | None = None) -> None:
    a = parse(argv)
    shoot(a, headers_of(a.header))


if __name__ == "__main__":
    main()
