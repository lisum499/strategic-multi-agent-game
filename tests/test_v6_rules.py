import unittest
from copy import deepcopy
from strategic_game import rules as r


def country(presence=100, conventional=20, nuclear=80):
    return {'name':'x','economic_presence':presence,'military_presence':presence,
            'soft_presence':presence,'conventional':conventional,'nuclear':nuclear,'alive':True,
            }

def state(ids=('A','B'), presence=100):
    return {'round':1,'countries':{c:country(presence) for c in ids},
            'conflicts':{}, 'threat_history':{},'nuclear_used':[]}

def decision(ids, actions=None):
    return {cid:{'actions':(actions or {}).get(cid,[])} for cid in ids}

def a(target, action, conventional=0, nuclear=0):
    return {'target_id':target,'action_id':action,'conventional':conventional,'nuclear':nuclear}

class TestV6(unittest.TestCase):
    def test_ipg_formula_and_action_weighted_transfer(self):
        s=state()
        d=decision(('A','B'), {'A':[a('B','经济施压')]})
        n,log=r.settle_round(s,d)
        self.assertEqual(n['countries']['A']['global_presence'],102)
        self.assertEqual(n['countries']['B']['global_presence'],98)
        changes={row['dimension']:row['transferred'] for row in log['presence_transfers'] if row['payer']=='B'}
        self.assertEqual(changes['military_presence'],0)
        self.assertEqual(changes['economic_presence'],3.72093)
        self.assertEqual(changes['soft_presence'],1.111111)

    def test_deescalation_clamped_and_no_upper_cap(self):
        s=state(presence=1000); s['conflicts']={'A':{'B':2},'B':{'A':1}}
        d=decision(('A','B'),{'A':[a('B','完全投降')],'B':[a('A','保持不变')]})
        new,_=r.settle_round(s,d)
        self.assertEqual(new['conflicts']['A']['B'],0)
        s['conflicts']['A']['B']=100
        d['A']['actions']=[a('B','军事示威')]
        new,_=r.settle_round(s,d)
        self.assertEqual(new['conflicts']['A']['B'],104)
        self.assertEqual(s['conflicts']['A']['B'],100)
    def test_nuclear_requires_pre_action_threshold_and_nuclear(self):
        s=state(); s['conflicts']={'A':{'B':44}}
        d=decision(('A','B'),{'A':[a('B','有限核武器使用',10,1)]})
        with self.assertRaises(ValueError): r.settle_round(s,d)
        s['conflicts']['A']['B']=45
        d['A']['actions'][0]['nuclear']=0
        with self.assertRaises(ValueError): r.settle_round(s,d)
    def test_threat_expires_but_keeps_actual_level(self):
        s=state(presence=1000); s['conflicts']={'A':{'B':85},'B':{'A':1}}
        d=decision(('A','B'),{'A':[a('B','战略核威胁')]})
        n,log=r.settle_round(s,d)
        self.assertEqual(n['conflicts']['A']['B'],170)
        self.assertEqual(log['pairs'][0]['effective_levels']['A'],120)
        d=decision(('A','B'))
        _,log=r.settle_round(n,d)
        self.assertEqual(log['pairs'][0]['effective_levels']['A'],85)
    def test_mutual_nuclear_loss_and_mad(self):
        s=state(presence=1000); s['conflicts']={'A':{'B':45},'B':{'A':45}}
        d=decision(('A','B'),{'A':[a('B','有限核武器使用',20,20)], 'B':[a('A','有限核武器使用',20,20)]})
        n,log=r.settle_round(s,d)
        self.assertEqual(log['pairs'][0]['losses']['A']['conventional'],4.5)
        self.assertEqual(log['pairs'][0]['losses']['A']['nuclear'],1.08)
        s['conflicts']={'A':{'B':100},'B':{'A':100}}
        d['A']['actions'][0]['action_id']=d['B']['actions'][0]['action_id']='战略核战争'
        n,log=r.settle_round(s,d)
        self.assertFalse(n['countries']['A']['alive'])
        self.assertFalse(n['countries']['B']['alive'])
        self.assertEqual(n['outcome']['reason'],'all_eliminated')
        self.assertIsNone(n['outcome']['winner_id'])
    def test_quiet_no_loss(self):
        s=state(presence=1000); s['conflicts']={'A':{'B':100},'B':{'A':100}}
        n,log=r.settle_round(s,decision(('A','B')))
        self.assertEqual(log['pairs'][0]['losses']['A']['conventional'],0)
    def test_proportional_distribution(self):
        s=state(('A','B','C','D'))
        s['conflicts']={'B':{'A':30},'C':{'A':40},'D':{'A':50}}
        n,log=r.settle_round(s,decision(('A','B','C','D')))
        transfers=[x for x in log['presence_transfers'] if x['payer']=='A' and x['dimension']=='economic_presence']
        self.assertEqual([x['transferred'] for x in transfers],[25,33.333333,41.666667])
        self.assertEqual(n['countries']['A']["global_presence"],0)
        self.assertFalse(n['countries']['A']['alive'])
    def test_dead_beneficiary_returns(self):
        s=state(('A','B','C'),10)
        s['conflicts']={'B':{'A':10},'A':{'C':5}}
        n,log=r.settle_round(s,decision(('A','B','C')))
        c_to_a=[x for x in log['presence_transfers'] if x['payer']=='C' and x.get('receiver')=='A'][0]
        self.assertEqual(c_to_a['returned'],5)
        self.assertEqual(n['countries']['A']["global_presence"],0)
    def test_id_order_allows_prior_receipt_funding_later(self):
        s=state(('A','B','C'),10)
        s['conflicts']={'B':{'A':10},'C':{'B':20}}
        n,log=r.settle_round(s,decision(('A','B','C')))
        t=[x for x in log['presence_transfers'] if x['payer']=='B' and x.get('receiver')=='C'][0]
        self.assertEqual(t['transferred'],20)
        self.assertEqual(n['countries']['B']["global_presence"],0)
    def test_round_limit_no_winner(self):
        s=state(); s['round']=30
        n,_=r.settle_round(s,decision(('A','B')))
        self.assertEqual(n['outcome'],{'finished':True,'winner_id':None,'reason':'round_limit'})
    def test_multi_front_over_allocation_rejected(self):
        s=state(('A','B','C'))
        d=decision(('A','B','C'),{'A':[a('B','军事示威',15),a('C','军事示威',15)]})
        with self.assertRaises(ValueError): r.settle_round(s,d)

if __name__=='__main__': unittest.main()
