#!/usr/bin/env bash
# Replaces Hello World. Use --local-build when Azure ACR Tasks is unavailable.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../.."
source "$SCRIPT_DIR/config.env"
BUILD_MODE="${1:---azure-build}"
case "$BUILD_MODE" in
 --local-build)
  command -v docker >/dev/null || { echo 'Install and start Docker Desktop before using --local-build.' >&2; exit 1; }
  docker info >/dev/null || { echo 'Start Docker Desktop, then retry.' >&2; exit 1; }
  docker buildx version >/dev/null ;;
 --azure-build) ;;
 *) echo 'Usage: bash deploy/azure/08_containerapp.sh [--local-build|--azure-build]' >&2; exit 2 ;;
esac
CONTAINER_APP_NAME=ms-pl-hackathon-team-player
CONTAINER_ENV_NAME=ms-pl-hackathon-team-player-env
ACR_NAME="${ACR_NAME:-tpregistry2155699}"
az account set --subscription "$SUBSCRIPTION_ID"
az provider register --namespace Microsoft.ContainerRegistry --wait
az containerapp show -g "$RESOURCE_GROUP" -n "$CONTAINER_APP_NAME" -o none
ENV_ID=$(az containerapp env show -g "$RESOURCE_GROUP" -n "$CONTAINER_ENV_NAME" --query id -o tsv)
ACTUAL_ENV=$(az containerapp show -g "$RESOURCE_GROUP" -n "$CONTAINER_APP_NAME" --query properties.managedEnvironmentId -o tsv)
[[ "$ACTUAL_ENV" == "$ENV_ID" ]] || { echo 'Container App environment mismatch'; exit 1; }
REGION=$(az containerapp env show -g "$RESOURCE_GROUP" -n "$CONTAINER_ENV_NAME" --query location -o tsv)
BUILD_DIR=$(mktemp -d)
trap 'rm -rf "$BUILD_DIR"' EXIT
python deploy/create_bundle.py --out "$BUILD_DIR/app.zip" --match matches/match_demo_2026_27_MW01_T_G.jsonl
python -m zipfile -e "$BUILD_DIR/app.zip" "$BUILD_DIR/app"
cp deploy/containerapp.Dockerfile "$BUILD_DIR/app/Dockerfile"
az acr create -g "$RESOURCE_GROUP" -n "$ACR_NAME" -l "$REGION" --sku Basic --admin-enabled false -o none
LOGIN_SERVER=$(az acr show -g "$RESOURCE_GROUP" -n "$ACR_NAME" --query loginServer -o tsv)
REGISTRY_ID=$(az acr show -g "$RESOURCE_GROUP" -n "$ACR_NAME" --query id -o tsv)
TAG="$(date -u +%Y%m%d%H%M%S)"
python deploy/create_generation_bundle.py --out "$BUILD_DIR/worker.zip"
python -m zipfile -e "$BUILD_DIR/worker.zip" "$BUILD_DIR/worker"
cp deploy/generation-container.Dockerfile "$BUILD_DIR/worker/Dockerfile"
if [[ "$BUILD_MODE" == --local-build ]]; then
 az acr login --name "$ACR_NAME"
 # Azure Container Apps requires Linux amd64, including when building on an Apple Silicon Mac.
 docker buildx build --platform linux/amd64 --provenance=false --tag "$LOGIN_SERVER/team-player:$TAG" --push "$BUILD_DIR/app"
 docker buildx build --platform linux/amd64 --provenance=false --tag "$LOGIN_SERVER/team-player-worker:$TAG" --push "$BUILD_DIR/worker"
else
 az acr build -r "$ACR_NAME" -t "team-player:$TAG" "$BUILD_DIR/app"
 az acr build -r "$ACR_NAME" -t "team-player-worker:$TAG" "$BUILD_DIR/worker"
fi
PRINCIPAL_ID=$(az containerapp identity assign -g "$RESOURCE_GROUP" -n "$CONTAINER_APP_NAME" --system-assigned --query principalId -o tsv)
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role AcrPull --scope "$REGISTRY_ID" -o none
STORAGE_ID=$(az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query id -o tsv)
for c in tracking analysis; do
 az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role 'Storage Blob Data Reader' --scope "$STORAGE_ID/blobServices/default/containers/$c" -o none
