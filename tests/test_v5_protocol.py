import copy
import json
import unittest
from strategic_game.protocol import (V5ValidationError, build_prompts, legal_actions,
    make_fallback, validate_decision, validate_scenario)

class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.state = {'rules_version': 'v5.0', 'round': 1,
            'countries': {'A': {'name': 'A', 'territory': 50, 'conventional': 80, 'nuclear': 20},
                          'B': {'name': 'B', 'territory': 50, 'conventional': 60, 'nuclear': 0}},
            'conflicts': {'A': {'B': 45}}, 'nuclear_used': []}
        self.situation = {'round': 1, 'country_id': 'A', 'snapshot_hash': 'hash', 'state': self.state}
    def test_scenario_version_and_initial_nuclear(self):
        validate_scenario(self.state)
        bad = copy.deepcopy(self.state); bad['countries']['B']['nuclear'] = 1
        with self.assertRaises(V5ValidationError): validate_scenario(bad)
        bad = copy.deepcopy(self.state); bad['rules_version'] = 'v4.1'
        with self.assertRaises(V5ValidationError): validate_scenario(bad)
    def test_threshold_uses_existing_level(self):
        menu = {v['action_id'] for v in legal_actions(self.state, 'A', 'B')}
        self.assertIn('有限核武器使用', menu); self.assertNotIn('升级性核行动', menu)
        self.state['conflicts']['A']['B'] = 44
        self.assertNotIn('有限核武器使用', {v['action_id'] for v in legal_actions(self.state, 'A', 'B')})
    def test_deescalation_when_other_side_has_conflict(self):
        self.state['conflicts'] = {'B': {'A': 1}}
        self.assertIn('外交降级', {v['action_id'] for v in legal_actions(self.state, 'A', 'B')})
        self.state['conflicts'] = {}
        self.assertNotIn('外交降级', {v['action_id'] for v in legal_actions(self.state, 'A', 'B')})
    def test_package_and_allocation(self):
        data = make_fallback(self.situation)
        data['actions'] = [{'target_id':'B','action_id':'有限核武器使用','conventional':10,'nuclear':1}]
        self.assertEqual(validate_decision(data, self.situation), data)
        data['actions'][0]['nuclear'] = 0
        with self.assertRaises(V5ValidationError): validate_decision(data, self.situation)
        data['actions'][0]['nuclear'] = 100
        with self.assertRaises(V5ValidationError): validate_decision(data, self.situation)
    def test_borrowed_funds_only_provisional(self):
        data = make_fallback(self.situation)
        data['borrows'] = [{'from_id':'B','conventional':10,'nuclear':0}]
        data['actions'] = [{'target_id':'B','action_id':'军事示威','conventional':90,'nuclear':0}]
        validate_decision(data, self.situation)
    def test_prompt_has_rules_and_alliance_contract(self):
        _, user = build_prompts(self.situation)
        for key in ('offer_return','counter_offer','军事示威','借入方','行动前'):
            self.assertIn(key, user)
    def test_dynamic_actions(self):
        self.state['rule_config'] = {'actions': {'保持不变':'0','经济施压':'4'}}
        result = legal_actions(self.state, 'A', 'B')
        self.assertEqual(result, [{'action_id':'保持不变','delta':0.0},{'action_id':'经济施压','delta':4.0}])

if __name__ == '__main__': unittest.main()
