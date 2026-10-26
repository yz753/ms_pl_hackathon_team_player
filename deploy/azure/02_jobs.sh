#!/usr/bin/env bash
# Registry + worker image (built in the cloud) + Container Apps environment + three jobs.
set -euo pipefail
source "$(dirname "$0")/config.env"
az account set --subscription "$SUBSCRIPTION_ID"
az extension add --name containerapp --upgrade -y
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry; do az provider register --namespace $ns --wait; done

az acr create -g "$RESOURCE_GROUP" -n "$ACR_NAME" --sku Basic -l "$LOCATION" -o none
# Cloud build: uploads only what .dockerignore allows (team_player/ and deploy/worker/).
az acr build -r "$ACR_NAME" -t "team-player-worker:${IMAGE_TAG}" -f deploy/worker/Dockerfile .

az identity create -g "$RESOURCE_GROUP" -n "$JOB_IDENTITY" -l "$LOCATION" -o none
IDENTITY_ID=$(az identity show -g "$RESOURCE_GROUP" -n "$JOB_IDENTITY" --query id -o tsv)
CLIENT_ID=$(az identity show -g "$RESOURCE_GROUP" -n "$JOB_IDENTITY" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show -g "$RESOURCE_GROUP" -n "$JOB_IDENTITY" --query principalId -o tsv)
ACR_ID=$(az acr show -n "$ACR_NAME" --query id -o tsv)
STORAGE_ID=$(az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query id -o tsv)
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role AcrPull --scope "$ACR_ID" -o none
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Storage Blob Data Contributor" --scope "$STORAGE_ID" -o none
echo "Waiting 60 s for role assignments to propagate..."; sleep 60

az containerapp env create -g "$RESOURCE_GROUP" -n "$JOBS_ENV" -l "$LOCATION" -o none

make_job () {   # name task seasons replicas timeout_s
  az containerapp job create -g "$RESOURCE_GROUP" -n "$1" --environment "$JOBS_ENV" \
    --trigger-type Manual --replica-timeout "$5" --replica-retry-limit 1 \
    --replica-completion-count "$4" --parallelism "$4" \
    --image "${ACR_NAME}.azurecr.io/team-player-worker:${IMAGE_TAG}" \
    --registry-server "${ACR_NAME}.azurecr.io" --registry-identity "$IDENTITY_ID" \
    --mi-user-assigned "$IDENTITY_ID" --cpu "$REPLICA_CPU" --memory "$REPLICA_MEMORY" \
    --env-vars TEAM_PLAYER_BLOB_ACCOUNT_URL="$STORAGE_URL" AZURE_CLIENT_ID="$CLIENT_ID" \
               TEAM_PLAYER_WORKERS="$REPLICA_CPU" TEAM_PLAYER_TASK="$2" TEAM_PLAYER_SEASONS="$3" TEAM_PLAYER_FIT_MODELS=1 -o none
}
make_job tp-simulate-history simulate     history "$REPLICAS" 14400
make_job tp-simulate-current simulate     current "$REPLICAS" 14400
make_job tp-analyse-history  analyse      history "$REPLICAS" 14400
make_job tp-build-store      build-store  history 1           7200
echo "Jobs created: tp-simulate-history, tp-simulate-current, tp-analyse-history, tp-build-store"
