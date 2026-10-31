"""Private alliance contracts and auditable loans for v6.

Mutates the supplied state; caller owns atomic round transaction/checkpointing.
Inputs are sealed before this function is called. Monetary quantities are
represented as JSON numbers rounded to six decimal places.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

KINDS = ('conventional', 'nuclear')
ZERO = Decimal(0)
Q = Decimal('0.000001')


def _num(value):
    if isinstance(value, bool):
        raise ValueError('兵力必须是非负有限数值')
    try:
        n = Decimal(str(value))
    except Exception as exc:
        raise ValueError('兵力必须是非负有限数值') from exc
    if not n.is_finite() or n < 0:
        raise ValueError('兵力必须是非负有限数值')
    return n.quantize(Q, rounding=ROUND_HALF_UP)


def _json(n):
    return float(n.quantize(Q, rounding=ROUND_HALF_UP))


def _amounts(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(KINDS):
        raise ValueError('兵力对象只能包含 conventional 和 nuclear')
    return {k: _json(_num(value.get(k, 0))) for k in KINDS}


def _positive(v):
    return any(_num(v[k]) > 0 for k in KINDS)


def _id(a, b):
    return '|'.join(sorted((a, b)))


def _stores(state):
    state.setdefault('alliances', {})
    state.setdefault('alliance_invitations', {})
    state.setdefault('alliance_loans', [])
    state.setdefault('alliance_private_events', [])
    state.setdefault('alliance_public_events', [])


def _alive(state, country):
    return country in state['countries'] and state['countries'][country].get('alive', True)


def _conflict(state, a, b):
    c = state.get('conflicts', {})
    return _num(c.get(a, {}).get(b, 0)) > 0 or _num(c.get(b, {}).get(a, 0)) > 0


def _active(state, a, b):
    x = state['alliances'].get(_id(a, b))
    return x if x and x.get('active') else None


def _public(agreement):
    return bool(agreement.get('public', False))


def _emit(state, event, parties=(), public=False):
    ev = {'round': state['round'], **event}
    if public:
        state['alliance_public_events'].append(ev)
    else:
        state['alliance_private_events'].append({'visible_to': sorted(parties), **ev})


def _break(state, a, b, reason):
    agreement = _active(state, a, b)
    if not agreement:
        return False
    agreement['active'] = False
    agreement['ended_round'] = state['round']
    if _public(agreement):
        _emit(state, {'type': 'alliance_ended', 'countries': sorted([a, b]), 'reason': 'alliance_ended'}, public=True)
    _emit(state, {'type': 'alliance_ended_detail', 'countries': sorted([a, b]), 'reason': reason}, (a, b))
    return True


def _eligible(state, a, b, planned_conflicts=frozenset()):
    if a == b or not _alive(state, a) or not _alive(state, b) or _active(state, a, b) or _conflict(state, a, b) or frozenset((a, b)) in planned_conflicts:
        return False
    # Only disclosed alliances constrain third-party eligibility.
    for ally in state['alliances'].values():
        if not ally.get('active') or not _public(ally) or b not in (ally['a'], ally['b']):
            continue
        other = ally['b'] if ally['a'] == b else ally['a']
        if _conflict(state, a, other) or frozenset((a, other)) in planned_conflicts:
            return False
    return True


def view(state, viewer=None):
    """Public relationships plus only viewer's private invitations and contracts."""
    alliances = state.get('alliances', {})
    public = [{'countries': sorted([x['a'], x['b']])}
              for x in alliances.values() if x.get('active') and _public(x)]
    out = {'alliances': sorted(public, key=lambda x: x['countries']),
           'events': deepcopy(state.get('alliance_public_events', []))}
    if viewer is not None:
        out['invitations'] = [deepcopy(x) for x in state.get('alliance_invitations', {}).values()
                              if viewer in (x['from_id'], x['to_id'])]
        out['contracts'] = [deepcopy(x) for x in alliances.values() if viewer in (x['a'], x['b'])]
        out['loans'] = [deepcopy(x) for x in state.get('alliance_loans', [])
                        if viewer in (x['from_id'], x['to_id'])]
        out['private_events'] = [deepcopy(x) for x in state.get('alliance_private_events', [])
                                 if viewer in x['visible_to']]
    return out


def _failure(state, actor, kind, target, reason):
    _emit(state, {'type': 'alliance_request_failed', 'actor': actor, 'operation': kind,
                  'target': target, 'reason': reason}, (actor,))