done
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role 53ca6127-db72-4b80-b1b0-d745d6d5456d --scope "$FOUNDRY_RESOURCE_ID" -o none
# Dedicated private worker, isolated from the public viewer and historical data.
WORKER_NAME="${CONTAINER_APP_NAME}-gen"
IDENTITY_NAME="${WORKER_NAME}-identity"
az storage container create --account-name "$STORAGE_ACCOUNT" --name generation --auth-mode login -o none
az storage queue create --account-name "$STORAGE_ACCOUNT" --name team-player-generation --auth-mode login -o none
az identity create -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" -l "$REGION" -o none
WORKER_ID=$(az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" --query id -o tsv)
WORKER_PRINCIPAL=$(az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" --query principalId -o tsv)
WORKER_CLIENT=$(az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" --query clientId -o tsv)
az role assignment create --assignee-object-id "$WORKER_PRINCIPAL" --assignee-principal-type ServicePrincipal --role AcrPull --scope "$REGISTRY_ID" -o none
for c in generation tracking; do
 az role assignment create --assignee-object-id "$WORKER_PRINCIPAL" --assignee-principal-type ServicePrincipal --role 'Storage Blob Data Contributor' --scope "$STORAGE_ID/blobServices/default/containers/$c" -o none
done
az role assignment create --assignee-object-id "$WORKER_PRINCIPAL" --assignee-principal-type ServicePrincipal --role 'Storage Queue Data Message Processor' --scope "$STORAGE_ID/queueServices/default/queues/team-player-generation" -o none
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role 'Storage Blob Data Contributor' --scope "$STORAGE_ID/blobServices/default/containers/generation" -o none
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role 'Storage Queue Data Message Sender' --scope "$STORAGE_ID/queueServices/default/queues/team-player-generation" -o none
if az containerapp show -g "$RESOURCE_GROUP" -n "$WORKER_NAME" -o none 2>/dev/null; then
 az containerapp update -g "$RESOURCE_GROUP" -n "$WORKER_NAME" --image "$LOGIN_SERVER/team-player-worker:$TAG" --min-replicas 1 --max-replicas 1 --set-env-vars TEAM_PLAYER_BLOB_ACCOUNT_URL="$STORAGE_URL" AZURE_CLIENT_ID="$WORKER_CLIENT" -o none
else
 az containerapp create -g "$RESOURCE_GROUP" -n "$WORKER_NAME" --environment "$ENV_ID" \
  --image "$LOGIN_SERVER/team-player-worker:$TAG" --registry-server "$LOGIN_SERVER" --registry-identity "$WORKER_ID" \
  --user-assigned "$WORKER_ID" --cpu 1 --memory 2Gi --min-replicas 1 --max-replicas 1 \
  --env-vars TEAM_PLAYER_BLOB_ACCOUNT_URL="$STORAGE_URL" AZURE_CLIENT_ID="$WORKER_CLIENT" -o none
fi
az containerapp registry set -g "$RESOURCE_GROUP" -n "$CONTAINER_APP_NAME" --server "$LOGIN_SERVER" --identity system -o none
az containerapp update -g "$RESOURCE_GROUP" -n "$CONTAINER_APP_NAME" --image "$LOGIN_SERVER/team-player:$TAG" --cpu 1 --memory 2Gi --min-replicas 1 --max-replicas 1 --set-env-vars \
 TEAM_PLAYER_AI=foundry TEAM_PLAYER_IDENTITY=managed TEAM_PLAYER_GENERATION=azure \
 FOUNDRY_PROJECT_ENDPOINT="$FOUNDRY_PROJECT_ENDPOINT" FOUNDRY_MODEL="$FOUNDRY_MODEL" \
 TEAM_PLAYER_BLOB_ACCOUNT_URL="$STORAGE_URL" TEAM_PLAYER_BLOB_CONTAINER=tracking \
 TEAM_PLAYER_ANALYSIS_CONTAINER=analysis TEAM_PLAYER_CACHE=/tmp/team_player_cache TEAM_PLAYER_SEASON=2026-27 -o none
az containerapp ingress enable -g "$RESOURCE_GROUP" -n "$CONTAINER_APP_NAME" --type external --target-port 8000 --transport auto -o none
az containerapp show -g "$RESOURCE_GROUP" -n "$CONTAINER_APP_NAME" --query properties.configuration.ingress.fqdn -o tsv
printf 'Viewer and generation worker deployed. Verify /api/generation and generate a match before sharing.\n'
