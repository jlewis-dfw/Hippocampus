"""Container health probe: ``python -m hippocampus.healthcheck`` (exit 0 if the API answers on :8090)."""
import sys
import urllib.request

try:
    urllib.request.urlopen("http://127.0.0.1:8090/docs", timeout=3).close()
except Exception as exc:  # noqa: BLE001
    print(exc, file=sys.stderr)
    sys.exit(1)
