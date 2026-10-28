#!/usr/bin/env bash
# Run after 05_webapp.sh. Source deployment only: no Docker or Container Apps.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../.."
source "$SCRIPT_DIR/config.env"
: "${FUNCTION_APP_NAME:=${APP_NAME}-gen}"
az account set --subscription "$SUBSCRIPTION_ID"
az provider register --namespace Microsoft.Web --wait
# Data-plane creation uses your signed-in account; requires the appropriate Storage roles.
az storage container create --account-name "$STORAGE_ACCOUNT" --name generation --auth-mode login -o none
az storage queue create --account-name "$STORAGE_ACCOUNT" --name team-player-generation --auth-mode login -o none
az webapp config appsettings set -g "$RESOURCE_GROUP" -n "$APP_NAME" --settings TEAM_PLAYER_GENERATION=disabled -o none
az functionapp create -g "$RESOURCE_GROUP" -n "$FUNCTION_APP_NAME" \
  --storage-account "$STORAGE_ACCOUNT" --consumption-plan-location "$LOCATION" \
  --os-type Linux --runtime python --runtime-version 3.12 --functions-version 4 -o none
WORKER_ID=$(az functionapp identity assign -g "$RESOURCE_GROUP" -n "$FUNCTION_APP_NAME" --query principalId -o tsv)
WEB_ID=$(az webapp identity show -g "$RESOURCE_GROUP" -n "$APP_NAME" --query principalId -o tsv)
STORAGE_ID=$(az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query id -o tsv)
for container in generation tracking; do
  az role assignment create --assignee-object-id "$WORKER_ID" --assignee-principal-type ServicePrincipal \
    --role 'Storage Blob Data Contributor' --scope "$STORAGE_ID/blobServices/default/containers/$container" -o none
done
az role assignment create --assignee-object-id "$WEB_ID" --assignee-principal-type ServicePrincipal \
  --role 'Storage Blob Data Contributor' --scope "$STORAGE_ID/blobServices/default/containers/generation" -o none
az role assignment create --assignee-object-id "$WEB_ID" --assignee-principal-type ServicePrincipal \
  --role 'Storage Queue Data Message Sender' --scope "$STORAGE_ID/queueServices/default/queues/team-player-generation" -o none
az functionapp config appsettings set -g "$RESOURCE_GROUP" -n "$FUNCTION_APP_NAME" --settings \
  TEAM_PLAYER_BLOB_ACCOUNT_URL="$STORAGE_URL" SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true \
  FUNCTIONS_WORKER_PROCESS_COUNT=1 PYTHON_THREADPOOL_THREAD_COUNT=1 PYTHON_ISOLATE_WORKER_DEPENDENCIES=1 \
  WEBSITE_MAX_DYNAMIC_APPLICATION_SCALE_OUT=2 -o none
python deploy/create_generation_bundle.py
az functionapp deployment source config-zip -g "$RESOURCE_GROUP" -n "$FUNCTION_APP_NAME" \
  --src deploy/generation-worker.zip --build-remote true -o none
# Publish matching source identity and queue client with the web app.
python deploy/create_bundle.py --out deploy/team-player-app.zip --match matches/match_demo_2026_27_MW01_T_G.jsonl --replace
az webapp deploy -g "$RESOURCE_GROUP" -n "$APP_NAME" --src-path deploy/team-player-app.zip --type zip -o none
az webapp config appsettings set -g "$RESOURCE_GROUP" -n "$APP_NAME" --settings \
  TEAM_PLAYER_GENERATION=azure TEAM_PLAYER_SEASON=2026-27 -o none
printf 'Generate matches at https://%s.azurewebsites.net/generate\n' "$APP_NAME"
printf 'Role changes can take several minutes to propagate. Check a generated match before sharing.\n'
