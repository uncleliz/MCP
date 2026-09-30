#!/usr/bin/env python3
"""Spike S1 (T-001): reachability probe for the 5 remote MCP sources.

Standalone on purpose: depends only on httpx (and boto3 if installed), NOT on mcp_common.
For each source it checks DNS, TCP, TLS, one cheap GET and the response time, then
classifies: reachable / auth-fail / unreachable.

Usage:
    uv run python scripts/probe_reachability.py [--markdown] [--timeout 5]

URLs/credentials come from the same MCP_* env vars as the servers (see .env.example).
Secrets are never printed.
"""

from __future__ import annotations

import argparse
import os
import socket
import ssl
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

REACHABLE = "reachable"
AUTH_FAIL = "auth-fail"
UNREACHABLE = "unreachable"
NOT_CONFIGURED = "not-configured"


@dataclass
class ProbeResult:
    source: str
    target: str
    status: str
    dns: str = "-"
    tcp: str = "-"
    tls: str = "-"
    http: str = "-"
    latency_ms: int | None = None
    detail: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def classify_http(status_code: int) -> str:
    """Map an HTTP status of the cheap GET to a reachability class."""
    if status_code in (401, 403):
        return AUTH_FAIL
    if status_code < 500:
        return REACHABLE
    return UNREACHABLE


def _split(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def probe_network(url: str, timeout: float) -> tuple[ProbeResult, bool]:
    """DNS + TCP + TLS stages. Returns (partial result, ok_to_continue)."""
    scheme, host, port = _split(url)
    res = ProbeResult(source="", target=f"{host}:{port}", status=UNREACHABLE)
    if not host:
        res.detail = "invalid URL"
        return res, False
    try:
        socket.getaddrinfo(host, port)
        res.dns = "ok"
    except OSError as exc:
        res.dns = "fail"
        res.detail = f"DNS: {exc}"
        return res, False
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        res.tcp = "ok"
    except OSError as exc:
        res.tcp = "fail"
        res.detail = f"TCP: {exc}"
        return res, False
    try:
        if scheme == "https":
            ctx = ssl.create_default_context()
            try:
                with ctx.wrap_socket(sock, server_hostname=host):
                    res.tls = "ok"
            except ssl.SSLError as exc:
                res.tls = "fail"
                res.detail = f"TLS: {exc}"
                return res, False
        else:
            res.tls = "n/a"
    finally:
        sock.close()
    return res, True


def probe_http(
    source: str,
    url: str,
    path: str,
    timeout: float,
    auth: tuple[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> ProbeResult:
    res, ok = probe_network(url, timeout)
    res.source = source
    if not ok:
        return res
    start = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout, auth=auth, headers=headers) as client:
            resp = client.get(url.rstrip("/") + path)
    except httpx.HTTPError as exc:
        res.http = "fail"
        res.detail = f"HTTP: {type(exc).__name__}"
        return res
    res.latency_ms = int((time.perf_counter() - start) * 1000)
    res.http = str(resp.status_code)
    res.status = classify_http(resp.status_code)
    return res


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value and os.environ.get(f"{name}_FILE"):
        try:
            with open(os.environ[f"{name}_FILE"], encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError:
            value = ""
    return value


def _not_configured(source: str, var: str) -> ProbeResult:
    return ProbeResult(source=source, target="-", status=NOT_CONFIGURED, detail=f"set {var}")


def probe_confluence(timeout: float) -> ProbeResult:
    base = _env("MCP_CONFLUENCE_BASE_URL")
    if not base:
        return _not_configured("confluence", "MCP_CONFLUENCE_BASE_URL")
    email, token = _env("MCP_CONFLUENCE_EMAIL"), _env("MCP_CONFLUENCE_API_TOKEN")
    auth = (email, token) if email and token else None
    return probe_http("confluence", base, "/rest/api/space?limit=1", timeout, auth=auth)


def probe_gitlab(timeout: float) -> ProbeResult:
    base = _env("MCP_GITLAB_BASE_URL")
    if not base:
        return _not_configured("gitlab", "MCP_GITLAB_BASE_URL")
    token = _env("MCP_GITLAB_PRIVATE_TOKEN")
    headers = {"PRIVATE-TOKEN": token} if token else None
    return probe_http("gitlab", base, "/api/v4/version", timeout, headers=headers)


def probe_opensearch(timeout: float) -> ProbeResult:
    hosts = _env("MCP_OPENSEARCH_HOSTS")
    if not hosts:
        return _not_configured("opensearch", "MCP_OPENSEARCH_HOSTS")
    user, pw = _env("MCP_OPENSEARCH_USERNAME"), _env("MCP_OPENSEARCH_PASSWORD")
    auth = (user, pw) if user and pw else None
    return probe_http("opensearch", hosts.split(",")[0].strip(), "/", timeout, auth=auth)


def probe_kibana(timeout: float) -> ProbeResult:
    base = _env("MCP_KIBANA_BASE_URL")
    if not base:
        return _not_configured("kibana", "MCP_KIBANA_BASE_URL")
    user, pw = _env("MCP_KIBANA_USERNAME"), _env("MCP_KIBANA_PASSWORD")
    auth = (user, pw) if user and pw else None
    return probe_http("kibana", base, "/api/status", timeout, auth=auth)


def probe_cloudwatch(timeout: float) -> ProbeResult:
    region = _env("MCP_CLOUDWATCH_REGION")
    if not region:
        return _not_configured("cloudwatch", "MCP_CLOUDWATCH_REGION")
    endpoint = f"https://logs.{region}.amazonaws.com"
    res, ok = probe_network(endpoint, timeout)
    res.source = "cloudwatch"
    if not ok:
        return res
    key, secret = _env("MCP_CLOUDWATCH_AWS_ACCESS_KEY_ID"), _env(
        "MCP_CLOUDWATCH_AWS_SECRET_ACCESS_KEY"
    )
    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.config import Config
        from botocore.exceptions import ClientError
    except ImportError:
        res.status = REACHABLE
        res.detail = "network only (boto3 not installed, auth not checked)"
        return res
    start = time.perf_counter()
    try:
        client = boto3.client(
            "logs",
            region_name=region,
            aws_access_key_id=key or None,
            aws_secret_access_key=secret or None,
            config=Config(
                connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 1}
            ),
        )
        client.describe_log_groups(limit=1)
        res.status, res.http = REACHABLE, "200"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        res.http = code
        res.status = AUTH_FAIL if code in {
            "AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException",
            "InvalidSignatureException",
        } else REACHABLE
    except Exception as exc:  # noqa: BLE001 - probe must classify any failure, never crash
        res.status, res.http, res.detail = UNREACHABLE, "fail", type(exc).__name__
    res.latency_ms = int((time.perf_counter() - start) * 1000)
    return res


PROBES = (probe_confluence, probe_gitlab, probe_opensearch, probe_kibana, probe_cloudwatch)


def render_markdown(results: list[ProbeResult]) -> str:
    lines = [
        "| Source | Target | DNS | TCP | TLS | HTTP | Latency (ms) | Status | Detail |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lat = "-" if r.latency_ms is None else str(r.latency_ms)
        lines.append(
            f"| {r.source} | {r.target} | {r.dns} | {r.tcp} | {r.tls} | {r.http} | {lat} "
            f"| {r.status} | {r.detail} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S1 reachability probe (5 remote sources)")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--markdown", action="store_true", help="emit a markdown table")
    args = parser.parse_args(argv)
    results = [probe(args.timeout) for probe in PROBES]
    sys.stdout.write(render_markdown(results) + "\n")
    return 0 if all(r.status in (REACHABLE, NOT_CONFIGURED) for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
