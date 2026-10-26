#!/usr/bin/env bash
# Simulate the current season's 380 matches. They are NOT analysed offline: the web app
# analyses each one online while it streams.
set -euo pipefail
source "$(dirname "$0")/config.env"
name=$(az containerapp job start -g "$RESOURCE_GROUP" -n tp-simulate-current --query name -o tsv)
echo "Started tp-simulate-current execution $name"
while true; do
  status=$(az containerapp job execution show -g "$RESOURCE_GROUP" -n tp-simulate-current --job-execution-name "$name" --query properties.status -o tsv)
  echo "  $(date +%H:%M:%S) $status"; [[ "$status" == Succeeded ]] && break
  [[ "$status" =~ ^(Failed|Stopped|Degraded)$ ]] && exit 1; sleep 60
done
