# Analysis methods

How the analyser scores strikers, fullbacks and wingers, how it finds similar past moments, and how results are explained to viewers. Code lives in `team_player/analyser/` and `team_player/agents/`. Every threshold mentioned here is defined in `team_player/config/analysis.py`, so you can change it there.

**Online rule.** Each frame is processed when it arrives, and no decision uses future frames. `tests/test_tactics.py::test_goal_inference_and_prefix_causality` checks this.

**Privacy rule.** The analyser sees positions only. It never imports `simulator/` or `config/styles.py`, so it has no knowledge of any player's personality.

## 1. Measurement building blocks (`analyser/models.py`, `analyser/analysis.py`)

| Question | Method | Why |
|---|---|---|
| How good is a shot or shooting position? | **xG**: logistic model on distance and goal-mouth angle (Pollard & Reep 1997), reduced for defenders inside the shot triangle and for close pressure (the StatsBomb freeze-frame idea). | Distance and angle carry most of the signal in every public xG model. The freeze-frame terms use positions we already have. |
| How valuable is the ball at a given spot? | **Expected Threat (xT)** (Singh 2018). | It turns a pass or carry into ΔxT, so a 40 m line-breaking pass and a 5 m sideways pass are not scored the same. |
| Who owns the space? | **Pitch control** (Spearman et al. 2017): P = logistic(π/√3/σ · (T_opp − T_team)), σ = 0.45 s, where T is each team's fastest arrival time. | A probability rather than a hard yes/no. P > 0.73 equals the older "0.25 s advantage" rule, so legacy metrics are unchanged. |
| How valuable is the space a team owns? | **Space value (OBSO-style)** (Spearman 2018): Σ P(ball goes there) · P(control) · xT, with a Gaussian ball-transition kernel (σ = 14 m). | It makes "space created" a goal-probability number. |
| Which passes break lines? | **Packing**: opponents bypassed by a completed forward pass (Impect). | The standard line-breaking measure. |
| What is a run? | Speed bands of Di Salvo et al. (2007): ≥ 4.0 m/s for 1 s. Peaks ≥ 5.5 m/s are flagged as high-speed (Bradley et al. 2009). | Synthetic wingers rarely exceed 5.5 m/s (the 95th percentile off the ball is 4.2 m/s), so a real-football high-speed threshold alone would miss almost every run. |
| How good is the team's situation right now? | **Possession value (PV)**, VAEP-style (Decroos et al. 2019): expected goals for minus against in the next 15 s. | Puts on-ball and off-ball behaviour in the same unit (expected goals). See section 3. |

**Priors and calibration.** The xG coefficients are a literature-shaped prior that reproduces public benchmarks: about 0.45 at 6 m central, 0.11 at the edge of the box and 0.02 at 30 m. The xT surface approximates the shape of Singh's grid. `python -m team_player.analyser.history fit --input <analysis> --out value_models.json` refits both on the analysed history seasons:

- xG: L2-penalised IRLS, shrunk towards the prior.
- xT: Singh's Markov value iteration, shrunk towards the prior where data are sparse.

On Azure the `tp-build-store` job writes this file next to `history.db`. The live analyser uses it only if `TEAM_PLAYER_VALUE_MODELS` points at it. The default stays on the prior so that history and live numbers come from the same model.

## 2. The three behaviours (`analyser/tactics.py`)

Each behaviour opens an **episode** when it is detected and closes it once its consequences have been seen. Live episodes feed the overlay cards. Closed episodes feed the "Benefit versus cost" panel, the player comparison and the situation library.

### Striker dropping deep (false-9 link)

- **Detect:** at least 14 m short of the opponents' deepest outfield player, outside the final third (x < 75), with the team on the ball for at least 2 s. The 2 s rule stops positions left over from defending counting as a choice. It must last 1 s, or start as soon as he receives the ball while dropped.
- **Benefit:**
  - receptions in the pocket;
  - each forward pass: length, packing, ΔxT, and whether it reached a winger;
  - how far the nearest opposing centre-back stepped out with him;
  - a link type: `long_line_break` (≥ 30 m and ≥ 3 bypassed), `to_winger` or `forward`.
