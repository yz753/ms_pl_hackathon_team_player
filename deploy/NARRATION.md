# Simple AI narration

Live captions and historical-situation explanations use one Microsoft Agent Framework EvidenceNarrator request each. Python selects the card/role, calculates statistics and supplies only the relevant measured facts. The model writes the explanation; it does not retrieve facts through tools or ask another agent to review its writing. Head-to-head is unchanged.

Default reasoning for these two paths is now `low`; an existing TEAM_PLAYER_REASONING environment value overrides it. The existing Foundry deployment is reused. The response schema has only headline and text. Output budget is 1200 tokens including reasoning, with shorter audience-specific visible word limits. Unsupported numbers, unknown players, excess length and specified overclaims reject the response. Validation is a guardrail, not a complete semantic proof; numerical model calibration is unchanged.

No local template replaces either AI explanation. The interface shows a pending/unavailable message while keeping numeric measurements and history tables. Historical AI explanations run automatically at most once every 20 wall-clock seconds when eligible evidence is available (or on request). They retain their original evidence timestamp. Player/audience changes invalidate in-flight history results. Live results still have freshness checks; validated slower explanations appear in Recent moments. Service failures can still prevent AI output.

From the repository with the virtual environment active:

```bash
source deploy/azure/config.env
export TEAM_PLAYER_AI=foundry
export TEAM_PLAYER_REASONING=low
python -m uvicorn team_player.app.server:app --port 8000
```

Restart an existing server and refresh the browser. To benchmark both paths:

```bash
python scripts/benchmark_narration.py --repeats 2 --output narration_benchmark.jsonl
```

That makes 12 paid requests: live/history for striker, winger and fullback, repeated twice. Uses the actual tracking file and SQLite history; records output, token usage, failures and elapsed time. Small samples and repeated snapshots do not establish production latency or general factual accuracy. Cases use first eligible live cards and the first history query, not curated signature-movement examples. No automatic retry of invalid output is hidden in the reported acceptance rate.

Only the latest results file represents the final implementation; earlier diagnostic runs are preserved separately. No model upgrade or Azure capacity purchase is needed to run this version.
