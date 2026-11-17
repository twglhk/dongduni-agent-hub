from __future__ import annotations

import configparser
import datetime as dt
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

IMDS = "http://169.254.169.254/latest"
MAX_EXPIRES = 7 * 24 * 3600


class S3Error(Exception):
    pass


@dataclass(frozen=True)
class Creds:
    key: str
    secret: str
    token: str = ""
    expires: float = float("inf")


def _imds(path: str, token: str = "", method: str = "GET") -> str:
    headers = {"X-aws-ec2-metadata-token": token} if token else {"X-aws-ec2-metadata-token-ttl-seconds": "21600"}
    req = urllib.request.Request(IMDS + path, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.read().decode()


def _from_env() -> Creds | None:
    key, secret = os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
    return Creds(key, secret, os.environ.get("AWS_SESSION_TOKEN", "")) if key and secret else None


def _from_file() -> Creds | None:
    path = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or Path.home() / ".aws" / "credentials")
    parser = configparser.RawConfigParser()
    try:
        if not parser.read(path, encoding="utf-8"):
            return None
    except configparser.Error:
        return None
    profile = os.environ.get("AWS_PROFILE") or "default"
    if not parser.has_section(profile):
        return None
    key, secret = parser.get(profile, "aws_access_key_id", fallback=""), parser.get(profile, "aws_secret_access_key", fallback="")
    return Creds(key, secret, parser.get(profile, "aws_session_token", fallback="")) if key and secret else None


def _from_instance_role() -> Creds:
    try:
        token = _imds("/api/token", method="PUT")
        role = _imds("/meta-data/iam/security-credentials/", token).split()[0]
        d = json.loads(_imds("/meta-data/iam/security-credentials/" + role, token))
        exp = dt.datetime.strptime(d["Expiration"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
        return Creds(d["AccessKeyId"], d["SecretAccessKey"], d.get("Token", ""), exp)
    except (OSError, ValueError, KeyError, IndexError) as e:
        raise S3Error("no AWS credentials: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, fill "
                      f"~/.aws/credentials, or run on a machine with an instance role ({type(e).__name__})") from None


def standard_creds() -> Creds:
    return _from_env() or _from_file() or _from_instance_role()


def _uri(s: str, slash: bool) -> str:
    return quote(s, safe="-_.~" + ("/" if slash else ""))


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def amz_date(t: float) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t))


class S3:
    def __init__(self, bucket: str, region: str, host: str = "",
                 creds: Callable[[], Creds] = standard_creds, clock: Callable[[], float] = time.time):
        self.bucket, self.region = bucket, region
        self.host = host or f"{bucket}.s3.{region}.amazonaws.com"
        self._fetch, self.clock = creds, clock
        self._creds: Creds | None = None
        self._lock = threading.Lock()

    def creds(self) -> Creds:
        with self._lock:
            if self._creds is None or self._creds.expires - self.clock() < 300:
                self._creds = self._fetch()
            return self._creds

    def _sign(self, method: str, key: str, query: dict[str, str], headers: dict[str, str], payload: str,
              stamp: str, c: Creds) -> tuple[str, str, str]:
        day = stamp[:8]
        scope = f"{day}/{self.region}/s3/aws4_request"
        hs = {k.lower(): " ".join(str(v).split()) for k, v in {**headers, "host": self.host}.items()}
        signed = ";".join(sorted(hs))
        cq = "&".join(f"{_uri(k, False)}={_uri(v, False)}" for k, v in sorted(query.items()))
        canon = "\n".join([method, "/" + _uri(key, True), cq, "".join(f"{k}:{hs[k]}\n" for k in sorted(hs)),
                           signed, payload])
        sts = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canon.encode()).hexdigest()])
        k = _hmac(_hmac(_hmac(_hmac(("AWS4" + c.secret).encode(), day), self.region), "s3"), "aws4_request")
        return cq, signed, hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()

    def presign(self, method: str, key: str, expires: int = 3600, query: dict[str, str] | None = None,
                headers: dict[str, str] | None = None) -> str:
        if not 1 <= expires <= MAX_EXPIRES:
            raise S3Error("expires must be 1 second to 7 days")
        c = self.creds()
        stamp = amz_date(self.clock())
        q = {**(query or {}), "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
             "X-Amz-Credential": f"{c.key}/{stamp[:8]}/{self.region}/s3/aws4_request",
             "X-Amz-Date": stamp, "X-Amz-Expires": str(expires),
             "X-Amz-SignedHeaders": ";".join(sorted({"host", *(k.lower() for k in headers or {})}))}
        if c.token:
            q["X-Amz-Security-Token"] = c.token
        cq, _, sig = self._sign(method, key, q, headers or {}, "UNSIGNED-PAYLOAD", stamp, c)
        return f"https://{self.host}/{_uri(key, True)}?{cq}&X-Amz-Signature={sig}"

    def signed_headers(self, method: str, key: str, query: dict[str, str] | None = None, body: bytes = b"",
                       headers: dict[str, str] | None = None) -> dict[str, str]:
        c = self.creds()
        stamp = amz_date(self.clock())
        hs = {**(headers or {}), "x-amz-content-sha256": hashlib.sha256(body).hexdigest(), "x-amz-date": stamp}
        if c.token:
            hs["x-amz-security-token"] = c.token
        _, signed, sig = self._sign(method, key, query or {}, hs, hs["x-amz-content-sha256"], stamp, c)
        hs["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={c.key}/{stamp[:8]}/{self.region}/s3/aws4_request, "
                               f"SignedHeaders={signed}, Signature={sig}")
        return hs

    def _request(self, method: str, key: str, query: dict[str, str] | None, body: bytes,
                 headers: dict[str, str] | None) -> urllib.request.Request:
        hs = self.signed_headers(method, key, query, body, headers)
        cq = "&".join(f"{_uri(k, False)}={_uri(v, False)}" for k, v in sorted((query or {}).items()))
        url = f"https://{self.host}/{_uri(key, True)}" + (f"?{cq}" if cq else "")
        return urllib.request.Request(url, data=body if method in ("POST", "PUT") else None, method=method, headers=hs)

    def call(self, method: str, key: str, query: dict[str, str] | None = None, body: bytes = b"",
             headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        try:
            with urllib.request.urlopen(self._request(method, key, query, body, headers), timeout=30) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
        except OSError as e:
            raise S3Error(f"S3 {method} failed: {type(e).__name__}") from None

    def open(self, key: str, rng: str = "") -> Any:
        req = self._request("GET", key, None, b"", {"Range": rng} if rng else None)
        try:
            return urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            e.close()
            raise S3Error(f"S3 GET {e.code}") from None
        except OSError as e:
            raise S3Error(f"S3 GET failed: {type(e).__name__}") from None
