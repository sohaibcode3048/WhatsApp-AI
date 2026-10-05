"""Health check script — MODULE 26.

Used by Docker HEALTHCHECK and external monitoring.

Exit codes:
  0 — healthy
  1 — unhealthy

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --url http://localhost:8000
"""

import sys
import urllib.request
import urllib.error
import json


def check(url: str = "http://localhost:8000") -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=5) as resp:
            body = json.loads(resp.read())
            status = body.get("status", "unknown")
            print(f"Health: {status}")
            return status in ("ok", "degraded")
    except urllib.error.URLError as exc:
        print(f"Health check failed: {exc}")
        return False
    except Exception as exc:
        print(f"Health check error: {exc}")
        return False


if __name__ == "__main__":
    url = "http://localhost:8000"
    for arg in sys.argv[1:]:
        if arg.startswith("--url="):
            url = arg.split("=", 1)[1]
        elif arg == "--url" and len(sys.argv) > sys.argv.index(arg) + 1:
            url = sys.argv[sys.argv.index(arg) + 1]

    healthy = check(url)
    sys.exit(0 if healthy else 1)
