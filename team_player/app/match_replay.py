"""Optional offline replay of position-only tracking using the original replay layout.

Consumes public observations only. No Azure calls, private maps or simulator imports.
"""
import argparse
from pathlib import Path
from team_player.utils.io import read_jsonl, open_text, match_files
from team_player.analyser.observation import TrackingObserver
from team_player.analyser.analysis import Analyzer
from team_player.app.replay import create_replay


def create_match_replay(tracking, output, *, initial_player=None, initial_time=0.):
    output=Path(output)
    if output.exists(): raise ValueError('Replay output already exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    with open_text(tracking) as handle:
        records=read_jsonl(handle)
        metadata=next(records,None)
        if metadata is None: raise ValueError('Empty tracking file')
        observer=TrackingObserver(metadata)
        analyzer=Analyzer(metadata)
        frames=[];analyses=[]
        for raw in records:
            frame=observer.update(raw)
            frames.append(frame)
            result=analyzer.update(frame)
            if result is not None: analyses.append(result)
    if not frames: raise ValueError('Tracking file contains no frames')
    create_replay(metadata,frames,analyses,output,initial_player=initial_player,initial_time=initial_time)
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,help='A .jsonl / .jsonl.gz match, or a folder of them')
    p.add_argument('--output',required=True,help='Output .html file, or an output folder when --input is a folder')
    p.add_argument('--player',help='Player selected when opening the HTML directly')
    p.add_argument('--start-time',type=float,default=0.,help='Initial replay time in seconds')
    args=p.parse_args()
    if Path(args.input).is_dir():
        # Folder mode: one HTML per match, mirroring sub-folders (e.g. seasons). Existing files are skipped.
        root=Path(args.input)
        for src in match_files(root):
            rel=src.relative_to(root);target=Path(args.output)/rel.parent/(src.name.replace('.jsonl.gz','').replace('.jsonl','')+'.html')
            if target.exists():continue
            target.parent.mkdir(parents=True,exist_ok=True)
            print(create_match_replay(src,target,initial_player=args.player,initial_time=args.start_time))
    else:
        print(create_match_replay(args.input,args.output,initial_player=args.player,initial_time=args.start_time))

if __name__=='__main__': main()
