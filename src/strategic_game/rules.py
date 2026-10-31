"""Deterministic v6.0 settlement. Inputs/outputs are JSON-compatible dicts.

Actions are sealed before invoking settle_round; this module performs no I/O
or model calls. It returns a fresh state and a fully auditable round record.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
from typing import Any
from pathlib import Path
import json
from .capabilities import has_second_strike, can_choose_nuclear_action

RULES_VERSION = '6.0'
Q = Decimal('0.000001')
ZERO = Decimal(0)
DEFAULT_CONFIG = json.loads((Path(__file__).parent / 'scenarios' / 'rules.json').read_text(encoding='utf-8'))
ACTION_LEVELS = DEFAULT_CONFIG['actions']
ACTUAL_NUCLEAR = frozenset({'有限核武器使用', '升级性核行动', '扩大核打击行动', '战略核战争'})
SECOND_STRIKE_TRIGGERS = frozenset({'升级性核行动', '扩大核打击行动', '战略核战争'})
THREATS = frozenset({'战略核威胁', '最后核警告'})
NUCLEAR_ACTIONS = ACTUAL_NUCLEAR | THREATS | frozenset({'核信号释放', '核军事预备', '核武器示威', '核威胁'})
CONVENTIONAL_MILITARY = frozenset({'军事示威','有限军事试探','升级边境军事行动','常规军事施压','大规模常规军事行动','纵深常规打击','最大规模常规作战'})
PRESENCE_FIELDS = ('economic_presence', 'military_presence', 'soft_presence')
DIPLOMATIC_ACTIONS = frozenset({'初步外交施压', '加强外交施压'})
ECONOMIC_ACTIONS = frozenset({'经济施压', '扩大经济战'})


def number(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError('boolean is not a number')
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError('invalid numeric value') from exc
    if not result.is_finite():
        raise ValueError('numeric value must be finite')
    return result


def quant(value: Decimal) -> Decimal:
    return value.quantize(Q, rounding=ROUND_HALF_UP)


def out(value: Decimal) -> float:
    return float(quant(value))


def global_presence(country: dict, config: dict | None = None) -> Decimal:
    """Calculate IPG from its three authoritative presence dimensions."""
    cfg = config or DEFAULT_CONFIG
    weights = cfg['presence_weights']
    return quant(sum(number(country[field]) * number(weights[field]) for field in PRESENCE_FIELDS))


def _refresh_global_presence(countries: dict, config: dict) -> None:
    for country in countries.values():
        country['global_presence'] = out(global_presence(country, config))


def _presence_category(action: str) -> str:
    if action in DIPLOMATIC_ACTIONS:
        return 'diplomatic'
    if action in ECONOMIC_ACTIONS:
        return 'economic'
    if action in CONVENTIONAL_MILITARY:
        return 'military'
    if action in NUCLEAR_ACTIONS:
        return 'nuclear'
    return 'status_quo'


def _get(matrix: dict, a: str, b: str) -> Decimal:
    return number(matrix.get(a, {}).get(b, 0))


def _put(matrix: dict, a: str, b: str, v: Decimal) -> None:
    matrix.setdefault(a, {})[b] = out(v)


def _config(config: dict | None) -> dict:
    c = deepcopy(DEFAULT_CONFIG)
    if config:
        unknown = set(config) - set(c)
        if unknown:
            raise ValueError(f'unknown v6 config fields: {sorted(unknown)}')
        c.update(deepcopy(config))
    if not isinstance(c['actions'], dict) or set(c['actions']) != set(ACTION_LEVELS):
        raise ValueError('规则参数 actions 必须包含全部原有行动名称')
    for name, value in c['actions'].items():
        n = number(value)
        if name == '保持不变' and n != 0:
            raise ValueError('保持不变必须为0')
        if name != '保持不变' and ((name in {'完全投降','大规模撤退','重大让步','中等规模撤退','有限战术撤退','小规模后撤','外交降级','最低限度让步'}) != (n < 0)):
            raise ValueError(f'行动数值符号不合法: {name}')
    for key in ('conventional_intensity_divisor','nuclear_intensity_divisor','capability_offset','nuclear_ipg_scale'):
        if number(c[key]) <= 0: raise ValueError(f'{key} 必须大于0')
    for key in ('ipg_win_min','ipg_win_gap','threat_effect','conventional_loss_rate','capability_min','capability_max','nuclear_ipg_max_bonus','second_strike_reserve_fraction','diplomatic_pressure_cost_rate','economic_pressure_cost_rate'):
        if number(c[key]) < 0: raise ValueError(f'{key} 必须非负')
    multipliers = c.get('repeat_pressure_multipliers')
    if (not isinstance(multipliers, list) or not multipliers
            or any(not ZERO < number(value) <= 1 for value in multipliers)
            or any(number(multipliers[index]) > number(multipliers[index - 1]) for index in range(1, len(multipliers)))):
        raise ValueError('repeat_pressure_multipliers 必须是非空、递减且大于0不超过1的数组')
    cost_multipliers = c.get('repeat_pressure_cost_multipliers')
    if (not isinstance(cost_multipliers, list) or not cost_multipliers
            or any(number(value) < 1 for value in cost_multipliers)
            or any(number(cost_multipliers[index]) < number(cost_multipliers[index - 1])
                   for index in range(1, len(cost_multipliers)))):
        raise ValueError('repeat_pressure_cost_multipliers 必须是非空、递增且不小于1的数组')
    if number(c['capability_min']) > number(c['capability_max']): raise ValueError('战损倍率上下界无效')
    if not ZERO < number(c['second_strike_reserve_fraction']) <= 1:
        raise ValueError('second_strike_reserve_fraction 必须大于0且不超过1')
    weights = c.get('presence_weights')
    if not isinstance(weights, dict) or set(weights) != set(PRESENCE_FIELDS):
        raise ValueError('presence_weights 必须包含三个存在维度')
    if any(number(weights[field]) <= 0 for field in PRESENCE_FIELDS) or sum((number(weights[field]) for field in PRESENCE_FIELDS), ZERO) != 1:
        raise ValueError('presence_weights 必须为正且总和为1')
    impacts = c.get('presence_impact_weights')
    if not isinstance(impacts, dict) or set(impacts) != {'status_quo','diplomatic','economic','military','nuclear'}:
        raise ValueError('presence_impact_weights 分类不完整')
    for category, values in impacts.items():
        if not isinstance(values, list) or len(values) != 3 or any(number(value) < 0 for value in values) or sum((number(value) for value in values), ZERO) != 1:
            raise ValueError(f'存在影响权重无效: {category}')
    return c


def validate_decisions(state: dict, decisions: dict, config: dict | None = None) -> None:
    cfg = _config(config)
    countries = state['countries']
    living = {cid for cid, c in countries.items() if c.get('alive', True)}
    if set(decisions) != living:
        raise ValueError('one decision required for each living country')
    for cid, decision in decisions.items():
        actions = decision.get('actions', [])
        if not isinstance(actions, list):
            raise ValueError('actions must be a list')
        targets = set()
        allocated = {'conventional': ZERO, 'nuclear': ZERO}
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError('action must be an object')
            target = action.get('target_id')
            name = action.get('action_id')
            if target not in living or target == cid or target in targets:
                raise ValueError('target must be a unique living opponent')
            targets.add(target)
            if name not in cfg['actions']:
                raise ValueError(f'unknown action: {name}')
            level = number(cfg['actions'][name])
            old = _get(state.get('conflicts', {}), cid, target)
            opposite = _get(state.get('conflicts', {}), target, cid)
            if level < 0 and old == 0 and opposite == 0:
                raise ValueError('cannot de-escalate a nonexistent conflict')
            if name in NUCLEAR_ACTIONS and old < level:
                raise ValueError('nuclear action requires its threshold before the action')
            if name in NUCLEAR_ACTIONS and not can_choose_nuclear_action(countries[cid], level):
                raise ValueError('nuclear capability status does not allow this action level')
            for k in allocated:
                v = number(action.get(k, 0))
                if v < 0:
                    raise ValueError('forces cannot be negative')
                allocated[k] += v
            if name in ACTUAL_NUCLEAR and (number(countries[cid].get('nuclear', 0)) <= 0 or number(action.get('nuclear', 0)) <= 0):
                raise ValueError('actual nuclear use requires allocated nuclear forces')
        for k,v in allocated.items():
            if v > number(countries[cid].get(k,0)):
                raise ValueError('forces cannot be allocated twice or exceed available forces')


def _allocate(amount: Decimal, claims: dict[str, Decimal]) -> dict[str, Decimal]:
    """One payment per payer; distribute shortage proportionally at 6 decimals."""
    total = sum(claims.values(), ZERO)
    pay = min(amount, total)
    if total <= 0 or pay <= 0:
        return {x: ZERO for x in claims}
    if pay == total:
        return dict(claims)
    # Round in stable ID order; the final beneficiary receives the remainder.
    keys = sorted(claims)
    shares = {}
    remaining = pay
    for key in keys[:-1]:
        share = min(quant(pay * claims[key] / total), claims[key], remaining)
        shares[key] = share
        remaining -= share
    shares[keys[-1]] = min(remaining, claims[keys[-1]])
    remaining -= shares[keys[-1]]
    # If rounding limited the last claim, return any residual to earlier claims.
    for key in keys[:-1]:
        if remaining <= 0:
            break
        delta = min(remaining, claims[key] - shares[key])
        shares[key] += delta
        remaining -= delta
    if remaining != 0:
        raise ArithmeticError('allocation precision error')
    return shares


def settle_round(state: dict, decisions: dict, config: dict | None = None) -> tuple[dict, dict]:
    """Return (new_state, log); validate all decisions before applying any changes.

    state.round denotes the round being executed. The returned state increments
    round on continuation. Alliance transfers should be settled before this call.
    """
    cfg = _config(config)
    validate_decisions(state, decisions, cfg)
    current = deepcopy(state)
    countries = current['countries']
    _refresh_global_presence(countries, cfg)
    conflicts = current.setdefault('conflicts', {})
    history = current.setdefault('threat_history', {})
    old_conflicts = deepcopy(conflicts)
    old_history = deepcopy(history)
    old_streaks = current.get('pressure_streaks', {})
    living_at_start = sorted(cid for cid,c in countries.items() if c.get('alive',True))
    round_number = int(current.get('round', 1))
    choices = {(cid, a['target_id']): a for cid,d in decisions.items() for a in d.get('actions',[])}
    automatic_second_strikes = []
    incoming = {
        target: sorted(source for (source, dst), action in choices.items()
                       if dst == target and action['action_id'] in SECOND_STRIKE_TRIGGERS)
        for target in living_at_start
    }
    for defender in living_at_start:
        attackers = incoming[defender]
        if not attackers or not has_second_strike(countries[defender]):
            continue
        reserve = number(countries[defender]['nuclear']) * number(cfg['second_strike_reserve_fraction'])
        allocation = quant(reserve / len(attackers))
        for attacker in attackers:
            existing = choices.get((defender, attacker))
            if existing and existing['action_id'] in ACTUAL_NUCLEAR:
                continue
            mirrored = choices[(attacker, defender)]['action_id']
            choices[(defender, attacker)] = {
                'target_id': attacker, 'action_id': mirrored,
                'conventional': 0, 'nuclear': out(allocation),
            }
            automatic_second_strikes.append({
                'source': defender, 'target': attacker, 'action_id': mirrored,
                'nuclear': out(allocation), 'replaced_action_id': existing['action_id'] if existing else None,
            })
    old_nuclear_users = set(current.get('nuclear_used', []))
    nuclear_users = old_nuclear_users | {a for (a,_),v in choices.items() if v['action_id'] in ACTUAL_NUCLEAR}
    current['nuclear_used'] = sorted(nuclear_users)
    pressure_streaks = {}
    effective_deltas = {}
    pressure_details = {}
    multipliers = [number(value) for value in cfg['repeat_pressure_multipliers']]
    for (actor, target), choice in sorted(choices.items()):
        name = choice['action_id']
        raw = number(cfg['actions'][name])
        category = _presence_category(name)
        count = 0
        multiplier = Decimal(1)
        if raw > 0:
            previous = old_streaks.get(actor, {}).get(target, {})
            count = int(previous.get('count', 0)) + 1 if previous.get('category') == category else 1
            multiplier = multipliers[min(count - 1, len(multipliers) - 1)]
            pressure_streaks.setdefault(actor, {})[target] = {'category': category, 'count': count}
        effective_deltas[(actor, target)] = quant(raw * multiplier)
        pressure_details[(actor, target)] = {'raw_delta': out(raw), 'effective_delta': out(effective_deltas[(actor, target)]),
                                             'category': category, 'streak': count, 'multiplier': out(multiplier)}
    current['pressure_streaks'] = pressure_streaks
    updated_threats = {}
    for (a,b),choice in sorted(choices.items()):
        name=choice['action_id']; delta=effective_deltas[(a,b)]
        old=_get(old_conflicts,a,b); old_threat=_get(old_history,a,b)
        new=max(ZERO,old+delta)
        # Historical threat contributions cannot exceed the remaining level.
        historic=min(old_threat,new)
        if name in THREATS:
            updated_threats[(a,b)]=delta
            historic += delta
        _put(conflicts,a,b,new)
        _put(history,a,b,historic)
    # Include opposite missing directions as neutral actions in every existing pair.
    logs=[]
    claims: dict[str,dict[str,dict[str,Decimal]]]={cid:{} for cid in living_at_start}
    combat_losses={cid:{'conventional':ZERO,'nuclear':ZERO} for cid in living_at_start}
    default={'action_id':'保持不变','conventional':0,'nuclear':0}
    mutual_nuclear=[]; mutual_surrender=[]
    for a,b in combinations(living_at_start,2):
        ca=choices.get((a,b),default); cb=choices.get((b,a),default)
        if not (_get(conflicts,a,b) or _get(conflicts,b,a) or (a,b) in choices or (b,a) in choices):
            continue
        aa,bb=ca['action_id'],cb['action_id']
        if aa==bb=='战略核战争': mutual_nuclear.append((a,b))
        if aa==bb=='完全投降': mutual_surrender.append((a,b))
        na,nb=_get(conflicts,a,b),_get(conflicts,b,a)
        def effective_increment(who, other, name):
            value = effective_deltas.get((who, other), ZERO)
            if name in THREATS and who not in nuclear_users and other not in nuclear_users:
                multiplier = number(pressure_details[(who, other)]['multiplier'])
                return quant(number(cfg['threat_effect']) * multiplier)
            return value
        ea=effective_increment(a,b,aa); eb=effective_increment(b,a,bb)
        difference=abs(ea-eb)
        if (aa in ACTUAL_NUCLEAR and bb in CONVENTIONAL_MILITARY) or (bb in ACTUAL_NUCLEAR and aa in CONVENTIONAL_MILITARY):
            theta=Decimal(1)+min(difference/number(cfg['nuclear_ipg_scale']),number(cfg['nuclear_ipg_max_bonus']))
        else:
            theta=Decimal(1)
        claim=quant(difference*theta)
        if ea>eb: loser,winner=b,a
        elif eb>ea: loser,winner=a,b
        else: loser=winner=None
        presence_claims = {}
        if loser and claim>0:
            category = _presence_category(ca['action_id'] if winner == a else cb['action_id'])
            impact = cfg['presence_impact_weights'][category]
            presence_claims = {
                field: quant(claim * number(impact[index]) / number(cfg['presence_weights'][field]))
                for index, field in enumerate(PRESENCE_FIELDS)
            }
            claims[loser][winner] = presence_claims
        combat = aa in CONVENTIONAL_MILITARY or bb in CONVENTIONAL_MILITARY
        actual=[name for name in (aa,bb) if name in ACTUAL_NUCLEAR]
        intensity=ZERO; rates={'conventional':ZERO,'nuclear':ZERO}
        if actual:
            intensity=min((na+nb)/number(cfg['nuclear_intensity_divisor']),Decimal(1))
            largest=max(number(cfg['actions'][name]) for name in actual)
            for threshold,cv,nv in cfg['nuclear_loss_tiers']:
                if largest>=number(threshold): rates={'conventional':number(cv),'nuclear':number(nv)}
        elif combat:
            intensity=min((na+nb)/number(cfg['conventional_intensity_divisor']),Decimal(1))
            rates['conventional']=number(cfg['conventional_loss_rate'])
        av=number(ca.get('conventional',0)); bv=number(cb.get('conventional',0))
        eps=number(cfg['capability_offset']); lo=number(cfg['capability_min']); hi=number(cfg['capability_max'])
        ma=min(hi,max(lo,((bv+eps)/(av+eps)).sqrt())); mb=min(hi,max(lo,((av+eps)/(bv+eps)).sqrt()))
        item_losses={}
        for cid,choice,mult in ((a,ca,ma),(b,cb,mb)):
            item_losses[cid]={}
            for kind in ('conventional','nuclear'):
                loss=quant(number(choice.get(kind,0))*intensity*rates[kind]*mult)
                loss=min(loss,number(choice.get(kind,0)))
                combat_losses[cid][kind]+=loss
                item_losses[cid][kind]=out(loss)
        logs.append({'parties':[a,b],'actions':{a:aa,b:bb}, 'levels':{a:out(na),b:out(nb)},
                     'effective_increments':{a:out(ea),b:out(eb)},
                     'pressure':{a:pressure_details.get((a,b), {'raw_delta':0,'effective_delta':0,'category':'status_quo','streak':0,'multiplier':1}),
                                 b:pressure_details.get((b,a), {'raw_delta':0,'effective_delta':0,'category':'status_quo','streak':0,'multiplier':1})},
                     'ipg_claim':out(claim),
                     'ipg_winner':winner, 'presence_claims':{k:out(v) for k,v in presence_claims.items()},
                     'theta':out(theta),'intensity':out(intensity),'losses':item_losses})
    pressure_costs=[]
    cost_multipliers = [number(value) for value in cfg['repeat_pressure_cost_multipliers']]
    for (actor, target), choice in sorted(choices.items()):
        name = choice['action_id']; effective_delta = max(ZERO, effective_deltas[(actor, target)])
        if name in DIPLOMATIC_ACTIONS:
            field, rate = 'soft_presence', number(cfg['diplomatic_pressure_cost_rate'])
        elif name in ECONOMIC_ACTIONS:
            field, rate = 'economic_presence', number(cfg['economic_pressure_cost_rate'])
        else:
            continue
        streak = int(pressure_details[(actor, target)]['streak'])
        cost_multiplier = cost_multipliers[min(max(streak - 1, 0), len(cost_multipliers) - 1)]
        raw_delta = max(ZERO, number(cfg['actions'][name]))
        requested = quant(raw_delta * rate * cost_multiplier)
        charged = min(number(countries[actor][field]), requested)
        countries[actor][field] = out(number(countries[actor][field]) - charged)
        pressure_costs.append({'actor':actor, 'target':target, 'action_id':name, 'dimension':field,
                               'effective_delta':out(effective_delta), 'rate':out(rate),
                               'streak':streak, 'cost_multiplier':out(cost_multiplier),
                               'requested':out(requested), 'charged':out(charged)})
    ledger=[]
    for payer in living_at_start:
        payer_claims=claims[payer]
        if not payer_claims: continue
        if not countries[payer].get('alive',True):
            ledger.append({'payer':payer,'status':'already_eliminated'})
            continue
        # Settle each authoritative presence dimension independently. Dead
        # beneficiaries' awards return to the payer and are not redistributed.
        for field in PRESENCE_FIELDS:
            dimension_claims={receiver: values[field] for receiver,values in payer_claims.items()}
            balance=number(countries[payer][field])
            allocated=_allocate(balance,dimension_claims)
            transferred=ZERO
            for receiver,share in sorted(allocated.items()):
                eligible=countries[receiver].get('alive',True)
                actual=share if eligible else ZERO
                transferred+=actual
                countries[receiver][field]=out(number(countries[receiver][field])+actual)
                ledger.append({'payer':payer,'receiver':receiver,'dimension':field,
                               'claim':out(dimension_claims[receiver]),'allocated':out(share),
                               'transferred':out(actual),'returned':out(share-actual)})
            countries[payer][field]=out(quant(balance-transferred))
        _refresh_global_presence(countries, cfg)
        if global_presence(countries[payer], cfg)<=0:
            countries[payer]['alive']=False
    # Losses arise from sealed actions; the entire round is settled even if IPG falls to 0.
    for cid in living_at_start:
        for kind in ('conventional','nuclear'):
            countries[cid][kind]=out(max(ZERO,number(countries[cid].get(kind,0))-combat_losses[cid][kind]))
    for a,b in mutual_nuclear:
        countries[a]['alive']=countries[b]['alive']=False
    # Mutual surrender is a draw only when exactly two countries remain.
    # Otherwise, surrender eliminates countries only when every other living
    # country surrenders to the same target in this round.
    two_country_mutual_surrender = len(living_at_start) == 2 and bool(mutual_surrender)
    surrender_winner=None
    if not two_country_mutual_surrender:
        for target in living_at_start:
            if all(choices.get((other,target),{}).get('action_id')=='完全投降' for other in living_at_start if other!=target):
                surrender_winner=target
                for other in living_at_start:
                    if other!=target: countries[other]['alive']=False
                break
    alive=[cid for cid in living_at_start if countries[cid].get('alive',True)]
    _refresh_global_presence(countries, cfg)
    winner=None; reason=None
    if two_country_mutual_surrender:
        reason='mutual_surrender'
    elif not alive:
        reason='all_eliminated'
    elif len(alive)==1:
        winner=alive[0]; reason='last_country'
    elif len(alive)>1:
        for cid in alive:
            value=global_presence(countries[cid], cfg)
            lead=max(global_presence(countries[other], cfg) for other in alive if other!=cid)
            if value>=number(cfg['ipg_win_min']) and value-lead>=number(cfg['ipg_win_gap']):
                winner=cid; reason='ipg_victory'; break
    if reason is None and round_number>=int(cfg['max_rounds']): reason='round_limit'
    current['round'] = round_number if reason else round_number+1
    current['outcome']={'finished':reason is not None, 'winner_id':winner, 'reason':reason}
    return current, {'round':round_number,'pairs':logs,'presence_transfers':ledger,
                     'pressure_costs':pressure_costs,
                     'automatic_second_strikes':automatic_second_strikes,
                     'outcome':deepcopy(current['outcome'])}
