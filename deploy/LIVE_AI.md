> Updated narration implementation: see [NARRATION.md](NARRATION.md). The older workflow descriptions and reasoning defaults below are historical.

# Live AI captions

The live viewer uses LiveAnalystNarrator: one tool-free structured model turn to select evidence and write the caption. It uses the existing Foundry deployment and default reasoning setting. The prompt retains role definitions, balance, caveats, recent context and audience instructions. Python validates selected IDs, structure, numbers and known forbidden claims. These checks are not a complete semantic proof of grounding.

The server cancels live work after five seconds, including capacity acquisition within the bounded call. Local evidence remains visible while waiting and when AI fails. Existing timestamp, possession and preference checks discard stale responses. The full Scout → Editor → Narrator workflow remains available for diagnostics; comparison and similar-situation consultants are unchanged. No background review agent is added by this change.

From the repository, with the virtual environment active:

```bash
source deploy/azure/config.env
export TEAM_PLAYER_AI=foundry
python scripts/smoke_test_azure.py --mode live --timeout 5
python -m uvicorn team_player.app.server:app --port 8000
```

The smoke test makes a paid model request. A timeout is a failed latency target, not a successful AI caption. To inspect whether a valid caption eventually arrives, run `python scripts/smoke_test_azure.py --mode live --timeout 30`. To test the old workflow use `--mode full --timeout 120`. Each run reports elapsed time. Measure repeated calls (including the first cold call), timeout rate and caption quality before claiming a 2–5 second service level. No Azure latency claim has been verified by local tests.

Restart an already-running server to load this change. No model redeployment is needed for local use; a hosted app needs its source redeployed.
