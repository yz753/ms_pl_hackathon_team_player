import json
from pathlib import Path
import tempfile
import unittest
from team_player.simulator.match import generate_match
from team_player.app.match_replay import create_match_replay


class MatchReplayTests(unittest.TestCase):
    def test_optional_html_keeps_tracking_identical_and_old_layout(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plain=root/'plain';preview=root/'preview'
            plain.mkdir();preview.mkdir()
            generate_match((0,42,2,5,str(plain)))
            generate_match((0,42,2,5,str(preview),True))
            self.assertFalse(list(plain.glob('*.html')))
            self.assertEqual((plain/'match_0000.jsonl').read_bytes(),(preview/'match_0000.jsonl').read_bytes())
            text=(preview/'match_0000.html').read_text()
            self.assertIn('Tracking replay',text)
            self.assertIn('id="seek"',text)
            self.assertIn('id="selected"',text)
            payload=json.loads(text.split('<script id="payload" type="application/json">')[1].split('</script>')[0])
            self.assertEqual(len(payload['frames']),11)
            self.assertTrue(payload['analysis'])
            self.assertNotIn('score',payload['frames'][0])
            self.assertNotIn('striker_style',text)

    def test_existing_replay_can_be_created_without_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);generate_match((0,7,1,5,str(root)))
            output=root/'preview.html'
            create_match_replay(root/'match_0000.jsonl',output)
            with self.assertRaises(ValueError):create_match_replay(root/'match_0000.jsonl',output)

    def test_empty_input_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'empty.jsonl';source.write_text('')
            with self.assertRaises(ValueError):create_match_replay(source,root/'preview.html')
            self.assertFalse((root/'preview.html').exists())
