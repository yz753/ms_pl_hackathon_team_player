"""Portable demo launcher. Load settings with uv run --env-file deploy/demo.env.

No Bash sourcing or virtual-environment activation is required.
"""
from __future__ import annotations
import argparse,json,os,socket,sys,threading,time,webbrowser
from pathlib import Path
from urllib.parse import urlencode,urlparse
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def prepare():
    os.chdir(ROOT)
    if os.environ.get('TEAM_PLAYER_AI')!='foundry':
        raise ValueError('Set TEAM_PLAYER_AI=foundry in deploy/demo.env; this demo requires real Azure AI.')
    endpoint=os.environ.get('FOUNDRY_PROJECT_ENDPOINT','')
    if urlparse(endpoint).scheme!='https' or '/api/projects/' not in endpoint:
        raise ValueError('Set the Foundry PROJECT endpoint, ending /api/projects/<project>, in deploy/demo.env.')
    if not os.environ.get('FOUNDRY_MODEL'):
        raise ValueError('Set FOUNDRY_MODEL to your deployed model name.')
    path=Path(os.environ.get('TEAM_PLAYER_MATCH',''))
    if not path.is_file():raise ValueError('TEAM_PLAYER_MATCH must point to an existing tracking recording.')
    path=path.resolve()
    if path.parent!=ROOT/'matches' or not path.name.startswith('match_') or path.suffix!='.jsonl':
        raise ValueError('For this launcher, put the public JSONL recording in matches/ with a match_ filename.')
    with path.open() as f:meta=json.loads(next(f))
    from team_player.schema import validate_metadata
    validate_metadata(meta)
    ids={p['id'] for p in meta['players']}
    player=os.environ.get('TEAM_PLAYER_FAVOURITE') or next(p['id'] for p in meta['players'] if p['role']=='ST')
    if player not in ids:raise ValueError(f'{player} is not in this recording. Change TEAM_PLAYER_FAVOURITE.')
    os.environ['TEAM_PLAYER_MATCH']=str(path)
    os.environ['TEAM_PLAYER_SEASON']=str(meta.get('season','2026-27'))
    return path,player,meta

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',type=int,default=int(os.environ.get('TEAM_PLAYER_PORT','8001')))
    p.add_argument('--check',action='store_true',help='Validate configuration and recording without calling Azure')
    p.add_argument('--no-browser',action='store_true');a=p.parse_args()
    try:
        path,player,meta=prepare()
        if not 1<=a.port<=65535:raise ValueError('Port must be between 1 and 65535.')
    except (ValueError,OSError,StopIteration) as e:
        print(f'Demo setup: {e}',file=sys.stderr);return 2
    url=f'http://127.0.0.1:{a.port}/?'+urlencode({'demo':path.name,'player':player})
    print(f"Recording: {path.name} | season {meta.get('season')} | follow {player}",flush=True)
    print('Azure AI: '+os.environ['FOUNDRY_MODEL'],flush=True)
    print('Open: '+url,flush=True)
    if a.check:
        print('Configuration and recording checked. Azure connectivity has not been tested.');return 0
    try:
        with socket.socket() as sock:sock.bind(('127.0.0.1',a.port))
    except OSError:
        print(f'Port {a.port} is already in use. Stop the previous viewer or add --port {a.port+1}.',file=sys.stderr);return 2
    if not a.no_browser:
        def open_when_ready():
            for _ in range(60):
                try:
                    with urlopen(f'http://127.0.0.1:{a.port}/health',timeout=1) as response:
                        if response.status==200:webbrowser.open(url);return
                except OSError:time.sleep(1)
        threading.Thread(target=open_when_ready,daemon=True).start()
    import uvicorn
    uvicorn.run('team_player.app.server:app',host='127.0.0.1',port=a.port)
    return 0
if __name__=='__main__':raise SystemExit(main())
