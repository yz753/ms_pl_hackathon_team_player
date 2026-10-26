import unittest,json
from pathlib import Path
import numpy as np
from team_player.simulator.qa.outcome_trials import vacated_marker_space

class WingerAttributionTests(unittest.TestCase):
 def test_disputed_recording_does_not_attribute_a03_space_to_a01_a07(self):
  data=json.loads((Path(__file__).parent/'fixtures/winger_false_positive.json').read_text())
  ids=[p['id'] for p in data['436.6']]
  before=np.array([[p['x'],p['y']] for p in data['436.6']]);after=np.array([[p['x'],p['y']] for p in data['439.6']])
  self.assertFalse(vacated_marker_space(before,after,list(range(1,11)),[ids.index('A01'),ids.index('A07')],ids.index('B10')))
 def test_marker_really_leaving_beneficiary_can_qualify(self):
  before=np.array([[60.,34.],[65,45],[61,34],[80,0]])
  after=before.copy();after[2]=[70,45]
  self.assertTrue(vacated_marker_space(before,after,[2,3],[2],0))
 def test_teammate_own_movement_is_not_marker_withdrawal(self):
  before=np.array([[60.,34.],[65,45],[61,34],[80,0]])
  after=before.copy();after[0]=[54,34]
  self.assertFalse(vacated_marker_space(before,after,[2,3],[2],0))
