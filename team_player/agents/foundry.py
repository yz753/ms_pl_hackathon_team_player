"""Foundry AI explanations grounded in public tracking evidence.

Live and historical-situation narration use one tool-free model call on evidence
selected by Python, including head-to-head. The legacy full diagnostic workflow retains
its existing tools. No private simulator profiles or future frames are exposed.
"""
import asyncio
import copy
import json
import os
import re
import time
from .reporting import report_units
from team_player.analyser.presentation import validate_selection, render_overlay
from team_player.agents.narration import (AUDIENCES, WORD_LIMIT, check_grounding, local_summary, terms_used,
                        overlay_facts, comparison_facts, similar_facts, live_context_facts)


class FoundryDirector:
    def __init__(self):
        from agent_framework.foundry import FoundryChatClient
        from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
        endpoint=os.environ.get('FOUNDRY_PROJECT_ENDPOINT','')
        model=os.environ.get('FOUNDRY_MODEL','')
        if not endpoint.startswith('https://') or not model:
            raise ValueError('Set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL')
        self.credential=(ManagedIdentityCredential(client_id=os.environ.get('AZURE_CLIENT_ID'))
            if os.environ.get('TEAM_PLAYER_IDENTITY')=='managed' else AzureCliCredential())
        self.client=FoundryChatClient(project_endpoint=endpoint,model=model,credential=self.credential)

    async def close(self):
        await self.credential.close()

    async def narrate(self,kind,payload,audience='fan',cards=()):
        """Narrator agent: turns measured numbers into plain English for the viewer's level.

        It may only use the fact sheet it reads; `submit_summary` rejects any number not
        in the facts, causal over-claims and over-long text, and the agent gets one
        retry. On failure the caller keeps the deterministic local summary."""
        from agent_framework import Agent,tool
        if audience not in AUDIENCES:raise ValueError('Unknown audience')
        facts={'overlay':lambda:overlay_facts(payload,cards),'comparison':lambda:comparison_facts(payload),
               'similar':lambda:similar_facts(payload)}[kind]()
        facts=report_units(facts)
        glossary=terms_used(facts)
        grounding=facts+[{'id':'glossary:'+k,'text':v} for k,v in glossary.items()]
        read=False;result={}
        style={'newcomer':'Assume no football analytics knowledge. No jargon without a plain explanation in the same sentence '
                          '(only a single shot xG is a scoring probability; never convert net contribution or summed xG to a percentage). Short sentences.',
               'fan':'Speak like a good TV co-commentator. Football language, one or two key numbers, no formulas.',
               'analyst':'Precise and compact. Keep the key numbers, sample sizes and intervals; technical terms are fine.'}[audience]
        @tool(approval_mode='never_require',max_invocations=1)
        def read_facts() -> str:
            """Read the only facts, numbers and glossary you may use, plus the audience style."""
            nonlocal read
            read=True
            return json.dumps({'audience':audience,'style':style,'word_limit':50,'facts':facts,'glossary':glossary})
        @tool(approval_mode='never_require',max_invocations=2)
        def submit_summary(headline: str, text: str) -> str:
            """Submit a headline (max 8 words) and summary. Every number must appear in the facts."""
            if not read:raise ValueError('Read the facts first')
            problems=check_grounding(headline+' '+text,grounding,audience)
            if len(headline.split())>10:problems.append('headline too long')
            if problems:return 'Rejected: '+'; '.join(problems)+'. Fix and resubmit once.'
            result.update(headline=headline.strip(),text=text.strip());return 'Accepted. Stop.'
        narrator=Agent(client=self.client,name='Narrator',
            instructions='You explain football tracking analysis to a viewer. Call read_facts, then submit_summary. '
            'Translate the numbers into everyday meaning but KEEP the important numbers exactly as given (you may write 0.12 as 12%). '
            'Always say both sides when there is a benefit and a cost. Keep one caveat that the figures show association, not proof. '
            'Read own value (individual) and team outcome (shared) TOGETHER using the balance fact: if his own figure is down but the team '
            'gained, say it positively (striker: unselfish, team-first; fullback: a risk that paid off; winger: the team gained though his spot added less); if both rose, say it paid off for him and the team; if his own rose and the '
            'team fell, stay cautious; if both fell, never say the movement brought no benefit - say the data did not pick up a gain and '
            'cannot see everything. What the two figures count depends on the role: follow the balance_definition fact exactly and never '
            'describe a part of another role\'s figure (a winger has no cost term; only strikers have the missed-chance cost; only '
            'fullbacks have the counter-attack risk). This is a live caption: say the balance in one short sentence with both numbers, '
            'no formulas. For a fullback there is a second, separate pair without the ball (defending value and xG prevented): both up = defending well; '
            'one up and one down = cautious, worth watching; both down = not a verdict; '
            'and since tackles and duels are not visible, never call him passive, idle or a poor defender. '
            'Never invent numbers, players or events; never say a movement caused a goal. At most three tool calls.',
            tools=[read_facts,submit_summary])
        await narrator.run(json.dumps({'kind':kind,'audience':audience}))
        if not result:raise RuntimeError('Narrator did not submit an accepted summary')
        return {**result,'method':'microsoft_foundry_agent','glossary':glossary if audience=='newcomer' else {}}

    async def _narrate_or_local(self,kind,payload,audience,cards=()):
        try:return await self.narrate(kind,payload,audience,cards)
        except Exception:
            fallback=local_summary(kind,payload,audience,cards)
            if fallback:fallback['method']='local_rules (AI narration rejected or unavailable)'
            return fallback

    async def _consult(self,name,instructions,prompt,view,advice,tool_name,tool_doc):
        """Shared consultant pattern: read one evidence view, then pick up to three existing advice IDs."""
        from agent_framework import Agent,tool
        state={'read':False,'selected':None}
        def inspect() -> str:
            state['read']=True
            return json.dumps(view)
        inspect.__name__,inspect.__doc__=tool_name,tool_doc
        @tool(approval_mode='never_require',max_invocations=1)
        def recommend(advice_ids: list[str]) -> str:
            """Select up to three existing advice IDs. Warnings are always retained."""
            allowed={a['id'] for a in advice}
            if not state['read'] or len(advice_ids)>3 or len(set(advice_ids))!=len(advice_ids) or any(i not in allowed for i in advice_ids):
                raise ValueError('Read evidence and select existing advice IDs')
            state['selected']=[a for a in advice if a['id'] in advice_ids]
            return 'Recommendation recorded. Stop.'
        agent=Agent(client=self.client,name=name,instructions=instructions,
                    tools=[tool(approval_mode='never_require',max_invocations=1)(inspect),recommend])
        await agent.run(prompt)
        if state['selected'] is None:raise RuntimeError('Consultant did not submit grounded advice')
        return state['selected']

    async def compare(self, snapshot, audience='fan'):
        """One AI call explains a compact, public comparison; no template fallback."""
        from .plain import explain
        from .impact import describe_impact
        from .reporting import valid_comparison, INVALID_COMPARISON
        if not valid_comparison(snapshot["left"]["id"], snapshot["right"]["id"]):
            return {"t":snapshot["t"], "method":"selection_required", "summary":{"text":INVALID_COMPARISON}}
        snapshot = report_units(copy.deepcopy(snapshot))
        both_live=all(snapshot[side].get('current') is not None for side in ('left','right'))
        facts=[{'id':'scope','text':'Compare observed behaviour, not assumed player personalities. Duration in a deeper area is neither depth in metres nor number of drops. Central attacking-third presence is not exact penalty-box occupancy. Absence at a delivery does not mean the player made that delivery. Contribution values cover scored movements only, not all normal positional play. Zero or absent movement scores do not imply a striker staying forward contributes nothing.'}]
        for side in ('left','right'):
            p=snapshot[side]
            facts.append({'id':side,'text':f"{p['id']} plays {p['role']}."})
            scopes=[('Historically',p)]
            if both_live:scopes.append(('In this match',p['current']))
            for scope,row in scopes:
                pct=row.get('percentages',{})
                labels={'deep_s':'share of team possession time in the deeper striker area','box_s':'share of team possession time centrally in the attacking third','forward_offer_s':'share of team possession time available for a forward pass','advanced_s':'share of team possession time advanced from fullback','followed_s':'share of team possession time with off-ball following evidence'}
                values=', '.join(f"{label} {pct[key]}%" for key,label in labels.items() if pct.get(key) is not None)
                facts.append({'id':side+scope+'_position','text':f"{scope}, {p['id']}: {values}. These are time shares, not movement frequency or distance. Interpret small differences cautiously, without assigning a box-striker or deep-striker identity."})
                rates=row.get('rates',{})
                values=', '.join(f"estimated {label} ({rates[key]:+.3f})" for key,label in (('indiv_net','personal gain'),('value_net','shared team gain')) if rates.get(key) is not None)
                episodes=row.get('episodes',p.get('value_n',0))
                facts.append({'id':side+scope+'_scores','text':f"{scope}, {p['id']}: {values}, per 10 minutes of team possession, for qualifying scored movements only. Scored movements observed: {episodes}. Shared gain is not solely attributable to this player. Do not treat missing qualifying movements as zero overall contribution."})
        facts += [{'id':f'warning{i}','text':w} for i,w in enumerate(snapshot['warnings'])]
        if both_live:
            facts.append({'id':'priority','text':'Discuss BOTH scopes: Historically describe the measured pattern, then In this match explain whether current positioning and scored contribution continue or differ from that pattern. Translate the contrast into football meaning without inventing passes, chances or causality. Early live rates are provisional. Never present historical differences as current-match facts.'})
        else:
            facts.append({'id':'priority','text':'Lead with Historically. One or both players are absent from this live match, so compare historical evidence only. Different opponents and samples limit interpretation.'})
        summary = await explain(self.client, facts, audience, max_output_tokens=2000, report_kind="comparison",required_terms=['Historically','In this match'] if both_live else ['Historically'])
        return {'t':snapshot['t'], 'warnings':snapshot['warnings'], 'method':'microsoft_foundry_agent', 'summary':summary}

    async def similar(self, result, audience='fan', *, reasoning=None, diagnostics=None):
        from .plain import explain
        from .impact import describe_impact
        result = copy.deepcopy(result)
        choice = result.get('focus_choice')
        role = result.get('focus_role')
        roles = {'striker_dropped':('ST','LST','RST'), 'fb_advanced':('LB','RB'), 'winger_decoy':('LW','RW','LM','RM')}
        group = result.get('by_choice', {}).get(choice, {})
        current = result.get('current_impact')
        alternative = group.get('closest_no' if current and current.get('choice') else 'closest_yes', [])
        candidates = alternative + group.get('closest_yes', []) + group.get('closest_no', []) + result.get('neighbours', [])
        candidates = sorted(candidates, key=lambda m: (0 if m in alternative else 1, m.get('distance') if m.get('distance') is not None else float('inf')))
        chosen = None
        focus_team = (current or {}).get('player', '')[:1]
        for moment in candidates:
            if focus_team and moment.get('team') == focus_team:
                continue
            actions = [a for a in moment.get('actions', []) if a.get('role') in ((role,) if role else roles.get(choice,())) and (not focus_team or not a.get('player','').startswith(focus_team))]
            # Skip empty windows before paid narration/editor calls. A failed
            # action can still be useful; a goal is never required.
            outcome = moment.get('outcome') or {}
            actions = [a for a in actions if (current and a.get('did') is False) or a.get('did') or outcome.get('shots') or outcome.get('goal') or abs(float(a.get('own_value') or 0)) >= .001]
            if actions:
                chosen = (moment, actions[0]);break
        if chosen is None:
            return {'t':result.get('query_t'), 'method':'withheld', 'publish':False, 'warnings':[], 'editor':{'publish':False,'reason':'No informative comparable episode'}}
        advice = [];required_phrase = None
        facts = [{'id':'scope','text':'Tell one historical example linked to the current impact: compare the specific choices and observed outcomes in connected prose, not a disconnected sequence-ended sentence. Centre the historical passage on the single selected historical player. Identify season, matchweek and fixture when supplied, then the observed action and subsequent outcome. A past example is not a prediction or causal proof. Do not discuss other historical players or compare multiple examples.'}]
        if current:
            facts.append({'id':'current_anchor','text':'CURRENT MATCH (not part of the historical fixture): '+describe_impact(current)+'. Link the historical example to this specific current action. Similarity uses pre-decision ball and team-shape features, not identical positions. State the observed difference, not what would have happened here. Do not invent CDM struggles, missed chances or a causal numerical effect.'})
        if chosen:
            moment, action = chosen
            required_terms=[prefix+str(moment[key]) for key,prefix in (('season',''),('matchweek','matchweek '),('home','Team '),('away','Team '),('clock','')) if moment.get(key) is not None]
            action_text = action.get('text','') if action.get('did') else 'No atypical movement was recorded for this player in this window; this does not establish habitual style or exact location.'
            if not action.get('did'):
                absent={'striker_dropped':'no drop was detected','fb_advanced':'no advance was detected','winger_decoy':'no decoy run was detected'}.get(choice,'no atypical movement was detected')
                action_text=absent+'. This is absence of a detected action, not evidence of standing still, staying high, staying in the box, or staying in a normal position.'
            action_text = re.sub(r'produced [0-9.]+ xG', 'produced a shot', action_text)
            # Keep one focal player; receiver identifiers are incidental to their action.
            outcome = moment.get('outcome') or {}
            if outcome.get('goal'): outcome_description='A goal was recorded.'
            elif outcome.get('shots'): outcome_description='A shot was recorded, without a recorded goal.'
            elif outcome.get('shots')==0: outcome_description='No shot was recorded in the follow-up window.'
            else: outcome_description='Follow-up outcome is unavailable; omit it.'
            facts.append({'id':'historical_episode','text':f"HISTORICAL MATCH ONLY: Season {moment.get('season','unknown')}; matchweek {moment.get('matchweek','unknown')}; Team {moment.get('home') or moment.get('team')} against Team {moment.get('away') or moment.get('opponent')}, at {moment.get('clock')}. Focus: {action['player']} (use this exact compact ID), Team {moment.get('team')}, role {action.get('role')}. Observed action: {action_text}. Subsequent recorded outcome: {outcome_description} This is an observed sequence, not evidence the player caused the outcome."})
        else:
            facts.append({'id':'missing','text':'No comparable historical episode for the selected role is available. Say this briefly; do not invent a historical player, action or result.'})
        start = time.perf_counter()
        summary = await explain(self.client, facts, audience, reasoning=reasoning or 'minimal', diagnostics=diagnostics, concise=audience!='analyst', required_phrase=required_phrase, require_uncertainty=False, max_output_tokens=2400, report_kind="history", required_terms=required_terms)
        return {'t':result.get('query_t'), 'advice':advice, 'warnings':result['warnings'], 'source':{k:moment.get(k) for k in ('season','matchweek','home','away','clock','fixture_id','file','t_start','t_end','distance')},
                'method':'microsoft_foundry_agent' if summary.get('method')!='withheld' else 'withheld', 'publish':summary.get('method')!='withheld', 'summary':summary if summary.get('method')!='withheld' else None, 'editor':summary.get('editor'),
                'latency_ms':round((time.perf_counter()-start)*1000,1)}

    async def explain_live(self, snapshot, *, transport="agent", reasoning=None, diagnostics=None):
        from .plain import explain
        from .impact import describe_impact
        snapshot = report_units(copy.deepcopy(snapshot))
        from .impact import impact_candidate
        impact = snapshot.get('impact') or impact_candidate(snapshot)
        if not impact:
            result=render_overlay(snapshot, [], 'no_impact_evidence');result['narrative']=None
            return result
        # Cards are already ranked by the analyser. AI only words the selected evidence.
        chosen = snapshot['cards'][:2 if snapshot['audience']=='analyst' else 1]
        facts = overlay_facts({**snapshot, 'similar_story': None}, []) if snapshot['audience']=='analyst' else []
        facts.append({'id':'scope','text':'Describe only the observed moment, never infer the player personality or usual playing style. A role is not a personality. Benefits and costs are uncertain unless actually observed.'})
        row=snapshot.get('observed_row') or {}
        if row and snapshot['audience']!='analyst' and not impact:
            role=snapshot['player']['role'];pid=snapshot['player']['player']
            if 'ST' in role:
                text=f"{pid}: {int(row.get('drop_n',0))} completed deeper-movement episodes so far; {int(row.get('drop_link_n',0))} forward link passes. At {int(row.get('drop_delivery_n',0))} later deliveries, absent from the box {int(row.get('drop_box_absent_n',0))} times. These totals do not describe the current movement or establish a habitual style."
            elif role in ('LB','RB'):
                text=f"{pid}: {int(row.get('fb_n',0))} completed advances; {int(row.get('fb_ft_receptions',0))} final-third receptions; team lost possession during {int(row.get('fb_turnover_n',0))} advances. A turnover is not necessarily this player's error."
            else:
                text=f"{pid}: {int(row.get('run_n',0))} completed off-ball runs; {int(row.get('decoy_drawn',0))} defender-following observations; space used after {int(row.get('decoy_used_n',0))} decoys. Following is not proof the runner attracted the defender."
            facts.append({'id':'observed_actions','text':text})
        for card in ([] if impact else chosen):
            facts.append({'id':card['id'], 'text':snapshot['player']['player'] + ': ' + (card['analyst'] if snapshot['audience']=='analyst' else card['fan'])})
            facts.append({'id':card['id']+':limit', 'text':card['caveat']})
        required_phrase = None
        required_values = {}
        facts.append({'id':'impact','text':'Observed impact trigger: '+describe_impact(impact)+'. Explain this sequence; inferred events are not official match events. Temporal association does not prove causation.'})
        # Completed episode details supply the observed sequence, not cumulative counts.
        recent = snapshot.get('verdicts') or []
        if recent and not impact:
            episode = recent[0]
            facts.append({'id':'recent_sequence','text':f"Recent completed movement at {episode.get('t')}: benefit/action: {episode.get('benefit')}; cost/return: {episode.get('cost')}. Describe this as an earlier movement, not a new action. Cumulative scores below are not the effect of this single movement."})
        live = snapshot.get('tactical_state') or {}
        if live.get('best_forward_pass') and not impact:
            facts.append({'id':'observed_pass','text':'Observed forward pass in the current movement: '+json.dumps(live['best_forward_pass'])+'. Do not claim a reception unless recorded.'})
        pid = snapshot['player']['player']
        facts.append({'id':'display_identity','text':f"Focus {pid}: use exactly {pid} in headline and narrative; use compact team-prefixed IDs for every other player too. Describe observed behaviour, not hidden personality. If no drop, overlap or decoy was observed, describe the actual positioning or support without forcing an atypical-movement story."})
        episode_value = (impact.get('evidence') or {}).get('value') or {}
        episode_individual = (impact.get('evidence') or {}).get('individual') or {}
        if 'net' in episode_value:
            scope = 'this completed movement'
            scores = {'team gain': episode_value['net'], 'personal gain': episode_individual.get('net')}
        elif row.get('value_n', 0) > 0:
            seconds = float(row.get('possession_s', 0))
            scale = 600 / seconds if seconds > 0 else 1
            scope = 'match-to-date, per 10 minutes of team possession' if seconds > 0 else 'across completed movements'
            scores = {'team gain':float(row.get('value_net',0))*scale, 'personal gain':float(row.get('indiv_net',0))*scale}
        else:
            scope = 'No completed scored movements yet'
            scores = {}
            facts.append({'id':'net_pending','text':'No completed scored movements are available yet. Do not report zero contribution. Describe only the observed positioning or action.'})
        if (impact.get('evidence') or {}).get('score_incomplete'):
            scores = {}
            scope = 'Observed counterattack; movement score excludes the later shot'
        for label, raw_value in scores.items():
            if raw_value is None:
                continue
            value = round(float(raw_value),3) + 0.0
            if abs(value) >= .001:
                required_values[label] = f'{value:+.3f}'
                facts.append({'id':label.replace(' ','_'),'text':f"Estimated {label} ({value:+.3f}), {scope}. Preserve the signed figure and label; omit units from prose because the glossary explains them."})
            else:
                facts.append({'id':label.replace(' ','_'),'text':f"{label}: negligible at display precision; do not print a rounded zero or conclude no football contribution."})
        if scores:
            facts.append({'id':'score_scope','text':f'These are the existing audited benefit-cost estimates for {scope}, not shot quality. Team gain is shared episode value change minus realised cost; personal gain is positional value plus decoy-space credit minus modelled cost. A negative team value warrants caution, not sole blame. Do not attribute match-to-date totals to one movement or turn absence from the box into a proven missed goal. The score scope is displayed separately.'})
        if ((impact.get('evidence') or {}).get('counter') or {}).get('goal_against'):
            facts.append({'id':'goal_priority','text':'The connected counterattack ended in an opposing goal. This is the decisive outcome: explicitly say the opponent scored after exploiting the vacated flank, qualified as inferred from tracking. Team gain belongs to the followed player\'s team. Do not soften the goal to mere progression or assign sole causal blame.'})
        start = time.perf_counter()
        narrative = await explain(self.client, facts, snapshot['audience'],
            transport=transport, reasoning=reasoning, diagnostics=diagnostics, concise=snapshot['audience']!='analyst', required_phrase=required_phrase, required_values=required_values,
            required_terms=['opponent scored'] if ((impact.get('evidence') or {}).get('counter') or {}).get('goal_against') else None)
        elapsed = round((time.perf_counter()-start)*1000,1)
        result = render_overlay(snapshot, [c['id'] for c in chosen], 'microsoft_foundry_live_agent',
            [{'stage':'EvidenceNarrator','tool':'single_response_validated','evidence_at':snapshot['t']}])
        narrative['score_scope'] = scope
        result.update(narrative=narrative, latency_ms=elapsed)
        return result

    async def explain(self,snapshot):
        from agent_framework import Agent,tool
        snapshot=report_units(copy.deepcopy(snapshot));trace=[];proposal=[];approved=[]
        read={'evidence':False,'alternatives':False,'submitted':False,'reviewed':False}
        started=time.perf_counter()
        def log(stage,action):trace.append({'stage':stage,'tool':action,'evidence_at':snapshot['t']})

        @tool(approval_mode='never_require',max_invocations=2)
        def inspect_player_evidence() -> str:
            """Get observed player metrics and candidate cards at the frozen evidence timestamp."""
            read['evidence']=True;log('TacticalScout','inspect_player_evidence')
            return json.dumps({'player':snapshot['player'],'cards':snapshot['cards'],'changes':snapshot['changes']})

        @tool(approval_mode='never_require',max_invocations=2)
        def inspect_recent_context() -> str:
            """Read only trailing inferred events; no future frames or simulator labels."""
            log('TacticalScout','inspect_recent_context')
            return json.dumps(snapshot['recent_events'])

        @tool(approval_mode='never_require',max_invocations=1)
        def propose_cards(evidence_ids: list[str]) -> str:
            """Choose up to two grounded cards, or an empty list to stay quiet."""
            if not read['evidence']:raise ValueError('Inspect evidence before proposing')
            validate_selection(snapshot,evidence_ids)
            proposal[:]=evidence_ids;read['submitted']=True;log('TacticalScout','propose_cards')
            return 'Proposal recorded. Stop this run.'

        scout=Agent(client=self.client,name='TacticalScout',
            instructions='Inspect evidence for the favourite player. Use recent context when useful. '
            'Select meaningful benefit, not movement alone, according to viewer focus. '
            'Call propose_cards once with existing evidence IDs, or [] if nothing is worth explaining. '
            'Do not write your own overlay or claim causality. Use at most four tool calls.',
            tools=[inspect_player_evidence,inspect_recent_context,propose_cards])
        await scout.run(json.dumps({'time':snapshot['t'],'player':snapshot['player']['player'],
                                   'focus':snapshot['focus'],'audience':snapshot['audience']}))
        if not read['submitted']:raise RuntimeError('Scout did not submit a grounded proposal')

        @tool(approval_mode='never_require',max_invocations=2)
        def check_alternatives() -> str:
            """Inspect proposed cards, uncertainty and team-wide exposure before approving."""
            read['alternatives']=True;log('EvidenceEditor','check_alternatives')
            return json.dumps({'proposed':validate_selection(snapshot,proposal),'team':snapshot['team'],
                               'limitations':snapshot['limitations'],'audience':snapshot['audience']})

        @tool(approval_mode='never_require',max_invocations=1)
        def approve_overlay(evidence_ids: list[str]) -> str:
            """Approve a subset of proposals, or veto all with []. No new claims are allowed."""
            if not read['alternatives']:raise ValueError('Review alternatives before approval')
            validate_selection(snapshot,evidence_ids)
            if any(i not in proposal for i in evidence_ids):raise ValueError('Reviewer cannot add unproposed evidence')
            approved[:]=evidence_ids;read['reviewed']=True;log('EvidenceEditor','approve_overlay')
            return 'Approval recorded. Stop this run.'

        editor=Agent(client=self.client,name='EvidenceEditor',
            instructions='Check alternatives and exposure. Review the proposal for a personalised football overlay. '
            'For fans prefer one clear useful story; for analysts allow two informative measurements. '
            'Veto weak or misleading evidence by submitting []. Retain caveats: geometry is not causation. '
            'Call check_alternatives, then approve_overlay using only proposed IDs. At most three tool calls.',
            tools=[check_alternatives,approve_overlay])
        await editor.run('Review the current proposal and approve a grounded subset for '+snapshot['audience'])
        if not read['reviewed']:raise RuntimeError('Reviewer did not approve an overlay')
        result=render_overlay(snapshot,approved,'microsoft_foundry_agents',trace)
        if approved:
            chosen=validate_selection(snapshot,approved)
            if snapshot['audience']!='analyst':chosen=chosen[:1]
            result['narrative']=await self._narrate_or_local('overlay',snapshot,snapshot['audience'],chosen)
            result['trace'].append({'stage':'Narrator','tool':'submit_summary' if result['narrative']['method']=='microsoft_foundry_agent' else 'local_fallback','evidence_at':snapshot['t']})
        result['latency_ms']=round((time.perf_counter()-started)*1000,1)
        return result


async def bounded_explanation(director,snapshot,timeout=10.):
    return await asyncio.wait_for(director.explain(snapshot),timeout=timeout)