def resolve(state, decisions, config=None):
    """Resolve sealed alliance choices deterministically. Return newly produced events.

    Response to an invitation created in round r is accepted only in round r+1.
    Invite, response, borrow and return entries follow the v6 protocol contract.
    Dynamic conflicts between simultaneous requests fail privately and visibly;
    invalid requests never mint force or expose secret details publicly.
    """
    _stores(state)
    before_public = len(state['alliance_public_events'])
    before_private = len(state['alliance_private_events'])
    round_no = state['round']
    countries = state['countries']
    ids = sorted(k for k in decisions if _alive(state, k))

    # Protect every country's sealed fighting commitments. A loan/return may use
    # only its remaining pool, including prior transfers in deterministic order.
    reserved = {cid: {k: sum((_num(action.get(k, 0)) for action in
                              decisions[cid].get('actions', [])), ZERO)
                      for k in KINDS} for cid in ids}
    from .rules import ACTION_LEVELS
    levels = (config or {}).get('actions', ACTION_LEVELS)
    planned_conflicts = {frozenset((cid, action['target_id'])) for cid in ids
                         for action in decisions[cid].get('actions', [])
                         if action['action_id'] in levels
                         and Decimal(str(levels[action['action_id']])) > 0}
    # A direct conflict breaks an existing alliance before borrowing. Sealed
    # decisions were already fixed, so this does not expose same-round choices.
    for agreement in list(state['alliances'].values()):
        if agreement.get('active') and (
            _conflict(state, agreement['a'], agreement['b']) or
            frozenset((agreement['a'], agreement['b'])) in planned_conflicts
        ):
            _break(state, agreement['a'], agreement['b'], '盟友间发生冲突')

    # Response and voluntary termination use the starting-round invitation state.
    for actor in ids:
        for response in decisions[actor].get('alliance_responses', []):
            invitation = state['alliance_invitations'].get(response.get('invite_id'))
            if not invitation or invitation['to_id'] != actor or invitation['status'] != 'pending' or invitation['round'] + 1 != round_no:
                _failure(state, actor, 'respond', response.get('invite_id'), '邀请不存在、不是本人邀请或不在下一轮')
                continue
            if not response.get('accept'):
                invitation['status'] = 'rejected'
                _emit(state, {'type': 'invite_rejected', 'invite_id': invitation['id']}, (actor, invitation['from_id']))
                continue
            sender = invitation['from_id']
            if not _eligible(state, sender, actor, planned_conflicts):
                invitation['status'] = 'invalidated'
                _failure(state, actor, 'respond', invitation['id'], '建立联盟时条件不再满足')
                continue
            counter = _amounts(response.get('counter_offer'))
            counter_return = _amounts(response.get('counter_return'))
            if _positive(invitation['request']) and (_positive(counter) or _positive(counter_return)):
                _failure(state, actor, 'respond', invitation['id'], '有条件邀请不能附主动提供')
                continue
            amount = invitation['request'] if _positive(invitation['request']) else counter
            if _positive(amount) and _num(amount['conventional']) < 1 and _num(amount['nuclear']) == 0:
                _failure(state, actor, 'respond', invitation['id'], '主动提供须至少1常规力量或正数核力量')
                continue
            if any(_num(amount[k]) > _num(countries[actor][k]) for k in KINDS):
                _failure(state, actor, 'respond', invitation['id'], '承诺超过当前力量')
                continue
            if any(_num(invitation['offer'][k]) > _num(countries[sender][k]) for k in KINDS):
                invitation['status'] = 'invalidated'
                _failure(state, actor, 'respond', invitation['id'], '邀请方已无力履行承诺')
                continue
            invitation['status'] = 'accepted'
            agreement = {'id': _id(sender, actor), 'a': sender, 'b': actor, 'active': True, 'public': False,
                         'formed_round': round_no, 'offer': invitation['offer'],
                         'offer_return': invitation['offer_return'], 'request': amount,
                         'request_return': invitation['request_return'] if _positive(invitation['request']) else counter_return}
            state['alliances'][agreement['id']] = agreement
            _emit(state, {'type': 'alliance_formed_detail', 'alliance_id': agreement['id']}, (sender, actor))

    for actor in ids:
        for item in decisions[actor].get('alliance_breaks', []):
            target = item.get('target_id') if isinstance(item, dict) else item
            if not _break(state, actor, target, '单方解除'):
                _failure(state, actor, 'break', target, '联盟不存在')

    # Settlement of loans uses actual balances and partner contract at execution.
    for actor in ids:
        for item in decisions[actor].get('borrows', []):
            lender = item.get('from_id')
            agreement = _active(state, actor, lender)
            if not agreement or not _alive(state, lender):
                _failure(state, actor, 'borrow', lender, '无有效联盟')
                continue
            amount = _amounts({k: item.get(k, 0) for k in KINDS})
            limit = agreement['offer'] if lender == agreement['a'] else agreement['request']
            outstanding = {k: sum((max(ZERO, _num(x[k]) - _num(x['returned'][k])) for x in state['alliance_loans']
                                   if x['alliance_id'] == agreement['id'] and x['from_id'] == lender and not x.get('closed')), ZERO) for k in KINDS}
            if not _positive(amount) or any(_num(amount[k]) + outstanding[k] > _num(limit[k]) for k in KINDS):
                _failure(state, actor, 'borrow', lender, '借调超过盟约承诺')
                continue
            if any(_num(amount[k]) > max(ZERO, _num(countries[lender][k]) - reserved.get(lender, {}).get(k, ZERO)) for k in KINDS):
                _failure(state, actor, 'borrow', lender, '出借方无力履行承诺')
                _break(state, actor, lender, '无力履行提供军事力量的承诺')
                continue
            for k in KINDS:
                countries[lender][k] = _json(_num(countries[lender][k]) - _num(amount[k]))
                countries[actor][k] = _json(_num(countries[actor][k]) + _num(amount[k]))
            existing = [x for x in state['alliance_loans'] if x['alliance_id'] == agreement['id']
                        and x['from_id'] == lender and x['to_id'] == actor]
            promise = agreement['offer_return'] if lender == agreement['a'] else agreement['request_return']
            # A contract's fixed repayment promise applies once per direction,
            # even when the lender makes several separate transfers.
            return_due = {k: (0.0 if existing else promise[k]) for k in KINDS}
            loan = {'id': f'{round_no}:{lender}:{actor}:{len(state["alliance_loans"])}', 'alliance_id': agreement['id'],
                    'from_id': lender, 'to_id': actor, **amount, 'return_due': return_due,
                    'returned': {k: 0.0 for k in KINDS}, 'closed': False}
            state['alliance_loans'].append(loan)
            _emit(state, {'type': 'loan', **deepcopy(loan)}, (actor, lender))
            outstanding_for_actor = {
                k: sum((max(ZERO, _num(x[k]) - _num(x['returned'][k]))
                        for x in state['alliance_loans']
                        if x['alliance_id'] == agreement['id'] and x['from_id'] == lender
                        and x['to_id'] == actor and not x.get('closed')), ZERO)
                for k in KINDS
            }
            if not _public(agreement) and any(outstanding_for_actor[k] >= 1 for k in KINDS):
                agreement['public'] = True
                agreement['revealed_round'] = round_no
                _emit(state, {'type': 'alliance_revealed',
                              'countries': sorted([agreement['a'], agreement['b']])}, public=True)
                _emit(state, {'type': 'alliance_revealed_detail',
                              'alliance_id': agreement['id'], 'trigger': 'foreign_force_at_least_1'},
                      (agreement['a'], agreement['b']))

    # Explicit repayment amount, and optional final settlement / non-return.
    for actor in ids:
        for item in decisions[actor].get('returns', []):
            lender = item.get('to_id')
            amount = _amounts({k: item.get(k, 0) for k in KINDS})
            loans = [x for x in state['alliance_loans'] if x['from_id'] == lender and x['to_id'] == actor and not x['closed']]
            if not loans:
                _failure(state, actor, 'return', lender, '没有未结借调')
                continue
            due = {k: sum((_num(x['return_due'][k]) - _num(x['returned'][k]) for x in loans), ZERO) for k in KINDS}
            if any(_num(amount[k]) > due[k] or _num(amount[k]) > max(ZERO, _num(countries[actor][k]) - reserved[actor][k]) for k in KINDS):
                _failure(state, actor, 'return', lender, '归还超过约定或当前余额')
                continue
            for k in KINDS:
                left = _num(amount[k]); countries[actor][k] = _json(_num(countries[actor][k]) - left)
                countries[lender][k] = _json(_num(countries[lender][k]) + left)
                for loan in loans:
                    take = min(left, _num(loan['return_due'][k]) - _num(loan['returned'][k]))
                    loan['returned'][k] = _json(_num(loan['returned'][k]) + take)
                    left -= take
            if item.get('close', False):
                unpaid = {k: _json(sum((_num(x['return_due'][k]) - _num(x['returned'][k]) for x in loans), ZERO)) for k in KINDS}
                for loan in loans:
                    loan['closed'] = True
                if _positive(unpaid):
                    _break(state, actor, lender, '未履行返还承诺')
            else:
                unpaid = None
            _emit(state, {'type': 'return', 'from_id': actor, 'to_id': lender,
                          'amount': amount, 'closed': bool(item.get('close', False)), 'unpaid': unpaid}, (actor, lender))

    # Invitations are created after responses and do not become eligible until next round.
    for actor in ids:
        for index, item in enumerate(decisions[actor].get('alliance_invites', [])):
            target = item.get('target_id')
            if not _eligible(state, actor, target, planned_conflicts):
                _failure(state, actor, 'invite', target, '邀请条件不满足')
                continue
            offer, request = _amounts(item.get('offer')), _amounts(item.get('request'))
            offer_return, request_return = _amounts(item.get('offer_return')), _amounts(item.get('request_return'))
            if not (_num(offer['conventional']) >= 1 or _num(offer['nuclear']) > 0):
                _failure(state, actor, 'invite', target, '邀请须承诺至少1常规力量或正数核力量')
                continue
            if _positive(request) and _num(request['conventional']) < 1 and _num(request['nuclear']) == 0:
                _failure(state, actor, 'invite', target, '条件须至少1常规力量或正数核力量')
                continue
            if any(_num(offer[k]) > _num(countries[actor][k]) or _num(request[k]) > _num(countries[target][k]) for k in KINDS):
                _failure(state, actor, 'invite', target, '盟约承诺超过国家力量')
                continue
            invitation_id = f'{round_no}:{actor}:{target}:{index}'
            invitation = {'id': invitation_id, 'round': round_no, 'from_id': actor, 'to_id': target,
                          'status': 'pending', 'offer': offer, 'request': request,
                          'offer_return': offer_return, 'request_return': request_return}
            state['alliance_invitations'][invitation_id] = invitation
            _emit(state, {'type': 'alliance_invited', 'invite_id': invitation_id}, (actor, target))

    for item in state['alliance_invitations'].values():
        if item['status'] == 'pending' and item['round'] + 1 <= round_no:
            item['status'] = 'expired'
    return {'public': deepcopy(state['alliance_public_events'][before_public:]),
            'private': deepcopy(state['alliance_private_events'][before_private:])}


