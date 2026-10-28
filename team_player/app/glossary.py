"""Viewer-only explanations. Does not modify the shared batch scoring configuration."""
from team_player.config.analysis import GLOSSARY

VIEW_GLOSSARY = {**GLOSSARY,
 'personal gain': 'the audited individual net benefit-cost value: positional value plus decoy-space credit minus modelled cost. The caption footer states its timeframe. It is not a goals probability or a complete performance rating',
 'team gain': 'the followed player’s team’s shared episode net value: possession value at episode end minus possession value at episode start minus realised cost. A completed-movement report shows that episode’s value. H2H rates divide accumulated value by team-possession seconds and multiply by 600. It is shared with teammates, not causal credit to one player',
 'individual net': 'the audited individual benefit-cost score: positional value plus decoy-space credit minus modelled cost. Live reports normalise it to 10 minutes of team possession alongside team gain. Never subtract this from team gain to infer cost',
 'movement net value': 'the individual benefit-cost net value of a stored historical movement. It is not a per-10-minute rate and cannot be directly compared with a live rate',
 'estimated net contribution': 'the cumulative individual model score over completed tracked attacking movements in this match: positional value plus decoy-space credit minus modelled cost. Positive means the model favours these movements relative to its reference; negative means the reverse. Zero or no detected movement does not mean zero football contribution. This is not a complete performance rating, shot xG, or a proven causal effect. See the equations below',
 'shared defensive value change': 'the separately modelled defensive team value change, shared with teammates; it is not a count of goals prevented',
 'model units': 'units of the experimental benefit–cost score, intended to be comparable to expected-goal value. This is a reporting label for the existing expected-goal-scale calculation, not a rescaling or a new formula. Saved paired synthetic trials test sensitivity to behaviour; calibration and causal attribution are separate questions. A score of +0.04 does not mean a 4% increase in the chance of scoring',
 'own value': 'the individual benefit–cost score, also called estimated net contribution. Check whether a view reports the cumulative total or a possession-normalised rate; these are different quantities',
 'team outcome': 'shared team possession-value change during a tracked episode, minus that episode’s realised cost. It is not the individual benefit and must not be subtracted from own value to construct a new score',
 'net value': 'the legacy label for shared team value change, not the individual estimated net contribution',
 'position value': 'the sum, during qualifying off-ball movement ticks, of actual possession value minus value with the selected player moved to a role-based reference position, weighted by tick duration divided by 15 seconds. Other player positions are frozen. This is a model counterfactual, not an observed alternative match',
 'possession value': 'a fitted state-value estimate: predicted shot xG for minus predicted shot xG against over the next 15 seconds. Predictions use position-derived features; future frames are not inputs during live analysis',
 'decoy-space credit': 'for a qualifying winger decoy, positive estimated teammate-space value relative to restoring following defenders to their earlier positions plus the defensive block’s shift. Following is a geometric criterion, not proof of intent. A later reception can remove this credit',
 'modelled cost': 'strikers: accumulated expected lost box opportunity; fullbacks: accumulated additional expected counter threat while advanced; wingers: currently no separate explicit cost term. A zero cost must not be described as no downside',
 'xG': 'shot expected goals: a model estimate of scoring probability for a shot from its location, angle and pressure/blocking context. A sum across shots is expected goals, not a probability for one event. It is distinct from the net contribution score',
 'decoy run': 'a tracked off-ball run not followed by a qualifying reception. It is not automatically a successful distraction: defender following, space estimates and subsequent use are separate observations',
 'CI': 'a confidence interval, when a procedure actually calculates one. Fixed score thresholds in the legacy balance label are not confidence intervals or significance tests',
}

