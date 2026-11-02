import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { Results } = await server.ssrLoadModule('/src/Results.tsx');
  const names = { C1: '甲国', C2: '乙国' };
  const selected = { round: 1, state_changes: [{path: 'countries/C1/nuclear', before: 41, after: 30}], settlement: {
    pairs: [{parties: ['C1', 'C2'], actions: {C1: '战略核战争', C2: '战略核战争'}, intensity: 1, losses: {C1: {conventional: 5, nuclear: 11}, C2: {conventional: 2, nuclear: 3}}}],
    automatic_second_strikes: [{source: 'C2', target: 'C1', action_id: '战略核战争'}],
    settlement_fallbacks: [{country_id: 'C1', target_id: 'C2', submitted_action: {action_id: '军事示威'}, reason: '借调依赖未履约'}],
    effective_decisions: {}
  }};
  const html = renderToStaticMarkup(React.createElement(Results, {selected, names, calls: [{round: 1, country_id: 'C1', attempt: 1, status: 'failed', error: '模型请求或输出校验失败', fallback: true}]}));
  for (const expected of ['战略核战争', '自动二次打击', '借调依赖未履约', '结算时取消', '核力量', '甲国', '模型请求或输出校验失败', '后续重试成功', '保守兜底决策']) assert.ok(html.includes(expected), `missing ${expected}`);
  const unknown = renderToStaticMarkup(React.createElement(Results, {selected: {round: 1}, names}));
  assert.ok(unknown.includes('无法确认'), 'missing indication of absent settlement data');
  console.log('Results rendering checks passed');
} finally {
  await server.close();
}
