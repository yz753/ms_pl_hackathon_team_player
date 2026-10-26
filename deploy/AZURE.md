# Deploying Team Player on Microsoft Azure and Microsoft Foundry

This guide assumes you have already created a **Foundry project with a deployed model** and nothing else. It goes from there to a public web page that:

- streams current-season matches as if live;
- analyses them online;
- compares them with five analysed history seasons;
- explains everything with Foundry agents.

Run every command from the **repository root** unless stated otherwise. The commands are written for bash: macOS/Linux Terminal, Git Bash on Windows, or Azure Cloud Shell. Each step says what to check before moving on.

---

## 0. What you will build

```
                ┌──────────────── Azure Container Apps Jobs (batch, run once) ────────────────┐
 your laptop ──►│ tp-simulate-history  tp-simulate-current  tp-analyse-history  tp-build-store │
 (az CLI)       │  image built in the cloud by Azure Container Registry (no local Docker)      │
                └───────────────┬───────────────────────────────┬──────────────────────────────┘
                                │ write                          │ write
                     ┌──────────▼──────────────── Storage account (Blob) ───────────────────────┐
                     │ tracking/      history/<season>/*.jsonl.gz   current/2024-25/*.jsonl.gz   │
                     │ ground-truth/  private player profiles (jobs only, never the web app)     │
                     │ analysis/      matches/<season>/*.json.gz   history.db   value_models.json│
                     │ work/          task claim markers                                         │
                     └──────────┬────────────────────────────────────────────────────────────────┘
                                │ read (managed identity, read-only on tracking + analysis)
 browser ──HTTPS/WebSocket──► Azure App Service (Python 3.12): viewer, online analyser, agents
                                │ Entra ID (managed identity, Foundry User role)
                                ▼
                     Microsoft Foundry project ──► your model deployment (e.g. gpt-5-mini)
```

| Piece | Azure service | Why |
|---|---|---|
| Bulk data (about 3 GB) | **Blob Storage** | Cheap and effectively unlimited. Foundry is **not** a bulk data store; it only sees the small fact sheets the agents read. |
| Simulation and offline analysis (about 13 CPU-hours) | **Container Apps Jobs** | Pay per second, run 20 replicas in parallel, then stop. The image is built in the cloud. |
| Live page, online analyser, agent orchestration | **App Service** (Linux, Python 3.12) | Runs the Python source directly and supports WebSockets. |
| Language model | **Microsoft Foundry** (your project) | Agents run in the app with the Microsoft Agent Framework and call your model deployment. |

## 1. Capacity, cost and why five history seasons

These figures were measured on this code: one 10-minute match takes about 15 s to simulate and about 8 s to analyse on one CPU, and a gzip match file is about 1.2 MB.

| Item | Per match | Per season (380) | 5 history + 1 current season |
|---|---|---|---|
| Tracking files | 1.2 MB | 470 MB | **2.8 GB** |
| Analysis files | 10 KB | 4 MB | 20 MB |
| history.db | ~100 KB | ~40 MB | ~190 MB (history only) |
| Simulate CPU time | 15 s | 1.6 h | 9.5 h |
| Analyse CPU time (history only) | 8 s | 0.85 h | 4.2 h |

- **Storage.** A storage account holds up to 5 PiB, so 3 GB is trivial. Hot LRS costs a few pence per GB-month.
- **Compute.** About 14 CPU-hours spread over 20 replicas × 2 vCPU finishes in roughly 20–40 minutes. At consumption pricing that is a few pounds at most.
- **Foundry.** The bulk data never goes to Foundry. Each agent call sends a small JSON fact sheet (a few KB). Cost depends on viewers: at most one review per viewer every 8 s, and two concurrent calls per server worker. Check the price of your model in the Foundry portal.
- **Choice.** Five history seasons (2019-20 to 2023-24) plus the current season (2024-25) fit comfortably. More history means better similar-situation matches (about 140,000 decision windows). To change the seasons, edit `HISTORY_SEASONS` / `CURRENT_SEASON` / `SEASON_START` in `team_player/config/league.py` **before** step 5.

## 2. Prerequisites

1. **Tools on your machine:**
   - Azure CLI 2.60 or newer (`az version`);
   - Python 3.12 (`python --version`);
   - git;
   - bash (Git Bash on Windows, or use Azure Cloud Shell, which has everything).
