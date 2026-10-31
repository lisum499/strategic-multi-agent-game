"""Sealed v6 runtime, private projections, checkpoints and reproducible replay."""
from __future__ import annotations
import copy, hashlib, html, json, marshal
from pathlib import Path
from . import protocol as protocol, rules as rules, alliances as alliances, diplomacy as diplomacy, knowledge as knowledge

DEFAULT_SCENARIO = str(Path(__file__).parent / 'scenarios' / 'three_states.json')

def public_state(state):
    return {'round': state['round'], 'countries': {k: {'name': v['name'],
                'strategic_culture': v['strategic_culture'],
                'ideology_focus': v['ideology_focus'],
                'nuclear': v['nuclear'],
                'economic_presence': v['economic_presence'], 'military_presence': v['military_presence'],
                'soft_presence': v['soft_presence'], 'global_presence': v['global_presence'],
                'alive': v['alive']} for k,v in state['countries'].items()},
            'conflicts': copy.deepcopy(state.get('conflicts', {})),
            'pressure_streaks': copy.deepcopy(state.get('pressure_streaks', {})),
            'nuclear_used': copy.deepcopy(state.get('nuclear_used', [])),
            'alliances': alliances.view(state),
            'diplomacy': diplomacy.view(state)}

def situation_for(state, country, history, config):
    public = public_state(state)
    # Opponent military strength can reveal confidential alliance transfers.
    private_state = copy.deepcopy(public)
    private_state['countries'][country] = copy.deepcopy(state['countries'][country])
    private_state['rule_config'] = copy.deepcopy(config)
    # Opponent forces are unknown, rather than fabricated zero values.
    private_state['alliance_info'] = alliances.view(state, country)
    own_observation = state.get('private_threat_assessments', {}).get(country)
    if own_observation is not None:
        private_state['last_threat_assessment'] = copy.deepcopy(own_observation)
    private_state['world_context'] = state.get('world_context', '')
    private_state['history'] = [{
        'round': row['round'],
        'unverified_declarations': copy.deepcopy(row.get('unverified_declarations', row.get('declarations', {}))),
        'confirmed_facts': copy.deepcopy(row.get('confirmed_facts', {
            'executed_actions': row.get('actions', {}),
            'presence_transfers': row.get('presence_transfers', []),
            'automatic_second_strikes': row.get('automatic_second_strikes', []),
            'outcome': row.get('outcome', {}),
        })),
    } for row in history]
    situation = {'round': state['round'], 'country_id': country, 'state': private_state}
    situation['snapshot_hash'] = protocol.snapshot_hash(situation)
    return situation

def decisions_for(state, provider, history, config, retries=3, no_prompts=False, calls_sink=None, on_call=None):
    """Collect sealed choices. calls_sink is updated before each provider call.

    A caller retaining this list can checkpoint partial attempts after an
    interruption, even when this function never returns.
    """
    decisions, calls, fallback = {}, calls_sink if calls_sink is not None else [], 0
    for cid in sorted(state['countries']):
        if not state['countries'][cid]['alive']: continue
        situation = situation_for(state, cid, history, config)
        system, user = protocol.build_prompts(situation)
        last = None
        for attempt in range(retries + 1):
            actual_user = user
            if last:
                actual_user += ('\n上次输出未通过校验：' + last
                                + '\n请重新输出完整JSON对象，不要只返回修正字段。'
                                  '确保全部联盟、借调和外交协议操作数组都存在；actions中每个target_id最多出现一次，'
                                  '同一目标的备选行动必须删除，只保留最终选择。')
            call = {'round': state['round'], 'country_id': cid, 'attempt': attempt + 1,
                    'status': 'started', 'error': None,
                    **({} if no_prompts else {'system': system, 'user': actual_user})}
            calls.append(call)
            if on_call is not None: on_call()
            try:
                if provider.name == 'mock':
                    data = protocol.make_fallback(situation)
                    data.update({'assessment':'离线流程测试', 'forecast':'对方行动未知', 'rationale':'离线测试'})
                    targets = [x for x in sorted(state['countries']) if x != cid and state['countries'][x]['alive']]
                    if targets:
                        data['actions'] = [{'target_id': targets[(state['round'] + len(cid)) % len(targets)], 'action_id': '初步外交施压', 'conventional': 0, 'nuclear': 0}]
                    response = json.dumps(data, ensure_ascii=False)
                else:
                    response = provider.decide('v6_sealed', system, actual_user, situation)
                call['response'] = response
                decision = protocol.validate_decision(response, situation)
                alliances.validate_decision_fields(decision)
            except Exception as exc:
                last = str(exc)[:500] if isinstance(exc, (ValueError, protocol.V6ValidationError)) else type(exc).__name__
                call.update({'status': 'failed', 'error': last, 'provider_info': copy.deepcopy(provider.last_call_info)})
                if on_call is not None: on_call()
            else:
                call.update({'status': 'completed', 'provider_info': copy.deepcopy(provider.last_call_info)})
                # Storage/UI callback failures are infrastructure errors, not
                # invalid model attempts, and must propagate to the runner.
                if on_call is not None: on_call()
                decisions[cid] = decision
                break
        else:
            fallback += 1
            decisions[cid] = protocol.make_fallback(situation)
    return decisions, calls, fallback


