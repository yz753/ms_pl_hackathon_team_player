"""Public reproducibility contract; no simulator personality data in requests."""
import hashlib,json,random
from pathlib import Path
from team_player.config.league import TEAMS,CURRENT_SEASON
NUMPY_VERSION='2.5.4'
SOURCE_FILES=('schema.py','config/league.py','config/styles.py','utils/geometry.py',
              'simulator/engine.py','simulator/match.py','simulator/league.py','simulator/season.py',
              'cloud/generation_spec.py','cloud/generate_match.py')

def build_identity():
    root=Path(__file__).resolve().parents[1]
    manifest=root/'generation_build.json'
    if manifest.exists():return json.loads(manifest.read_text())
    hashes={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in SOURCE_FILES}
    revision=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    return {'revision':revision,'source_sha256':hashes,'numpy':NUMPY_VERSION,'python':'3.12'}

def make_spec(seed,home=None,away=None):
    if type(seed) is not int or not 0<=seed<=4294967295:raise ValueError('Seed must be a whole number from 0 to 4294967295')
    if CURRENT_SEASON!='2026-27':raise ValueError('Generation is restricted to 2026-27')
    for team in (home,away):
        if team is not None and team not in TEAMS:raise ValueError('Choose a team from A to T')
    if home is not None and home==away:raise ValueError('Choose two different teams')
    rng=random.Random(seed)
    home=home or rng.choice([t for t in TEAMS if t!=away])
    away=away or rng.choice([t for t in TEAMS if t!=home])
    spec={'version':1,'season':'2026-27','seed':seed,'home':home,'away':away,
          'duration_s':600,'sample_hz':10,'build':build_identity()}
    spec['id']=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:32]
    return spec

def validate_spec(spec):
    expected=make_spec(spec['seed'],spec['home'],spec['away'])
    if spec!=expected:raise ValueError('Reproduction settings or source version differ; use the same deployed source and dependencies')
    return spec

def recording_name(spec):return f"generated_{spec['id']}_{spec['home']}-{spec['away']}.jsonl.gz"