2. **Permissions.** You need **Owner**, or **Contributor + Role Based Access Control Administrator**, on the resource group. The scripts create role assignments, and Contributor alone cannot do that.
3. **Sign in:**
   ```bash
   az login
   az account set --subscription 8d325680-f74f-4368-9f4b-e1c77abb609e
   az account show -o table
   ```
4. **Quota check.** Container Apps uses consumption cores. 20 replicas × 2 vCPU = 40 cores. If you are unsure, check Portal → Subscriptions → *Usage + quotas* → filter "Container Apps". If your quota is lower, reduce `REPLICAS` in step 4.

## 3. Collect the Foundry details

1. Open the Foundry portal (ai.azure.com) and select your project.
2. **Endpoint.** On the project overview, copy the **project endpoint**: `https://<resource>.services.ai.azure.com/api/projects/<project>`.
3. **Deployment name.** Open **Models + endpoints**, find your model deployment and copy its **deployment name**, which is not necessarily the model family name. It must support tool/function calling; gpt-5-mini does.
4. **Resource ID.** Portal → the Foundry (AI Services) **resource** → *Properties* → **Resource ID** (`/subscriptions/.../providers/Microsoft.CognitiveServices/accounts/<resource>`).
5. **Give yourself access** (for local testing): Foundry resource → *Access control (IAM)* → *Add role assignment* → **Foundry User** (role ID `53ca6127-db72-4b80-b1b0-d745d6d5456d`; formerly "Azure AI User") → your account.

## 4. Fill in `deploy/azure/config.env`

Open `deploy/azure/config.env`. Your values from the old `app-service.sh` are already filled in. Check and edit:

- `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL`, `FOUNDRY_RESOURCE_ID`: from step 3.
- `STORAGE_ACCOUNT`: 3–24 lowercase letters/digits, globally unique. `ACR_NAME`: 5–50 letters/digits, globally unique. Change the suffix if a name is taken.
- `REPLICAS`, `REPLICA_CPU`: parallelism of the batch jobs.
- `APP_NAME`: must be globally unique (it becomes `https://<APP_NAME>.azurewebsites.net`). `PLAN_SKU`: `P0V3` is recommended; `B2` is cheaper.

## 5. Test locally first (10 minutes)

```bash
python -m venv .venv && source .venv/bin/activate          # Windows Git Bash: source .venv/Scripts/activate
python -m pip install -r requirements-agent.txt             # web + agents + Azure SDKs
python -m unittest discover -s tests -q                    # all tests should pass
python -m uvicorn team_player.app.server:app --port 8000    # open http://127.0.0.1:8000
```

With the example data you should see:

- a match list (3 current-season matches);
- the moving pitch;
- a "Similar situation in history" story after a few seconds;
- a working **Head-to-head ↗** button.

The badge reads "Local rules · AI not connected". Stop the server with Ctrl+C.

Now turn on the agents locally. Your `az login` identity is used:

```bash
export FOUNDRY_PROJECT_ENDPOINT='<from step 3>'
export FOUNDRY_MODEL='<deployment name>'
export TEAM_PLAYER_AI=foundry
python scripts/smoke_test_azure.py                          # makes real (paid) model calls
python -m uvicorn team_player.app.server:app --port 8000
```

The smoke test must finish without errors. In the browser the badge now reads "Microsoft Foundry agents", and *How this was checked* shows the trace TacticalScout → EvidenceEditor → Narrator. If it fails, see section 12 (troubleshooting).

## 6. Create the storage account

```bash
bash deploy/azure/01_storage.sh
```

This creates the storage account with shared keys disabled, plus the containers `tracking`, `ground-truth`, `analysis` and `work`, and gives *you* "Storage Blob Data Contributor". **Check:** Portal → storage account → *Containers* shows the four containers.

## 7. Create the registry, worker image and batch jobs

```bash
bash deploy/azure/02_jobs.sh
```

This does the following:

1. installs the `containerapp` CLI extension;
2. creates the registry and **builds the worker image in the cloud** (`az acr build` uploads only `team_player/` and `deploy/worker/`);
3. creates a user-assigned identity with AcrPull and Storage Blob Data Contributor;
4. creates the Container Apps environment and four manual jobs:
   - `tp-simulate-history` (`TEAM_PLAYER_TASK=simulate`, `TEAM_PLAYER_SEASONS=history`);
   - `tp-simulate-current` (`simulate`, `current`);
   - `tp-analyse-history` (`analyse`, `history`);
   - `tp-build-store` (`build-store`; also refits xG/xT into `value_models.json`).

