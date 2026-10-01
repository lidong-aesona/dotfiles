#!/usr/bin/env python3
"""Refresh quota.toml from what the providers themselves report.

    python3 quota_read.py <estate-dir>

Reads each provider's weekly window with the login its harness already holds on this machine,
and rewrites that provider's `period_start`, `period_days`, `used` and `read_at`:

    anthropic  Claude Code's login (~/.claude/.credentials.json), the endpoint behind `/usage`
    openai     pi's openai-codex login (~/.pi/agent/auth.json), the endpoint behind Codex usage

A provider with no reader here (xai) keeps its hand-taken reading. Prints JSON, one entry per
provider in quota.toml. A reader that fails leaves its provider's reading untouched and makes the
exit nonzero; the others are still written.

Tokens are only ever sent to the provider's own endpoint and never printed. This script never
refreshes a login: an expired one is reported, and opening a session of that harness renews it.
"""
import json, os, re, sys, tomllib, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/api/oauth/usage",
    "openai": "https://chatgpt.com/backend-api/wham/usage",
}


class Unreadable(Exception):
    pass


def endpoint(provider):
    """The provider's endpoint, or a loopback stand-in for tests, never any other host."""
    url = os.environ.get(f"QUOTA_{provider.upper()}_URL", ENDPOINTS[provider])
    if url != ENDPOINTS[provider] and not url.startswith("http://127.0.0.1:"):
        raise Unreadable(f"refusing to send the {provider} login to {url}")
    return url


def fetch(url, headers):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise Unreadable(f"HTTP {e.code} from {url}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise Unreadable(f"{url}: {e}") from None


def login(path, *keys):
    try:
        cred = json.loads(Path(path).expanduser().read_text())
    except FileNotFoundError:
        raise Unreadable(f"no login file at {path}") from None
    for k in keys:
        cred = cred.get(k) or {}
    return cred


def expired(ms):
    return ms and ms / 1000 < datetime.now(timezone.utc).timestamp()


def anthropic():
    cred = login("~/.claude/.credentials.json", "claudeAiOauth")
    if not cred.get("accessToken"):
        raise Unreadable("no Claude Code login in ~/.claude/.credentials.json (macOS keeps it in the Keychain)")
    if expired(cred.get("expiresAt")):
        raise Unreadable("Claude Code login expired; open a Claude Code session here to renew it")
    week = fetch(endpoint("anthropic"), {"Authorization": f"Bearer {cred['accessToken']}",
                                         "anthropic-beta": "oauth-2025-04-20"}).get("seven_day")
    if not week or week.get("utilization") is None or not week.get("resets_at"):
        raise Unreadable("the anthropic response has no seven_day window")
    resets = datetime.fromisoformat(week["resets_at"])
    return resets, timedelta(days=7), float(week["utilization"]) / 100


def openai():
    cred = login("~/.pi/agent/auth.json", "openai-codex")
    if not cred.get("access"):
        raise Unreadable("no openai-codex login in ~/.pi/agent/auth.json; run `pi` and /login")
    if expired(cred.get("expires")):
        raise Unreadable("pi's openai-codex login expired; open a pi session on openai-codex to renew it")
    headers = {"Authorization": f"Bearer {cred['access']}", "User-Agent": "codex_cli_rs"}
    if cred.get("accountId"):
        headers["chatgpt-account-id"] = cred["accountId"]
    limit = fetch(endpoint("openai"), headers).get("rate_limit") or {}
    window = limit.get("primary_window")
    if not window or window.get("used_percent") is None or not window.get("reset_at"):
        raise Unreadable("the openai response has no primary window")
    used = 1.0 if limit.get("limit_reached") else float(window["used_percent"]) / 100
    resets = datetime.fromtimestamp(window["reset_at"], timezone.utc)
    return resets, timedelta(seconds=window["limit_window_seconds"]), used


READERS = {"anthropic": anthropic, "openai": openai}


def stamp(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def rewrite(text, table, values):
    """Set keys inside one [table], keeping every comment and every other line as written."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[{table}]"), None)
    if start is None:
        return text.rstrip("\n") + f"\n\n[{table}]\n" + "".join(f"{k} = {v}\n" for k, v in values.items())
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    missing = dict(values)
    for i in range(start + 1, end):
        m = re.match(r"\s*([A-Za-z_]+)\s*=", lines[i])
        if m and m.group(1) in missing:
            lines[i] = f"{m.group(1)} = {missing.pop(m.group(1))}\n"
    lines[start + 1:start + 1] = [f"{k} = {v}\n" for k, v in missing.items()]
    return "".join(lines)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1]) / "quota.toml"
    text = path.read_text()
    ledger = tomllib.loads(text)
    now = datetime.now(timezone.utc)
    report, failed = {}, False
    for provider in dict.fromkeys([*ledger, *READERS]):
        if provider not in READERS:
            report[provider] = {"status": "by hand", "read_at": ledger[provider].get("read_at")}
            continue
        try:
            resets, period, used = READERS[provider]()
        except Unreadable as e:
            failed = True
            report[provider] = {"status": "unreadable", "why": str(e),
                                "read_at": ledger.get(provider, {}).get("read_at")}
            continue
        values = {"period_start": f'"{stamp(resets - period)}"', "period_days": round(period / timedelta(days=1), 3),
                  "used": round(used, 4), "read_at": f'"{stamp(now)}"'}
        text = rewrite(text, provider, values)
        report[provider] = {"status": "read", "used": round(used, 4), "resets_at": stamp(resets)}
    tomllib.loads(text)
    path.write_text(text)
    print(json.dumps(report, indent=2))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
