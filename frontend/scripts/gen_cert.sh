#!/usr/bin/env bash
#
# Generate a self-signed certificate for the raw TCP chat server (stretch
# goal). The certificate is for a LAN demo, not for production: use a real
# certificate authority for anything that matters.
#
# Usage:
#   bash scripts/gen_cert.sh
#
# Produces:
#   frontend/certs/server.key   the private key
#   frontend/certs/server.crt   the certificate
#
# Then start the TCP server with TLS:
#   CT_TLS_CERT=../certs/server.crt CT_TLS_KEY=../certs/server.key python run.py
# and connect the CLI client with:
#   python -m network.cli_client --tls --ca ../certs/server.crt ...

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="${HERE}/../certs"
HOST="${1:-localhost}"

mkdir -p "${CERT_DIR}"

echo "Generating a self-signed certificate for '${HOST}' in ${CERT_DIR} …"

openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "${CERT_DIR}/server.key" \
    -out "${CERT_DIR}/server.crt" \
    -days 365 \
    -subj "/CN=${HOST}" \
    -addext "subjectAltName=DNS:${HOST},DNS:localhost,IP:127.0.0.1"

chmod 600 "${CERT_DIR}/server.key"

echo
echo "Done."
echo "  certificate : ${CERT_DIR}/server.crt"
echo "  private key : ${CERT_DIR}/server.key"
echo
echo "The certificate and the key are git-ignored on purpose — never commit them."