def advance(state, decisions, config):
    """Resolve loans and cancel invalid actions until preemptions match execution."""
    effective = copy.deepcopy(decisions)
    replacements = []
    while True:
        new = copy.deepcopy(state)
        alliance_events = alliances.resolve(new, effective, config)
        revised, failures = alliances.executable_decisions(new, effective)
        if not failures:
            break
        replacements.extend(failures)
        effective = revised
    diplomatic_events = diplomacy.resolve(new, effective)
    rules.validate_decisions(new, effective, config)
    new, record = rules.settle_round(new, effective, config)
    alliances.break_conflicting_alliances(new)
    for cid, decision in effective.items():
        assessment = decision.get('threat_assessment', '')
        if assessment:
            new.setdefault('private_threat_assessments', {})[cid] = {'round': record['round'], 'text': assessment}
    record['alliance_events'] = alliance_events
    record['diplomatic_events'] = diplomatic_events
    record['settlement_fallbacks'] = replacements
    record['effective_decisions'] = effective
    return new, record

def public_round(record, decisions, state):
    # Display only actions actually executed, never failed packages or alliance terms.
    effective = record.get('effective_decisions', decisions)
    declarations = {cid:d['declaration'] for cid,d in effective.items()}
    confirmed = {
        'executed_actions': {cid:[{'target_id':a['target_id'],'action_id':a['action_id']} for a in d['actions']] for cid,d in effective.items()},
        'presence_transfers': copy.deepcopy(record['presence_transfers']),
        'pressure_costs': copy.deepcopy(record.get('pressure_costs', [])),
        'automatic_second_strikes': copy.deepcopy(record.get('automatic_second_strikes', [])),
        'diplomatic_events': copy.deepcopy(record.get('diplomatic_events', [])),
        'outcome': copy.deepcopy(record['outcome']),
    }
    return {'round':record['round'], 'declarations': declarations,
            'unverified_declarations': copy.deepcopy(declarations), 'confirmed_facts': confirmed,
            'actions':{cid:[{'target_id':a['target_id'],'action_id':a['action_id']} for a in d['actions']] for cid,d in effective.items()},
            'presence_transfers':copy.deepcopy(record['presence_transfers']),
            'automatic_second_strikes':copy.deepcopy(record.get('automatic_second_strikes', [])),
            'alliances':alliances.view(state), 'diplomacy':diplomacy.view(state),
            'state':public_state(state), 'outcome':copy.deepcopy(record['outcome']),
            'settlement_fallbacks': [x['country_id'] for x in record.get('settlement_fallbacks', [])]}


def state_changes(before, after):
    """Return deterministic leaf-level changes for auditing a round."""
    changes = []
    missing = object()

    def walk(old, new, path):
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(set(old) | set(new)):
                walk(old.get(key, missing), new.get(key, missing), path + [str(key)])
            return
        if old == new:
            return
        item = {'path': '/'.join(path)}
        if old is not missing:
            item['before'] = copy.deepcopy(old)
        if new is not missing:
            item['after'] = copy.deepcopy(new)
        changes.append(item)

    walk(before, after, [])
    return changes


def replay(data):
    state = copy.deepcopy(data['initial_state'])
    history = []
    for row in data['rounds']:
        try:
            for cid, decision in row['decisions'].items():
                situation = situation_for(state, cid, history, data['rule_config'])
                protocol.validate_decision(decision, situation)
            state, record = advance(state, row['decisions'], data['rule_config'])
            public = public_round(record, row['decisions'], state)
        except (ValueError, KeyError, TypeError) as exc:
            return False, f"第 {row.get('round')} 轮决策或结算无效：{type(exc).__name__}"
        if state != row['state'] or record != row['settlement'] or public != row['public']:
            return False, f"第 {row['round']} 轮结算或公开记录与保存结果不一致"
        history.append(public)
    return state == data['final_state'], '一致' if state == data['final_state'] else '最终状态不一致'

def reports(data):
    lines = ['# v6.0 模拟报告', '', f"状态：{data['run_status']}；胜者：{data['result'].get('winner_id') or '无'}；原因：{data['result'].get('reason') or '未完成'}", '']
    for row in data['rounds']:
        lines += [f"## 第 {row['round']} 轮", '', '```json', json.dumps(row['public'], ensure_ascii=False, indent=2), '```', '']
    md = '\n'.join(lines)
    return md, '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>v6.0 模拟报告</title><body><pre style="white-space:pre-wrap">'+html.escape(md)+'</pre></body></html>'

def _module_bytes(module):
    """Return stable module bytes in source and PyInstaller environments."""
    path = Path(module.__file__)
    if path.is_file():
        return path.read_bytes()
    loader = getattr(module, '__loader__', None)
    code = loader.get_code(module.__name__) if loader and hasattr(loader, 'get_code') else None
    if code is None:
        raise RuntimeError(f'无法读取规则模块指纹：{module.__name__}')
    return marshal.dumps(code)


def fingerprint():
    h = hashlib.sha256()
    for module in (rules, protocol, alliances, diplomacy, knowledge):
        h.update(_module_bytes(module))
    h.update(_module_bytes(__import__(__name__, fromlist=['*'])))
    return h.hexdigest()


def config_fingerprint(config):
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
