# Team Player · Beyond the ball

Team Player is a synthetic football league with live tactical analysis and AI explanations. Pick any player in a live match; for strikers, wingers and fullbacks the page shows, as play happens, what their movement *without the ball* gave the team and what it cost:

- **Strikers dropping deep** to link play. Is the box still covered when the ball arrives?
- **Fullbacks pushing forward** as an extra winger. What happens on the counter?
- **Wingers making decoy runs.** Do they pull defenders away and open space for team-mates?

Each explanation comes with the numbers behind it. Terms such as xG have a small **?** that links to a glossary. The page also finds **similar moments from past seasons**, for example: "in 2021-22, matchweek 14, C v H at 06:42, C09 dropped deep…; result: …". A **head-to-head** page compares the player with anyone in the league, showing history and this match so far.

The analyser receives **positions only**, one frame at a time, and never sees the simulator's player personalities. Microsoft Foundry agents pick and phrase the stories, and every number they write is checked against the measurements. All data are synthetic.

## The league

- **Clubs:** 20 (A–T), fixed squads (4-2-3-1). Each player keeps the same personality in every season.
- **Format:** Premier League. Every club plays every other club home and away, so a season has 38 matchweeks and 380 matches. Matches last 10 minutes.
- **History seasons** (2019-20 to 2023-24, set in `config/league.py`) are simulated and analysed **offline** into `history.db`.
- **Current season** (2024-25) is simulated in advance but streamed **as if live**, so it is analysed **online only**. It is stored separately from the history seasons.
- **Reproducible:** a fixed `SEASON_SEED` gives identical fixtures and matches.

## Quick start (local)

```sh
python3 -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements-web.txt
python -m uvicorn team_player.app.server:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The repository ships a small example: 6 history matches from 2019-20 already analysed into `data/history.db`, and 3 current-season matches in `data/current/2024-25/`. Local mode uses deterministic rules and is labelled *Local rules · AI not connected*. For the Foundry agents and the full league on Azure, follow [deploy/AZURE.md](deploy/AZURE.md).

## Repository layout

```
team_player/
  config/      EDIT HERE. league.py: clubs, lineup, formations, seasons, seeds.
               styles.py: player personalities (PRIVATE, simulator only).
               analysis.py: every threshold, model prior, similarity weight, glossary term.
  simulator/   engine.py + match.py (match engine), league.py (squads), season.py (fixtures and seasons), qa/ (private checks)
  analyser/    online analysis: observation → pitch control and value models → tactical episodes → own value
               and team outcome → similar situations; history.py = offline analysis and the history.db store
  agents/      Foundry agents (foundry.py) and plain-English narration with number checks (narration.py)
  app/         FastAPI server, web pages (viewer, head-to-head, glossary), data source (local folder or Blob)
  cloud/       Azure batch worker (simulate / analyse / build-store) for Container Apps Jobs
  utils/       shared geometry, number formatting and file helpers
  schema.py    the public data contract between simulator and analyser
scripts/       benchmark_stream.py, smoke_test_azure.py
deploy/        azure/ (numbered deployment scripts + config.env), worker/ (cloud-built image), create_bundle.py, AZURE.md
data/          example data: history/, current/, analysis/, history.db
matches/       older demo recordings used by tests and as a fallback
docs/          methods, demo plan, version history, validation reports
```

**Separation rule.** `analyser/`, `agents/` and `app/` never import `simulator/` or `config/styles.py`. The web bundle is an explicit allowlist that leaves both out.

## Local workflow (small samples; full runs go to Azure)

```sh
# Simulate part of a season (history or current is decided by the season name)
python -m team_player.simulator.season generate --season 2019-20 --out data --private-out private --matchweeks 1-2 --workers 4
python -m team_player.simulator.season generate --season 2024-25 --out data --private-out private --matchweeks 1 --limit 3

# Analyse the history matches offline and build the store (optionally refit xG/xT)
python -m team_player.analyser.history analyse --input data/history --out data/analysis --workers 4
python -m team_player.analyser.history build --input data/analysis --db data/history.db
python -m team_player.analyser.history fit --input data/analysis --out data/value_models.json   # optional

# Tests and a streaming benchmark
python -m unittest discover -s tests -q
python scripts/benchmark_stream.py
```

Useful environment variables:

- `TEAM_PLAYER_DATA`: data folder (default `data`).
- `TEAM_PLAYER_HISTORY_DB`: path to the history store.
- `TEAM_PLAYER_MATCH`: stream one specific file.
- `TEAM_PLAYER_MIN_POSSESSION`: seconds of control before highlights appear.
- `TEAM_PLAYER_VALUE_MODELS`: use refitted xG/xT models.
- `TEAM_PLAYER_AI=foundry`: turn on the agents.

## What the page shows

**Left: the live pitch.** All players are labelled. Click a player to follow him.

**Right: the followed player.**

- *What is happening*: the narrative, written by the Foundry Narrator or, without Azure, by local rules.
- *The numbers*: measured values behind the narrative.
- *Benefit versus cost*: verdicts for his completed episodes.
- *Similar situation in history*: the closest past moments, with the same choice and the other choice.

Goalkeepers, centre-backs and midfielders show a "to be developed…" note.

**Head-to-head ↗** opens the comparison page. The first player must be in this match; the second can be anyone in the league. Each is shown with history figures and, if he is playing, this match so far.

Two figures matter most:

- **Own value** is the player's individual figure: possession value with him where he stands, minus with an average player in that role standing there, plus space a decoy run opened, minus the cost he caused.
- **Team outcome** is what happened to the team meanwhile. It is shared with team-mates.

See [docs/ANALYSIS_METHODS.md](docs/ANALYSIS_METHODS.md) for the methods and equations.

## Honest limits

- The simulator is an uncalibrated 2D model.
- Movement evidence is association, not causation. Similar-situation comparisons are matching on observed context.
- Shot detection from x/y is about 87% recall and 89% precision on simulator ground truth.
- Live Azure inference and browser visuals have not been verified from this repository.

More documentation:

- [deploy/AZURE.md](deploy/AZURE.md): full deployment guide.
- [docs/HACKATHON.md](docs/HACKATHON.md): demo plan.
- [docs/HISTORY.md](docs/HISTORY.md): version history.
- [docs/validation/](docs/validation/): earlier simulator audits.
