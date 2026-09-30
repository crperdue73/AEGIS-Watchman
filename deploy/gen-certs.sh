#!/usr/bin/env bash
# gen-certs.sh — generate the mutual-TLS material for the collector -> central hop.
#
# Produces a CA and one leaf cert usable by BOTH the central server and the
# collectors (a single shared identity is fine for a single-tenant fleet; issue
# a separate leaf per collector if you want per-host revocation).
#
# Usage:
#   ./gen-certs.sh <outdir> [hostname]
#
# Then point both configs at the files:
#   central.yaml:  tls: {cert, key, ca}
#   collector.yaml: report.tls: {cert, key, ca, server_name}
set -euo pipefail

OUT="${1:?usage: gen-certs.sh <outdir> [hostname]}"
HOST="${2:-central.example.com}"
mkdir -p "$OUT"

echo "==> CA"
openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/ca.key"
openssl req -x509 -new -key "$OUT/ca.key" -sha256 -days 3650 \
  -subj "/CN=agentids-ca" -out "$OUT/ca.crt"

echo "==> leaf ($HOST)"
openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/leaf.key"
openssl req -new -key "$OUT/leaf.key" -subj "/CN=$HOST" -out "$OUT/leaf.csr"
cat > "$OUT/leaf.ext" <<EOF
subjectAltName = DNS:$HOST, DNS:localhost, IP:127.0.0.1
extendedKeyUsage = serverAuth, clientAuth
keyUsage = digitalSignature
EOF
openssl x509 -req -in "$OUT/leaf.csr" -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" \
  -CAcreateserial -days 825 -sha256 -extfile "$OUT/leaf.ext" -out "$OUT/leaf.crt"

chmod 600 "$OUT"/*.key
rm -f "$OUT/leaf.csr" "$OUT/leaf.ext" "$OUT/ca.srl"

echo
echo "wrote: $OUT/{ca.crt,ca.key,leaf.crt,leaf.key}"
echo "point central tls.cert/key at leaf.crt/leaf.key, tls.ca at ca.crt"
echo "point collector report.tls the same way (server_name: $HOST)"
echo
echo "NOTE: keys are mode 0600, owned by YOU. Both agentids services run as"
echo "      User=agentids and must be able to read them, or central fails to"
echo "      start with 'load key pair: permission denied'. After generating:"
echo "        sudo chown -R agentids:agentids $OUT"
echo "        sudo chmod 0640 $OUT/*.key   # readable by agentids, no one else"
echo "      (In a container the reader is uid 65532 instead; see deploy/README.md.)"