- **Cost:** at the team's next **delivery** (any pass or shot released beyond 75 m), the tracker records:
  - whether he is in the box, and his distance to goal;
  - how many team-mates are in the box, and the best location xG among them;
  - his arrival time back to the penalty spot.

  `cost_flag` = he is absent and at most one team-mate is in the box.

### Fullback advancing (extra winger)

- **Detect:** beyond max(halfway, defensive-midfield line + 6 m) for 1 s. Labelled overlap or underlap relative to the same-side winger.
- **Benefit:** flank overload (attackers minus defenders in his channel ahead of the ball), final-third receptions, and his leave-one-out space-value contribution.
- **Risk (sampled at 2 Hz while he is forward):**
  - opponent-controlled area and the best counter-attack value in his vacated own-half channel;
  - **recovery deficit**: his arrival time back to his defensive slot minus the nearest opponent's arrival time. Positive means an opponent gets there first;
  - team-mates behind the ball.
- **Realised cost:** if the ball is lost while he is forward, a 12 s counter watch records opponent shots, xG against, goals, and whether the attack went **through his vacated flank**.

### Winger off-ball run (decoy versus target)

- **Detect:** a run without the ball at ≥ 4 m/s for 1 s, covering ≥ 7 m. Classified as in-behind, inside, outside, forward or check. If he receives the ball within 1.5 s of the run ending it is a target run; otherwise it is a **decoy**.
- **Defenders drawn:** an opponent who started within 15 m, moved more than 2 m in the run's direction (cos > 0.5) after removing the whole block's shift, and stayed within 3 m of his starting distance to the runner.
- **Space created (counterfactual):** team-mates' space value, and attacking-half area controlled in m² (runner excluded), compared with the same quantities when the drawn defenders are put back where they were (plus the block shift). This follows the "space creation" idea of Fernández & Bornn (2018). It can be negative: dragging a defender *into* a team-mate's area costs that team-mate space.
- **Space used:** the next pass by another team-mate within 4 s gains more than 0.01 xT, or a shot follows.

## 3. Crediting a behaviour: the player's own value and the team outcome

xG only rates shots, and the three behaviours rarely end in the player's own shot. Each episode therefore gets **two** figures in expected-goal units: the player's **own value** (the headline), and the **team outcome** while he did it (shared with team-mates).

**Possession value of a state s**, for the team on the ball:

  PV(s) = max(0, f(s)·β_for) − max(0, f(s)·β_against)

- f(s) has 11 features: constant, ball x and x², |ball y − 34|, xT and location-xG of the ball, opponents goal-side of the ball, team-mates ahead of it, distance to the nearest opponent, team space value, and attacking-half area controlled.
- β are ridge regressions of the xG created (conceded) in the next 15 s. They are stored as `PV_PRIOR` in `config/analysis.py`.

### Own value (individual)

  Own value = position value + decoy space − own cost

**Position value.** At every analysis tick (2 Hz) while the episode is active, the player is compared with **an average player in the role he is actually filling**. Team-mates and opponents are frozen where they are.

  position value = Σ_ticks [PV(him where he is) − PV(him at the average spot)] × Δt / 15 s

The average spot (`ROLE_TEMPLATES`) is the median position of that role, across all clubs, for the current ball position (7.5 m bins). It is *role-swap aware*. If a team-mate already occupies his role's spot, he is compared with an average player in that team-mate's role. For example, when the attacking midfielder rotates into the striker's position, the dropped striker is judged as the midfielder he is replacing. A striker who drops while others fill the box is not penalised for "leaving" it.

**Decoy space (wingers).** The team-mates' space value gained compared with the drawn defenders staying where they were. This applies only to decoy runs that drew someone.

**Own cost.** Expected rather than realised, so one lucky or unlucky ball does not decide the figure.

