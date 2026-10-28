"""Validate and upload only explicitly selected 2026-27 public recordings.

Defaults to validation only. --upload writes to tracking/current/2026-27/.
Never uploads reference analyses, captions, profiles or simulator events.
"""
import argparse,gzip,hashlib,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from team_player.schema import validate_metadata,validate_frame
from team_player.utils.io import open_text
from team_player.config.league import CURRENT_SEASON

def validate(path):
    with open_text(path) as handle:
        meta=json.loads(next(handle));validate_metadata(meta)
        if meta.get('season')!=CURRENT_SEASON or CURRENT_SEASON!='2026-27':raise ValueError('Only 2026-27 current-season recordings may be uploaded here')
        allowed={'kind','schema_version','pitch','sample_hz','teams','players','input_type','match_id','season','matchweek','home','away','kickoff','dataset_role'}
        if set(meta)-allowed or meta.get('input_type')!='positions_only':raise ValueError('Unexpected or private metadata')
        if any(set(p)!={'id','team','role'} for p in meta['players']) or any(set(t)!={'id','attack_direction','formation'} for t in meta['teams']):raise ValueError('Unexpected or private player/team metadata')
        lines=[json.dumps(meta,separators=(',',':'))];previous=None
        for line in handle:
            f=json.loads(line)
            if set(f)!={'kind','t','players','ball'} or set(f['ball'])!={'x','y'} or any(set(p)!={'id','x','y'} for p in f['players']):raise ValueError('Only public position observations may be uploaded')
            validate_frame(f,meta,previous);previous=f['t'];lines.append(json.dumps(f,separators=(',',':')))
        if previous is None:raise ValueError('Empty recording')
    payload=gzip.compress(('\n'.join(lines)+'\n').encode(),mtime=0)
    name=path.name.removesuffix('.gz').removesuffix('.jsonl')+'.jsonl.gz'
    return f'current/{CURRENT_SEASON}/{name}',payload

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,action='append',required=True);p.add_argument('--upload',action='store_true');a=p.parse_args()
    prepared=[validate(path) for path in a.input]
    if len({name for name,_ in prepared})!=len(prepared):raise ValueError('Input names must be unique')
    service=None
    if a.upload:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        service=BlobServiceClient(os.environ['TEAM_PLAYER_BLOB_ACCOUNT_URL'],credential=DefaultAzureCredential())
    for name,payload in prepared:
        digest=hashlib.sha256(payload).hexdigest()
        if service:
            from azure.core.exceptions import ResourceExistsError
            blob=service.get_blob_client('tracking',name)
            try:blob.upload_blob(payload,overwrite=False,metadata={'sha256':digest,'dataset_role':'current_season_demo','season':CURRENT_SEASON})
            except ResourceExistsError:
                if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:raise ValueError(f'Different data already exist at {name}; choose a new recording name')
            print('Uploaded/verified: tracking/'+name)
        else:print('Validated (not uploaded): tracking/'+name)
if __name__=='__main__':main()
