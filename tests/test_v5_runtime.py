import contextlib, io, json, tempfile, unittest
from pathlib import Path
from strategic_game import cli, runtime as v, protocol as p

class V5RuntimeTests(unittest.TestCase):
    def test_complete_record_public_and_replay(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)/'run'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['run','--provider','mock','--rounds','3','--out',str(out)]),0)
            data=json.loads((out/'run.json').read_text())
            self.assertEqual(data['rules_version'],'v5.0')
            self.assertEqual(len(data['rounds']),3)
            self.assertTrue(v.replay(data)[0])
            public=(out/'public.json').read_text()
            self.assertNotIn('rationale',public)
            self.assertNotIn('alliance_loans',public)
            self.assertNotIn('conventional',public)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['replay',str(out/'run.json')]),0)
    def test_failed_concurrent_borrow_falls_back_transactionally(self):
        state=p.validate_scenario(json.loads(Path(v.DEFAULT_SCENARIO).read_text()))
        state['round']=1
        config=__import__('strategic_game.rules',fromlist=['DEFAULT_CONFIG']).DEFAULT_CONFIG
        decisions={cid:p.make_fallback(v.situation_for(state,cid,[],config)) for cid in state['countries']}
        own=state['countries']['C1']['conventional']
        decisions['C1']['borrows']=[{'from_id':'C2','conventional':10,'nuclear':0}]
        decisions['C1']['actions']=[{'target_id':'C2','action_id':'军事示威','conventional':own+1,'nuclear':0}]
        p.validate_decision(decisions['C1'],v.situation_for(state,'C1',[],config))
        new,record=v.advance(state,decisions,config)
        self.assertEqual([x['country_id'] for x in record['settlement_fallbacks']],['C1'])
        self.assertEqual(record['effective_decisions']['C1']['actions'],[])
        self.assertNotIn('alliance_loans',state)
        self.assertEqual(v.public_round(record,decisions,new)['actions']['C1'],[])

    def test_call_sink_preserves_invalid_response_and_interruption(self):
        state=p.validate_scenario(json.loads(Path(v.DEFAULT_SCENARIO).read_text()))
        state['round']=1
        class BadProvider:
            name='bad'; last_call_info={}
            def decide(self,*args): return '{invalid'
        sink=[]; snapshots=[]
        _,calls,count=v.decisions_for(state,BadProvider(),[],{},retries=0,calls_sink=sink,on_call=lambda:snapshots.append(len(sink)))
        self.assertIs(calls,sink)
        self.assertEqual(count,len(state['countries']))
        self.assertEqual(sink[0]['response'],'{invalid')
        self.assertEqual(sink[0]['status'],'failed')
        self.assertTrue(snapshots)
        class Interrupted(BadProvider):
            def decide(self,*args): raise KeyboardInterrupt()
        sink=[]
        with self.assertRaises(KeyboardInterrupt):
            v.decisions_for(state,Interrupted(),[],{},retries=0,calls_sink=sink)
        self.assertEqual(sink[0]['status'],'started')

    def test_replay_rejects_tampered_public_record(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'run'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['run','--provider','mock','--rounds','2','--out',str(out)]),0)
            data=json.loads((out/'run.json').read_text())
            data['rounds'][0]['public']['declarations']['C1']='伪造信息'
            self.assertFalse(v.replay(data)[0])

    def test_private_view_hides_others_details(self):
        state=p.validate_scenario(json.loads(Path(v.DEFAULT_SCENARIO).read_text()))
        state['round']=1
        state['countries']['C2']['profile']='PRIVATE_SECRET'
        state['alliance_invitations']={'x':{'from_id':'C2','to_id':'C3','secret':'HIDDEN_SECRET'}}
        view=v.situation_for(state,'C1',[],{})
        self.assertNotIn('PRIVATE_SECRET',json.dumps(view))
        self.assertNotIn('HIDDEN_SECRET',json.dumps(view))
        self.assertNotIn('conventional',view['state']['countries']['C2'])
        self.assertNotIn('nuclear',view['state']['countries']['C2'])

if __name__ == '__main__': unittest.main()

class RealAdapterTests(unittest.TestCase):
    def test_model_receives_filtered_context_and_returns_valid_decision(self):
        from strategic_game.rules import DEFAULT_CONFIG
        class Provider:
            name='scripted'
            last_call_info={}
            def decide(self, phase, system, user, situation):
                assert phase == 'v5_sealed'
                assert all('profile' not in value for key, value in situation['state']['countries'].items() if key != situation['country_id'])
                return json.dumps(p.make_fallback(situation),ensure_ascii=False)
        state=p.validate_scenario(json.loads(Path(v.DEFAULT_SCENARIO).read_text()))
        state['round']=1
        decisions, calls, failed=v.decisions_for(state,Provider(),[],DEFAULT_CONFIG,retries=0)
        self.assertEqual(len(decisions),3)
        self.assertEqual(failed,0)
        self.assertTrue(all(x['status']=='completed' for x in calls))

class ReplayIntegrityTests(unittest.TestCase):
    def test_changed_parameter_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'run'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['run','--mock','--rounds','1','--out',str(out)]),0)
            path=out/'run.json'
            data=json.loads(path.read_text())
            data['rule_config']['conventional_loss_rate']='0.99'
            path.write_text(json.dumps(data,ensure_ascii=False))
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['replay',str(path)]),1)
    def test_old_record_can_show_saved_summary(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'old.json'
            path.write_text(json.dumps({'rules_version':'v4.1','rounds':[],'result':{'winner':None}}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['replay',str(path),'--show-only']),0)
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['replay',str(path)]),1)
