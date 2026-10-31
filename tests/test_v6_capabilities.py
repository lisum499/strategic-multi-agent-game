import json
import unittest
from pathlib import Path

from strategic_game import capabilities, protocol, rules, runtime


class CapabilityRulesTest(unittest.TestCase):
    def scenario(self):
        return json.loads(Path(runtime.DEFAULT_SCENARIO).read_text(encoding='utf-8'))

    def test_nuclear_threshold_and_second_strike_are_hard_constraints(self):
        scenario = self.scenario()
        scenario['countries']['C1']['conventional'] = 39
        with self.assertRaises(protocol.V6ValidationError):
            protocol.validate_scenario(scenario)

        scenario = self.scenario()
        scenario['countries']['C1'].update({'conventional': 90, 'nuclear': 41})
        state = protocol.validate_scenario(scenario)
        self.assertNotIn('nuclear_status', state['countries']['C1'])

    def test_actual_nuclear_use_has_no_geographic_filter(self):
        scenario = self.scenario()
        scenario['countries']['C1'].update({'nuclear': 40})
        scenario['conflicts'] = {'C1': {'C2': 45, 'C3': 45}}
        state = protocol.validate_scenario(scenario)
        self.assertNotIn('有限核武器使用', {x['action_id'] for x in protocol.legal_actions(state, 'C1', 'C2')})
        self.assertNotIn('有限核武器使用', {x['action_id'] for x in protocol.legal_actions(state, 'C1', 'C3')})
        scenario = self.scenario()
        scenario['countries']['C1'].update({'nuclear': 41})
        scenario['conflicts'] = {'C1': {'C3': 45}}
        state = protocol.validate_scenario(scenario)
        self.assertIn('有限核武器使用', {x['action_id'] for x in protocol.legal_actions(state, 'C1', 'C3')})

    def test_current_nuclear_limits_action_levels(self):
        expected = (
            (0, set(), {'核信号释放', '有限核武器使用'}),
            (1, {'核信号释放'}, {'有限核武器使用', '升级性核行动'}),
            (40, {'核信号释放'}, {'有限核武器使用', '升级性核行动'}),
            (41, {'核信号释放', '有限核武器使用', '升级性核行动'}, set()),
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

    def test_permissions_change_immediately_with_current_nuclear(self):
        scenario = self.scenario()
        scenario['countries']['C1'].update({'conventional': 90, 'nuclear': 40})
        scenario['conflicts'] = {'C1': {'C2': 100}}
        state = protocol.validate_scenario(scenario)
        self.assertNotIn('有限核武器使用', {x['action_id'] for x in protocol.legal_actions(state, 'C1', 'C2')})
        state['countries']['C1']['nuclear'] = 41
        self.assertIn('有限核武器使用', {x['action_id'] for x in protocol.legal_actions(state, 'C1', 'C2')})
        self.assertTrue(capabilities.has_second_strike(state['countries']['C1']))
        state['countries']['C1']['nuclear'] = 40
        self.assertFalse(capabilities.has_second_strike(state['countries']['C1']))

    def test_second_strike_is_automatic_and_audited(self):
        scenario = self.scenario()
        scenario['countries']['C1'].update({'conventional': 90, 'nuclear': 41})
        scenario['countries']['C2'].update({'conventional': 90, 'nuclear': 41})
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
            'nuclear': 10.25, 'replaced_action_id': None,
        }])
        self.assertEqual(record['pairs'][0]['actions']['C1'], '升级性核行动')

    def test_limited_nuclear_use_does_not_trigger_second_strike(self):
        scenario = self.scenario()
        scenario['countries']['C1'].update({'conventional': 90, 'nuclear': 41})
        scenario['countries']['C2'].update({'conventional': 90, 'nuclear': 41})
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

    def test_removed_geography_fields_are_rejected(self):
        scenario = self.scenario()
        scenario['countries']['C1']['region'] = 'region_1'
        with self.assertRaises(protocol.V6ValidationError):
            protocol.validate_scenario(scenario)
        scenario = self.scenario()
        scenario['distance_bands'] = {}
        with self.assertRaises(protocol.V6ValidationError):
            protocol.validate_scenario(scenario)


if __name__ == '__main__':
    unittest.main()
