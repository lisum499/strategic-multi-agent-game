"""Deterministic v5.0 settlement. Inputs/outputs are JSON-compatible dicts.

Actions are sealed before invoking settle_round; this module performs no I/O
or model calls. It returns a fresh state and a fully auditable round record.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from itertools import combinations
from typing import Any
from pathlib import Path
import json

RULES_VERSION = '5.0'
Q = Decimal('0.000001')
ZERO = Decimal(0)
DEFAULT_CONFIG = json.loads((Path(__file__).parent / 'scenarios' / 'rules.json').read_text(encoding='utf-8'))
ACTION_LEVELS = DEFAULT_CONFIG['actions']
ACTUAL_NUCLEAR = frozenset({'有限核武器使用', '升级性核行动', '扩大核打击行动', '战略核战争'})
THREATS = frozenset({'战略核威胁', '最后核警告'})
NUCLEAR_ACTIONS = ACTUAL_NUCLEAR | THREATS | frozenset({'核信号释放', '核军事预备', '核武器示威', '核威胁'})
CONVENTIONAL_MILITARY = frozenset({'军事示威','有限军事试探','升级边境军事行动','常规军事施压','大规模常规军事行动','纵深常规打击','最大规模常规作战'})


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


def _get(matrix: dict, a: str, b: str) -> Decimal:
    return number(matrix.get(a, {}).get(b, 0))


def _put(matrix: dict, a: str, b: str, v: Decimal) -> None:
    matrix.setdefault(a, {})[b] = out(v)


def _config(config: dict | None) -> dict:
    c = deepcopy(DEFAULT_CONFIG)
    if config:
        unknown = set(config) - set(c)
        if unknown:
            raise ValueError(f'unknown v5 config fields: {sorted(unknown)}')
        c.update(deepcopy(config))
    if not isinstance(c['actions'], dict) or set(c['actions']) != set(ACTION_LEVELS):
        raise ValueError('规则参数 actions 必须包含全部原有行动名称')
    for name, value in c['actions'].items():
        n = number(value)
        if name == '保持不变' and n != 0:
            raise ValueError('保持不变必须为0')
        if name != '保持不变' and ((name in {'完全投降','大规模撤退','重大让步','中等规模撤退','有限战术撤退','小规模后撤','外交降级','最低限度让步'}) != (n < 0)):
            raise ValueError(f'行动数值符号不合法: {name}')
    for key in ('conventional_intensity_divisor','nuclear_intensity_divisor','capability_offset','nuclear_territory_scale'):
        if number(c[key]) <= 0: raise ValueError(f'{key} 必须大于0')
    for key in ('territory_win_min','territory_win_gap','threat_effect','conventional_loss_rate','capability_min','capability_max','nuclear_territory_max_bonus','nuclear_capability_threshold'):
        if number(c[key]) < 0: raise ValueError(f'{key} 必须非负')
    if number(c['capability_min']) > number(c['capability_max']): raise ValueError('战损倍率上下界无效')
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
    conflicts = current.setdefault('conflicts', {})
    history = current.setdefault('threat_history', {})
    old_conflicts = deepcopy(conflicts)
    old_history = deepcopy(history)
    living_at_start = sorted(cid for cid,c in countries.items() if c.get('alive',True))
    round_number = int(current.get('round', 1))
    choices = {(cid, a['target_id']): a for cid,d in decisions.items() for a in d.get('actions',[])}
    old_nuclear_users = set(current.get('nuclear_used', []))
    nuclear_users = old_nuclear_users | {a for (a,_),v in choices.items() if v['action_id'] in ACTUAL_NUCLEAR}
    current['nuclear_used'] = sorted(nuclear_users)
    updated_threats = {}
    for (a,b),choice in sorted(choices.items()):
        name=choice['action_id']; delta=number(cfg['actions'][name])
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
    logs=[]; claims: dict[str,dict[str,Decimal]]={cid:{} for cid in living_at_start}
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
        prior_a=min(_get(old_history,a,b),na)
        prior_b=min(_get(old_history,b,a),nb)
        # Current threats contribute just this round; previous threat increments never recur.
        def effective(who,other,name,level,prior):
            base=max(ZERO,level-prior-updated_threats.get((who,other),ZERO))
            if name in THREATS:
                d=number(cfg['actions'][name]) if who in nuclear_users or other in nuclear_users else number(cfg['threat_effect'])
                return base+d
            return base
        ea=effective(a,b,aa,na,prior_a); eb=effective(b,a,bb,nb,prior_b)
        difference=abs(ea-eb)
        if (aa in ACTUAL_NUCLEAR and bb in CONVENTIONAL_MILITARY) or (bb in ACTUAL_NUCLEAR and aa in CONVENTIONAL_MILITARY):
            theta=Decimal(1)+min(difference/number(cfg['nuclear_territory_scale']),number(cfg['nuclear_territory_max_bonus']))
        else:
            theta=Decimal(1)
        claim=quant(difference*theta)
        if ea>eb: loser,winner=b,a
        elif eb>ea: loser,winner=a,b
        else: loser=winner=None
        if loser and claim>0: claims[loser][winner]=claim
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
        logs.append({'parties':[a,b],'actions':{a:aa,b:bb}, 'levels':{a:out(na),b:out(nb)},'territory_levels':{a:out(ea),b:out(eb)}, 'territory_claim':out(claim),'territory_winner':winner,'theta':out(theta),'intensity':out(intensity),'losses':item_losses})
    ledger=[]
    for payer in living_at_start:
        payer_claims=claims[payer]
        if not payer_claims: continue
        balance=number(countries[payer]['territory'])
        if not countries[payer].get('alive',True):
            ledger.append({'payer':payer,'status':'already_eliminated','claims':{k:out(v) for k,v in payer_claims.items()}})
            continue
        # Dead beneficiaries' awards return to payer, and are not redistributed.
        allocated=_allocate(balance,payer_claims)
        transferred=ZERO
        for receiver,share in sorted(allocated.items()):
            eligible=countries[receiver].get('alive',True)
            actual=share if eligible else ZERO
            transferred+=actual
            countries[receiver]['territory']=out(number(countries[receiver]['territory'])+actual)
            ledger.append({'payer':payer,'receiver':receiver,'claim':out(payer_claims[receiver]),'allocated':out(share),'transferred':out(actual),'returned':out(share-actual)})
        remaining=quant(balance-transferred)
        countries[payer]['territory']=out(remaining)
        if remaining<=0:
            countries[payer]['alive']=False
    # Losses arise from sealed actions; the entire round is settled even if territory falls to 0.
    for cid in living_at_start:
        for kind in ('conventional','nuclear'):
            countries[cid][kind]=out(max(ZERO,number(countries[cid].get(kind,0))-combat_losses[cid][kind]))
    for a,b in mutual_nuclear:
        countries[a]['alive']=countries[b]['alive']=False
    # Mutual surrender ends the match without a winner; other surrender requires unanimous surrender to one.
    surrender_winner=None
    if not mutual_surrender:
        for target in living_at_start:
            if all(choices.get((other,target),{}).get('action_id')=='完全投降' for other in living_at_start if other!=target):
                surrender_winner=target
                for other in living_at_start:
                    if other!=target: countries[other]['alive']=False
                break
    alive=[cid for cid in living_at_start if countries[cid].get('alive',True)]
    winner=None; reason=None
    if mutual_surrender:
        reason='mutual_surrender'
    elif not alive:
        reason='all_eliminated'
    elif len(alive)==1:
        winner=alive[0]; reason='last_country'
    elif len(alive)>1:
        for cid in alive:
            value=number(countries[cid]['territory'])
            lead=max(number(countries[other]['territory']) for other in alive if other!=cid)
            if value>=number(cfg['territory_win_min']) and value-lead>=number(cfg['territory_win_gap']):
                winner=cid; reason='territory_victory'; break
    if reason is None and round_number>=int(cfg['max_rounds']): reason='round_limit'
    current['round'] = round_number if reason else round_number+1
    current['outcome']={'finished':reason is not None, 'winner_id':winner, 'reason':reason}
    return current, {'round':round_number,'pairs':logs,'territory_transfers':ledger,'outcome':deepcopy(current['outcome'])}
