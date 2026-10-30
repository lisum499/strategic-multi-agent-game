import unittest
from strategic_game import v5_alliances as a

def setup():
    s={'round':1,'countries':{x:{'alive':True,'conventional':30,'nuclear':0} for x in ('A','B','C')},'conflicts':{x:{} for x in ('A','B','C')}}
    a.resolve(s, {'A':{'alliance_invites':[{'target_id':'B','offer':{'conventional':10},'offer_return':{'conventional':10}}]}})
    s['round']=2
    a.resolve(s, {'B':{'alliance_responses':[{'invite_id':'1:A:B:0','accept':True}]}})
    s['round']=3
    return s

class ReservationTests(unittest.TestCase):
    def test_prevents_double_spending(self):
        s=setup()
        decisions={'A':{'actions':[{'target_id':'C','action_id':'军事示威','conventional':25,'nuclear':0}]},
                   'B':{'borrows':[{'from_id':'A','conventional':10}], 'actions':[{'target_id':'C','action_id':'军事示威','conventional':35,'nuclear':0}]},'C':{}}
        a.resolve(s, decisions)
        self.assertEqual(s['countries']['A']['conventional'],30)
        self.assertEqual(s['countries']['B']['conventional'],30)
        self.assertEqual(a.view(s)['alliances'],[])
        actual, failures=a.executable_decisions(s,decisions)
        self.assertEqual(actual['A']['actions'],decisions['A']['actions'])
        self.assertEqual(actual['B']['actions'],[])
        self.assertEqual(failures[0]['country_id'],'B')
        self.assertEqual(decisions['B']['actions'][0]['conventional'],35)
    def test_break_before_borrow_and_correct_round(self):
        s=setup()
        decisions={'A':{'actions':[{'target_id':'B','action_id':'初步外交施压','conventional':0,'nuclear':0}]},'B':{'borrows':[{'from_id':'A','conventional':10}]}}
        events=a.resolve(s,decisions)
        self.assertEqual(a.view(s)['alliances'],[])
        self.assertEqual(s['countries']['B']['conventional'],30)
        self.assertEqual(events['public'][0]['round'],3)
        self.assertEqual(events['public'][0]['type'],'alliance_ended')
    def test_eligible_loan_keeps_lender_fighting_force(self):
        s=setup()
        decisions={'A':{'actions':[{'target_id':'C','action_id':'军事示威','conventional':20,'nuclear':0}]},'B':{'borrows':[{'from_id':'A','conventional':10}]}}
        a.resolve(s,decisions)
        self.assertEqual(s['countries']['A']['conventional'],20)
        self.assertEqual(s['countries']['B']['conventional'],40)
        actual, failures=a.executable_decisions(s,decisions)
        self.assertFalse(failures)
    def test_fund_reservation_return(self):
        s=setup(); a.resolve(s, {'B':{'borrows':[{'from_id':'A','conventional':10}]}}); s['round']=4
        decisions={'B':{'actions':[{'target_id':'C','action_id':'军事示威','conventional':35,'nuclear':0}], 'returns':[{'to_id':'A','conventional':10,'close':True}]}}
        a.resolve(s,decisions)
        self.assertEqual(s['countries']['B']['conventional'],40)
        self.assertEqual(s['countries']['A']['conventional'],20)

if __name__=='__main__': unittest.main()
