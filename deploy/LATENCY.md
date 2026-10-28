> Updated narration implementation: see [NARRATION.md](NARRATION.md). The older workflow descriptions and reasoning defaults below are historical.

# Measuring live AI latency

The viewer allows a request to finish for up to 30 seconds. Only validated responses generated within five seconds and still matching the player, audience, possession and evidence window may replace the live overlay. Valid slower responses appear in Recent moments, labelled with their original match time and generation duration. Only eight recent AI moments are retained per viewer connection; the panel filters to the selected player and audience. Local prose is never added as AI. Reloading clears this session history.

The model and default reasoning are unchanged. To experiment with lower reasoning, set `TEAM_PLAYER_REASONING=low` before restarting the server; unset it to restore the model default. Lower reasoning is not assumed to preserve quality: inspect the saved captions.

Run from the repository with the virtual environment active:

```bash
source deploy/azure/config.env
python scripts/benchmark_azure.py --repeats 1 --output azure_latency.jsonl
```

This makes up to 12 paid requests: striker, winger and fullback evidence, each through Agent Framework and direct Azure Responses, with default and low reasoning. It uses one credential/client throughout, reports initial credential acquisition separately, and reuses identical snapshots across configurations. The direct route is a diagnostic baseline, not a change to the production agent. Results append to JSONL and include timing, available token usage, caption or error, and valid-caption-within-five-seconds status. Timeouts are censored observations; vetoes are valid quiet decisions but are not counted as delivered captions.

Use `--repeats 3` for a larger 36-request exploratory sample (configuration order reverses on alternate repeats). This still does not establish a production p95 or quality guarantee. Inspect saved prose for correct role, pros/cons and uncertainty. Selection/number checks are not complete semantic validation.

The request timer includes SDK work, networking, generation and any automatic retries. It does not separate them; use Azure Monitor response latency, generated tokens and HTTP 429 counts to investigate further. Authentication prewarming is explicit in this benchmark, so its model measurements do not include the cold credential acquisition shown separately. Warm connection and prompt-cache effects can affect order comparisons.

Start the viewer:

```bash
export TEAM_PLAYER_AI=foundry
python -m uvicorn team_player.app.server:app --port 8000
```

No new deployment, paid capacity allocation or model change is performed by these scripts.
