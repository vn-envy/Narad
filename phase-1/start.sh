#!/usr/bin/env bash
# Start the Narad backend.
# Sets SSL_CERT_FILE at the shell level so OpenSSL picks it up before Python starts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/../.venv/bin/python"

# Build (or reuse) the combined CA bundle: certifi + Cisco Umbrella corporate proxy CA
CA_BUNDLE="$("$VENV_PYTHON" -c '
import certifi, pathlib, subprocess
certifi_dir = pathlib.Path(certifi.where()).parent
combined = certifi_dir / "narad_cacert.pem"
content = pathlib.Path(certifi.where()).read_bytes()
for kw in ("Umbrella", "Cisco Umbrella"):
    try:
        r = subprocess.run(["security","find-certificate","-c",kw,"-a","-p"], capture_output=True, timeout=5)
        if r.stdout: content += b"\n" + r.stdout
    except Exception: pass
combined.write_bytes(content)
print(combined)
')"

export SSL_CERT_FILE="$CA_BUNDLE"
export REQUESTS_CA_BUNDLE="$CA_BUNDLE"
export CURL_CA_BUNDLE="$CA_BUNDLE"

echo "[narad] SSL_CERT_FILE → $CA_BUNDLE"
echo "[narad] Starting backend on :8000"

cd "$SCRIPT_DIR"
exec "$VENV_PYTHON" -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
