import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const {SceneBrief}=await server.ssrLoadModule('/src/SceneBrief.tsx');
  const config={world_context:'当前设定',countries:{}};
  const history=renderToStaticMarkup(React.createElement(SceneBrief,{initial:{world_context:'历史背景',countries:{}},config,hasRun:true}));
  assert.ok(history.includes('历史背景'));
  assert.ok(!history.includes('当前设定'));
  const old=renderToStaticMarkup(React.createElement(SceneBrief,{initial:{countries:{}},config,hasRun:true}));
  assert.ok(old.includes('没有保存'));
  assert.ok(!old.includes('当前设定'));
  console.log('Scene background provenance checks passed');
} finally {await server.close()}
