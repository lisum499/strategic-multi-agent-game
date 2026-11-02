import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({server: {middlewareMode: true, hmr: false, ws: false}, appType:'custom'});
try {
  const {StrategicMap, mapActions, CountryBrief} = await server.ssrLoadModule('/src/StrategicMap.tsx');
  const selected = {round:2, settlement:{
    pairs:[{parties:['C1','C2'],actions:{C1:'战略核战争',C2:'战略核战争'}},{parties:['C1','C3'],actions:{C1:'核威胁',C3:'保持不变'}}],
    automatic_second_strikes:[{source:'C2',target:'C1',action_id:'战略核战争'}],
    settlement_fallbacks:[{country_id:'C3',target_id:'C1',submitted_action:{action_id:'军事示威'},reason:'未实际获得行动所需力量'}]
  }};
  const actions = mapActions(selected);
  assert.equal(actions.length,4);
  assert.deepEqual(actions.map(a=>[a.source,a.target,a.kind,a.cancelled,a.automatic]), [
    ['C1','C2','nuclear',false,false], ['C2','C1','nuclear',false,true], ['C1','C3','other',false,false], ['C3','C1','cancelled',true,false],
  ]);
  assert.equal(mapActions({round:1,settlement:{}}),null);
  const names={C1:'甲',C2:'乙',C3:'丙'};
  const html=renderToStaticMarkup(React.createElement(StrategicMap,{rounds:[selected],selected,round:2,setRound:()=>{},names}));
  for (const text of ['甲 → 乙','乙 → 甲','自动二次打击','未执行','未实际获得行动所需力量','同一轮行动统一结算']) assert.ok(html.includes(text),`missing ${text}`);
  assert.ok(!html.includes('保持不变'), 'neutral placeholders are not executed arrows');
  const brief=renderToStaticMarkup(React.createElement(CountryBrief, {id:'C1', country:{global_presence:51, conventional:33, nuclear:5, alive:true}, initial:{profile:'初始画像'}, decision:{declaration:'公开声明',rationale:'私人理由'}, selected, calls:[], actions:mapActions(selected), names}));
  for (const text of ['初始画像','公开声明','私人理由','战略核战争','51','33']) assert.ok(brief.includes(text), `missing ${text}`);
  assert.ok(!brief.includes('军事示威 →'), 'cancelled action must not be presented as executed');
  const empty=renderToStaticMarkup(React.createElement(StrategicMap,{rounds:[],selected:null,round:0,setRound:()=>{},names}));
  assert.ok(empty.includes('等待回合结算'));
  console.log('Strategic map settlement and rendering checks passed');
} finally { await server.close(); }
