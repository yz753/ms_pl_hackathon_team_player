"""Publication criteria over existing public observations; no simulator or score changes.
Thresholds select broadcast moments, not scientific significance or causal effects.
"""
def impact_candidate(snapshot):
    p=snapshot['player'];pid=p['player'];role=p.get('role','');now=snapshot['t']
    def result(kind,start,evidence,choice=None):
        return {'key':f'{pid}:{kind}:{start}', 'kind':kind,'player':pid,'t_start':start,
                'observed_at':now,'evidence':evidence,'choice':choice}
    stalled=snapshot.get('forward_stall')
    if stalled and stalled.get('player')==pid:
        return result('striker_forward_stalled',stalled['t_start'],stalled,True)
    lost=snapshot.get('passing_option_loss')
    if lost and lost.get('player')==pid and 0<=now-lost['observed_at']<=45:
        return result('winger_lost_option',lost['t_start'],lost,True)
    for e in reversed(snapshot.get('recent_episodes',[])):
        if e.get('player')!=pid:continue
        k=e.get('kind');start=e.get('t_start',now)
        if now-e.get('t_end',now)>45:continue
        lp=e.get('link_pass') or {};dl=e.get('delivery') or {};po=e.get('possession_outcome') or {}
        if k=='striker_drop':
            strong=lp.get('forward_m',0)>=20 and lp.get('packing',0)>=2 and (lp.get('end_local') or [0])[0]>=70
            shots=[shot for shot in snapshot.get('recent_shots',[]) if shot.get('player')==pid and start<=shot['t']<=now and (shot.get('goal') or shot.get('xg',0)>=.1)]
            chance=bool((lp and lp.get('forward_m',0)>=10 and (po.get('goal') or po.get('xg',0)>=.1)) or (dl.get('striker_in_box') and shots))
            missed=dl.get('striker_in_box') is False and dl.get('missed_chance_xg',0)>=.03
            if strong or chance or missed:
                return result(k,start,{'pass':lp,'delivery':dl,'outcome':po,'player_shots':shots,'team_shots':[shot for shot in snapshot.get('recent_shots',[]) if shot.get('team')==p.get('team') and start<=shot['t']<=now],'value':e.get('value'),'individual':e.get('individual')},True)
        if k=='fullback_advance':
            c=e.get('counter') or {}
            exploited=c.get('through_vacated_flank') and (c.get('shots_against',0)>0 or c.get('opp_progress_m',0)>=20)
            gain=(e.get('value') or {}).get('net',0)
            shots=[s for s in snapshot.get('recent_shots',[]) if s.get('team')==p.get('team') and start<=s['t']<=e.get('t_end',now)+4 and (s.get('goal') or s.get('xg',0)>=.1)]
            later_shots=[s for s in snapshot.get('recent_shots',[]) if s.get('team')!=p.get('team') and e.get('t_end',now)<=s['t']<=min(now,e.get('t_end',now)+20)] if exploited else []
            if exploited or (shots and (e.get('receptions',0)>0 or (e.get('max_overload',0)>=2 and (e.get('individual') or {}).get('position_value',0)>=.001))) or (e.get('final_third_receptions',0)>0 and gain>=.03):
                return result('fullback_counter' if exploited else k,start,{'counter':c,'shots':shots,'later_opponent_shots':later_shots,'score_incomplete':bool(later_shots and not c.get('shots_against')),'final_third_receptions':e.get('final_third_receptions',0),'value':e.get('value'),'individual':e.get('individual')},True)
        if k=='winger_run' and e.get('decoy') and e.get('drawn_defenders') and e.get('space_used'):
            na=e.get('next_action') or {}
            if e.get('shots_within_4s',0)>0 or na.get('delta_xt',0)>=.02:
                return result(k,start,{x:e.get(x) for x in ('drawn_defenders','beneficiary','next_action','space_used','shots_within_4s','goal_within_4s','value','individual')},True)
    live=snapshot.get('tactical_state') or {};lp=live.get('best_forward_pass') or {}
    if 'ST' in role and lp.get('forward_m',0)>=20 and lp.get('packing',0)>=2 and (lp.get('end_local') or [0])[0]>=70:
        return result('striker_progression',lp.get('t',now),{'completed_pass':lp},True)
    # Every striker may offer danger near goal, irrespective of private personality.
    option=p.get('pass_option') or {};pos=snapshot.get('focus_position') or {};local_x=pos.get('local_x',0)
    if 'ST' in role and snapshot.get('eligible') and local_x>=83 and abs(pos.get('y',0)-34)<=14 and option.get('accessible') and option.get('interception_margin_s',0)>=.25 and p.get('nearest_opponent_m',0)>=1.5:
        candidate = result('striker_receiving_danger',now,{'local_x':local_x,'y':pos['y'],'passing_lane':option,'nearest_opponent_m':p['nearest_opponent_m'],'interpretation':'Accessible onside receiving lane near goal; a potential threat, not a completed chance or guaranteed goal.'},False)
        candidate['key']=f"{pid}:striker_receiving_danger:{snapshot.get('episode_id')}"
        return candidate
    return None