**Check:**

```bash
az containerapp job list -g rg-s2155699-2564 -o table
```

### 7a. Trial run (strongly recommended)

Run only two fixtures per season first:

```bash
source deploy/azure/config.env
az containerapp job update -g "$RESOURCE_GROUP" -n tp-simulate-history --set-env-vars TEAM_PLAYER_LIMIT=2
az containerapp job start  -g "$RESOURCE_GROUP" -n tp-simulate-history
az containerapp job execution list -g "$RESOURCE_GROUP" -n tp-simulate-history -o table    # wait for Succeeded
az storage blob list --account-name "$STORAGE_ACCOUNT" -c tracking --auth-mode login --prefix history/ -o table
```

You should see 10 files (2 per season). Then remove the limit; the full run skips the files that already exist:

```bash
az containerapp job update -g "$RESOURCE_GROUP" -n tp-simulate-history --remove-env-vars TEAM_PLAYER_LIMIT
```

## 8. Simulate and analyse the history seasons, and build history.db

```bash
bash deploy/azure/03_run_history.sh
```

This runs three steps in order, each waiting for the previous one to finish:

1. Simulate 1,900 matches into `tracking/history/<season>/`, with private profiles in `ground-truth/`.
2. Analyse them offline into `analysis/matches/<season>/`.
3. Build `analysis/history.db` and `analysis/value_models.json`.

Expect roughly 20–40 minutes in total. It is **safe to re-run**: finished matches are skipped, and tasks from crashed replicas are re-claimed after 3 hours.

How work is shared: every replica runs `python -m team_player.cloud.worker`. Each of its processes claims one task at a time by creating `work/claims/<task>`, which can only be created once, so no two workers do the same match.

## 9. Simulate the current season

```bash
bash deploy/azure/04_current_season.sh
```

This writes 380 matches to `tracking/current/2024-25/`. They are **not** analysed offline: the web app analyses each one online while it streams, and they never enter `history.db`.

## 10. Deploy the web app

```bash
bash deploy/azure/05_webapp.sh
```

This does the following:

1. builds `deploy/team-player-app.zip`, an allowlist that excludes the simulator and `config/styles.py`;
2. creates the App Service plan and web app (Python 3.12) with a system-assigned identity;
3. grants that identity **Storage Blob Data Reader** on `tracking` and `analysis` only (never `ground-truth`), and **Foundry User** on your Foundry resource;
4. sets the app settings (`TEAM_PLAYER_AI=foundry`, `TEAM_PLAYER_IDENTITY=managed`, the Foundry endpoint and model, the Blob URL);
5. enables WebSockets and Always On, sets the startup command `python -m uvicorn team_player.app.server:app --host 0.0.0.0 --port 8000`, and deploys the ZIP (App Service installs `requirements.txt`).

The first start takes 2–5 minutes, because it installs packages and downloads `history.db` to `/home/team_player_cache`.

## 11. Verify end to end

```bash
bash deploy/azure/06_check.sh
```

- `/health` should show `"ai_backend": "foundry"` and `"history": {"matches": 1900, ...}`.
- `/api/matches` lists the current-season fixtures.
- **In the browser** (`https://<APP_NAME>.azurewebsites.net`):
  1. Pick a match and a striker.
  2. Within about 10 s the badge says "Microsoft Foundry agents" and *What is happening* shows an AI-reviewed story with numbers and **?** links.
  3. *Similar situation in history* names a past season, match, time and player.
  4. *Head-to-head ↗* opens the comparison page; *Ask the AI consultant* returns a plain-English summary.
  5. Picking a centre-back shows the "to be developed…" note.

**Lock it down before sharing:**

1. Portal → the web app → *Authentication* → *Add identity provider* → *Microsoft* → *Require authentication*.
2. Set a spending budget: *Cost Management* → *Budgets*.

## 12. Operations and troubleshooting

| Symptom | Fix |
|---|---|
| `AuthorizationFailed` during role assignments | You need Owner or RBAC Administrator on the resource group (section 2). |
| `StorageAccountAlreadyTaken` / registry name taken | Change `STORAGE_ACCOUNT` / `ACR_NAME` in `config.env`. |
| `az storage ...` fails with a permission error just after step 6 | Role propagation can take a few minutes. Wait, then re-run. |
| Job execution `Failed` | Read the logs (below). The most common cause is a role not yet propagated; re-start the job. |
| App shows "No match available" | Step 9 has not finished, or the web identity lacks Reader on `tracking`. Check with `06_check.sh`. |
| App badge says "AI unavailable (…)" | Check `FOUNDRY_MODEL` is the **deployment** name, the identity has Foundry User, and the model supports tools. Run `scripts/smoke_test_azure.py` locally with the same values. |
| Page loads but the pitch never moves | WebSockets are off: `az webapp config set -g $RESOURCE_GROUP -n $APP_NAME --web-sockets-enabled true`. |

