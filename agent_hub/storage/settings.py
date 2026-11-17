from __future__ import annotations

import re
from typing import Any

PROVIDERS = ("local", "drive", "s3", "off")
DEFAULT_MAX_MB = 25
DEFAULT_MAX_FILES = 10
DEFAULT_TOTAL_MB = 2048
MAX_MB_LIMIT = 51200
MAX_FILES_LIMIT = 50
BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
REGION = re.compile(r"[a-z]{2}(-[a-z]+)+-\d")
PREFIX = re.compile(r"([A-Za-z0-9._-]+/)*")
REMOTE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
ROOT = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")
KEYS = {
    "local": {"provider", "max_mb", "max_files", "max_total_mb"},
    "drive": {"provider", "max_mb", "max_files", "remote", "root"},
    "s3": {"provider", "max_mb", "max_files", "bucket", "region", "prefix"},
    "off": {"provider"},
}


def _whole(raw: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    v = raw.get(key)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
        raise ValueError(f'uploads.{key} must be a whole number from {low} to {high}, got {v!r}')
    return v


def _text(raw: dict[str, Any], key: str, pattern: re.Pattern[str], what: str, required: bool = True) -> str:
    v = raw.get(key)
    if v is None or v == "":
        if required:
            raise ValueError(f'uploads.{key} is needed for this provider ({what})')
        return ""
    if not isinstance(v, str) or not pattern.fullmatch(v):
        raise ValueError(f'uploads.{key} must be {what}, got {v!r}')
    return v


def parse(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError('uploads must be an object like {"provider": "local"}')
    provider = raw.get("provider", "local")
    if provider not in PROVIDERS:
        raise ValueError(f'uploads.provider must be one of {", ".join(PROVIDERS)}, got {provider!r}')
    stray = sorted(set(raw) - KEYS[provider])
    if stray:
        raise ValueError(f'uploads: {", ".join(stray)} does not belong to provider {provider!r}')
    if provider == "off":
        return {"provider": "off"}
    out: dict[str, Any] = {"provider": provider,
                           "max_mb": _whole(raw, "max_mb", DEFAULT_MAX_MB, 1, MAX_MB_LIMIT),
                           "max_files": _whole(raw, "max_files", DEFAULT_MAX_FILES, 1, MAX_FILES_LIMIT)}
    if provider == "local":
        out["max_total_mb"] = _whole(raw, "max_total_mb", DEFAULT_TOTAL_MB, 0, 10 ** 7)
    elif provider == "drive":
        out["remote"] = _text(raw, "remote", REMOTE, "an rclone remote name without the colon")
        root = _text(raw, "root", ROOT, "a folder path of letters, digits, . _ - and /", required=False)
        if ".." in root.split("/") or "." in root.split("/"):
            raise ValueError(f'uploads.root must not contain . or .. parts, got {root!r}')
        out["root"] = root
    else:
        out["bucket"] = _text(raw, "bucket", BUCKET, "an S3 bucket name")
        out["region"] = _text(raw, "region", REGION, "an AWS region such as eu-west-1")
        prefix = _text(raw, "prefix", PREFIX, "a folder prefix ending in /, such as agent-hub/", required=False)
        if ".." in prefix.split("/"):
            raise ValueError(f'uploads.prefix must not contain .. parts, got {prefix!r}')
        out["prefix"] = prefix
    return out
