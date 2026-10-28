# Team Player

**Beyond the usual positions, beyond goals and assists.**

Team Player explores the benefits and costs of moving away from a player's usual position. A striker can drop deep to connect play, a fullback can join the attack and leave space behind, and a winger can run without the ball to draw defenders away.

The application turns synthetic player and ball positions into online football analysis and personalised Microsoft Foundry AI explanations. Watch the pitch, follow a player, explore a similar historical situation, or compare corresponding players across teams. The prototype focuses on strikers, fullbacks and wingers.

[Open the application](https://ms-pl-hackathon-team-player.redisland-fd1d12ca.northeurope.azurecontainerapps.io/) · [Project pitch](PITCH.md) · [Source code](https://github.com/yz753/ms_pl_hackathon_team_player)

## Contents

- [What runs where](#what-runs-where)
- [1. Install and run locally](#1-install-and-run-locally)
- [2. Create the Azure resources and Foundry deployment](#2-create-the-azure-resources-and-foundry-deployment)
- [3. Run locally with Azure AI](#3-run-locally-with-azure-ai)
- [4. Generate historical data and upload it automatically](#4-generate-historical-data-and-upload-it-automatically)
- [5. Deploy the website and generation worker](#5-deploy-the-website-and-generation-worker)
- [6. Generate and view additional current-season matches](#6-generate-and-view-additional-current-season-matches)
- [7. Update the application or reanalyse history](#7-update-the-application-or-reanalyse-history)
- [Data locations](#data-locations)
- [Tests and troubleshooting](#tests-and-troubleshooting)
- [Repository guide](#repository-guide)

## What runs where

All matches are synthetic. No real Premier League match data or player tracking is used. Twenty fictional teams, A–T, follow a 38-matchweek schedule with ten fixtures per matchweek. Each team meets every other team twice, giving 380 fixtures per complete season.

Each recording is a ten-minute segment, starting with goalkeeper possession or possession elsewhere on the pitch. This simplifies the demonstration. The analyser processes incoming frames incrementally, so ten minutes is not a limit imposed by analysis speed. Playback defaults to 2×. Select 1× to give AI explanations more time to arrive during the corresponding action.

| Component | Responsibility | Where it runs |
| --- | --- | --- |
| Simulator | Generates reproducible player and ball tracking, with varied playing styles | Locally for batch generation, or in the Azure generation worker |
| Online analyser | Infers events and measures movements, personal gain and shared team gain from frames already received | Local Python server or Azure viewer |
| Historical analysis | Processes completed historical recordings and builds the comparison database | Local batch worker with automatic Blob uploads |
| AI workflows | Explain current play, historical comparisons and player comparisons, with insight selection and checking | Application code calling a model deployed in Microsoft Foundry |
| Viewer | Renders the pitch, player selection, explanations and glossary | Browser, served locally or by Azure Container Apps |
| Storage and queue | Store historical and generated recordings, analysis results and generation jobs | Azure Blob Storage and Azure Queue Storage |

The five stages are **ingest → interpret → explain → render → personalise**. The analyser receives tracking observations rather than the simulator's private personality labels or scripted event outcomes. Current recordings are replayed as a stream and analysed using only observations available so far.

**Historical season: 2021–22. Current season: 2026–27.** Historical recordings are analysed offline for retrieval and comparison. Current recordings are analysed online during playback and must never be added to the historical comparison database.

AI explanations are generated during playback, not prerecorded. Separate AI workflows can use the same deployed model. You do not need to create separate agents manually in the Foundry portal. Reports have a 50-word maximum and may be withheld when the evidence is not informative enough. AI latency varies with service conditions, and an AI failure does not stop pitch playback.

Personal gain and team gain are modelled contribution measures. They are not goal probabilities. The application's glossary explains their calculation and limitations. Similar historical situations provide context, not proof that a different decision caused a different outcome.

## 1. Install and run locally

### Prerequisites

- Git and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Python **3.12**, installed through uv below.
- For Azure features, Azure CLI **2.60 or newer** and an Azure subscription.
- For the recommended Azure deployment, Docker Desktop running locally. Docker is not needed for local Python playback or local batch simulation.

The deployment commands below use **Bash**. On macOS or Linux, run them in a terminal from the repository root. On Windows, use WSL with its own Python environment for the complete deployment procedure. The local Python application can also run directly in PowerShell.

### Get the source and install dependencies

```bash
git clone https://github.com/yz753/ms_pl_hackathon_team_player.git
cd ms_pl_hackathon_team_player
uv python install 3.12
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
```

In PowerShell, replace the activation command with:

```powershell
.venv\Scripts\Activate.ps1
```

Use `requirements.txt` for the complete application dependencies. The existing `pyproject.toml` is not a replacement for that installation step. Whenever opening a new terminal, return to this directory and activate `.venv` again.

### Preview the interface without Azure

This mode displays tracking and local measurements. It does **not** generate Azure AI explanations or download historical data.

```bash
export TEAM_PLAYER_AI=local
export TEAM_PLAYER_BLOB_ACCOUNT_URL=""
export TEAM_PLAYER_HISTORY_DB=""
export TEAM_PLAYER_SEASON=2026-27
python -m uvicorn team_player.app.server:app --port 8001
```

Open **http://127.0.0.1:8001**. Leave the terminal running while using the website. `Ctrl+C` stops the server. In PowerShell, set environment variables using `$env:TEAM_PLAYER_AI="local"`, and similarly for the other variables.

### Selected examples

The lobby links to five current-season recordings. Each uses the same viewer, online analyser and AI code as other matches. The timestamps below identify useful viewing moments, not scheduled AI triggers.

| Focus | Sequence | Match time | Recording in `matches/` |
| --- | --- | --- | --- |
| T09 | Deep reception and long forward pass, followed by a teammate's goal | 1:43–1:53 | `match_demo_2026_27_MW01_T_G.jsonl` |
| M09 | Forward progression stalls against blocked lanes and numerical disadvantage | 1:11–1:28 | `match_demo_2026-27-trial00033-J-M.jsonl` |
| K02 | Fullback advances, receives and scores | 4:24–4:32 | `match_demo_2026-27-trial00039-K-N.jsonl` |
| S02 | Opponent exploits the advanced fullback's vacated flank and scores | 8:41–9:02 | `match_demo_2026_27_MW04_S_L.jsonl` |
| T11 | Off-ball run removes a passing option before the attack is lost | 5:10–5:16 | `match_demo_2026-27-trial00041-T-S.jsonl` |

For example, with the server running:

```text
http://127.0.0.1:8001/watch?demo=match_demo_2026_27_MW01_T_G.jsonl&player=T09
```

`demo` selects a recording and `player` selects the initial focus. Neither changes simulation, scoring or narration logic.

## 2. Create the Azure resources and Foundry deployment

Skip resource creation if you already have the required resources. Reuse their actual names and IDs in the configuration.

You need permission to create resources and assign roles. A resource-management role alone does not necessarily grant access to Blob data or Foundry inference. The steps below explicitly assign those permissions.

Azure services can incur charges, including idle Container Apps replicas, Container Registry, storage and model calls. Subscription credit does not automatically grant compute quota. Check subscription limits and your remaining credit before deployment.

### Install Azure CLI and sign in

On macOS with Homebrew:

```bash
brew install azure-cli
az login
az account list --output table
```

Alternatively, install Azure CLI as an isolated uv tool:

```bash
uv tool install --python 3.12 --prerelease=allow "azure-cli>=2.60"
uv tool update-shell
```

Open a new terminal after updating the shell, then run `az version` and `az login`. The prerelease flag allows Azure CLI's required prerelease dependencies. See the [official Azure CLI installation guide](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) for other operating systems.

### Create a Foundry project and deploy a model

In [Microsoft Foundry](https://ai.azure.com/):

1. Select the intended Azure subscription and create a Foundry resource and project, or open an existing project.
2. Open the model catalogue or **Models** page, select `gpt-5-mini`, and create a deployment supported by your subscription and region.
3. Record the **deployment name**. If it is `gpt-5-mini`, use that exact value. There is no need to rename an existing deployment.
4. Copy the **project endpoint** from the project details. It must have this form:

   ```text
   https://YOUR_RESOURCE.services.ai.azure.com/api/projects/YOUR_PROJECT
   ```

5. In the Azure portal, open the parent Foundry resource. Copy its **Resource ID** from **Properties** or **JSON View**. It ends with `/Microsoft.CognitiveServices/accounts/YOUR_RESOURCE`.

Do not use the model's `/openai/v1/responses` URL as `FOUNDRY_PROJECT_ENDPOINT`. A project endpoint and a model API endpoint serve different purposes. See [Foundry endpoint documentation](https://learn.microsoft.com/en-us/azure/foundry/reference/rest/).

The resource ID identifies the parent resource for access permissions. It is not an API key and is not the project endpoint. The application uses Microsoft Entra authentication, so no model API key is required for this setup.

### Create the deployment configuration

```bash
cp deploy/azure/config.env.example deploy/azure/config.env
```

Edit `deploy/azure/config.env`, replacing every placeholder. Do not overwrite an existing working configuration without preserving your settings.

```bash
export SUBSCRIPTION_ID="YOUR_SUBSCRIPTION_ID"
export RESOURCE_GROUP="team-player-rg"
export LOCATION="northeurope"
export STORAGE_ACCOUNT="YOUR_UNIQUE_STORAGE_ACCOUNT"
export STORAGE_URL="https://${STORAGE_ACCOUNT}.blob.core.windows.net"
export ACR_NAME="YOUR_UNIQUE_REGISTRY_NAME"
export FOUNDRY_PROJECT_ENDPOINT="https://YOUR_RESOURCE.services.ai.azure.com/api/projects/YOUR_PROJECT"
export FOUNDRY_MODEL="gpt-5-mini"
export FOUNDRY_RESOURCE_ID="/subscriptions/YOUR_SUBSCRIPTION_ID/resourceGroups/YOUR_FOUNDRY_RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/YOUR_RESOURCE"
export TEAM_PLAYER_BLOB_ACCOUNT_URL="$STORAGE_URL"
```

Storage account names must be globally unique, use lowercase letters and numbers, and be 3–24 characters long. Registry names must also be globally unique. Choose a region with Container Apps capacity for your subscription. The Foundry resource may be in a different region from the application.

There are **no spaces around `=`** in Bash assignments. Paste plain URLs, not Markdown links. This file contains machine-specific configuration and should not be committed to GitHub.

Load it and create the application resource group:

```bash
source deploy/azure/config.env
az account set --subscription "$SUBSCRIPTION_ID"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION"
```

Register the resource providers and install the Container Apps CLI extension:

```bash
for provider in Microsoft.App Microsoft.OperationalInsights Microsoft.ManagedIdentity Microsoft.ContainerRegistry Microsoft.Storage Microsoft.CognitiveServices
do
  az provider register --namespace "$provider" --wait
done
az extension add --name containerapp --upgrade
```

### Create storage and grant access

```bash
bash deploy/azure/01_storage.sh
```

This creates a private storage account and the `tracking`, `ground-truth`, `analysis` and `work` containers. It disables anonymous Blob access and shared-key authentication, and assigns your signed-in user Blob data access.

Grant your user queue access for creating the website's generation queue, and Foundry access for local AI calls:

```bash
STORAGE_ID=$(az storage account show --resource-group "$RESOURCE_GROUP" --name "$STORAGE_ACCOUNT" --query id --output tsv)
ME=$(az ad signed-in-user show --query id --output tsv)
az role assignment create --assignee-object-id "$ME" --assignee-principal-type User --role "Storage Queue Data Contributor" --scope "$STORAGE_ID"
az role assignment create --assignee-object-id "$ME" --assignee-principal-type User --role "53ca6127-db72-4b80-b1b0-d745d6d5456d" --scope "$FOUNDRY_RESOURCE_ID"
```

The final role is Foundry User, formerly Azure AI User. Role assignments can take several minutes to propagate. If storage creation failed with a permission error, verify **Access control (IAM)** on the storage account, wait for propagation and rerun the storage script.

## 3. Run locally with Azure AI

Create a local viewer configuration:

```bash
cp deploy/demo.env.example deploy/demo.env
```

Edit the project endpoint, model deployment and storage account URL in `deploy/demo.env`. Unlike the Bash deployment configuration, this is a dotenv file with plain `NAME=value` lines, without `export` or shell variable expansion. Keep the selected example path and player, or choose another pair from the table above.

If you previously ran the offline preview, remove its explicit history override:

```bash
unset TEAM_PLAYER_HISTORY_DB TEAM_PLAYER_AI TEAM_PLAYER_BLOB_ACCOUNT_URL
az login
uv run --no-project --env-file deploy/demo.env python scripts/run_demo.py --check
uv run --no-project --env-file deploy/demo.env python scripts/smoke_test_azure.py --mode live --timeout 30
uv run --no-project --env-file deploy/demo.env python scripts/run_demo.py
```

Run these inside the activated environment from section 1. `--no-project` tells uv not to resolve the repository's minimal project metadata. The installed `requirements.txt` dependencies are used instead.

`--check` validates the configuration and recording. The smoke test makes a real Foundry call. The last command starts the local server and opens the selected match. If no historical database has been uploaded yet, historical comparison will not be available until section 4 is complete.

On PowerShell, remove earlier overrides with `Remove-Item Env:TEAM_PLAYER_HISTORY_DB,Env:TEAM_PLAYER_AI,Env:TEAM_PLAYER_BLOB_ACCOUNT_URL -ErrorAction SilentlyContinue`. The `uv run` commands and dotenv file otherwise work the same way.

For a different port:

```bash
uv run --no-project --env-file deploy/demo.env python scripts/run_demo.py --port 8002
```

## 4. Generate historical data and upload it automatically

The recommended batch workflow runs on your computer and uploads each completed output directly to Azure Blob Storage. A virtual machine is not required. It uses temporary local working files rather than retaining the entire season locally.

Use the same source version and pinned dependencies as the viewer. Always specify the historical season explicitly:

```bash
source .venv/bin/activate
source deploy/azure/config.env
export TEAM_PLAYER_BLOB_ACCOUNT_URL="$STORAGE_URL"
export TEAM_PLAYER_WORKERS=3

python -m team_player.cloud.worker simulate --seasons 2021-22 &&
python -m team_player.cloud.worker analyse --seasons 2021-22 &&
python -m team_player.cloud.worker build-store --seasons 2021-22
```

These stages generate the historical tracking, analyse completed recordings, and build `analysis/history.db`. The `&&` operators prevent later stages from starting after a failed command. Three workers means three batch processes, not three AI agents. Historical analysis does not require Foundry narration calls.

On macOS, prefix each Python command with `caffeinate -i` to prevent idle sleep while that command runs. Keep the machine awake and connected to the internet for uploads. Runtime depends on CPU capacity and workload, so there is no fixed completion time.

The worker skips existing outputs and uses claims to avoid duplicate processing. Restarting a command normally resumes remaining work. After an interrupted process, an abandoned claim may remain until its three-hour expiry. Do not start duplicate workers or delete active claims.

Check the number of uploaded recordings and analyses:

```bash
az storage blob list --account-name "$STORAGE_ACCOUNT" --container-name tracking --prefix history/2021-22/ --auth-mode login --num-results 5000 --query "length(@)" --output tsv
az storage blob list --account-name "$STORAGE_ACCOUNT" --container-name analysis --prefix matches/2021-22/ --auth-mode login --num-results 5000 --query "length(@)" --output tsv
az storage blob show --account-name "$STORAGE_ACCOUNT" --container-name analysis --name history.db --auth-mode login --query properties.lastModified --output tsv
```

A complete season should have **380 tracking files and 380 per-match analysis files**, followed by a successfully built database. The number of historical situations is larger than the number of matches because one match can contain many situations. A database built before all analyses finish is only a partial history.

### Optional: keep the historical workflow entirely local

Use this instead of the Blob batch workflow if you want local historical files:

```bash
python -m team_player.simulator.season generate --season 2021-22 --out runs/history_build --private-out runs/history_private --workers 3
python -m team_player.analyser.history analyse --input runs/history_build/history/2021-22 --out runs/history_analysis --workers 3
python -m team_player.analyser.history build --input runs/history_analysis --db runs/history.db
```

Set `TEAM_PLAYER_HISTORY_DB=runs/history.db` in the viewer environment to use it. Remove that override when returning to Azure Blob history. The private simulation output is not an analyser input and must not be added to the public tracking stream.

## 5. Deploy the website and generation worker

The recommended hosting route is **Azure Container Apps**. It hosts both the public viewer and a separate private generation worker. Docker packages the existing Python code and dependencies into deployable images. It does not replace or change the simulator or analyser.

### Create the initial Container App

The current deployment script expects these exact names within your resource group:

- Container App: `ms-pl-hackathon-team-player`
- Environment: `ms-pl-hackathon-team-player-env`

If you want different names, update `CONTAINER_APP_NAME` and `CONTAINER_ENV_NAME` in `deploy/azure/08_containerapp.sh` first and use matching names in the commands below.

**Portal option:** Open [Azure portal](https://portal.azure.com/), search for **Container Apps**, and choose **Create**. Select your subscription and resource group, enter the names above, and choose a supported region and a Consumption environment. Use the **simple Hello World quickstart image** and enable external HTTP ingress. Deploy it and confirm its application URL opens. This is only a placeholder, replaced by the next step. See the [Microsoft portal quickstart](https://learn.microsoft.com/en-us/azure/container-apps/quickstart-portal).

**CLI alternative:**

```bash
source deploy/azure/config.env
az containerapp env create --name ms-pl-hackathon-team-player-env --resource-group "$RESOURCE_GROUP" --location "$LOCATION"
az containerapp create \
  --name ms-pl-hackathon-team-player \
  --resource-group "$RESOURCE_GROUP" \
  --environment ms-pl-hackathon-team-player-env \
  --image mcr.microsoft.com/k8se/quickstart:latest \
  --ingress external --target-port 80 \
  --cpu 0.5 --memory 1Gi --min-replicas 0 --max-replicas 1
```

Wait for the environment and app to finish provisioning before continuing. App Service quota and Container Apps quota are separate. The completed deployment needs capacity for the viewer and worker, each configured with 1 vCPU and 2 GiB memory.

### Build and deploy the actual project

Start Docker Desktop, activate the Python environment and run:

```bash
source .venv/bin/activate
source deploy/azure/config.env
az login
az account set --subscription "$SUBSCRIPTION_ID"
docker info
bash deploy/azure/08_containerapp.sh --local-build
```

The script:

1. Packages the application, public roster and selected recordings.
2. Builds Linux `amd64` viewer and worker images, including on Apple Silicon Macs.
3. Pushes the images to your Azure Container Registry.
4. Assigns managed identities and the required Foundry, Blob, queue and registry permissions.
5. Creates the `generation` container and `team-player-generation` queue.
6. Deploys the private `ms-pl-hackathon-team-player-gen` worker and updates the public viewer.
7. Enables external ingress on port 8000 and prints the website address.

Use `--local-build` when ACR Tasks is unavailable to your subscription. The default Azure build mode depends on ACR Tasks and is not required for this deployment path.

Both services use a minimum of one replica. They can incur idle charges even when nobody is viewing the website. The registry and other Azure services have their own charges. Use Azure Cost Management to monitor spending and clean up resources when they are no longer needed, after any required judging period.

### Verify the complete deployment

```bash
APP_HOST=$(az containerapp show --name ms-pl-hackathon-team-player --resource-group "$RESOURCE_GROUP" --query properties.configuration.ingress.fqdn --output tsv)
curl --fail "https://$APP_HOST/health"
curl --fail "https://$APP_HOST/api/generation"
```

Open `https://YOUR_APP_HOST` in a browser and check:

- A selected example plays beyond the opening seconds.
- An informative moment receives an Azure AI report.
- Historical comparison can retrieve the uploaded historical database.
- Head-to-head comparison works for corresponding players from different teams, such as T09 and G09.
- **Generate a new match** accepts a seed, completes the job and opens the new recording.

A healthy HTTP response alone does not prove that Foundry, historical retrieval and generation all work. Verify each feature before sharing the application.

Judges use the public website without installing Python, cloning the repository or logging into your Azure account. Your Azure login is for resource administration. The hosted application uses its managed identities to access private services. Do not enable a login requirement on the website unless you also provide judges with the required access.

## 6. Generate and view additional current-season matches

### Through the hosted website

Open **Generate a new match**, choose teams and a seed, then submit. The viewer places a request on Azure Queue Storage. The worker runs the repository simulator and saves the recording under `tracking/current/2026-27/`. Once ready, open it in the normal viewer.

The default seed is 1. Reproduction requires the same seed, teams, code and pinned dependency versions. Goals or particular tactical sequences are not guaranteed. Generating current matches does not modify the historical database.

### On your own computer

Use the same generation entry point as the website worker:

```bash
python -m team_player.cloud.generate_match \
  --seed 1 --home A --away B \
  --output data/current/2026-27/generated_seed1.jsonl.gz
```

This writes compressed tracking for a ten-minute 2026–27 match. To view it from local storage, start the server with:

```bash
export TEAM_PLAYER_DATA=data
export TEAM_PLAYER_SEASON=2026-27
export TEAM_PLAYER_BLOB_ACCOUNT_URL=""
uv run --no-project --env-file deploy/demo.env python -m uvicorn team_player.app.server:app --port 8001
```

Then open:

```text
http://127.0.0.1:8001/watch?match=generated_seed1.jsonl.gz&player=A09
```

This command loads the Foundry settings from section 3 for Azure narration. For an offline preview, use `python -m uvicorn team_player.app.server:app --port 8001` with `TEAM_PLAYER_AI=local` instead. With Blob disabled, use an explicit local `TEAM_PLAYER_HISTORY_DB` if you also want historical comparison. `run_demo.py` is intended for the uncompressed example files in `matches/`, so use the server command above for this compressed recording.

### Optional: copy selected examples to Blob

The selected examples are bundled with the viewer image, so uploading them separately is not required for their lobby links to work. To also store a copy in Blob:

```bash
source deploy/azure/config.env
python scripts/upload_current_demo.py \
  --input matches/match_demo_2026_27_MW01_T_G.jsonl \
  --input matches/match_demo_2026-27-trial00033-J-M.jsonl \
  --input matches/match_demo_2026-27-trial00039-K-N.jsonl \
  --input matches/match_demo_2026_27_MW04_S_L.jsonl \
  --input matches/match_demo_2026-27-trial00041-T-S.jsonl \
  --upload
```

Without `--upload`, the script validates the inputs only. These are current-season recordings, not historical training or comparison data. Uploading copies does not change the lobby's bundled-file links.

## 7. Update the application or reanalyse history

### Application, wording or AI instruction changes

Edit the source, run the relevant checks, then redeploy:

```bash
bash deploy/azure/08_containerapp.sh --local-build
```

Continuous deployment is not required. Each run builds new images and updates the services. Restart a local server after changing Python code, or use `--reload` during local development. Refresh the browser after webpage changes.

Existing and newly generated matches use the deployed generic viewer and analyser during playback. You do not need to regenerate tracking because of a wording, narration or analyser change. If you change simulation behaviour, only subsequently generated recordings reflect that change.

### Reanalyse historical recordings after changing the analyser

Historical analyses are stored results, so redeploying the viewer does not update them. Rebuild them from the existing historical tracking with the same analyser version used by the viewer.

First stop any batch workers for this season. The following commands delete **derived 2021–22 analyses and their analysis claims only**. They retain the original tracking and the existing database until a replacement is ready:

```bash
source deploy/azure/config.env
export TEAM_PLAYER_WORKERS=3
az storage blob delete-batch --account-name "$STORAGE_ACCOUNT" --source analysis --pattern 'matches/2021-22/*' --auth-mode login
az storage blob delete-batch --account-name "$STORAGE_ACCOUNT" --source work --pattern 'claims/analyse/2021-22/*' --auth-mode login
python -m team_player.cloud.worker analyse --seasons 2021-22 &&
python -m team_player.cloud.worker build-store --seasons 2021-22
```

Do not delete `tracking/history/2021-22/`, regenerate the season, or empty the entire storage account. If per-match analyses were already deleted, clearing stale analysis claims and rerunning the last two commands is sufficient. Local historical copies do not need to be deleted when using Blob history.

### Refresh the viewer's cached historical database

The application downloads the database to a local cache and holds historical information in memory. Replacing the Blob alone does not refresh an already-running viewer. After the rebuild succeeds, use a new cache directory and restart the app revision:

```bash
az containerapp update \
  --name ms-pl-hackathon-team-player \
  --resource-group "$RESOURCE_GROUP" \
  --set-env-vars "TEAM_PLAYER_CACHE=/tmp/team_player_cache_$(date +%s)"
```

For local use, change `TEAM_PLAYER_CACHE` in `deploy/demo.env` to a new path and restart the server. Remove `TEAM_PLAYER_HISTORY_DB` if it points to an old local database and you intend to use Blob. Start a new viewing session after refreshing.

## Data locations

Azure Blob Storage has a **storage account**, then **containers**, then blob names that look like folders. `tpdata2155699`, for example, is the project's storage account name, not a container name. Your own setup uses the account configured above.

| Container | Blob path | Purpose |
| --- | --- | --- |
| `tracking` | `history/2021-22/*.jsonl.gz` | Historical synthetic tracking |
| `ground-truth` | `2021-22/*.json` | Private simulator output for validation, not runtime analyser input |
| `analysis` | `matches/2021-22/*.json.gz` | Offline per-match historical analyses |
| `analysis` | `history.db` | Historical retrieval and player comparison database |
| `work` | `claims/simulate/...`, `claims/analyse/...` | Batch coordination and resume markers |
| `tracking` | `current/2026-27/*.jsonl.gz` | Generated or uploaded current-season recordings |
| `generation` | `jobs/<job-id>.json` | Website generation status and job metadata |

The `team-player-generation` queue is an **Azure Queue Storage queue**, not a Blob folder.

The five selected recordings and public roster are included in the viewer package. New website-generated recordings persist in Blob, not just inside a temporary container filesystem. The viewer caches downloaded files locally for playback. Historical data remains separate from current recordings and current-session analysis.

The simulator's private style configuration is needed to generate matches, but those labels are not passed to the analyser or AI as evidence. Do not confuse the source configuration with the public roster and tracking files.

## Tests and troubleshooting

Run the Python tests from the repository root:

```bash
python -m unittest discover -s tests -q
```

Test real Azure narration separately:

```bash
uv run --no-project --env-file deploy/demo.env python scripts/smoke_test_azure.py --mode live --timeout 30
```

A longer diagnostic timeout, such as `--timeout 180`, can help distinguish a slow response from an immediate failure. It does not change the live application's reporting deadline. Do not present local rule-based output as an Azure AI response.

View hosted logs:

```bash
az containerapp logs show --name ms-pl-hackathon-team-player --resource-group "$RESOURCE_GROUP" --type console --follow
az containerapp logs show --name ms-pl-hackathon-team-player-gen --resource-group "$RESOURCE_GROUP" --type console --follow
```

Run each log command in its own terminal if you want to follow both services.

| Problem | What to check |
| --- | --- |
| `az: command not found` | Install Azure CLI, update the shell PATH and open a new terminal. |
| uv cannot resolve Azure CLI dependencies | Use `--prerelease=allow` in the tool installation command. |
| Local page cannot be reached | Keep uvicorn running and use the port shown in its terminal output. |
| `Address already in use` | Stop your existing server or select another port. Do not start duplicate servers on the same port. |
| Foundry endpoint or authentication error | Use the project endpoint containing `/api/projects/`, the actual deployment name and the correct signed-in subscription. Check Foundry User permissions. |
| Blob or queue `403` | Check the relevant data role, scope and propagation time. Subscription Contributor alone is not sufficient for data access. |
| `MissingSubscriptionRegistration` | Register the named resource provider in the selected subscription. |
| `TasksOperationsNotAllowed` during build | Start Docker Desktop and use `08_containerapp.sh --local-build`. |
| Compute quota is zero | Request the required quota or use an eligible region/subscription. Having credit does not remove quota restrictions. |
| History missing or stale | Check `analysis/history.db`, remove an unintended local override, refresh the cache and restart. |
| AI report missing | Check logs and the smoke test. The insight checker may also reject an uninformative moment. AI is not expected to narrate every frame. |
| Match generation unavailable or stuck | Check `/api/generation`, the private worker logs, queue roles and generation job status. |
| Head-to-head selection rejected | Select the same player number from different teams, such as A09 and B09. The same player twice or A02 versus B03 is intentionally rejected. |

## Repository guide

| Path | Contents |
| --- | --- |
| `team_player/simulator/` | Match simulation and season generation |
| `team_player/config/` | League, simulation and analysis configuration |
| `team_player/analyser/` | Online evidence, movement scoring and historical analysis |
| `team_player/agents/` | Foundry calls, narration instructions and insight checking |
| `team_player/app/` | FastAPI server, storage access, replay and glossary |
| `team_player/app/web/` | Lobby, pitch viewer, generation and comparison interfaces |
| `team_player/app/web/examples.json` | Selected-example titles, descriptions, files and timestamps |
| `team_player/cloud/` | Batch history and queued current-match generation workers |
| `matches/` | Bundled example recordings and public roster |
| `scripts/` | Local launcher, smoke tests and supporting tools |
| `deploy/azure/` | Azure setup and deployment scripts |
| `deploy/demo.env.example` | Local viewer configuration template |
| `deploy/azure/config.env.example` | Azure deployment configuration template |
| `tests/` | Automated checks |

For webpage wording, edit the appropriate file in `team_player/app/web/`. Edit the example cards in `examples.json`. AI-generated sentences are controlled by the generic code and instructions in `team_player/agents/`, rather than by individual example HTML files.

### Which deployment scripts should I run?

The numbered files are **not** a sequence to execute indiscriminately.

| Script | Use in the recommended setup |
| --- | --- |
| `01_storage.sh` | Create Blob containers and storage permissions |
| `08_containerapp.sh --local-build` | Deploy the viewer and private generation worker |
| `02_jobs.sh`, `03_run_history.sh` | Alternative Azure batch-job workflow, not required for local batch generation with automatic uploads |
| `04_current_season.sh` | Alternative batch generation of a current season, not required for website generation |
| `05_webapp.sh`, `06_check.sh`, `07_generation.sh` | Earlier App Service/Functions deployment route, not part of the Container Apps instructions above |

Older supplementary deployment notes may describe previous seasons or hosting routes. Use this README for the current end-to-end setup.

### Before publishing the repository

Commit the source, dependency files, deployment templates, tests, public roster and selected recordings needed to reproduce the application. Exclude local virtual environments, caches, build archives, private validation outputs and personal configuration files.

In particular, inspect `git status` and check whether private configuration is already tracked:

```bash
git ls-files deploy/azure/config.env deploy/demo.env
```

If either file is listed, adding it to `.gitignore` does not remove it from version control. Remove it from tracking before publishing, while retaining your local copy. Publish the `.example` templates instead. Also make sure required new tests and source files are included, rather than accidentally excluded by broad ignore rules.

### Scope and limitations

This is a synthetic football demonstration, not an assessment of real players. Playing styles are probabilistic, so a seed does not guarantee a particular tactical outcome. Historical retrieval matches measured context, not every cause. Contribution estimates and narrative conclusions should be interpreted alongside their definitions and uncertainty.

The hosted route needs internet access for Foundry, Blob and queue operations. The simulator and analyser can run without internet once dependencies and input files are available locally. The website's AI explanations require a working Foundry deployment.