def break_conflicting_alliances(state):
    """Call after bilateral actions update conflicts; publish breaks, never terms."""
    _stores(state)
    for agreement in list(state['alliances'].values()):
        if agreement['active'] and _conflict(state, agreement['a'], agreement['b']):
            _break(state, agreement['a'], agreement['b'], '盟友间发生冲突')


def executable_decisions(state, decisions):
    """Explicitly replace actions which depend on failed transfers.

    Return a copied decision mapping and an audit list; the caller must persist
    both the submitted and effective decisions and count each as a system
    fallback. Settlement and replay must use the effective mapping. The
    declaration and alliance requests are left intact.
    """
    from .rules import ACTUAL_NUCLEAR
    effective = deepcopy(decisions)
    failures = []
    for cid, decision in sorted(effective.items()):
        actions = decision.get('actions', [])
        allocated = {k: sum((_num(a.get(k, 0)) for a in actions), ZERO) for k in KINDS}
        insufficient = [k for k in KINDS if allocated[k] > _num(state['countries'][cid].get(k, 0))]
        if any(a.get('action_id') in ACTUAL_NUCLEAR for a in actions) and _num(state['countries'][cid].get('nuclear', 0)) <= 0:
            if 'nuclear' not in insufficient:
                insufficient.append('nuclear')
        if insufficient:
            failures.append({'country_id': cid, 'reason': '未实际获得行动所需力量',
                             'insufficient': insufficient,
                             'submitted_actions': deepcopy(actions),
                             'effective_actions': []})
            decision['actions'] = []
    return effective, failures


