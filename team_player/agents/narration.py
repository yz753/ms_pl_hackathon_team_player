"""Plain-English summaries of measured numbers, for three audience levels.

The Foundry Narrator agent (foundry_agents.py) writes the prose. This module provides:
  * fact sheets  -- the only material the model may use (texts + numbers),
  * a glossary   -- one-line explanations of xG, xT, possession value, ...,
  * a grounding check -- every number in the model's text must come from the facts,
    and causal over-claims are rejected,
  * a deterministic local summary -- used when no model is connected or when the
    model's text fails the check, so every viewer always gets readable English.

Audiences: "newcomer" (no jargon; terms explained), "fan" (football language, light
numbers), "analyst" (all key numbers, technical terms allowed).
"""
from __future__ import annotations

import re

from team_player.config.analysis import BALANCE_OWN_EPS, BALANCE_TEAM_EPS
from team_player.config.league import FULLBACK_ROLES, STRIKER_ROLES, WINGER_ROLES
from team_player.config.analysis import AUDIENCES, WORD_LIMIT, GLOSSARY  # editable definitions
# We can never be certain a movement did nothing: such verdicts are always rejected.
CERTAIN_NEGATIVE = re.compile(r"\b(no (?:real )?(?:benefit|value|impact|effect|use)|useless|pointless|wasted|worthless|did nothing|"
                              r"created nothing|achieved nothing|brought nothing|contributed nothing|made no difference|"
                              r"did(?:n't| not) help|of no help)\b", re.I)
FULLBACK_DISMISSIVE = re.compile(r"\b(passive|idle|inactive|lazy|contributed little|did little|poor(?:ly)? defend\w*|"
                                 r"bad defend\w*|weak defend\w*|not (?:a )?good defender)\b", re.I)
CAUSAL = re.compile(r"\b(caused|causes|proves?|proved|guarantee[sd]?|because of (him|her|them)|thanks to (him|her))\b", re.I)
NUMBER = re.compile(r"(?<![A-Za-z])[-+−]?\d+(?:\.\d+)?")


def numbers_in(text):
    return [float(x.replace("−", "-")) for x in NUMBER.findall(text or "")]


def allowed_numbers(facts):
    allowed = set()
    for f in facts:
        for x in numbers_in(f["text"]):
            for v in (x, abs(x)):
                allowed.update({round(v, 3), round(v, 2), round(v, 1), round(v)})
                if abs(v) <= 1:  # 0.12 may be written as 12%
                    allowed.update({round(100 * v), round(100 * v, 1)})
    return allowed


def check_grounding(text, facts, audience):
    """Return a list of problems (empty = accepted)."""
    problems = []
    allowed = allowed_numbers(facts)
    for x in numbers_in(text):
        if not any(abs(x - a) < 1e-9 for a in allowed) and round(abs(x), 3) not in allowed:
            problems.append(f"number {x:g} is not in the evidence")
    if CAUSAL.search(text or ""):
        problems.append("causal claim; the evidence is associative")
    if CERTAIN_NEGATIVE.search(text or ""):
        problems.append("too certain: the tracking data cannot show that a movement brought no benefit")
    roles = {m.group(1) for f in facts for m in [re.search(r"balance_definition_(\w+)$", f.get("id", ""))] if m}
    if "fullback" in roles and FULLBACK_DISMISSIVE.search(text or ""):
        problems.append("too certain: tackles and duels are not visible in positions, so a figure cannot call a fullback passive or a poor defender")
    if len(roles) == 1 and FOREIGN_TERMS[next(iter(roles))].search(text or ""):
        problems.append(f"wrong figure: that part is not in a {next(iter(roles))}'s figures")
    if len((text or "").split()) > WORD_LIMIT[audience] + 15:
        problems.append("too long")
    if audience != "analyst" and not numbers_in(text):
        problems.append("keep at least one number")
    return problems


def terms_used(facts):
    blob = " ".join(f["text"] for f in facts)
    return {k: v for k, v in GLOSSARY.items() if re.search(rf"(?<![A-Za-z]){re.escape(k)}(?![A-Za-z])", blob)}


# ----------------------------------------------------------- fact sheets ---