| Behaviour | Own cost |
|---|---|
| Striker drop | While he is dropped or still returning and the ball is near the final third, at every tick: 0.24 passes per second × Δt × **missed chance**. A missed chance is the best box spot the ball can reach through an open lane (it arrives 0.15 s before any defender), worth **at least 0.15 xG**, that an average striker would have reached first but nobody did. If wingers, midfielders or anyone else cover the box, the missed chance is 0. |
| Fullback advance | 0.0185 ball losses per second × Δt × max(0, opponent counter value in his own-half channel with him there − with an average fullback there). |
| Winger run | none |

### Team outcome (shared)

  Team outcome = V(t_end) − V(t_start) − realised cost

Here V(t) is the team's PV, negative while the opponent has the ball. The realised cost is the counter xG after a loss (fullbacks), or the missed chance at the actual delivery (strikers). It shows what happened while the player did it, and it also moves with what team-mates did.

### Reading the two figures together

The agents and the page always read own value (individual) and team outcome (shared) side by side, per 10 minutes of team possession. A figure within ±0.003 xG (own) or ±0.03 xG (team) counts as about zero; the thresholds are `BALANCE_*_EPS` in `config/analysis.py`.

| Own value | Team outcome | How it is said |
|---|---|---|
| down | up | **Said positively.** Striker: team-first, unselfish. Fullback: a risk that paid off. Winger (no cost term): the team gained though his spot added less than usual. |
| up | up | **Paying off** for him and for the team. |
| up | down | **Cautious.** The situations he was in may explain it rather than his choices; worth watching, not judging. |
| down | down | **Never a verdict.** The data did not pick up a gain, and tracking cannot see everything (defenders kept busy, passes made safer). Phrases such as "no benefit", "useless", "made no difference" or "did not help" are rejected by the agents' text check. |

**In the 30 league matches** (2019-20, matchweeks 1–3), player-matches fall into these readings as follows:

- **Strikers:** mostly team-first. `drops_and_stays` strikers are in that reading in 17 of 18 player-matches, `decoy_runner` strikers in 13 of 18.
- **`often_overlaps` fullbacks:** paying off in 35 of 36.
- **`stays_back` fullbacks:** mostly both close to zero.

Each line is one short sentence with both numbers, e.g. "A09 (this match): team first, own value −0.030 xG, team outcome +0.25 xG per 10 min on the ball. Unselfish: less threat for himself, more for the team." The agents also get a definition fact for the player's role, and their text is rejected if it borrows a part of another role's figure (a winger's "missed chance", a striker's "counter risk").

### Fullbacks without the ball

A second pair of figures, per 10 minutes of the opponents' possession, in three phases that each charge something different, so nothing is counted twice:

| Phase | When | Own figure (defending value) compares him with |
|---|---|---|
| A. With the ball | his team has the ball | (as above) attacking gain − **expected** counter risk |
| B. Recovery | from the moment the opponents win the ball until he is within 5 m of his defensive spot (max 12 s) | an average fullback recovering **from the same spot** at 2.04 m/s, so the gap left by pushing forward (already charged in A) cancels out |
| C. Settled defence | until his team wins the ball back | an average fullback's defending spot for that ball position |

- **Defending value (own)** = opponents' threat with the average fullback there − with him there, × Δt / 15 s; everyone else frozen. Threat = the attacking part of possession value.
- **xG prevented (team, shared)**, per 15 s window: calibrated opponent threat at the window start − xG they shot in it. Losing the ball ends the window with nothing remaining; at a phase change only the elapsed share of the forecast is compared.
- **Fitted from history positions only** (`history fit-defence`): the defending spots by ball position, the recovery speed and the threat calibration (0.742: the forecast over-predicted real shots, 32.7 vs 24.3 xG). The calibration is fitted on the same 30 matches it is tested on.
- **Online:** each phase is settled when it ends, from frames up to now; nothing that happens after a push is credited back to it.
- **Not visible:** tackles, duels and talking. The agents may never call a fullback passive or a poor defender.

