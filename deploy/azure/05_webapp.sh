#!/usr/bin/env bash
# Web app on App Service: viewer + online analyser + Foundry agents. Reads Blob with its
# managed identity (read-only on tracking + analysis; no access to ground-truth).
set -euo pipefail
source "$(dirname "$0")/config.env"
az account set --subscription "$SUBSCRIPTION_ID"
python deploy/create_bundle.py --out deploy/team-player-app.zip --replace

az appservice plan create -g "$RESOURCE_GROUP" -n "$PLAN_NAME" -l "$LOCATION" --sku "$PLAN_SKU" --is-linux -o none
az webapp create -g "$RESOURCE_GROUP" -p "$PLAN_NAME" -n "$APP_NAME" --runtime "PYTHON:3.12" -o none
PRINCIPAL_ID=$(az webapp identity assign -g "$RESOURCE_GROUP" -n "$APP_NAME" --query principalId -o tsv)
STORAGE_ID=$(az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query id -o tsv)
for c in tracking analysis; do
  az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
    --role "Storage Blob Data Reader" --scope "$STORAGE_ID/blobServices/default/containers/$c" -o none
done
# Foundry User (formerly Azure AI User) on the Foundry resource: lets the app call your model.
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role 53ca6127-db72-4b80-b1b0-d745d6d5456d --scope "$FOUNDRY_RESOURCE_ID" -o none

az webapp config appsettings set -g "$RESOURCE_GROUP" -n "$APP_NAME" -o none --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true TEAM_PLAYER_AI=foundry TEAM_PLAYER_IDENTITY=managed \
  FOUNDRY_PROJECT_ENDPOINT="$FOUNDRY_PROJECT_ENDPOINT" FOUNDRY_MODEL="$FOUNDRY_MODEL" \
  TEAM_PLAYER_BLOB_ACCOUNT_URL="$STORAGE_URL" TEAM_PLAYER_BLOB_CONTAINER=tracking \
  TEAM_PLAYER_ANALYSIS_CONTAINER=analysis TEAM_PLAYER_CACHE=/home/team_player_cache TEAM_PLAYER_SEASON=2026-27
az webapp config set -g "$RESOURCE_GROUP" -n "$APP_NAME" -o none --web-sockets-enabled true --always-on true \
  --startup-file "python -m uvicorn team_player.app.server:app --host 0.0.0.0 --port 8000"
az webapp deploy -g "$RESOURCE_GROUP" -n "$APP_NAME" --src-path deploy/team-player-app.zip --type zip
echo "Open: https://${APP_NAME}.azurewebsites.net   (health: /health)"
