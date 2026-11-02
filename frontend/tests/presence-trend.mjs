import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const {roundDeltas,extremes,formatDelta}=await server.ssrLoadModule('/src/presence.ts');
  const {PresenceHighlights,PresenceTrend}=await server.ssrLoadModule('/src/PresenceTrend.tsx');
  const initial={A:{global_presence:50},B:{global_presence:50},C:{global_presence:50},D:{global_presence:50}};
  const r1={round:1,state:{countries:{A:{global_presence:50.000001},B:{global_presence:49.9},C:{global_presence:49.9}}},state_changes:[]};
  const ds=roundDeltas(r1,[r1],initial);
  assert.equal(ds.A,.000001);assert.equal(ds.B,-.1);assert.equal(ds.D,undefined);
  assert.equal(formatDelta(ds.A),'+0.000001');
  assert.deepEqual(extremes(ds).losses.map(([id])=>id),['B','C']);
  const r3={round:3,state:{countries:{A:{global_presence:51}}}};
  assert.deepEqual(roundDeltas(r3,[r1,r3],initial),{});
  const authoritative={...r3,state_changes:[{path:'countries/A/global_presence',before:50.8,after:51}]};
  assert.equal(roundDeltas(authoritative,[r1,authoritative],initial).A,.2);
  const names={A:'甲',B:'乙',C:'丙',D:'丁'};
  const highlight=renderToStaticMarkup(React.createElement(PresenceHighlights,{selected:r1,rounds:[r1],initialCountries:initial,names}));
  for(const text of ['甲 +0.000001','乙 -0.1','丙 -0.1','并列均显示']) assert.ok(highlight.includes(text),`missing ${text}`);
  const trend=renderToStaticMarkup(React.createElement(PresenceTrend,{selected:r3,rounds:[r1,r3],initialCountries:initial,names}));
  for(const text of ['相对开局','原始数值','纵轴自动缩放','缺失记录会断开折线']) assert.ok(trend.includes(text),`missing ${text}`);
  const missing=renderToStaticMarkup(React.createElement(PresenceHighlights,{selected:r3,rounds:[r3],initialCountries:{},names}));
  assert.ok(missing.includes('数据不足'));
  console.log('Presence changes, ties, missing rounds and trend checks passed');
} finally {await server.close()}
