# Submission notes and demo plan

## Problem and value

Goals and assists miss most of what strikers, fullbacks and wingers do. Three examples:

- a striker drops deep to start an attack, but may then be missing from the box;
- a fullback joins the attack, but leaves space behind him;
- a winger's run without the ball drags defenders away from a team-mate.

Team Player follows a viewer's favourite player and shows, as play happens, what each movement gave and what it cost. Every point comes with its numbers and its uncertainty, in plain English pitched at the viewer's level.

Scope: strikers, wingers and fullbacks. Data are synthetic. The simulator and analyser are separate, and the deployed app contains no simulator code or player personalities.

## Judging criteria: what to show

| Criterion | What exists | Do not claim yet |
|---|---|---|
| Technological implementation | Seeded continuous simulator; position-only observer; xG, xT, pitch control and possession value; online episode tracker; similar-situation search over pooled matches; prefix-causality tests; allowlisted Azure source bundle. | A calibrated Premier League engine. |
| Agentic design | Five Foundry agents with read-only tools: TacticalScout and EvidenceEditor (overlay selection with veto), ComparisonConsultant, SituationConsultant, and Narrator (plain English, numbers checked against the facts, one retry, labelled local fallback). Deadlines, stale-result rejection, visible tool trace. | Live Azure execution until the smoke test and a deployed demo succeed. |
| Real-world impact | Plain-English explanations with the numbers alongside and **?** glossary links, so any viewer can follow them; machine-readable timed overlay JSON for a broadcast renderer. | Proven audience engagement or scouting accuracy. |
| User experience | Live pitch with any player selectable, narrative plus numbers, Benefit-versus-cost verdicts, similar-situation stories from five history seasons, head-to-head page (history versus this match). | Completed visual browser QA or a recorded demo. |
| Category adherence | Ingest → interpret → explain → render → personalise over a continuous position stream. | Official event data or certainty from positions alone. |

## Suggested 2–3 minute video

1. **The input.** Show the raw file: metadata plus x/y positions and timestamps only. The app receives one frame at a time.
2. **Follow a striker.** When he drops deep, *What is happening* explains the link in plain English, with its numbers and **?** links. The verdict panel then shows whether he was back in the box when the ball arrived.
3. **Switch to a fullback, still live:** overload, recovery deficit and, after a turnover, the counter-attack xG against.
4. **Similar past situations.** After a possession with a shot, compare "striker dropped" with "stayed high" across pooled matches, including n and intervals, and open a closest-alternative clip.
5. **The agents (once Azure is configured).** Expand the tool trace (Scout → Editor → Narrator). Show one case where the editor withholds a story and one where the Narrator's number check forces a retry or a local fallback.
6. **Deployment.** App Service source app, Foundry model, optional Blob storage, private profiles kept out of the bundle. Close by stating the scope and uncertainty.

The interface has no seek-ahead control, and accelerated playback is labelled.

## Before submission

1. Create the Foundry deployment and run `python scripts/smoke_test_azure.py`.
2. Measure end-to-end AI latency and how often fallbacks are used.
3. Check the interface in a real browser.
4. Record the demo.
5. Confirm the official rules.
