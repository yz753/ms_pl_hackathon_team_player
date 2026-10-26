"""Microsoft Foundry agents with bounded, read-only tools.

TacticalScout + EvidenceEditor choose overlay cards, ComparisonConsultant and
SituationConsultant choose grounded advice, and Narrator writes plain English that is
checked against the facts. Tool calls, not unconstrained model prose, decide content. No source files,
private profiles, future frames, shell tools or network browsing are exposed.
"""
import asyncio
import copy
import json
import os
import time
from team_player.analyser.presentation import validate_selection, render_overlay
from team_player.agents.narration import (AUDIENCES, WORD_LIMIT, check_grounding, local_summary, terms_used,
                        overlay_facts, comparison_facts, similar_facts)


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
        glossary=terms_used(facts)
        grounding=facts+[{'id':'glossary:'+k,'text':v} for k,v in glossary.items()]
        read=False;result={}
        style={'newcomer':'Assume no football analytics knowledge. No jargon without a plain explanation in the same sentence '
                          '(e.g. "0.12 expected goals, roughly a 1-in-8 chance"). Short sentences.',
               'fan':'Speak like a good TV co-commentator. Football language, one or two key numbers, no formulas.',
               'analyst':'Precise and compact. Keep the key numbers, sample sizes and intervals; technical terms are fine.'}[audience]
        @tool(approval_mode='never_require',max_invocations=1)
        def read_facts() -> str:
            """Read the only facts, numbers and glossary you may use, plus the audience style."""
            nonlocal read
            read=True
            return json.dumps({'audience':audience,'style':style,'word_limit':WORD_LIMIT[audience],'facts':facts,'glossary':glossary})
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

    async def compare(self,snapshot,audience='fan'):
        """ComparisonConsultant selects grounded advice; mandatory suitability warnings cannot be removed."""
        snapshot=copy.deepcopy(snapshot)
        selected=await self._consult('ComparisonConsultant',
            'Act as a cautious football comparison consultant. Inspect the evidence, then recommend existing advice IDs. '
            'Prioritise role compatibility, sample size and contextual limitations. Never rank incompatible roles or equate movement with benefit. '
            'No hidden styles or causal conclusions are available. Use at most two tool calls; do not invent prose or metrics.',
            'Review this comparison for the viewer.',snapshot,snapshot['advice'],
            'inspect_comparison','Read public observations, sample sizes, role suitability and grounded advice.')
        summary=await self._narrate_or_local('comparison',{**snapshot,'advice':selected or snapshot['advice']},audience)
        return {'t':snapshot['t'],'advice':selected,'warnings':snapshot['warnings'],'method':'microsoft_foundry_agent','summary':summary}

    async def similar(self,result,audience='fan'):
        """SituationConsultant: reads pooled neighbours and selects grounded advice IDs.
        Warnings (association, sample size) cannot be removed; no numbers are invented."""
        result=copy.deepcopy(result)
        view={k:result.get(k) for k in ('mode','query_t','k','pool_size','by_choice','current','warnings','advice')}
        selected=await self._consult('SituationConsultant',
            'You review past situations that resemble the current moment. Inspect them, then recommend existing advice IDs '
            'that best explain the trade-off for the viewer (e.g. striker dropping: link value versus box presence). Prefer comparisons with larger n '
            'and non-overlapping intervals; always keep the associative caveat. Never claim a choice caused an outcome. At most two tool calls.',
            'Review these similar situations.',view,result['advice'],
            'inspect_similar_situations','Read the matched past situations split by player choice, with sample sizes and intervals.')
        keep=[a for a in result['advice'] if a['id'] in ('associative','current')]
        summary=await self._narrate_or_local('similar',{**result,'advice':keep+[a for a in selected if a not in keep]},audience)
        return {'t':result.get('query_t'),'advice':selected,'warnings':result['warnings'],'method':'microsoft_foundry_agent','summary':summary}

    async def explain(self,snapshot):
        from agent_framework import Agent,tool
        snapshot=copy.deepcopy(snapshot);trace=[];proposal=[];approved=[]
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
