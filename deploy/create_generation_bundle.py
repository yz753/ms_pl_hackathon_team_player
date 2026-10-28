"""Source-only Azure Function bundle; uses the repository simulator unchanged."""
import argparse,json,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from team_player.cloud.generation_spec import SOURCE_FILES,build_identity

def build(output):
    modules=set(SOURCE_FILES)|{'__init__.py','cloud/__init__.py','config/__init__.py','utils/__init__.py','simulator/__init__.py','cloud/generation_store.py','cloud/generation_worker.py','cloud/queue_runner.py'}
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for name in sorted(modules):z.write(ROOT/'team_player'/name,'team_player/'+name)
        for name in ('function_app.py','host.json','requirements.txt'):z.write(ROOT/'deploy/generation'/name,name)
        z.writestr('team_player/generation_build.json',json.dumps(build_identity(),indent=2))
    return output
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',default=str(ROOT/'deploy/generation-worker.zip'));a=p.parse_args();print(build(a.out))
