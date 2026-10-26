"""Evidence-bound personalised cards and machine-readable overlay contract."""
from dataclasses import dataclass
from team_player.config.analysis import SUPPORTED_ROLES as SUPPORTED, AUDIENCES
from team_player.utils.fmt import z as _z, pp as _pp

@dataclass(frozen=True)
class Preferences:
    player: str='A09'
    audience: str='fan'
    focus: str='all'

    def validate(self,metadata):
        players={p['id']:p for p in metadata['players']}
        if self.player not in players:raise ValueError('Unknown player')
        if self.audience not in AUDIENCES:raise ValueError('Unknown audience')
        if self.focus not in ('all','support','space','runs'):raise ValueError('Unknown focus')


def evidence_snapshot(analysis,gate,preferences,recent_events=(),tactics=None,episodes=()):
    p=next(p for p in analysis['players'] if p['player']==preferences.player)
    team=analysis['teams'][p['team']];cards=[];t=analysis['t']
    supported=p['role'] in SUPPORTED   # other roles: "to be developed" in the viewer
    active=gate['eligible'] and gate['team']==p['team'] and supported
    def add(key,title,fan,detail,caveat,topic,value=None,unit=None):
        cards.append({'id':f'{t:.3f}:{p["player"]}:{key}','player':p['player'],'title':title,
            'fan':fan,'analyst':detail,'caveat':caveat,'topic':topic,'value':value,'unit':unit})
    live=(tactics or {}).get('live',{}).get(p['player'],{})
    if active:
        tactical_cards(add,p,live,team)
        option=p['pass_option']
        if p['excursion']=='striker_dropping_deep':
            if option and option['accessible']:
                add('deep','An extra link in the build-up',f'{p["player"]} has dropped back and offers a way to move the ball on.',
                    f'Outside the role band by {p["outside_role_region_m"]:.1f} m; passing-lane arrival margin {option["interception_margin_s"]:.2f} s.',
                    f'The team currently has {team["advanced_central_players"]} advanced central players. Dropping deep alone does not prove a benefit.','support',option['interception_margin_s'],'s')
            else:
                add('deep_wait','Looking for a deeper connection',f'{p["player"]} has moved deeper, but a clear passing route is not established yet.',
                    f'Role-band departure {p["outside_role_region_m"]:.1f} m; no accessible passing option under the arrival model.',
                    'Space near the opposition goal may be left unoccupied; this is an opportunity to watch, not a credited success.','support')
        if p['excursion']=='fullback_advanced':
            add('overlap','A defender joins the attack',f'{p["player"]} has moved ahead of the usual defensive band to offer support.',
                f'Role-band departure {p["outside_role_region_m"]:.1f} m; direct attacking coverage {p["direct_attacking_coverage_m2"]:.0f} m².',
                f'Own wide-channel exposure is {team["exposed_wide_area_m2"]:.0f} m² under the arrival model; this is team-wide, not solely caused by this player.','support')
        follows=[f for f in analysis['following'] if f['runner']==p['player']]
        if follows:
            defenders=sorted({f['defender'] for f in follows})
            add('run','A run worth following',f'{p["player"]} moves without the ball while '+', '.join(defenders)+' move in a similar direction.',
                f'{len(defenders)} defender(s) meet trailing movement/lag criteria: '+', '.join(defenders)+'.',
                'This is following evidence, not proof of attraction. Ball movement and team shape can explain the same pattern.','runs',len(defenders),'defenders')
        if p['direct_attacking_coverage_m2']>0:
            add('space','An option higher up the pitch',f'{p["player"]} is helping the team reach attacking space sooner in our tracking model.',
                f'Direct attacking coverage: {p["direct_attacking_coverage_m2"]:.0f} m² using a leave-one-player-out arrival comparison.',
                'This measures current geometric coverage, not space causally created by a run or a scoring probability.','space',p['direct_attacking_coverage_m2'],'m²')
        if option and option['accessible'] and not any(c['topic']=='support' for c in cards):
            add('outlet','A passing option is available',f'{p["player"]} offers a route for the teammate on the ball.',
                f'Lane margin {option["interception_margin_s"]:.2f} s; progression {option["forward_m"]:.1f} m.',
                'Straight-line arrival estimates omit turning and technique; an available lane does not guarantee a completed pass.','support',option['interception_margin_s'],'s')
    # Tactical cards are richer than the legacy geometry cards; keep one card per topic.
    seen=set();unique=[]
    for c in cards:
        if c['topic'] in seen and not c['id'].split(':')[-1].startswith('tac_'):continue
        seen.add(c['topic']);unique.append(c)
    cards=unique
    if preferences.focus!='all':cards=[c for c in cards if c['topic']==preferences.focus]
    return {'t':t,'episode_id':gate['episode_id'],'eligible':active,'supported':supported,'player':p,'team':team,
            'audience':preferences.audience,'focus':preferences.focus,'cards':cards,
            'recent_events':[e for e in recent_events if e['t']<=t][-20:],
            'changes':analysis['changes_over_2s'].get(p['team'],{}),
            'tactical_state':live,'verdicts':verdicts(p['player'],episodes,t),
            'possession_context':(tactics or {}).get('possession'),
            'limitations':['Positions alone cannot establish player intent or causal attribution.',
                           'Possession and event classifications are inferred, not official match data.']}


