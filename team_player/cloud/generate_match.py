"""Identical generation entry point for Azure and local reproduction.

python -m team_player.cloud.generate_match --seed 732 --home A --away B --output generated.jsonl.gz
"""
import argparse,gzip,hashlib,json,os,sys,tempfile
from pathlib import Path
from team_player.cloud.generation_spec import make_spec,validate_spec,NUMPY_VERSION

def generate(spec,output):
    import numpy as np
    from team_player.simulator.season import fixtures,simulate_fixture
    validate_spec(spec)
    if sys.version_info[:2]!=(3,12) or np.__version__!=NUMPY_VERSION:
        raise ValueError(f'Reproduction requires Python 3.12 and numpy=={NUMPY_VERSION}')
    fx=next(dict(f) for f in fixtures('2026-27') if f['home']==spec['home'] and f['away']==spec['away'])
    fx.update(seed=spec['seed'],fixture_id='generated-'+spec['id'])
    meta,frames,_private=simulate_fixture(fx,duration=600,hz=10)
    # Private profiles never enter the output or job status.
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256();count=0
    fd,tmp=tempfile.mkstemp(dir=output.parent,suffix='.part')
    try:
        with os.fdopen(fd,'wb') as raw:
            with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as zipped:
                for record in (meta,):
                    line=(json.dumps(record,separators=(',',':'))+'\n').encode();digest.update(line);zipped.write(line)
                for frame in frames:
                    line=(json.dumps(frame,separators=(',',':'))+'\n').encode();digest.update(line);zipped.write(line);count+=1
        os.replace(tmp,output)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return {'sha256_positions':digest.hexdigest(),'frames':count,'duration_s':600}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seed',type=int,required=True);p.add_argument('--home');p.add_argument('--away');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    spec=make_spec(a.seed,a.home,a.away)
    result=generate(spec,a.output);print(json.dumps(result))