**Logs:**

```bash
source deploy/azure/config.env
# Web app
az webapp log config -g "$RESOURCE_GROUP" -n "$APP_NAME" --application-logging filesystem --docker-container-logging filesystem
az webapp log tail   -g "$RESOURCE_GROUP" -n "$APP_NAME"
# Batch jobs (Log Analytics)
WS=$(az containerapp env show -g "$RESOURCE_GROUP" -n "$JOBS_ENV" --query properties.appLogsConfiguration.logAnalyticsConfiguration.customerId -o tsv)
az monitor log-analytics query -w "$WS" --analytics-query \
  "ContainerAppConsoleLogs_CL | where ContainerJobName_s startswith 'tp-' | project TimeGenerated, ContainerJobName_s, Log_s | order by TimeGenerated desc | take 50" -o table
```

**Updating code:**

- *Web app changes:* re-run `bash deploy/azure/05_webapp.sh`. It is idempotent apart from re-deploying the ZIP.
- *Simulator or analyser changes for the batch:*
  1. Rebuild the image with a new tag: set `IMAGE_TAG=v2` in `config.env`, then run `az acr build -r $ACR_NAME -t team-player-worker:v2 -f deploy/worker/Dockerfile .`.
  2. Point each job at it: `az containerapp job update -g $RESOURCE_GROUP -n tp-analyse-history --image $ACR_NAME.azurecr.io/team-player-worker:v2` (repeat for each job).
- *Re-generating data after changing `config/`:* delete the affected blobs first, because outputs that already exist are skipped. For example, to redo the whole analysis: `az storage blob delete-batch --account-name $STORAGE_ACCOUNT -s analysis --auth-mode login`, then `delete-batch -s work --pattern "claims/analyse/*"`, then re-run step 8.

**Pausing and cleaning up:**

- *Stop paying for the web app when idle:* `az webapp stop -g $RESOURCE_GROUP -n $APP_NAME` (and `start` to resume). The jobs cost nothing when they are not running.
- *Remove everything this guide created* (Foundry is kept):
  ```bash
  az webapp delete -g $RESOURCE_GROUP -n $APP_NAME
  az appservice plan delete -g $RESOURCE_GROUP -n $PLAN_NAME -y
  for j in tp-simulate-history tp-simulate-current tp-analyse-history tp-build-store; do az containerapp job delete -g $RESOURCE_GROUP -n $j -y; done
  az containerapp env delete -g $RESOURCE_GROUP -n $JOBS_ENV -y
  az acr delete -n $ACR_NAME -y
  az storage account delete -g $RESOURCE_GROUP -n $STORAGE_ACCOUNT -y
  az identity delete -g $RESOURCE_GROUP -n $JOB_IDENTITY
  ```

## 13. Optional: connect the storage account to your Foundry project

This is not needed for the app to work. It makes the data visible from the Foundry portal, and lets you add Blob-backed tools to agents later.

1. Foundry portal → your project → *Management center* → *Connected resources* → *New connection* → *Azure Blob Storage* → choose `STORAGE_ACCOUNT`, container `analysis`, authentication **Microsoft Entra ID**.
2. Give the Foundry project's managed identity **Storage Blob Data Reader** on that container (resource → IAM).
3. Keep `ground-truth` out of any Foundry connection: it holds the private player personalities that the analysis must never see.

## 14. What runs where (security summary)

- **Web app identity:** read-only on `tracking` and `analysis`, plus Foundry User. No access to `ground-truth`, the simulator code or `config/styles.py`; the bundle excludes them, and `tests/test_deployment.py` checks this.
- **Batch jobs:** a separate user-assigned identity that can write all containers. The image contains the simulator, so keep the registry private.
- **No secrets anywhere:** storage shared keys are disabled, and everything authenticates with Entra ID.
- **Agents:** read-only tools over a frozen evidence snapshot. The Narrator's text is rejected if it contains a number not in the facts, or a causal claim.
