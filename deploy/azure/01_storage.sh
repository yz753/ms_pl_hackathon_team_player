#!/usr/bin/env bash
# Storage account + containers. Shared-key access is disabled: everything uses Entra ID roles.
set -euo pipefail
source "$(dirname "$0")/config.env"
az account set --subscription "$SUBSCRIPTION_ID"
az provider register --namespace Microsoft.Storage --wait
az storage account create -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" -l "$LOCATION" \
  --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 \
  --allow-blob-public-access false --allow-shared-key-access false
STORAGE_ID=$(az storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" --query id -o tsv)
ME=$(az ad signed-in-user show --query id -o tsv)
# You (for uploads, checks and local runs against Azure data).
az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "Storage Blob Data Contributor" --scope "$STORAGE_ID" -o none || true
echo "Waiting 60 s for the role assignment to propagate..."; sleep 60
for c in tracking ground-truth analysis work; do
  az storage container create --account-name "$STORAGE_ACCOUNT" -n "$c" --auth-mode login -o none
done
echo "Storage ready: $STORAGE_URL (containers: tracking, ground-truth, analysis, work)"
