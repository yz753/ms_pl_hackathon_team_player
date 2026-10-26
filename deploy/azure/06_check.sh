#!/usr/bin/env bash
# Quick checks after deployment.
set -euo pipefail
source "$(dirname "$0")/config.env"
URL="https://${APP_NAME}.azurewebsites.net"
echo "Health:";  curl -fsS "$URL/health"; echo
echo "Matches:"; curl -fsS "$URL/api/matches" | head -c 400; echo
echo "Blob counts:"
for p in history/ current/; do
  echo -n "  tracking/$p: "; az storage blob list --account-name "$STORAGE_ACCOUNT" -c tracking --auth-mode login --prefix "$p" --num-results 5000 --query "length(@)" -o tsv
done
echo -n "  analysis/matches/: "; az storage blob list --account-name "$STORAGE_ACCOUNT" -c analysis --auth-mode login --prefix matches/ --num-results 5000 --query "length(@)" -o tsv
echo "Recent app log lines:"; az webapp log tail -g "$RESOURCE_GROUP" -n "$APP_NAME" --timeout 20 || true
