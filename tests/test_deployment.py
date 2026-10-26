import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig

class DeploymentTests(unittest.TestCase):
    def test_allowlisted_bundle_is_importable_without_simulator(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('bundle_builder',root/'deploy/create_bundle.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d);path=temp/'match.jsonl'
            sim=MatchSimulator(SimulationConfig(duration=.2));meta=sim.metadata();meta['input_type']='positions_only'
            path.write_text('\n'.join(json.dumps(x) for x in [meta,*sim.position_frames()]))
            archive=builder.build_bundle(temp/'app.zip',path)
            with zipfile.ZipFile(archive) as z:
                self.assertFalse(any(n.startswith('team_player/simulator/') for n in z.namelist()))
                self.assertNotIn('team_player/config/styles.py',z.namelist())
                self.assertFalse(any('private' in n for n in z.namelist()))
                z.extractall(temp/'isolated')
            try:import fastapi
            except ImportError:self.skipTest('Web dependencies not installed')
            result=subprocess.run([sys.executable,'-c',"import importlib.util; import team_player.app.server; assert importlib.util.find_spec('team_player.simulator') is None"],
                cwd=temp/'isolated',env={**os.environ,'PYTHONPATH':str(temp/'isolated')},capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_builder_rejects_engine_fields(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('bundle_builder',root/'deploy/create_bundle.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d);sim=MatchSimulator(SimulationConfig(duration=.1));meta=sim.metadata();meta['input_type']='positions_only'
            frame=next(sim.position_frames());frame['ball']['owner']='A01'
            path=temp/'tracking.jsonl';path.write_text(json.dumps(meta)+'\n'+json.dumps(frame)+'\n')
            with self.assertRaises(ValueError):builder.build_bundle(temp/'app.zip',path)

if __name__=='__main__':unittest.main()
