import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from strategic_game import cli, protocol, runtime, rules


class CountryTagsTest(unittest.TestCase):
    def scenario(self):
        scenario = json.loads(Path(runtime.DEFAULT_SCENARIO).read_text(encoding='utf-8'))
        scenario['countries']['C1']['tags'] = {'strategic_culture': '现实主义', 'foreign_policy': '维持现状', 'ideology': 3}
        scenario['countries']['C2']['tags'] = {'war_aim': '复仇'}
        return scenario

    def test_resolves_only_explicit_tags_and_model_sees_only_own(self):
        state = protocol.validate_scenario(self.scenario())
        state['round'] = 1
        own = state['countries']['C1']['tag_knowledge']
        self.assertEqual(set(own['dimensions']), {'strategic_culture', 'foreign_policy', 'ideology'})
        self.assertEqual(own['dimensions']['ideology']['value'], 3)
        self.assertIn('国家利益', own['dimensions']['strategic_culture']['definition'])
        self.assertIn('草案', own['status'])
        self.assertEqual(len(own['sha256']), 64)
        view = runtime.situation_for(state, 'C1', [], rules.DEFAULT_CONFIG)
        _, prompt = protocol.build_prompts(view)
        self.assertIn('现实主义', prompt)
        self.assertNotIn('复仇', prompt)
        self.assertNotIn('tag_knowledge', view['state']['countries']['C2'])
        self.assertNotIn('tag_knowledge', json.dumps(runtime.public_state(state), ensure_ascii=False))
        self.assertNotIn('tag_knowledge', state['countries']['C3'])

    def test_unknown_values_rejected_and_no_implicit_tags(self):
        for tags in ({'unknown':'foo'}, {'strategic_culture':'不存在'}, {'ideology':6}, {'ideology':True}, {'ideology':'侧重程度'}, ['现实主义']):
            with self.subTest(tags=tags):
                scenario=self.scenario(); scenario['countries']['C1']['tags']=tags
                with self.assertRaises(protocol.V5ValidationError):
                    protocol.validate_scenario(scenario)
        state=protocol.validate_scenario(json.loads(Path(runtime.DEFAULT_SCENARIO).read_text()))
        self.assertTrue(all('tag_knowledge' not in country for country in state['countries'].values()))

    def test_snapshot_replay_does_not_read_changed_dictionary(self):
        with tempfile.TemporaryDirectory() as temp:
            scenario=Path(temp)/'scenario.json'; scenario.write_text(json.dumps(self.scenario(),ensure_ascii=False))
            output=Path(temp)/'run'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['run','--provider','mock','--rounds','1','--scenario',str(scenario),'--out',str(output)]),0)
            data=json.loads((output/'run.json').read_text())
            self.assertEqual(data['initial_state']['countries']['C1']['tag_knowledge']['dimensions']['ideology']['value'],3)
            public=(output/'public.json').read_text()
            self.assertNotIn('现实主义',public)
            self.assertNotIn('复仇',public)
            with patch('strategic_game.protocol.load_dictionary', side_effect=AssertionError('unexpected dictionary load')):
                self.assertTrue(runtime.replay(data)[0])


if __name__ == '__main__':
    unittest.main()
