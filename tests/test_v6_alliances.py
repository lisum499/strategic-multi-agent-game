import copy
import unittest
from strategic_game import alliances as a


def world():
    return {'round':1, 'countries':{x:{'alive':True, 'conventional':30, 'nuclear':5} for x in ('A','B','C')}, 'conflicts':{x:{} for x in ('A','B','C')}}

def invite():
    return {'target_id':'B','offer':{'conventional':10},'request':{},'offer_return':{'conventional':10},'request_return':{}}

class AllianceTest(unittest.TestCase):
    def establish(self):
        s=world()
        a.resolve(s, {'A':{'alliance_invites':[invite()]}})
        self.assertEqual(a.view(s)['alliances'], [])
        self.assertEqual(len(a.view(s,'B')['invitations']),1)
        self.assertEqual(len(a.view(s,'C')['invitations']),0)
        s['round']=2
        a.resolve(s, {'B':{'alliance_responses':[{'invite_id':'1:A:B:0','accept':True}]}})
        return s
    def borrow(self, s, amount, *, accept=True):
        request_round = s['round']
        a.resolve(s, {'B':{'borrows':[{'from_id':'A','conventional':amount}]}})
        request_id = f'{request_round}:A:B:0'
        s['round'] = request_round + 1
        a.resolve(s, {'A':{'loan_responses':[{'request_id':request_id,'accept':accept}]}})
        return request_id
    def test_invite_next_round_and_privacy(self):
        s=self.establish()
        self.assertEqual(a.view(s,'C')['alliances'],[])
        self.assertEqual(a.view(s,'C')['contracts'],[])
        self.assertEqual(a.view(s,'C')['loans'],[])
        self.assertEqual(len(a.view(s,'A')['contracts']),1)
        self.assertFalse(a.view(s,'A')['contracts'][0]['public'])
        self.assertNotIn('offer', str(a.view(s)))
    def test_not_accept_same_round(self):
        s=world()
        a.resolve(s,{'A':{'alliance_invites':[invite()]},'B':{'alliance_responses':[{'invite_id':'1:A:B:0','accept':True}]}})
        self.assertEqual(a.view(s)['alliances'],[])
    def test_borrow_and_refuse_repayment(self):
        s=self.establish(); s['round']=3
        self.borrow(s,10)
        self.assertEqual(a.view(s)['alliances'],[{'countries':['A','B']}])
        self.assertEqual(s['alliance_public_events'][-1]['type'],'alliance_revealed')
        self.assertEqual(s['countries']['A']['conventional'],20)
        self.assertEqual(s['countries']['B']['conventional'],40)
        s['countries']['B']['conventional'] -= 3 # borrower takes losses
        s['round']=5
        a.resolve(s,{'B':{'returns':[{'to_id':'A','conventional':0,'close':True}]}})
        self.assertEqual(s['countries']['B']['conventional'],37)
        self.assertEqual(s['countries']['A']['conventional'],20)
        self.assertEqual(a.view(s)['alliances'],[])
        self.assertEqual(s['alliance_private_events'][-1]['unpaid']['conventional'],10)
    def test_cannot_lend_more_than_commitment(self):
        s=self.establish(); s['round']=3
        a.resolve(s,{'B':{'borrows':[{'from_id':'A','conventional':11}]}})
        self.assertEqual(s['countries']['A']['conventional'],30)
        self.assertEqual(s['countries']['B']['conventional'],30)
    def test_rejection_and_no_response_break_alliance(self):
        s=self.establish(); s['round']=3
        request_id=self.borrow(s,5,accept=False)
        self.assertEqual(s['pending_loan_requests'][request_id]['status'],'rejected')
        self.assertFalse(s['alliances']['A|B']['active'])

        s=self.establish(); s['round']=3
        a.resolve(s,{'B':{'borrows':[{'from_id':'A','conventional':5}]}})
        s['round']=4
        a.resolve(s,{'A':{}})
        self.assertEqual(s['pending_loan_requests']['3:A:B:0']['status'],'rejected_no_response')
        self.assertFalse(s['alliances']['A|B']['active'])
    def test_allied_conflict_breaks(self):
        s=self.establish(); s['conflicts']['A']['B']=.3
        a.break_conflicting_alliances(s)
        self.assertEqual(a.view(s)['alliances'],[])
        self.assertEqual(a.view(s)['events'],[])
        self.assertEqual(s['alliance_private_events'][-1]['type'],'alliance_ended_detail')

    def test_reveal_threshold_is_cumulative_per_borrower(self):
        s=self.establish(); s['round']=3
        self.borrow(s,.5)
        self.assertEqual(a.view(s)['alliances'],[])
        s['round']=5
        self.borrow(s,.5)
        self.assertEqual(a.view(s)['alliances'],[{'countries':['A','B']}])
        self.assertEqual(s['alliance_public_events'][-1]['type'],'alliance_revealed')
        s['round']=7
        a.resolve(s,{'B':{'returns':[{'to_id':'A','conventional':1,'close':False}]}})
        self.assertEqual(a.view(s)['alliances'],[{'countries':['A','B']}])

    def test_only_public_ally_blocks_new_invitation(self):
        s=self.establish(); s['round']=3; s['conflicts']['A']['C']=.3
        offer={'target_id':'B','offer':{'conventional':1}}
        a.resolve(s,{'C':{'alliance_invites':[offer]}})
        self.assertIn('3:C:B:0',s['alliance_invitations'])

        s=self.establish(); s['round']=3; s['conflicts']['A']['C']=.3
        self.borrow(s,1)
        s['round']=5
        a.resolve(s,{'C':{'alliance_invites':[offer]}})
        self.assertNotIn('3:C:B:0',s['alliance_invitations'])
        self.assertEqual(s['alliance_private_events'][-1]['type'],'alliance_request_failed')
    def test_fixed_repayment_and_partial(self):
        s=self.establish(); s['round']=3
        self.borrow(s,10)
        s['round']=5
        a.resolve(s,{'B':{'returns':[{'to_id':'A','conventional':7,'close':True}]}})
        self.assertEqual(s['countries']['A']['conventional'],27)
        self.assertEqual(s['countries']['B']['conventional'],33)
        self.assertEqual(s['alliance_private_events'][-1]['unpaid']['conventional'],3)

if __name__=='__main__': unittest.main()
