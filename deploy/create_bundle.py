"""Build a source-deploy ZIP using an allowlist; simulator and truth stay local."""
import argparse
import json
from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parents[1]
# Explicit allowlist. Never add simulator/ or config/styles.py (private personalities).
MODULES=('__init__.py','schema.py',
         'config/__init__.py','config/league.py','config/analysis.py',
         'utils/__init__.py','utils/geometry.py','utils/fmt.py','utils/io.py',
         'analyser/__init__.py','analyser/observation.py','analyser/analysis.py','analyser/models.py','analyser/tactics.py',
         'analyser/situations.py','analyser/comparison.py','analyser/live.py','analyser/presentation.py','analyser/history.py',
         'agents/__init__.py','agents/foundry.py','agents/narration.py','agents/plain.py','agents/reporting.py','agents/impact.py','agents/option_watch.py',
         'cloud/__init__.py','cloud/generation_spec.py','cloud/generation_store.py','app/generation.py',
         'app/__init__.py','app/server.py','app/glossary.py','app/storage.py','app/blob_transfer.py')
WEB_FILES=('index.html','compare.html','reports.html','generate.html','lobby.html','examples.json')

def validate_tracking(match,current=False):

    with match.open() as source:
        metadata=json.loads(next(source))
        if metadata.get('input_type')!='positions_only':raise ValueError('Use a position-only continuous match')
        if current and metadata.get('season')!='2026-27':raise ValueError('Example must be current season')
        allowed_meta={'kind','schema_version','pitch','sample_hz','teams','players','input_type','match_id',
                      'season','matchweek','home','away','kickoff','dataset_role'}
        if set(metadata)-allowed_meta:raise ValueError('Unexpected metadata fields')
        if 'dataset_role' in metadata and metadata['dataset_role']!='current_season_demo':
            raise ValueError('Unexpected dataset role')
        for line in source:
            frame=json.loads(line)
            if set(frame)!={'kind','t','players','ball'} or set(frame['ball'])!={'x','y'}:
                raise ValueError('Private or unexpected fields in tracking input')
            if any(set(p)!={'id','x','y'} for p in frame['players']):raise ValueError('Unexpected player fields')

def build_bundle(output,match,replace=False):
    output=Path(output);match=Path(match)
    if replace and output.exists():output.unlink()
    validate_tracking(match)
    examples=json.loads((ROOT/'team_player/app/web/examples.json').read_text())
    for example in examples:
        validate_tracking(ROOT/'matches'/example['file'], current=True)
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED) as archive:
        import sys
        sys.path.insert(0,str(ROOT))
        from team_player.cloud.generation_spec import build_identity
        archive.writestr('team_player/generation_build.json',json.dumps(build_identity()))
        for module in MODULES:archive.write(ROOT/'team_player'/module,'team_player/'+module)
        for name in WEB_FILES:archive.write(ROOT/'team_player/app/web'/name,'team_player/app/web/'+name)
        archive.write(ROOT/'deploy/requirements-runtime.txt','requirements.txt')
        archive.write(match,'matches/match_0000.jsonl')
        for example in examples:
            archive.write(ROOT/'matches'/example['file'],'matches/'+example['file'])
        for name in ('roster.json','comparison_history.json'):
            path=match.parent/name
            if path.exists():
                # Copy only the public comparison schema, not arbitrary source fields.
                data=json.loads(path.read_text())
                keys=('id','team','role')
                if name=='roster.json':clean={'players':[{k:p[k] for k in keys} for p in data['players']]}
                else:
                    import sys;sys.path.insert(0,str(ROOT))
                    from team_player.analyser.comparison import FIELDS
                    clean={'version':2,'matches':[{'fingerprint':m['fingerprint'],'file':m['file'],'players':[{**{k:p[k] for k in keys},**{k:p.get(k,0.) for k in FIELDS}} for p in m['players']]} for m in data['matches']]}
                archive.writestr('matches/'+name,json.dumps(clean))
        # In Azure the app reads current-season matches and history.db from Blob Storage;
        # the demo match above is only a fallback when no Blob account is configured.
    return output

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default=str(ROOT/'deploy/team-player-app.zip'))
    p.add_argument('--match',default=str(ROOT/'matches/match_0000.jsonl'))
    p.add_argument('--replace',action='store_true',help='overwrite an existing ZIP')
    a=p.parse_args();print(build_bundle(a.out,a.match,a.replace))
