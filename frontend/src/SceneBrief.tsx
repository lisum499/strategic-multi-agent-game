import { useState } from 'react';
import './SceneBrief.css';

type Props = {initial:any; config:any; hasRun:boolean};
export function SceneBrief({initial,config,hasRun}:Props){
  const [expanded,setExpanded]=useState(false);
  const source=hasRun?initial:config;
  const world=source?.world_context;
  const countries=source?.countries||{};
  return <section className="scene-brief"><div className="scene-brief-heading"><div><span className="scene-kicker">WORLD BRIEFING</span><h2>{hasRun?'本次推演的开局背景':'当前设定的开局背景'}</h2></div><button onClick={()=>setExpanded(!expanded)}>{expanded?'收起国家画像':'查看国家画像'}</button></div>
    <p className="scene-intro">{world || (hasRun?'这份记录没有保存可供展示的世界背景。':'尚未设置世界背景，可在设置中补充。')}</p>
    {expanded&&<div className="scene-profiles"><p>以下私人画像来自开局设定，仅供主持人或研究者查看，不代表各国相互知晓。</p>{Object.entries(countries).map(([id,c]:[string,any])=><details key={id}><summary>{c.name||id} <small>{id} · {c.strategic_culture||'未设置战略文化'}</small></summary><p>{c.profile||'未填写私人画像。'}</p></details>)}</div>}
  </section>;
}