**Results (30 league matches).** The defending value hardly separates the types: `often_overlaps` +0.001, `sometimes_overlaps` +0.002, `stays_back` −0.002 xG per 10 min (per-match spread ±0.01). In the controlled test (same match, only the overlap tendency changed, 10 pairs) the high-overlap version is −0.010 (t = −1.6, 6/10), not a clear effect. This is expected: in the simulator every fullback type defends the same way (recover at 92% of top speed, then hold the shape and mark the nearest attacker), so the figure has nothing to pick up yet.

### Does the own value reflect the single player?

Personalities are drawn **per player**: two fullbacks or two wingers of the same club can differ. Two checks were run (`scripts/validate_individual_credit.py`, matches saved in `validation/<role>/`).

**1. Controlled test.** Ten paired 300 s matches per role. Within a pair the clubs and random seed are the same, and only the focal player's tendency changes, low versus high (drop 0.08 / 0.92, overlap 0.07 / 0.90, decoy 0.15 / 0.90). The shift is high minus low, per 10 minutes of team possession.

| | Focal own value | Focal team outcome | Spill-over to other players' own value |
|---|---|---|---|
| Striker | +0.061, t = 1.0 (6/10 seeds) | **+0.165, t = 2.7** (6/10) | team-mates ≤ 4%; opponents' fullbacks/wingers shift slightly (t ≈ −2) |
| Fullback | **+0.028, t = 9.3 (10/10)** | **+0.59, t = 5.2 (9/10)** | ≤ 8% for every non-striker |
| Winger | +0.000, t = 0.3 (7/10) | −0.003, t = 0.0 | — |

**2. League data.** 30 matches (2019-20 matchweeks 1–3), with rank correlation against the hidden tendency, and the largest partial correlation with team-mates' tendencies (leakage) in brackets:

| | Own value | Team outcome | Behaviour count | Time share |
|---|---|---|---|---|
| Striker (60) | −0.37 (0.34) | +0.48 (0.14) | +0.45 (0.47) | **+0.88** (0.10) |
| Fullback (120) | **+0.89** (0.09) | +0.81 (0.07) | +0.91 (0.21) | +0.94 (0.09) |
| Winger (120) | +0.21 (0.10) | +0.06 (0.31) | +0.34 (0.43) | +0.47 (0.09)* |

\* For wingers, time share is the time defenders were seen following him.

**What this says.**

- **Fullbacks.** Every measure separates the personalities, and the own value does so individually, with little leakage.
- **Strikers.** The behaviour itself is detected (time spent deep, ρ 0.88), and it helps the team (team outcome positive in both checks). The own value is weak or negative, because the frozen comparison cannot credit the rotation and link play the drop creates.
- **Wingers.** Defenders do follow the decoy runners (ρ 0.47), but that does not yet show up as value in either the own value or the team outcome.

**Limits.**

- **Frozen frame.** The comparison ignores how others would have reacted.
- **Passes shown separately.** The player's own passes are reported in the verdict (packing, ΔxT), not added to the own value: in tests they were too noisy to tell players apart.

## 4. Similar past situations (`analyser/situations.py`)

**Where it runs.** The index holds every decision window of the history seasons (`history.db`, built offline by `analyser/history.py`). Each window also stores what each striker, fullback and winger did, and the result, so the live narrative can say: *"Similar situation: in 2021-22 (matchweek 14), C v H at 06:42, C09 (striker) dropped 18 m deep…; result: …"*. It also gives the closest moment where the other choice was made.

**Unit: one decision window.** After 1.5 s of control, and then every 8 s, the tracker freezes the context:

- ball position;
- opponent line height, front height, compactness and width;
- opponents goal-side of the ball and team-mates ahead of it;
- how the ball was won (keeper or transition);
- score difference and minute.

It then records the **choices** made in the next 8 s (striker dropped, fullback advanced, winger decoy run) and the **outcomes** over the next 15 s or until the ball is lost (xG, goal, max xT, final third, and xG conceded in the 12 s after a loss). Whole possessions are too coarse: in long synthetic possessions almost every choice happens at some point.

**Matching.** Weighted, z-scored Euclidean nearest neighbours on the context (weights in `SIMILARITY_FEATURES`), with small bonuses for the same team or opponent.

