"""Plain-English summaries: grounding rules, audience levels and the Narrator agent tools."""
import asyncio
import sys
import types
import unittest
from unittest.mock import patch
from team_player.agents.narration import check_grounding, local_summary, numbers_in, AUDIENCES
from team_player.analyser.presentation import render_overlay

CARD = {'id': 'c1', 'player': 'A09', 'title': 'Dropping into the pocket', 'topic': 'support', 'value': 18., 'unit': 'm',
        'fan': 'A09 has come short and found a 34 m forward pass.',
        'analyst': '18 m short of the last defender; best forward pass 34 m, 3 opponents bypassed, xT +0.041.',
        'caveat': 'The box can be empty while he links play.'}
SNAP = {'t': 30., 'episode_id': 2, 'eligible': True, 'player': {'player': 'A09'}, 'audience': 'fan', 'cards': [CARD],
        'verdicts': [{'kind': 'striker_drop', 't': 20., 'benefit': 'Linked play with a 34 m pass.', 'cost': 'He was NOT in the box.',
                      'outcome': 'Net value +0.052 expected goals', 'flag': True}]}
FACTS = [{'id': 'a', 'text': 'Striker dropped deep: 0.120 xG in the next 15 s (n=24), versus 0.050 (n=16).'}]


class GroundingTests(unittest.TestCase):
    def test_accepts_exact_and_percent_numbers(self):
        self.assertEqual(check_grounding('When he dropped, the team made 0.12 xG, about 12% per moment, from 24 cases.', FACTS, 'fan'), [])

    def test_rejects_invented_number_and_causal_claim(self):
        problems = check_grounding('His drop caused 0.30 xG.', FACTS, 'fan')
        self.assertTrue(any('0.3' in p for p in problems));self.assertTrue(any('causal' in p for p in problems))

    def test_non_analysts_must_keep_a_number(self):
        self.assertIn('keep at least one number', check_grounding('He helped a lot.', FACTS, 'newcomer'))

    def test_local_summaries_for_every_audience_keep_numbers(self):
        for audience in AUDIENCES:
            s = local_summary('overlay', SNAP, audience, [CARD])
            self.assertTrue(numbers_in(s['text']), audience)
        self.assertIn('xT =', local_summary('overlay', {**SNAP, 'verdicts': []}, 'newcomer', [{**CARD, 'fan': CARD['analyst']}])['text'])
        overlay = render_overlay({**SNAP, 'audience': 'newcomer'}, ['c1'])
        self.assertEqual(overlay['narrative']['method'], 'local_rules')


class NarratorAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_narrator_retry_and_grounded_acceptance(self):
        fake = types.ModuleType('agent_framework')
        fake.tool = lambda **kw: (lambda f: f)
        calls = []
        class FakeAgent:
            def __init__(self, **kw): self.tools = kw['tools']
            async def run(self, *a):
                self.tools[0]()
                calls.append(self.tools[1]('Big link', 'He made 0.99 xG.'))          # invented -> rejected
                calls.append(self.tools[1]('A useful link', 'He found a 34 m pass that moved the ball 0.041 closer to a goal, though the box can be empty.'))
        fake.Agent = FakeAgent
        from team_player.agents.foundry import FoundryDirector
        director = FoundryDirector.__new__(FoundryDirector);director.client = object()
        with patch.dict(sys.modules, {'agent_framework': fake}):
            out = await director.narrate('overlay', SNAP, 'fan', [CARD])
        self.assertTrue(calls[0].startswith('Rejected'));self.assertEqual(calls[1], 'Accepted. Stop.')
        self.assertEqual(out['method'], 'microsoft_foundry_agent');self.assertIn('34 m', out['text'])

    async def test_failure_falls_back_to_local_summary(self):
        from team_player.agents.foundry import FoundryDirector
        director = FoundryDirector.__new__(FoundryDirector);director.client = object()
        async def boom(*a, **k): raise RuntimeError('no model')
        director.narrate = boom
        out = await director._narrate_or_local('overlay', SNAP, 'newcomer', [CARD])
        self.assertTrue(out['method'].startswith('local_rules'));self.assertTrue(numbers_in(out['text']))


if __name__ == '__main__':
    unittest.main()
