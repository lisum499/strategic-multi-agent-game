import json
import unittest
from pathlib import Path

from strategic_game import protocol, rules, runtime


class CapabilityRulesTest(unittest.TestCase):
    def scenario(self):
        return json.loads(Path(runtime.DEFAULT_SCENARIO).read_text(encoding='utf-8'))

    def test_nuclear_threshold_and_second_strike_are_hard_constraints(self):
        scenario = self.scenario()
        scenario['countries']['C1']['conventional'] = 39
        with self.assertRaises(protocol.V6ValidationError):
            protocol.validate_scenario(scenario)

        scenario = self.scenario()
        scenario['countries']['C1'].update({'conventional': 90, 'nuclear': 61})
        state = protocol.validate_scenario(scenario)
        self.assertEqual(state['countries']['C1']['nuclear_status'], '二次核打击')

    def test_nuclear_status_is_derived_from_initial_force_values(self):
        cases = (
            (0, '无'),
            (20, '无'),
            (21, '有'),
            (59, '有'),
            (60, '有'),
            (61, '二次核打击'),
            (100, '二次核打击'),
        )
        for nuclear, expected in cases:
            with self.subTest(nuclear=nuclear):
                scenario = self.scenario()
                scenario['countries']['C1'].update({'conventional': 90, 'nuclear': nuclear})
                state = protocol.validate_scenario(scenario)
                self.assertEqual(state['countries']['C1']['nuclear_status'], expected)

    def test_delivery_range_filters_actual_nuclear_use(self):
        scenario = self.scenario()
        scenario['countries']['C1'].update({'nuclear': 21})
        scenario['conflicts'] = {'C1': {'C2': 45, 'C3': 45}}
        state = protocol.validate_scenario(scenario)
        self.assertIn('有限核武器使用', {x['action_id'] for x in protocol.legal_actions(state, 'C1', 'C2')})
        self.assertNotIn('有限核武器使用', {x['action_id'] for x in protocol.legal_actions(state, 'C1', 'C3')})

        scenario = self.scenario()
        scenario['countries']['C1'].update({'nuclear': 61})
        scenario['conflicts'] = {'C1': {'C3': 45}}
        state = protocol.validate_scenario(scenario)
        self.assertIn('有限核武器使用', {x['action_id'] for x in protocol.legal_actions(state, 'C1', 'C3')})

    def test_nuclear_status_limits_action_levels(self):
        expected = (
            (20, set(), {'核信号释放', '有限核武器使用'}),
            (21, {'核信号释放', '有限核武器使用'}, {'升级性核行动'}),
            (61, {'有限核武器使用', '升级性核行动'}, {'核信号释放'}),
        )
        for nuclear, present, absent in expected:
            with self.subTest(nuclear=nuclear):
                scenario = self.scenario()
                scenario['countries']['C1'].update({'conventional': 90, 'nuclear': nuclear})
                scenario['conflicts'] = {'C1': {'C2': 100}}
                state = protocol.validate_scenario(scenario)
                menu = {x['action_id'] for x in protocol.legal_actions(state, 'C1', 'C2')}
                self.assertTrue(present <= menu)
                self.assertTrue(absent.isdisjoint(menu))

    def test_second_strike_is_automatic_and_audited(self):
        scenario = self.scenario()
        scenario['countries']['C1'].update({'conventional': 90, 'nuclear': 61})
        scenario['countries']['C2'].update({'conventional': 90, 'nuclear': 61})
        scenario['conflicts'] = {'C2': {'C1': 57.5}}
        state = protocol.validate_scenario(scenario)
        state['round'] = 1
        decisions = {cid: {'actions': []} for cid in state['countries']}
        decisions['C2']['actions'] = [{
            'target_id': 'C1', 'action_id': '升级性核行动',
            'conventional': 0, 'nuclear': 1,
        }]
        _, record = rules.settle_round(state, decisions)
        self.assertEqual(record['automatic_second_strikes'], [{
            'source': 'C1', 'target': 'C2', 'action_id': '升级性核行动',
            'nuclear': 15.25, 'replaced_action_id': None,
        }])
        self.assertEqual(record['pairs'][0]['actions']['C1'], '升级性核行动')

    def test_limited_nuclear_use_does_not_trigger_second_strike(self):
        scenario = self.scenario()
        scenario['countries']['C1'].update({'conventional': 90, 'nuclear': 61})
        scenario['conflicts'] = {'C2': {'C1': 45}}
        state = protocol.validate_scenario(scenario)
        state['round'] = 1
        decisions = {cid: {'actions': []} for cid in state['countries']}
        decisions['C2']['actions'] = [{
            'target_id': 'C1', 'action_id': '有限核武器使用',
            'conventional': 0, 'nuclear': 1,
        }]
        new_state, record = rules.settle_round(state, decisions)
        self.assertEqual(record['automatic_second_strikes'], [])
        self.assertIn('C2', new_state['nuclear_used'])

    def test_region_is_required_and_distance_matrix_is_rejected(self):
        scenario = self.scenario()
        del scenario['countries']['C1']['region']
        with self.assertRaises(protocol.V6ValidationError):
            protocol.validate_scenario(scenario)
        scenario = self.scenario()
        scenario['countries']['C1']['region'] = 'region_5'
        with self.assertRaises(protocol.V6ValidationError):
            protocol.validate_scenario(scenario)
        scenario = self.scenario()
        scenario['distance_bands'] = {}
        with self.assertRaises(protocol.V6ValidationError):
            protocol.validate_scenario(scenario)


if __name__ == '__main__':
    unittest.main()