def validate_selection(snapshot,ids):
    available={c['id']:c for c in snapshot['cards']}
    if not isinstance(ids,list) or len(ids)>2 or len(set(ids))!=len(ids) or any(i not in available for i in ids):
        raise ValueError('Invalid evidence selection')
    return [available[i] for i in ids]


def render_overlay(snapshot,ids,method='local_rules',trace=()):
    cards=validate_selection(snapshot,ids)
    if not snapshot['eligible']:cards=[]
    if snapshot['audience']!='analyst':cards=cards[:1]
    from team_player.agents.narration import local_summary
    return {'kind':'overlay','schema_version':1,'player':snapshot['player']['player'],
        'audience':snapshot['audience'],'episode_id':snapshot['episode_id'],
        'evidence_at':snapshot['t'],'valid_from':snapshot['t'],'valid_until':snapshot['t']+12,
        'placement':'lower_third','selection_method':method,'trace':list(trace),
        'cards':[{'evidence_id':c['id'],'title':c['title'],
                  'body':c['analyst'] if snapshot['audience']=='analyst' else c['fan'],
                  'numbers':c['analyst'],
                  'caveat':c['caveat'],'stat':{'value':c['value'],'unit':c['unit']} if c['value'] is not None else None}
                 for c in cards],
        # Plain-English summary with the numbers kept; replaced by the Foundry Narrator when connected.
        'narrative':local_summary('overlay',snapshot,snapshot['audience'],cards) if cards else None,
        'grounding':'Measured geometry and inferred events, not causal proof.'}


def local_overlay(snapshot):
    return render_overlay(snapshot,[c['id'] for c in snapshot['cards'][:2]],trace=[{'stage':'local_rules','status':'No AI model used'}])


def overlay_is_current(overlay,t,episode,preferences):
    return bool(overlay and overlay['player']==preferences.player and overlay['audience']==preferences.audience
                and overlay['episode_id']==episode and overlay['evidence_at']<=t<=overlay['valid_until'])


def tactical_cards(add,p,live,team):
    """Cards from the online tactical tracker. Every number is measured, not generated."""
    pid=p['player'];state=live.get('state')
    if state=='dropping_deep':
        bp=live.get('best_forward_pass')
        detail=f"{live['depth_m']:.0f} m short of the last defender; {live['receptions']} reception(s); opposing CB stepped out {live['cb_step_m']:.1f} m."
        if bp:detail+=f" Best forward pass {bp['length_m']:.0f} m, {bp['packing']} opponents bypassed, xT {bp['delta_xt']:+.3f}."
        add('tac_drop','Dropping into the pocket',
            f'{pid} has come short to link play'+(f' and has already found a {bp["length_m"]:.0f} m forward pass.' if bp else '.'),
            detail,'While he links play the box can be empty; the verdict checks who is there when the ball arrives.','support',live['depth_m'],'m')
    elif state=='advanced':
        add('tac_fb','A fullback acting as an extra winger',
            f'{pid} has pushed on to add width'+(' with an overlap.' if live.get('overlap_type')=='overlap' else '.'),
            f"Flank overload {live['max_overload']:+d}; {live.get('rest_defenders')} team-mates behind the ball; "
            f"recovery deficit to his defensive slot {live.get('recovery_deficit_s')} s; opponent-controlled area in his vacated channel {live.get('opp_area_vacated_m2')} m².",
            'Positive recovery deficit means an opponent would reach the space behind him first if the ball is lost.','support',live.get('recovery_deficit_s'),'s')
    elif state=='run_completed' and live.get('decoy'):
        drawn=live.get('drawn_defenders') or [];ben=live.get('beneficiary')
        add('tac_decoy','A run that drags defenders',
            f'{pid} ran without the ball'+(f' and took {len(drawn)} defender(s) with him' if drawn else '')+(f', freeing {ben["player"]}.' if ben else '.'),
            f"{live['run_type']} run; drawn: {', '.join(drawn) or 'none'}; team-mates gained {live.get('space_created_m2') or 0:.0f} m² of attacking-half control and {_pp(live['space_created_obso'])} pp of space value versus defenders staying put"
            +(f"; {ben['player']} gained {ben['separation_gain_m']} m of separation." if ben else '.'),
            'Counterfactual restores only the drawn defenders; other players would also have reacted.','space',_pp(live['space_created_obso']),'pp')
    elif state=='running':
        add('tac_run','Making a run',f'{pid} is sprinting without the ball.',f"Peak speed {live['peak_speed_m_s']} m/s.",
            'Whether the run pulls defenders is judged when it ends.','runs',live['peak_speed_m_s'],'m/s')