# What each figure counts, by role (read by the agents; short so it fits a live caption).
DEFINITION = {
    "striker": ("{p} is a striker. His figures count only while he drops deep or runs back up front. "
                "Own value: the threat of where he stood minus that of an average striker there, minus the expected cost of open "
                "box chances (at least 0.15 xG) that nobody was there to take. Team outcome: the change in his team's possession "
                "value over those moments, minus a clear box chance actually missed when the ball came in; shared with team-mates. "
                "Both per 10 minutes of his team's possession."),
    "winger": ("{p} is a winger. His figures count only during his runs. Own value: the threat of where he stood minus that of "
               "an average winger there, plus the space a decoy run (not passed to) opened for team-mates; there is no cost term. "
               "Team outcome: the change in his team's possession value during his runs; shared with team-mates. "
               "Both per 10 minutes of his team's possession."),
    "fullback": ("{p} is a fullback. His figures count only while he is pushed forward with his team on the ball. Own value: the "
                 "threat of where he stood minus that of an average fullback there, minus the expected counter-attack risk behind "
                 "him. Team outcome: the change in his team's possession value, minus the xG of any counter after the ball was "
                 "lost while he was forward; shared with team-mates. Both per 10 minutes of his team's possession. Defending "
                 "without the ball is a separate figure."),
    "fullback_defence": ("Without the ball ({p}, fullback): counted from the moment the opponents win the ball. While he "
                         "recovers he is compared with an average fullback recovering from the same spot, so the gap left by "
                         "pushing forward is not charged twice; once settled, with an average fullback's defending spot. "
                         "Defending value: the opponents' threat with that average fullback there minus with him there. "
                         "xG prevented: the opponents' expected threat minus the xG they actually shot; shared with team-mates. "
                         "Both per 10 minutes of the opponents' possession. Tackles, duels and talking are not visible in "
                         "positions, so never call him passive or a poor defender."),
}
# Words that describe another role's figure; the agent may not use them for this role.
FOREIGN_TERMS = {
    "striker": re.compile(r"\b(counter(?:-attack)? risk|decoy space|space (?:his|the) decoy)\b", re.I),
    "winger": re.compile(r"\b(missed chance|counter(?:-attack)? risk|cost (?:him|of his)|sacrific\w*)\b", re.I),
    "fullback": re.compile(r"\b(missed chance|decoy space|space (?:his|the) decoy)\b", re.I),
}
SCOPE_LABEL = {"in this match": "this match", "across his history matches": "history"}


def role_family(role):
    return ("striker" if role in STRIKER_ROLES else "winger" if role in WINGER_ROLES
            else "fullback" if role in FULLBACK_ROLES else None)


def balance_reading(player, own, team, scope="in this match", role=None):
    """Read the individual figure (own value) and the shared one (team outcome) together, in one short line.

    own, team: per 10 min of team possession, in expected goals. Returns category + plain text.
      sacrifice           own down, team up   -> positive (strikers: unselfish; fullbacks: a risk that paid off)
      both_gain           both up             -> paying off for him and the team
      own_gain_team_down  own up, team down   -> cautious, worth watching
      both_down           both down           -> never a verdict: tracking cannot see everything
      unclear             at least one figure about zero
    role: adds the role definition fact; fullbacks also get "defending without the ball isn't counted".
    """
    if own is None or team is None:
        return None
    own, team = round(float(own), 3) + 0.0, round(float(team), 2) + 0.0   # no "-0.00" in text
    fam = role_family(role)
    o = "up" if own > BALANCE_OWN_EPS else "down" if own < -BALANCE_OWN_EPS else "flat"
    t = "up" if team > BALANCE_TEAM_EPS else "down" if team < -BALANCE_TEAM_EPS else "flat"
    lead = f"{player} ({SCOPE_LABEL.get(scope, scope)})"
    nums = f"own value {own:+.3f} xG, team outcome {team:+.2f} xG per 10 min on the ball"
    if o == "down" and t == "up":
        cat = "sacrifice"
        text = {"winger": f"{lead}: the team gained though his spot added less than usual: {nums}.",
                "fullback": f"{lead}: a risk that paid off for the team: {nums}."}.get(
               fam, f"{lead}: team first, {nums}. Unselfish: less threat for himself, more for the team.")
    elif o == "up" and t == "up":
        cat, text = "both_gain", f"{lead}: paying off for him and the team: {nums}."
    elif o == "up" and t == "down":
        cat, text = "own_gain_team_down", f"{lead}: {nums}. Worth watching, not judging yet."
    elif o == "down" and t == "down":
        cat, text = "both_down", f"{lead}: {nums}. No gain picked up yet; tracking can't see everything."
    else:
        cat, text = "unclear", f"{lead}: {nums}. Too small to read yet."
    facts = [{"id": "balance", "text": text}, {"id": "balance_numbers", "text": nums}]
    if fam:
        facts.append({"id": f"balance_definition_{fam}", "text": DEFINITION[fam].format(p=player)})
    return {"category": cat, "text": text, "own": round(own, 4), "team": round(team, 4), "scope": scope, "role": fam, "facts": facts}


