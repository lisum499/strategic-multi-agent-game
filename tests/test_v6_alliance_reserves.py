import unittest
from strategic_game import alliances as a

def setup():
    s={'round':1,'countries':{x:{'alive':True,'conventional':30,'nuclear':0} for x in ('A','B','C')},'conflicts':{x:{} for x in ('A','B','C')}}
    a.resolve(s, {'A':{'alliance_invites':[{'target_id':'B','offer':{'conventional':10},'offer_return':{'conventional':10}}]}})
    s['round']=2
    a.resolve(s, {'B':{'alliance_responses':[{'invite_id':'1:A:B:0','accept':True}]}})
    s['round']=3
    return s

def request(s, amount=10):
    round_no=s['round']
    a.resolve(s, {'B':{'borrows':[{'from_id':'A','conventional':amount}]}})
    s['round']=round_no+1
    return f'{round_no}:A:B:0'

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
        self.assertEqual(events['public'],[])
        ended=[event for event in events['private'] if event['type']=='alliance_ended_detail']
        self.assertEqual(len(ended),1)
        self.assertEqual(ended[0]['round'],3)
    def test_eligible_loan_keeps_lender_fighting_force(self):
        s=setup()
        request_id=request(s)
        decisions={'A':{'loan_responses':[{'request_id':request_id,'accept':True}],
                        'actions':[{'target_id':'C','action_id':'军事示威','conventional':20,'nuclear':0}]},
                   'B':{'actions':[{'target_id':'C','action_id':'军事示威','conventional':35,'nuclear':0,
                                    'requires_loan_request_ids':[request_id]}]}}
        a.resolve(s,decisions)
        self.assertEqual(s['countries']['A']['conventional'],20)
        self.assertEqual(s['countries']['B']['conventional'],40)
        actual, failures=a.executable_decisions(s,decisions)
        self.assertFalse(failures)
    def test_fund_reservation_return(self):
        s=setup(); request_id=request(s); a.resolve(s, {'A':{'loan_responses':[{'request_id':request_id,'accept':True}]}}); s['round']+=1
        decisions={'B':{'actions':[{'target_id':'C','action_id':'军事示威','conventional':35,'nuclear':0}], 'returns':[{'to_id':'A','conventional':10,'close':True}]}}
        a.resolve(s,decisions)
        self.assertEqual(s['countries']['B']['conventional'],40)
        self.assertEqual(s['countries']['A']['conventional'],20)
    def test_only_failed_dependent_action_is_removed(self):
        s=setup(); request_id=request(s)
        decisions={'A':{'loan_responses':[{'request_id':request_id,'accept':False}]},
                   'B':{'actions':[
                       {'target_id':'A','action_id':'保持不变','conventional':0,'nuclear':0},
                       {'target_id':'C','action_id':'军事示威','conventional':35,'nuclear':0,
                        'requires_loan_request_ids':[request_id]}]}}
        a.resolve(s,decisions)
        actual,failures=a.executable_decisions(s,decisions)
        self.assertEqual([x['target_id'] for x in actual['B']['actions']],['A'])
        self.assertEqual(failures[0]['target_id'],'C')

if __name__=='__main__': unittest.main()
