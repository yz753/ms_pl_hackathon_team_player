"""Read-only quality checks on PUBLIC tracking, independent of private intentions."""
from __future__ import annotations
import numpy as np
from team_player.schema import validate_metadata, validate_frame


def audit_tracking(metadata, frames):
    validate_metadata(metadata)
    if len(frames)<2: raise ValueError('At least two frames are required')
    ids=[p['id'] for p in metadata['players']]
    previous=None;positions=[];owner_distances=[];loose_run=longest_loose=0.
    stationary_run=longest_stationary=0.
    for frame in frames:
        validate_frame(frame,metadata,previous)
        lookup={p['id']:p for p in frame['players']}
        positions.append([[lookup[i]['x'],lookup[i]['y']] for i in ids])
        if frame['ball']['owner'] is not None:
            p=lookup[frame['ball']['owner']]
            owner_distances.append(float(np.hypot(p['x']-frame['ball']['x'],p['y']-frame['ball']['y'])))
        dt=frame['t']-previous if previous is not None else 0.
        loose_run=loose_run+dt if frame['ball'].get('state')=='loose' else 0.
        longest_loose=max(longest_loose,loose_run)
        if len(positions)>1:
            old=frames[len(positions)-2]['ball'];ball=frame['ball']
            movement=np.hypot(old['x']-ball['x'],old['y']-ball['y'])
            stationary_run=stationary_run+dt if ball.get('state')=='loose' and movement<.005 else 0.
            longest_stationary=max(longest_stationary,stationary_run)
        previous=frame['t']
    p=np.array(positions);t=np.array([f['t'] for f in frames]);dt=np.diff(t)
    velocity=np.diff(p,axis=0)/dt[:,None,None]
    speeds=np.linalg.norm(velocity,axis=2)
    acceleration=np.linalg.norm(np.diff(velocity,axis=0)/((dt[1:]+dt[:-1])/2)[:,None,None],axis=2)
    distance=np.linalg.norm(p[:,:,None,:]-p[:,None,:,:],axis=3)
    distance[:,np.arange(len(ids)),np.arange(len(ids))]=np.inf
    balls=np.array([[f['ball']['x'],f['ball']['y']] for f in frames])
    ball_speed=np.linalg.norm(np.diff(balls,axis=0)/dt[:,None],axis=1)
    return {'duration_s':frames[-1]['t'],'frames':len(frames),
            'maximum_player_speed_m_s':round(float(speeds.max()),4),
            'maximum_sampled_acceleration_m_s2':round(float(acceleration.max()) if acceleration.size else 0,4),
            'minimum_player_separation_m':round(float(distance.min()),4),
            'maximum_controlled_ball_distance_m':round(max(owner_distances,default=0),4),
            'maximum_ball_speed_m_s':round(float(ball_speed.max()),4),
            'longest_loose_ball_s':round(longest_loose,3),
            'longest_stationary_loose_ball_s':round(longest_stationary,3),
            'events':[{'t':f['t'],**e} for f in frames for e in f['events']],
            'interpretation':'Engineering invariants and observed events, not a realism or accuracy score.'}