def defence_reading(player, own, team, scope="in this match"):
    """Fullback without the ball: own defending value and the shared xG prevented, in one short line."""
    if own is None or team is None:
        return None
    own, team = round(float(own), 3) + 0.0, round(float(team), 2) + 0.0
    o = "up" if own > BALANCE_OWN_EPS else "down" if own < -BALANCE_OWN_EPS else "flat"
    t = "up" if team > BALANCE_TEAM_EPS else "down" if team < -BALANCE_TEAM_EPS else "flat"
    lead = f"{player} ({SCOPE_LABEL.get(scope, scope)}, without the ball)"
    nums = f"defending value {own:+.3f} xG, xG prevented {team:+.2f} per 10 min"
    if o == "up" and t == "up":
        cat, text = "both_gain", f"{lead}: defending well, for him and the team: {nums}."
    elif o == "up" and t == "down":
        cat, text = "own_gain_team_down", f"{lead}: his position helped, though the team let in more than expected: {nums}."
    elif o == "down" and t == "up":
        cat, text = "team_gain_own_down", f"{lead}: the team held well; worth watching his position: {nums}."
    elif o == "down" and t == "down":
        cat, text = "both_down", f"{lead}: {nums}. No gain picked up yet; tracking can't see tackles or duels."
    else:
        cat, text = "unclear", f"{lead}: {nums}. Too small to read yet."
    facts = [{"id": "defence", "text": text}, {"id": "defence_numbers", "text": nums},
             {"id": "balance_definition_fullback", "text": DEFINITION["fullback_defence"].format(p=player)}]
    return {"category": cat, "text": text, "own": round(own, 4), "team": round(team, 4), "scope": scope, "facts": facts}


def defence_from_row(row, scope="in this match"):
    if not row or role_family(row.get("role")) != "fullback" or not row.get("opp_possession_s"):
        return None
    per = 600 / row["opp_possession_s"]
    return defence_reading(row["id"], (row.get("def_rec_own", 0) + row.get("def_set_own", 0)) * per,
                           (row.get("def_rec_team", 0) + row.get("def_set_team", 0)) * per, scope)


def balance_from_row(row, scope="in this match"):
    if not row or not row.get("possession_s"):
        return None
    per = 600 / row["possession_s"]
    return balance_reading(row["id"], row.get("indiv_net", 0) * per, row.get("value_net", 0) * per, scope, row.get("role"))


def overlay_facts(snapshot, cards):
    facts = []
    for key in ("balance", "defence"):
        if snapshot.get(key):
            facts += snapshot[key]["facts"]
    if snapshot.get("similar_story"):
        facts.append({"id": "similar_story", "text": snapshot["similar_story"]["text"]})
    for c in cards:
        facts.append({"id": c["id"], "text": f"{c['title']}. {c['fan']} {c['analyst']}"})
        facts.append({"id": c["id"] + ":caveat", "text": c["caveat"]})
    for v in snapshot.get("verdicts", [])[:1]:
        facts.append({"id": "verdict", "text": f"Benefit: {v['benefit']} Cost: {v['cost']} {v.get('outcome', '')}"})
    return facts