def validate_decision_fields(decision):
    """Reject malformed alliance payloads before any sealed decision is committed.

    Context-dependent conflicts and resource availability are rechecked at
    execution time, since simultaneous choices may make earlier valid choices
    impossible. Those are logged as private failed requests, not forged actions.
    """
    specs = {
        'alliance_invites': ({'target_id'}, {'target_id', 'offer', 'request', 'offer_return', 'request_return'}),
        'alliance_responses': ({'invite_id', 'accept'}, {'invite_id', 'accept', 'counter_offer', 'counter_return'}),
        'alliance_breaks': ({'target_id'}, {'target_id'}),
        'borrows': ({'from_id'}, {'from_id', 'conventional', 'nuclear'}),
        'returns': ({'to_id'}, {'to_id', 'conventional', 'nuclear', 'close'}),
    }
    for field, (required, allowed) in specs.items():
        entries = decision.get(field, [])
        if not isinstance(entries, list):
            raise ValueError(f'{field} 必须为数组')
        for item in entries:
            if not isinstance(item, dict) or not required <= item.keys() or set(item) - allowed:
                raise ValueError(f'{field} 内容字段不合法')
            for key in ('target_id', 'invite_id', 'from_id', 'to_id'):
                if key in item and (not isinstance(item[key], str) or not item[key]):
                    raise ValueError(f'{field}.{key} 必须为非空字符串')
            for key in ('accept', 'close'):
                if key in item and not isinstance(item[key], bool):
                    raise ValueError(f'{field}.{key} 必须是布尔值')
            for key in ('offer', 'request', 'offer_return', 'request_return', 'counter_offer', 'counter_return'):
                if key in item:
                    _amounts(item[key])
            for key in KINDS:
                if key in item:
                    _num(item[key])
