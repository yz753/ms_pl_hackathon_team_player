#!/usr/bin/env bash
# Simulate the history seasons, analyse them offline, build history.db. Safe to re-run:
# finished matches are skipped. Each step waits for the previous one.
set -euo pipefail
source "$(dirname "$0")/config.env"
run () {
  local name=$(az containerapp job start -g "$RESOURCE_GROUP" -n "$1" --query name -o tsv)
  echo "Started $1 execution $name"
  while true; do
    status=$(az containerapp job execution show -g "$RESOURCE_GROUP" -n "$1" --job-execution-name "$name" --query properties.status -o tsv)
    echo "  $(date +%H:%M:%S) $1: $status"
    case "$status" in Succeeded) break;; Failed|Stopped|Degraded) echo "$1 $status — see logs (guide section 9)"; exit 1;; esac
    sleep 60
  done
}
run tp-simulate-history
run tp-analyse-history
run tp-build-store
az storage blob list --account-name "$STORAGE_ACCOUNT" -c analysis --auth-mode login --prefix history.db -o table
