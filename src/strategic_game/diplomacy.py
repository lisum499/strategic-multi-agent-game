"""Structured, publicly confirmed diplomatic agreements."""
from __future__ import annotations

from copy import deepcopy

KINDS = frozenset({'ceasefire', 'non_aggression', 'cooperation', 'consultation', 'other'})


def _stores(state):
    state.setdefault('diplomatic_proposals', {})
    state.setdefault('diplomatic_agreements', {})
    state.setdefault('diplomatic_events', [])


def validate_decision_fields(decision):
    proposals = decision.get('diplomatic_proposals')
    responses = decision.get('diplomatic_responses')
    if not isinstance(proposals, list) or not isinstance(responses, list):
        raise ValueError('diplomatic_proposals 和 diplomatic_responses 必须是数组')
    targets = set()
    for item in proposals:
        if (not isinstance(item, dict) or set(item) != {'target_id', 'kind', 'terms'}
                or not isinstance(item['target_id'], str) or item['kind'] not in KINDS
                or not isinstance(item['terms'], str) or not item['terms'].strip()
                or len(item['terms']) > 1000):
            raise ValueError('外交提案须包含合法的 target_id、kind 和非空 terms')
        if item['target_id'] in targets:
            raise ValueError('同轮对同一国家至多提出一项外交协议')
        targets.add(item['target_id'])
    seen = set()
    for item in responses:
        if (not isinstance(item, dict) or set(item) != {'proposal_id', 'accept'}
                or not isinstance(item['proposal_id'], str) or type(item['accept']) is not bool
                or item['proposal_id'] in seen):
            raise ValueError('外交回应须包含唯一 proposal_id 和布尔值 accept')
        seen.add(item['proposal_id'])


def view(state):
    pending = [deepcopy(x) for x in state.get('diplomatic_proposals', {}).values() if x['status'] == 'pending']
    active = [deepcopy(x) for x in state.get('diplomatic_agreements', {}).values() if x.get('active', True)]
    return {'pending_proposals': sorted(pending, key=lambda x: x['id']),
            'agreements': sorted(active, key=lambda x: x['id']),
            'events': deepcopy(state.get('diplomatic_events', []))}


def resolve(state, decisions):
    """Accept only prior-round proposals; then publish new proposals."""
    _stores(state)
    round_no = state['round']
    events = []
    living = {cid for cid, country in state['countries'].items() if country.get('alive', True)}

    for actor in sorted(decisions):
        for response in decisions[actor].get('diplomatic_responses', []):
            proposal = state['diplomatic_proposals'].get(response['proposal_id'])
            if (not proposal or proposal['status'] != 'pending' or proposal['target_id'] != actor
                    or proposal['created_round'] + 1 != round_no):
                events.append({'type': 'diplomatic_response_failed', 'actor': actor,
                               'proposal_id': response['proposal_id'], 'reason': '提案不存在、非本国提案或不在下一轮'})
                continue
            if proposal['from_id'] not in living or actor not in living:
                proposal['status'] = 'invalidated'
                events.append({'type': 'diplomatic_proposal_invalidated', 'proposal_id': proposal['id']})
            elif response['accept']:
                proposal['status'] = 'accepted'
                agreement = {'id': proposal['id'], 'from_id': proposal['from_id'],
                             'target_id': actor, 'kind': proposal['kind'], 'terms': proposal['terms'],
                             'confirmed_round': round_no, 'active': True}
                state['diplomatic_agreements'][agreement['id']] = agreement
                events.append({'type': 'diplomatic_agreement_confirmed', 'agreement': deepcopy(agreement)})
            else:
                proposal['status'] = 'rejected'
                events.append({'type': 'diplomatic_proposal_rejected', 'proposal_id': proposal['id'],
                               'from_id': proposal['from_id'], 'target_id': actor})

    for proposal in state['diplomatic_proposals'].values():
        if proposal['status'] == 'pending' and proposal['created_round'] + 1 <= round_no:
            proposal['status'] = 'expired'
            events.append({'type': 'diplomatic_proposal_expired', 'proposal_id': proposal['id']})

    for actor in sorted(decisions):
        for index, item in enumerate(decisions[actor].get('diplomatic_proposals', [])):
            target = item['target_id']
            if target == actor or target not in living or actor not in living:
                events.append({'type': 'diplomatic_proposal_failed', 'actor': actor,
                               'target_id': target, 'reason': '目标无效或已退场'})
                continue
            proposal_id = f'{round_no}:{actor}:{target}:{index}'
            proposal = {'id': proposal_id, 'from_id': actor, 'target_id': target,
                        'kind': item['kind'], 'terms': item['terms'],
                        'created_round': round_no, 'response_due_round': round_no + 1,
                        'status': 'pending'}
            state['diplomatic_proposals'][proposal_id] = proposal
            events.append({'type': 'diplomatic_proposal_created', 'proposal': deepcopy(proposal)})
    state['diplomatic_events'].extend({'round': round_no, **event} for event in events)
    return events