def comparison_facts(c):
    facts = [{"id": a["id"], "text": a["text"]} for a in c.get("advice", [])]
    for side in ("left", "right"):
        for key in ("balance", "current_balance", "defence", "current_defence"):
            b = c[side].get(key)
            if b:
                facts += [{"id": f"{side}_{key}_{f['id']}", "text": f["text"]} for f in b["facts"]]
    for side in ("left", "right"):
        p = c[side]
        rates = "; ".join(f"{m['label']} {p['rates'][m['key']]:.2f} per 10 min" for m in c.get("event_metrics", [])
                          if p.get("rates", {}).get(m["key"]) is not None)
        if rates:
            facts.append({"id": side, "text": f"{p['id']} ({p['role']}), {p['possession_s'] / 60:.1f} minutes of team possession observed: {rates}."})
    facts += [{"id": f"warning{i}", "text": w} for i, w in enumerate(c.get("warnings", []))]
    return facts


def similar_facts(r):
    facts = [{"id": a["id"], "text": a["text"]} for a in r.get("advice", [])]
    facts.append({"id": "pool", "text": f"{r.get('k')} closest moments out of {r.get('pool_size')} pooled moments."})
    facts += [{"id": f"warning{i}", "text": w} for i, w in enumerate(r.get("warnings", []))]
    return facts


# -------------------------------------------------------- local summaries ---

def _first_sentence(text):
    return re.split(r"(?<=[.!?])\s", text.strip(), 1)[0]


def local_summary(kind, payload, audience="fan", cards=()):
    """Deterministic plain-English summary with the key numbers kept."""
    if kind == "overlay":
        if not cards:
            return None
        c = cards[0]
        body = c["analyst"] if audience == "analyst" else c["fan"]
        verdict = (payload.get("verdicts") or [None])[0]
        text = f"{body} {c['caveat']}"
        if verdict and audience != "newcomer":
            text += f" Last time: {verdict['benefit']} {verdict['cost']}"
        for key in ("balance", "defence"):
            if payload.get(key) and payload[key]["category"] != "unclear":
                text += " " + payload[key]["text"]
        headline = c["title"]
    elif kind == "comparison":
        a, b = payload["left"], payload["right"]
        lead = next((m for m in payload.get("event_metrics", []) if m["key"] == "indiv_net"), None)
        if lead and a.get("rates", {}).get("indiv_net") is not None and b.get("rates", {}).get("indiv_net") is not None:
            va, vb = a["rates"]["indiv_net"], b["rates"]["indiv_net"]
            ta, tb = a["rates"].get("value_net") or 0, b["rates"].get("value_net") or 0
            text = (f"Per 10 minutes on the ball, {a['id']}'s own movement was worth {va:+.3f} expected goals compared with an average "
                    f"player in his role; {b['id']}'s was worth {vb:+.3f}.")
            if audience != "newcomer":
                text += f" Team outcome while they did it (shared with team-mates): {ta:+.2f} versus {tb:+.2f}."
        else:
            text = "There is not enough evidence yet to compare their signature movements."
        for p in (a, b):   # read both figures together, this match first, else history
            for bal in (p.get("current_balance") or p.get("balance"), p.get("current_defence") or p.get("defence")):
                if bal:
                    text += " " + bal["text"]
        if audience == "analyst":
            text += " " + " ".join(x["text"] for x in payload.get("advice", [])[1:3])
        text += " " + _first_sentence(payload["warnings"][0]) if payload.get("warnings") else ""
        headline = f"{a['id']} versus {b['id']}"
    elif kind == "similar":
        if not payload.get("available"):
            return {"headline": "No similar moments yet", "text": payload["warnings"][0], "method": "local_rules", "glossary": {}}
        rows = [a["text"] for a in payload.get("advice", []) if a["id"] not in ("associative", "current")]
        if audience == "analyst":
            text = (f"{payload['k']} nearest of {payload['pool_size']} pooled moments. " + (" ".join(rows) if rows else
                    "No choice has at least 5 neighbours on both sides.") + " Associative, not counterfactual.")
        else:
            plain = {"striker_dropped": ("the striker dropped deep", "he stayed up front"),
                     "fb_advanced": ("a fullback pushed forward", "the fullbacks stayed back"),
                     "winger_decoy": ("a winger made a decoy run", "no winger did")}
            valid = [(k, v) for k, v in payload["by_choice"].items() if v["xg_difference"] is not None]
            text = f"We found {payload['k']} past moments that looked like this one. "
            if valid:
                key, v = max(valid, key=lambda kv: abs(kv[1]["xg_difference"]))
                yes, no = plain[key]
                text += (f"When {yes}, the team went on to create {v['yes']['xg']:.3f} expected goals in the next 15 seconds "
                         f"({v['yes']['n']} cases) and reached the final third {100 * v['yes']['final_third_rate']:.0f}% of the time; "
                         f"when {no}, {v['no']['xg']:.3f} ({v['no']['n']} cases) and {100 * v['no']['final_third_rate']:.0f}%. ")
            else:
                text += "Too few of them involved a different choice to compare. "
            cur = payload.get("current")
            if cur:
                text += f"This time the move produced {cur['outcome']['xg']:.3f} expected goals" + (" and a goal. " if cur["outcome"]["goal"] else ". ")
            text += "That shows what tended to happen, not what would have happened."
        headline = "What happened in similar moments"
    else:
        raise ValueError("Unknown summary kind")
    facts = {"overlay": lambda: overlay_facts(payload, cards), "comparison": lambda: comparison_facts(payload),
             "similar": lambda: similar_facts(payload)}[kind]()
    glossary = terms_used(facts) if audience == "newcomer" else {}
    if glossary:
        text += " (" + "; ".join(f"{k} = {v}" for k, v in list(glossary.items())[:2]) + ".)"
    return {"headline": headline, "text": text.strip(), "method": "local_rules", "glossary": glossary}


