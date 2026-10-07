#!/usr/bin/env bash
# Run on YOUR PC (Git Bash/WSL). Encrypts deploy/orion.private.env -> deploy/orion.private.env.enc
# so the (encrypted) file can be committed to a PRIVATE repo. Pod needs only ORION_SECRET_PASSPHRASE.
set -euo pipefail
cd "$(dirname "$0")"
[ -f orion.private.env ] || { echo "Create deploy/orion.private.env first (copy orion.env.example, fill the token)."; exit 1; }
read -r -s -p "Passphrase: " P; echo
ORION_SECRET_PASSPHRASE="$P" openssl enc -aes-256-cbc -pbkdf2 -salt -in orion.private.env -out orion.private.env.enc -pass env:ORION_SECRET_PASSPHRASE
echo "Wrote deploy/orion.private.env.enc  (commit this; NEVER commit orion.private.env)"