def verdicts(pid,episodes,t,window=25.):
    """Benefit-versus-cost summaries for this player's recently completed episodes."""
    out=[]
    for e in reversed(list(episodes)):
        if e.get('player')!=pid or t-e.get('t_end',t)>window:continue
        if e['kind']=='striker_drop':
            lp=e.get('link_pass');dl=e.get('delivery');po=e.get('possession_outcome') or {}
            benefit=(f"Linked play with a {lp['length_m']:.0f} m pass ({lp['packing']} bypassed, xT {lp['delta_xt']:+.3f}) to {lp['receiver']}." if lp
                     else f"Dropped {e['max_depth_m']:.0f} m but no forward pass from the pocket.")
            cost=("No final-third delivery followed." if not dl else
                  f"At the delivery he was {'in' if dl['striker_in_box'] else 'NOT in'} the box ({dl['striker_to_goal_m']:.0f} m from goal); {dl['box_targets']} team-mate(s) in the box.")
            out.append({'kind':e['kind'],'t':e['t_end'],'benefit':benefit,'cost':cost,
                        'outcome':f"Possession: {po.get('shots',0)} shot(s), xG {po.get('xg',0):.2f}"+(', GOAL' if po.get('goal') else ''),'flag':e.get('cost_flag')})
        elif e['kind']=='fullback_advance':
            c=e.get('counter')
            benefit=f"Advanced to {e['max_x']:.0f} m, overload {e['max_overload']:+d}, {e['receptions']} reception(s), OBSO contribution {_pp(e.get('mean_obso_contribution'))} pp."
            cost=(f"Ball lost while advanced: opponent counter produced {c['shots_against']} shot(s), xG {c['xg_against']:.2f}"
                  +(', through his flank' if c['through_vacated_flank'] else '')+('; GOAL conceded' if c['goal_against'] else '')+'.') if c else \
                 f"No turnover while advanced; mean recovery deficit {e.get('mean_recovery_deficit_s')} s."
            out.append({'kind':e['kind'],'t':e['t_end'],'benefit':benefit,'cost':cost,'outcome':'','flag':bool(c and c['xg_against']>.1)})
        elif e['kind']=='winger_run':
            na=e.get('next_action')
            benefit=(f"{'Decoy' if e['decoy'] else 'Target'} {e['run_type']} run, {e['length_m']:.0f} m; drew {len(e['drawn_defenders'])} defender(s); "
                     f"space for team-mates {e.get('space_created_m2') or 0:.0f} m² / {_pp(e.get('space_created_obso'))} pp.")
            cost=(f"Space used: next pass xT {na['delta_xt']:+.3f}" if na else "Space not used within 4 s")+(f"; {e['shots_within_4s']} shot(s), xG {e['xg_within_4s']:.2f}" if e['shots_within_4s'] else '')+'.'
            out.append({'kind':e['kind'],'t':e['t_end'],'benefit':benefit,'cost':cost,'outcome':'GOAL' if e.get('goal_within_4s') else '','flag':e.get('space_used')})
        if out and out[-1]['t']==e['t_end'] and e.get('value'):
            v=e['value'];iv=e.get('individual') or {};out[-1]['net_value']=v['net'];out[-1]['own_value']=iv.get('net')
            own=(f"Own value {_z(iv['net']):+.3f} expected goals (position {_z(iv['position_value']):+.3f} vs an average player in his role"
                 +(f", minus {_z(iv['cost_xg']):.3f} cost" if iv.get('cost_xg') else '')+'). ') if iv else ''
            out[-1]['outcome']=(out[-1]['outcome']+' · ' if out[-1]['outcome'] else '')+own+f"Team outcome {_z(v['net']):+.3f} (shared with team-mates)"
        if len(out)>=3:break
    return out