def describe_impact(impact):
    """Compact public facts, excluding internal xG/PV fields and identifiers."""
    pid=impact['player'];e=impact.get('evidence') or {};kind=impact.get('kind','');parts=[]
    if kind=='striker_forward_stalled':
        lp=e['pass'];parts.append(f"{pid} received deeper and passed forward to {lp['receiver']}. At reception, forward passing lanes were blocked and the attacking side was outnumbered locally: {e['nearby_attackers']} attacker(s) against {e['nearby_defenders']} defender(s). His team subsequently lost possession without a shot in that sequence. The pass alone did not overcome the defence; do not blame the striker for causing the loss.")
    elif kind in ('striker_drop','striker_progression','striker_missed_return'):
        parts.append(f'{pid} moved deeper earlier in the attack.')
        lp=e.get('pass') or e.get('completed_pass') or {}
        if lp:
            parts.append(f"Completed a {lp.get('length_m',lp.get('forward_m',0)):.0f} m forward pass to {lp.get('receiver','an unidentified teammate')}, bypassing {lp.get('packing',0)} opponents; reception inferred from tracking.")
        dl=e.get('delivery') or {}
        if 'striker_in_box' in dl:
            parts.append('Returned to the box before the subsequent delivery.' if dl['striker_in_box'] else ('Was absent from the box at the subsequent delivery; the model detected an opportunity cost, not proof a goal was missed.' if dl.get('missed_chance_xg', 0) > 0 else 'Was outside the box at the subsequent delivery; no missed-chance cost was measured.'))
        shots=e.get('player_shots') or []
        if shots:
            shot=shots[-1];parts.append(f"{pid} subsequently took a shot from {shot.get('distance_m',0):.0f} m. "+('A goal was inferred.' if shot.get('goal') else 'No goal was recorded; do not infer whether it was saved, blocked or off target.'))
        elif any(shot.get('goal') for shot in e.get('team_shots', [])):
            scorer = next(shot['player'] for shot in reversed(e['team_shots']) if shot.get('goal'))
            parts.append(f'{scorer} subsequently scored in the same possession; the goal is inferred from tracking, not proof that the drop caused it.')
        elif (e.get('outcome') or {}).get('shots'):
            parts.append('A teammate scored later in the same possession; the goal is inferred from tracking, and temporal association alone does not prove the drop caused it.' if e['outcome'].get('goal') else 'A teammate shot later in the possession; this is association, not proof the drop created it.')
    elif kind in ('fullback_advance','fullback_counter'):
        parts.append(f'{pid} advanced from fullback.')
        counter=e.get('counter') or {}
        if counter.get('through_vacated_flank'):
            parts.append('After possession was lost, the opponent progressed through the vacated flank.')
            if counter.get('shots_against'):parts.append('The counterattack resulted in an opposing shot.')
            if counter.get('goal_against'):parts.append('An opposing goal was inferred.')
            later=e.get('later_opponent_shots') or []
            if later:parts.append('An opposing goal was subsequently inferred in the observed follow-up.' if any(x.get('goal') for x in later) else 'An opposing shot followed in the observed follow-up.')
        else:
            if e.get('final_third_receptions'):parts.append(f"Received the ball in the final third {int(e['final_third_receptions'])} times.")
            if e.get('shots'):
                own = [shot for shot in e['shots'] if shot.get('player') == pid]
                if any(shot.get('goal') for shot in own):parts.append(f'{pid} subsequently scored; the goal is inferred from tracking.')
                elif own:parts.append(f'{pid} subsequently shot; no goal is recorded in this evidence.')
                else:parts.append('A teammate subsequently had a promising shot; the overlap was part of the sequence, not proven to have caused it.')
    elif kind=='winger_lost_option':
        parts.append(f'{pid} ran away from an accessible passing option. The lane became inaccessible, and the team subsequently lost possession.')
        parts.append('The same later ball and defender positions still offer a lane to his earlier position under the existing tracking model. This supports a positional drawback, not proof the run caused the turnover. No useful space gain or shot was observed in this sequence.')
    elif kind=='winger_run':
        parts.append(f"{pid} ran without the ball; {len(e.get('drawn_defenders') or [])} defenders followed under the tracking criteria.")
        ben=e.get('beneficiary') or {};na=e.get('next_action') or {}
        if ben.get('player'):parts.append(f"{ben['player']} gained separation.")
        if e.get('space_used'):parts.append('A teammate used the resulting space in the observed follow-up.')
        if na.get('receiver'):parts.append(f"The next recorded pass reached {na['receiver']}.")
        if e.get('shots_within_4s'):parts.append('A teammate shot within four seconds.')
        if e.get('goal_within_4s'):parts.append('A goal was inferred in that follow-up.')
        parts.append('Defender following is not proof of deliberate attraction.')
    elif kind=='striker_receiving_danger':
        parts.append(f'{pid} has an accessible onside passing lane in a central position near the box, with separation from the nearest opponent. This is an immediate receiving threat, not a completed chance or guaranteed goal.')
        margin=(e.get('passing_lane') or {}).get('interception_margin_s',e.get('passing_lane_margin_s'))
        if margin is not None:parts.append(f'Estimated passing-lane arrival margin {margin:g} s.')
    if not parts:parts.append(f'{pid}: no detailed movement observation supplied.')
    return ' '.join(parts)
