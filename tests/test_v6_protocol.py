import copy
import json
import unittest
from strategic_game.protocol import (V6ValidationError, build_prompts, legal_actions,
    make_fallback, validate_decision, validate_scenario)

class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.raw_state = {'rules_version': 'v6.0', 'round': 1,
            'countries': {'A': {'name': 'A', 'strategic_culture': '现实主义', 'ideology_focus': 2, 'economic_presence': 50, 'military_presence': 50,
                                'soft_presence': 50, 'conventional': 80, 'nuclear': 41},
                          'B': {'name': 'B', 'strategic_culture': '民族主义', 'ideology_focus': 4, 'economic_presence': 50, 'military_presence': 50,
                                'soft_presence': 50, 'conventional': 60, 'nuclear': 0}},
            'conflicts': {'A': {'B': 45}}, 'nuclear_used': []}
        self.state = validate_scenario(self.raw_state)
        self.state['round'] = 1
        self.situation = {'round': 1, 'country_id': 'A', 'snapshot_hash': 'hash', 'state': self.state}
    def test_scenario_version_and_initial_nuclear(self):
        self.assertNotIn('nuclear_status', self.state['countries']['A'])
        bad = copy.deepcopy(self.raw_state); bad['countries']['B'].update({'conventional': 39, 'nuclear': 1})
        with self.assertRaises(V6ValidationError): validate_scenario(bad)
        bad = copy.deepcopy(self.raw_state); bad['countries']['A']['conventional'] = 39
        with self.assertRaises(V6ValidationError): validate_scenario(bad)
        bad = copy.deepcopy(self.raw_state); bad['countries']['A']['territory'] = 50
        with self.assertRaises(V6ValidationError): validate_scenario(bad)
        bad = copy.deepcopy(self.raw_state); bad['countries']['A']['nuclear_status'] = '有'
        with self.assertRaises(V6ValidationError): validate_scenario(bad)
        bad = copy.deepcopy(self.raw_state); bad['countries']['A']['tags'] = {'nuclear_status': '有'}
        with self.assertRaises(V6ValidationError): validate_scenario(bad)
        bad = copy.deepcopy(self.raw_state); bad['rules_version'] = 'v4.1'
        with self.assertRaises(V6ValidationError): validate_scenario(bad)
        bad = copy.deepcopy(self.raw_state); del bad['countries']['A']['strategic_culture']
        with self.assertRaises(V6ValidationError): validate_scenario(bad)
        bad = copy.deepcopy(self.raw_state); del bad['countries']['A']['ideology_focus']
        with self.assertRaises(V6ValidationError): validate_scenario(bad)
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
        with self.assertRaises(V6ValidationError): validate_decision(data, self.situation)
        data['actions'][0]['nuclear'] = 100
        with self.assertRaises(V6ValidationError): validate_decision(data, self.situation)
    def test_only_due_cross_round_request_is_provisional(self):
        data = make_fallback(self.situation)
        data['borrows'] = [{'from_id':'B','conventional':10,'nuclear':0}]
        data['actions'] = [{'target_id':'B','action_id':'军事示威','conventional':90,'nuclear':0}]
        with self.assertRaises(V6ValidationError): validate_decision(data, self.situation)
        request={'id':'req-1','lender_id':'B','borrower_id':'A','conventional':10,'nuclear':0,
                 'status':'pending','response_due_round':1}
        self.situation['state']['alliance_info']={'loan_requests':[request]}
        data = make_fallback(self.situation)
        data['actions'] = [{'target_id':'B','action_id':'军事示威','conventional':90,'nuclear':0,
                            'requires_loan_request_ids':['req-1']}]
        validate_decision(data, self.situation)
    def test_prompt_has_rules_and_alliance_contract(self):
        _, user = build_prompts(self.situation)
        for key in ('offer_return','counter_offer','军事示威','借入方','本轮开始'):
            self.assertIn(key, user)
    def test_dynamic_actions(self):
        self.state['rule_config'] = {'actions': {'保持不变':'0','经济施压':'4'}}
        result = legal_actions(self.state, 'A', 'B')
        self.assertEqual(result, [{'action_id':'保持不变','delta':0.0},{'action_id':'经济施压','delta':4.0}])

if __name__ == '__main__': unittest.main()
