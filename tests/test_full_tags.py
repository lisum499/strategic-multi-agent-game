import copy, json, unittest
from pathlib import Path
from strategic_game import protocol, runtime, rules
from strategic_game.knowledge import load_dictionary, describe_tags, describe_world_tags

class FullTagsTests(unittest.TestCase):
    def scenario(self):
        s=json.loads(Path(runtime.DEFAULT_SCENARIO).read_text())
        s['world_tags']={'polarity':'多极','system_environment':'紧张/复杂','strategic_environment':'约束'}
        s['countries']['C1']['strategic_culture']='现实主义'
        s['countries']['C1']['ideology_focus']=6
        s['countries']['C1']['tags']={
            'foreign_policy':'威权','war_aim':'利益',
            'threat_perception':'急迫','comprehensive_power':'强国',
            'power_c':2,'power_e':3,'strategic_will':5,
            'stakeholders':{'allies':['C2'],'adversaries':['C3']},'relative_geography':{'C2':'两国接壤'}}
        s['countries']['C2']['strategic_culture']='民族主义'
        s['countries']['C2']['ideology_focus']=8
        s['countries']['C2']['tags']={}
        s['countries']['C1']['conventional']=90;s['countries']['C1']['nuclear']=60
        return s
    def test_world_public_country_private_and_two_source_views(self):
        s=protocol.validate_scenario(self.scenario());s['round']=1
        self.assertEqual(len(s['countries']['C1']['tag_knowledge']['dimensions']),9)
        self.assertEqual(s['world_tag_knowledge']['dimensions']['polarity']['value'],'多极')
        v1=runtime.situation_for(s,'C1',[],rules.DEFAULT_CONFIG); v2=runtime.situation_for(s,'C2',[],rules.DEFAULT_CONFIG)
        p1=protocol.build_prompts(v1)[1];p2=protocol.build_prompts(v2)[1]
        self.assertIn('两国接壤',p1);self.assertNotIn('两国接壤',p2)
        self.assertIn('多极',p1);self.assertIn('多极',p2)
        self.assertIn('核禁忌',p1);self.assertIn('不同',p1)
        self.assertNotIn('source_material',p1)
        public=json.dumps(runtime.public_state(s),ensure_ascii=False)
        self.assertIn('多极',public);self.assertNotIn('两国接壤',public)
        self.assertNotIn('tags',runtime.public_state(s)['countries']['C1'])
        self.assertNotIn('tag_knowledge',runtime.public_state(s)['countries']['C1'])
    def test_all_options_and_scale_extremes(self):
        d,h=load_dictionary();ids={'C1','C2','C3'}
        for key,info in d['dimensions'].items():
            for value in info.get('values',{}):
                if key=='ideology' and value=='侧重程度':continue
                with self.subTest(key=key,value=value):describe_tags({key:value},d,h,ids,'C1')
            if 'scale' in info:
                for value in (1,5):describe_tags({key:value},d,h,ids,'C1')
        for key,info in d['world_dimensions'].items():
            for value in info.get('values',{}):describe_world_tags({key:value},d,h)
    def test_invalid_references_and_values_rejected(self):
        for tags in ({'power_m':1},{'ideology':3},{'threat_perception':'无'},{'stakeholders':{'allies':['C1']}},{'relative_geography':{'C9':'距离远'}},{'stakeholders':{'allies':['C2','C2']}}):
            with self.subTest(tags=tags):
                s=self.scenario();s['countries']['C1']['tags']=tags
                with self.assertRaises(protocol.V6ValidationError): protocol.validate_scenario(s)
        s=self.scenario();s['world_tags']['polarity']='未知'
        with self.assertRaises(protocol.V6ValidationError):protocol.validate_scenario(s)
        s=self.scenario();s['countries']['C1']['strategic_culture']='不存在'
        with self.assertRaises(protocol.V6ValidationError):protocol.validate_scenario(s)
    def test_threat_assessment_private_and_persistent(self):
        s=protocol.validate_scenario(self.scenario());s['round']=1
        choices={cid:protocol.make_fallback(runtime.situation_for(s,cid,[],rules.DEFAULT_CONFIG)) for cid in s['countries']}
        choices['C1']['threat_assessment']='秘密判断：C2可能升级，依据有限。'
        protocol.validate_decision(choices['C1'],runtime.situation_for(s,'C1',[],rules.DEFAULT_CONFIG))
        new,record=runtime.advance(s,choices,rules.DEFAULT_CONFIG)
        self.assertIn('秘密判断',json.dumps(runtime.situation_for(new,'C1',[],rules.DEFAULT_CONFIG),ensure_ascii=False))
        self.assertNotIn('秘密判断',json.dumps(runtime.situation_for(new,'C2',[],rules.DEFAULT_CONFIG),ensure_ascii=False))
        self.assertNotIn('秘密判断',json.dumps(runtime.public_round(record,choices,new),ensure_ascii=False))
        self.assertEqual(new['private_threat_assessments']['C1']['round'],1)
    def test_missing_fields_stay_unknown(self):
        s=json.loads(Path(runtime.DEFAULT_SCENARIO).read_text());s=protocol.validate_scenario(s)
        self.assertNotIn('world_tag_knowledge',s)
        self.assertTrue(all(not x.get('tag_knowledge', {}).get('dimensions') for x in s['countries'].values()))

class FullTagsReplayTests(unittest.TestCase):
    def test_saved_replay_with_private_observations(self):
        import contextlib, io, tempfile
        from unittest.mock import patch
        from strategic_game import cli
        class Agent:
            name='scripted'; model='test'; last_call_info={}
            def decide(self, phase, system, user, situation):
                d=protocol.make_fallback(situation)
                d['threat_assessment']='仅本国可见-'+situation['country_id']
                return json.dumps(d,ensure_ascii=False)
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run'
            scenario=Path(__file__).resolve().parents[1]/'src/strategic_game/scenarios/full_tags_example.json'
            with patch.object(cli,'get_provider',return_value=Agent()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['run','--provider','openai','--scenario',str(scenario),'--rounds','2','--out',str(out)]),0)
            data=json.loads((out/'run.json').read_text())
            self.assertTrue(runtime.replay(data)[0])
            public=(out/'public.json').read_text()
            self.assertNotIn('仅本国可见',public)
            self.assertNotIn('两国接壤',public)
            self.assertIn('仅本国可见-C1',data['rounds'][0]['state']['private_threat_assessments']['C1']['text'])
