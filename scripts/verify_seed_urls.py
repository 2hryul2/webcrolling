"""One-shot verifier for `config/seed_sites.yaml` URLs.

Each site is fetched with httpx (timeout=10s, follow_redirects=True). The result is
classified as `verified` or `unreachable` based on:

- HTTP status (200 OK required)
- Content-Type matches the declared `crawl_method`:
  - rss  → application/rss+xml | application/atom+xml | application/xml | text/xml
  - html → text/html

Run from project root:
    python scripts/verify_seed_urls.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "config" / "seed_sites.yaml"

UA = "Watchtower/1.0 (+https://watchtower.shinhan.local)"

RSS_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
)
HTML_TYPES = ("text/html",)


def classify(method: str, status: int, ctype: str) -> tuple[str, str]:
    if status != 200:
        return "FAIL", f"HTTP {status}"
    ctype_lc = (ctype or "").lower()
    if method == "rss":
        if any(t in ctype_lc for t in RSS_TYPES):
            return "OK", ctype
        return "FAIL", f"unexpected content-type {ctype}"
    if method == "html":
        if any(t in ctype_lc for t in HTML_TYPES):
            return "OK", ctype
        return "FAIL", f"unexpected content-type {ctype}"
    return "FAIL", f"unknown crawl_method {method}"


def main() -> int:
    if not SEED.exists():
        print(f"seed file missing: {SEED}", file=sys.stderr)
        return 2
    sites = yaml.safe_load(SEED.read_text(encoding="utf-8")).get("sites", [])
    headers = {"User-Agent": UA, "Accept": "*/*"}
    results: list[tuple[str, str, str, str, str]] = []
    with httpx.Client(headers=headers, timeout=10.0, follow_redirects=True) as client:
        for s in sites:
            sid = s.get("id", "?")
            url = s.get("url", "")
            method = s.get("crawl_method", "")
            try:
                r = client.get(url)
                outcome, detail = classify(method, r.status_code, r.headers.get("content-type", ""))
            except Exception as exc:
                outcome, detail = "FAIL", f"{type(exc).__name__}: {str(exc)[:80]}"
            results.append((sid, s.get("name", ""), method, outcome, detail))
            print(f"{sid:<4} {method:<5} {outcome:<5} {detail[:120]}  [{url}]")

    ok = sum(1 for r in results if r[3] == "OK")
    print(f"\n{ok}/{len(results)} sites verified")
    # Print just the OK ids so the operator can mark seed yaml.
    print("OK ids:", ",".join(r[0] for r in results if r[3] == "OK"))
    print("FAIL ids:", ",".join(r[0] for r in results if r[3] != "OK"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