# ------------------------------------------------- similar-situation story ---
CHOICE_FOR_ROLE = {"ST": "striker_dropped", "LST": "striker_dropped", "RST": "striker_dropped",
                   "LB": "fb_advanced", "RB": "fb_advanced", "LW": "winger_decoy", "RW": "winger_decoy",
                   "LM": "winger_decoy", "RM": "winger_decoy"}
ROLE_NAME = {"ST": "striker", "LST": "striker", "RST": "striker", "LB": "left-back", "RB": "right-back",
             "LW": "left winger", "RW": "right winger", "LM": "left midfielder", "RM": "right midfielder"}
DID = {"striker_dropped": ("dropped deep", "stayed up front"), "fb_advanced": ("pushed forward", "stayed back"),
       "winger_decoy": ("made a decoy run", "made no decoy run")}


def _moment(n, role):
    """'in 2021-22 (matchweek 14), C v H at 06:42, C09 (striker) dropped ...; result: ...'"""
    acts = n.get("actions") or []
    family = [a for a in acts if a["role"] == role] or [a for a in acts if CHOICE_FOR_ROLE.get(a["role"]) == CHOICE_FOR_ROLE.get(role)]
    who = f"{family[0]['player']} ({ROLE_NAME.get(family[0]['role'], family[0]['role'])}) {family[0]['text']}" if family else "the team kept the ball"
    when = f"in {n.get('season')} (matchweek {n.get('matchweek')}), {n.get('home')} v {n.get('away')} at {n.get('clock')}" if n.get("season") else "in a past match"
    return f"{when}, {who}; result: {n.get('outcome_text') or 'no shot within 15 s'}."


def similar_story(result, player, role, team):
    """Narrative sentence about the most similar history moment for this player's role.

    Returns None when there is nothing to say (no history, unsupported role, or the
    player's team is not the one in possession)."""
    key = CHOICE_FOR_ROLE.get(role)
    if not key or not result or not result.get("available") or result.get("team") != team:
        return None
    v = result["by_choice"][key];now = bool((result.get("current_choices") or {}).get(key))
    same = v["closest_yes" if now else "closest_no"];other = v["closest_no" if now else "closest_yes"]
    did, didnt = DID[key];rn = ROLE_NAME.get(role, role)
    parts = []
    if same:
        parts.append(f"Similar situation: {_moment(same[0], role)}")
    if other:
        parts.append(f"When the {rn} instead {didnt if now else did}: {_moment(other[0], role)}")
    if v["xg_difference"] is not None:
        y, n = v["yes"], v["no"]
        parts.append(f"Across the {result['k']} closest history moments, when the {rn} {did} the team created {y['xg']:.3f} xG in the next 15 s "
                     f"({y['n']} cases) against {n['xg']:.3f} when he {didnt} ({n['n']} cases). This shows what tended to happen, not what would have happened.")
    if not parts:
        return None
    return {"text": " ".join(parts), "player": player, "query_t": result.get("query_t"), "mode": result.get("mode"),
            "facts": [{"id": "similar_story", "text": " ".join(parts)}]}
