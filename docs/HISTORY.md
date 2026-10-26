# Version history

Earlier update notes merged into one file, newest first. Test counts and diagnostics describe the code *at that time*, not the current version. Commands use the current module paths.

## 0.6: seasons, history store, live viewer, Azure batch

- **Seasons.** A Premier League calendar of 380 matches (`simulator/season.py`), seeded and reproducible, with season, matchweek, home, away and kickoff in the metadata. History seasons are 2019-20 to 2023-24; the current season is 2024-25. Matches are stored as gzip JSONL.
- **History store.** Offline analysis of the history seasons goes into `history.db` (SQLite). It holds decision windows with what each striker, fullback and winger did, possession results and per-player totals. The current season is analysed online only.
- **Web app.** A replay-style live viewer (pitch on the left, followed player on the right), narrative plus numbers with **?** glossary links, a similar-situation story, a head-to-head page (history versus this match) and a "to be developed" note for other roles.
- **Simulator.**
  - Decoy runs stay onside and sprint across the line, and defenders react to runs attacking the space at or behind it.
  - When the striker drops, the attacking midfielder rotates into his position.
- **Analyser.**
  - The striker cost now counts only a clear missed chance (≥ 0.15 xG, reachable through an open lane, nobody there), charged in expectation.
  - The own value is role-swap aware.
  - Fullback cost is the expected counter risk.
  - Wingers get decoy-space credit.
- **Azure.** `cloud/worker.py` uses claim-based parallel tasks; Container Apps Jobs run from a cloud-built image; numbered deployment scripts are in `deploy/azure/`.
- **Removed.** The legacy short-clip tools and their tests.

## 0.5: package restructure, value-based credit, plain-English agents

- **Folders.** Code is split by job into `config/`, `simulator/`, `analyser/`, `agents/`, `app/` and `utils/`, plus `scripts/` and `docs/`. Lineups, personalities and every analysis threshold are now in `config/`. The refactor was checked to be behaviour-identical: simulator output, pipeline packets, situation queries and comparisons hash the same before and after.
- **Analyser upgrade.**
  - xG, xT, probabilistic pitch control and space value;
  - online episodes for striker drops, fullback advances and winger runs, each with benefit and cost;
  - goal inference from tracking;
  - a similar-situation search over pooled matches (`situations.json`);
  - net-value credit based on possession value (see [ANALYSIS_METHODS.md](ANALYSIS_METHODS.md)).
- **Agents.** SituationConsultant and Narrator were added. Explanations come in newcomer, fan and analyst versions, and the numbers are checked against the facts.
- **Comparison history.** Now version 2, adding the episode-based fields.
- **Removed.** The old `backup_before_*` folders.

## 0.4: persistent league and player comparison

- **League.** Each file draws two distinct clubs uniformly from A–T; each pairing has probability 1/190. Squads use 4-2-3-1 with stable IDs (00 GK, 01 LB, 04 RB, 07 LW, 09 RW, 10 ST). Club attributes are fixed by `--league-seed` (default 2026).
- **Comparison panel.** Combines completed recordings with the live prefix. The active recording is excluded by content hash, measures are shares of team possession, and role-incompatibility and limited-evidence warnings are always shown. With Foundry enabled, a ComparisonConsultant agent picks grounded advice.
- **Simulator behaviour.**
  - Deep strikers seek a midfield pocket with visible incoming and outgoing passing lanes.
  - Long ground passes up to 60 m are possible.
  - Wingers choose carrying corridors and hold a reached run position briefly.
  - Defenders can commit to a visible runner for 5–10 s.
  - Propensities are time-based rates, so a low-propensity player does not fire repeatedly.
- **Engine changes.**
  - No forced shots: shooting uses range, angle, pressure and patience, with execution error.
  - Goals restart with the conceding keeper (a demo convention, not the centre-kick rule).
  - Keepers follow the shot line and can parry.
  - Mixed, patient and direct tempos; coordinated defence (compact block, lane screens, pressure, cover) and reachable interception.
  - Long passes can use a private lofted trajectory while public data stay x/y only.
- **Default matches.** Four 600 s recordings: B–P, P–M, E–F and O–E. Older default recordings are in `previous_*_matches/`, and the `examples_*` folders are historical previews.

### Validation at 0.4

- **Streaming.** A full 600 s stream processed in 6.0 s unpaced (about 100× real time). Median 0.12 ms per frame, p95 4.6 ms.
- **Position audit.** Maximum player speed 8.93 m/s, maximum acceleration 4.20 m/s², minimum separation 0.55 m.
- **Bundle.** The deployment ZIP imports in isolation without simulator or profile modules.
- **Not verified:** live Foundry inference, browser visuals and an Azure deployment.

## Earlier reports

The behaviour audit (ten trials per archetype) and the complete attacking-sequence audit are in [validation/](validation/). Their tracking files and HTML examples are in `behaviour_validation/` and `outcome_validation/`.