- The context deliberately leaves out the choices themselves, so the search does not match on the decision being studied.
- Normalisation statistics are recomputed with the live recording excluded, so nothing from later in the live match leaks in.

**Output.** The 40 nearest moments, split by each choice. Each side gets:

- n and effective n;
- kernel-weighted xG with a 90% weighted bootstrap interval;
- final-third rate and xG conceded;
- the three closest moments where the *other* choice was made, each with its file, time and outcome.

Comparisons with fewer than 5 neighbours on one side are hidden.

**When it runs.** A live query runs when a new window opens. A retrospective query runs when a possession of the live match that contained a shot finishes, so "the striker dropped and we scored" is shown next to similar moments where he stayed high. The live recording is excluded by content hash. A query takes about 17 ms.

**Always-shown caveat.** This is matching on observed covariates. It is not a replay of what would have happened.

### Why Python for the search and an agent for the explanation

| Layer | Where | Why |
|---|---|---|
| Building the library from hundreds of files | Python batch job (`situations build --workers N`). On Azure: a Container Apps Job or Azure Batch reading Blob storage. | Same pipeline as live, so the numbers are identical. Deterministic and cheap. |
| Similarity query | Python inside App Service. Above about 100k windows, use Azure AI Search, Cosmos DB vector search or pgvector, with team, opponent and choice as filters. | Needs exact, reproducible numbers and intervals at live speed. |
| Explanation | Foundry `SituationConsultant` + `Narrator` agents (`agents/foundry.py`). | The agents choose what matters to the viewer and phrase it. They cannot invent numbers or drop warnings. |

## 5. Explaining results in plain English (`agents/narration.py`)

Every viewer sees the same two things side by side:

- **a plain-English narrative** that keeps its key numbers;
- **the measured numbers** themselves.

Football terms (xG, xT, possession value, own value, packing and so on) get a small **?** that opens a short glossary page (`/glossary`, text in `config/analysis.py`). The narrative also carries the **similar-situation story** from the history seasons.

- **Narrator agent.** It reads a fact sheet built only from measured numbers and texts, then submits a headline and summary. Its tools work like this:
  - `read_facts` returns the fact sheet;
  - `submit_summary` rejects any number not in the facts (0.12 may be written as 12%), causal words ("caused", "proves", "because of him"), over-long text, and, for non-analysts, text with no number at all;
  - the agent gets one retry.
- **Fallback.** If the model is unavailable or its text is rejected, a deterministic local summary with the same numbers is shown and labelled `local_rules`.
- **Where it appears.** The viewer's *What is happening* panel, the head-to-head page (ComparisonConsultant + Narrator) and the similar-situations explanation (SituationConsultant + Narrator).

## 6. Validation and limits

- **Event inference against private simulator events** (9 matches, QA only):
  - goals: 14 of 14 detected, no false positives;
  - shots: 87% recall and 89% precision;
  - passes: inferred counts within 1% of true counts.
- **Pool:** 36 matches, giving 648 possessions, 2,678 decision windows, 269 shots and 35 goals. The prior xG summed to 37.9 against those 35 goals.
- **Tests:** `tests/test_tactics.py` and `tests/test_narration.py` cover the value models, the three behaviours, causality, situation splitting, grounding checks and the Narrator retry. The two web tests need `fastapi` installed.
- **Not tested:** visual browser playback and live Foundry calls.
- **Scope:** all data are synthetic and uncalibrated against real football.

## References

Pollard & Reep (1997) *Measuring the effectiveness of playing strategies at soccer*; Caley (2015) xG notes; Singh (2018) *Introducing Expected Threat*; Spearman, Basye, Dick, Hotovy & Pop (2017) *Physics-based modeling of pass probabilities in soccer*; Spearman (2018) *Beyond Expected Goals*; Fernández & Bornn (2018) *Wide Open Spaces*; Decroos, Bransen, Van Haaren & Davis (2019) *Actions Speak Louder than Goals (VAEP)*; Di Salvo et al. (2007) *Performance characteristics according to playing position in elite soccer*; Bradley et al. (2009) *High-intensity running in FA Premier League matches*; Impect, *Packing*.
