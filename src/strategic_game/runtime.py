"""Sealed v5 runtime, private projections, checkpoints and reproducible replay."""
from __future__ import annotations
import copy, hashlib, html, json
from pathlib import Path
from . import protocol as protocol, rules as rules, alliances as alliances, knowledge as knowledge

DEFAULT_SCENARIO = str(Path(__file__).parent / 'scenarios' / 'three_states.json')

def public_state(state):
    return {'round': state['round'], 'countries': {k: {'name': v['name'], 'territory': v['territory'], 'alive': v['alive']} for k,v in state['countries'].items()},
            'conflicts': copy.deepcopy(state.get('conflicts', {})), 'nuclear_used': copy.deepcopy(state.get('nuclear_used', [])),
            'alliances': alliances.view(state)}

def situation_for(state, country, history, config):
    public = public_state(state)
    # Opponent military strength can reveal confidential alliance transfers.
    private_state = copy.deepcopy(public)
    private_state['countries'][country] = copy.deepcopy(state['countries'][country])
    private_state['rule_config'] = copy.deepcopy(config)
    # Opponent forces are unknown, rather than fabricated zero values.
    private_state['alliance_info'] = alliances.view(state, country)
    private_state['world_context'] = state.get('world_context', '')
    private_state['history'] = copy.deepcopy(history)
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
            actual_user = user + (f'\n上次错误：{last}' if last else '')
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
                    response = provider.decide('v5_sealed', system, actual_user, situation)
                call['response'] = response
                decision = protocol.validate_decision(response, situation)
                alliances.validate_decision_fields(decision)
                call.update({'status': 'completed', 'provider_info': copy.deepcopy(provider.last_call_info)})
                if on_call is not None: on_call()
                decisions[cid] = decision
                break
            except Exception as exc:
                last = str(exc)[:500] if isinstance(exc, (ValueError, protocol.V5ValidationError)) else type(exc).__name__
                call.update({'status': 'failed', 'error': last, 'provider_info': copy.deepcopy(provider.last_call_info)})
                if on_call is not None: on_call()
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
    rules.validate_decisions(new, effective, config)
    new, record = rules.settle_round(new, effective, config)
    alliances.break_conflicting_alliances(new)
    record['alliance_events'] = alliance_events
    record['settlement_fallbacks'] = replacements
    record['effective_decisions'] = effective
    return new, record

def public_round(record, decisions, state):
    # Display only actions actually executed, never failed packages or alliance terms.
    effective = record.get('effective_decisions', decisions)
    return {'round':record['round'], 'declarations': {cid:d['declaration'] for cid,d in effective.items()},
            'actions':{cid:[{'target_id':a['target_id'],'action_id':a['action_id']} for a in d['actions']] for cid,d in effective.items()},
            'territory_transfers':copy.deepcopy(record['territory_transfers']),
            'alliances':alliances.view(state), 'state':public_state(state), 'outcome':copy.deepcopy(record['outcome']),
            'settlement_fallbacks': [x['country_id'] for x in record.get('settlement_fallbacks', [])]}


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
    lines = ['# v5.0 模拟报告', '', f"状态：{data['run_status']}；胜者：{data['result'].get('winner_id') or '无'}；原因：{data['result'].get('reason') or '未完成'}", '']
    for row in data['rounds']:
        lines += [f"## 第 {row['round']} 轮", '', '```json', json.dumps(row['public'], ensure_ascii=False, indent=2), '```', '']
    md = '\n'.join(lines)
    return md, '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>v5.0 模拟报告</title><body><pre style="white-space:pre-wrap">'+html.escape(md)+'</pre></body></html>'

def fingerprint():
    h = hashlib.sha256()
    for module in (rules, protocol, alliances, knowledge): h.update(Path(module.__file__).read_bytes())
    h.update(Path(__file__).read_bytes())
    return h.hexdigest()


def config_fingerprint(config):
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
