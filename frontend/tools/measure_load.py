"""Measure a page load the way a browser experiences it.

Fetches the HTML, then every stylesheet, script, font and icon it references -
in parallel, the way a browser does - and reports the total time to first
paint. This is what "the site is slow on another device" actually means.
"""

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://172.16.240.172:5000"
PAGES = sys.argv[2:] or ["/", "/login", "/register", "/dashboard.html"]

RESOURCE_RE = re.compile(
    r'(?:href|src)="([^"]+\.(?:css|js|woff2|svg|png|ico))"', re.I
)


def load(url):
    started = time.perf_counter()
    try:
        response = requests.get(url, timeout=30)
        return response.status_code, len(response.content), time.perf_counter() - started
    except Exception as exc:
        return f"ERR {exc}", 0, time.perf_counter() - started


def measure(path):
    base = BASE + path
    started = time.perf_counter()
    status, size, _ = load(base)
    html = requests.get(base, timeout=30).text

    resources = sorted({urljoin(base, r) for r in RESOURCE_RE.findall(html)})
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(load, resources))

    total = time.perf_counter() - started
    slowest = max((r[2] for r in results), default=0)
    errors = [r[0] for r in results if r[0] != 200]
    print(
        f"  {path:20} html {status} {size / 1024:6.1f} KB  "
        f"{len(resources):2} assets  total {total * 1000:7.0f} ms  "
        f"slowest {slowest * 1000:6.0f} ms"
        + (f"  ERRORS: {errors}" if errors else "")
    )
    return total


print(f"\nPage load over {BASE} (assets fetched in parallel, like a browser)\n")
overall = [measure(page) for page in PAGES]
print(f"\n  slowest page: {max(overall) * 1000:.0f} ms")