EQUATIONS = '''
<section id="equations"><h2>The implemented individual score</h2>
<p>Complexity is not evidence of validity. These equations document the existing implementation; they do not establish causal attribution or a validated universal measure of football performance.</p>
<pre style="white-space:pre-wrap;line-height:1.7">
Nᵢ,e = Aᵢ,e + Dᵢ,e − Cᵢ,e
Nᵢ,match = Σ completed qualifying episodes e Nᵢ,e

Aᵢ,e = Σ eligible ticks t [Δt / H] · [V(Sₜ) − V(Sₜ⁽ⁱ←r⁾)]
H = 15 seconds
V(S) = max(0, φ(S)ᵀβ_for) − max(0, φ(S)ᵀβ_against)
</pre>
<p>A is a signed positional-value difference, D is decoy-space credit, C is modelled cost. The live figure is the cumulative N, rounded to three decimals; it is <b>not</b> per ten minutes. Optional comparison rates use 600 × N / observed team-possession seconds.</p>
<p>Eligible ticks are active striker drops, fullback advances or winger runs while the player’s team has possession and the player is not the ball carrier. The reference r is a role median conditioned on the ball’s longitudinal bin (7.5 m); a teammate occupying that slot may trigger a role-swap reference. The counterfactual uses zero velocity for the relocated player. It is not a fitted personality-specific baseline.</p>
<p>The feature vector φ contains an intercept, scaled ball position and squared forward position, xT, location xG, opponent/teammate counts ahead of the ball, nearest-opponent distance, weighted pitch-control threat and attacking control area. The two coefficient vectors predict future shot value. The implementation clips each prediction below at zero.</p>
<h2>Striker cost</h2>
<pre style="white-space:pre-wrap;line-height:1.7">
Mₜ = max over box grid q { open_lane(q) ·
      max[0, P_control(reference,q) − P_control(actual,q)] · xG_location(q) }
Mₜ := 0 if Mₜ &lt; 0.15
C_ST,e = Σ eligible cost ticks t 0.24 · Δt · Mₜ
</pre>
<p>The cost applies while the striker is dropped or returning, outside the box and off the ball, with the ball beyond 65 m in the attacking coordinate system. An open lane requires the ball to arrive at least 0.15 s before defenders under the arrival model. The 0.24 per-second constant is a pass-rate assumption, not a personalised probability. Missed opportunities are estimates, not observed missed shots.</p>
<h2>Fullback cost</h2>
<pre style="white-space:pre-wrap;line-height:1.7">
R(S) = max over own-half wide-channel q { P_opponent_control(S,q) · xT_opponent(q) }
C_FB,e = Σ active off-ball ticks t 0.0185 · Δt · max[0, R(Sₜ) − R(Sₜ⁽ⁱ←r⁾)]
</pre>
<p>This charges additional modelled counter exposure while advanced, using a pooled loss-rate constant. It is not the number of actual turnovers and does not blame the fullback for every team loss. Recovery and settled defensive scores remain separately computed; they are not included in this attacking-movement figure.</p>
<h2>Winger credit and its limitation</h2>
<pre style="white-space:pre-wrap;line-height:1.7">
D_W,e = 1(qualifying decoy and following defenders) · max[0, O_actual − O_restored]
C_W,e = 0 in the current implementation
</pre>
<p>O is pitch-control-weighted location threat with ball-transition weights, excluding the runner. In the restored state, following defenders return to their run-start positions plus the median defensive-block displacement. No pass reception must qualify under the follow-up rule. The extra credit may overlap with positional value; this is a calibration and attribution limitation, not an independently additive causal effect. For strikers and fullbacks, D = 0.</p>
<h2>Shared team figure — retained, reporting paused on the main panel</h2>
<pre style="white-space:pre-wrap;line-height:1.7">T_e = V_end − V_start − C_realised,e</pre>
<p>After an advanced fullback’s team loses possession, the connected counterattack is followed until a goal, regain or out-of-bounds event, with a maximum of 30 seconds. Its shot-value cost is charged to the advance once, not charged again to the same player’s defensive episode. The goal is also reported explicitly. Reaching the cap means the follow-up is incomplete. Legacy history fields labelled 12s retain their original 12-second horizon.</p>
<p>T is shared with teammates and uses a different cost treatment. It is neither A nor C in the individual equation. Do not subtract T from N or claim all of T belongs to one player.</p>
<h2>AI reporting rules</h2>
<p>Preserve the supplied sign, precision, units and cumulative timeframe. Describe one observed movement and an evidenced benefit or cost. Do not infer a hidden personality, intent, statistical significance or causal impact. If no scored movement has completed, say the score is pending rather than zero. A player staying in position may contribute in ways this score does not cover. A rounded zero, zero explicit winger cost, or absence of a counterattack is not evidence of no effect or no risk.</p>
</section>'''
