#!/usr/bin/env python3
"""Keep only Iran IPTV streams that respond successfully."""

from __future__ import annotations

import re
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 12
WORKERS = 16


def parse_channels(text: str) -> list[dict]:
    lines = text.splitlines()
    channels: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            meta = [line]
            name_match = re.search(r",(.+)$", line)
            name = name_match.group(1).strip() if name_match else "Unknown"
            referrer = None
            user_agent = UA
            inf = re.search(r'http-referrer="([^"]+)"', line)
            if inf:
                referrer = inf.group(1)
            ua = re.search(r'http-user-agent="([^"]+)"', line)
            if ua:
                user_agent = ua.group(1)
            i += 1
            while i < len(lines) and lines[i].startswith("#") and not lines[i].startswith("#EXTINF"):
                meta.append(lines[i])
                if lines[i].startswith("#EXTVLCOPT:http-referrer="):
                    referrer = lines[i].split("=", 1)[1].strip()
                if lines[i].startswith("#EXTVLCOPT:http-user-agent="):
                    user_agent = lines[i].split("=", 1)[1].strip()
                i += 1
            url = ""
            if i < len(lines) and lines[i].strip() and not lines[i].startswith("#"):
                url = lines[i].strip()
                i += 1
            channels.append(
                {
                    "name": name,
                    "url": url,
                    "meta": meta,
                    "referrer": referrer,
                    "user_agent": user_agent,
                }
            )
        else:
            i += 1
    return channels


def is_alive(ch: dict) -> bool:
    url = ch["url"]
    if not url:
        return False
    headers = {"User-Agent": ch["user_agent"] or UA}
    if ch["referrer"]:
        headers["Referer"] = ch["referrer"]
    req = urllib.request.Request(url, headers=headers, method="GET")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            data = resp.read(2048)
            code = getattr(resp, "status", 200)
            if code >= 400:
                return False
            text = data.decode("utf-8", errors="ignore")
            return ("#EXTM3U" in text) or ("#EXT-X-" in text) or (len(data) > 100)
    except Exception:
        # Retry once with unverified SSL (some CDNs have broken certs but streams work in VLC)
        try:
            insecure = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=insecure) as resp:
                data = resp.read(2048)
                code = getattr(resp, "status", 200)
                if code >= 400:
                    return False
                text = data.decode("utf-8", errors="ignore")
                return ("#EXTM3U" in text) or ("#EXT-X-" in text) or (len(data) > 100)
        except Exception:
            return False


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: prune_dead_streams.py <input.m3u> <output.m3u>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    channels = parse_channels(src.read_text(encoding="utf-8", errors="ignore"))
    alive: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(is_alive, ch): ch for ch in channels}
        for fut in as_completed(futures):
            ch = futures[fut]
            try:
                ok = fut.result()
            except Exception:
                ok = False
            if ok:
                alive.append(ch)
            else:
                print(f"DEAD\t{ch['name']}\t{ch['url']}", flush=True)

    # Preserve original order
    alive_urls = {id(ch) for ch in alive}
    ordered = [ch for ch in channels if id(ch) in alive_urls]

    out_lines = ["#EXTM3U"]
    for ch in ordered:
        out_lines.extend(ch["meta"])
        out_lines.append(ch["url"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"Kept {len(ordered)}/{len(channels)} channels -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
