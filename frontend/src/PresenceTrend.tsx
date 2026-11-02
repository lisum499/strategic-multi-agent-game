import { useMemo, useState } from 'react';
import { extremes, finite, formatDelta, presence, roundDeltas } from './presence';
import './PresenceTrend.css';

type Props={rounds:any[];selected:any;initialCountries:Record<string,any>;names:Record<string,string>};
const colors=['#2467b2','#d45d54','#2c937e','#aa58a6','#c08323','#56718c','#6e64bc','#8a7139','#2683a3','#ba587f'];
export function PresenceHighlights({rounds,selected,initialCountries,names}:Props){
  const deltas=roundDeltas(selected,rounds,initialCountries), ex=extremes(deltas), name=(id:string)=>names[id]||id;
  if(!Object.keys(deltas).length)return <div className="presence-highlights"><p>本轮存在值变化数据不足，无法比较。</p></div>;
  return <div className="presence-highlights"><div><small>本轮增加最多</small><strong className="presence-up">{ex.gains.length?ex.gains.map(([id,v])=>`${name(id)} ${formatDelta(v)}`).join('、'):'无增加'}</strong></div><div><small>本轮减少最多</small><strong className="presence-down">{ex.losses.length?ex.losses.map(([id,v])=>`${name(id)} ${formatDelta(v)}`).join('、'):'无减少'}</strong></div><span>综合存在值 · 与上一轮结算后比较（第 1 轮与开局比较）；并列均显示</span></div>;
}
export function PresenceTrend({rounds,selected,initialCountries,names}:Props){
  const [mode,setMode]=useState<'change'|'value'>('change');
  const [focus,setFocus]=useState<string|null>(null);
  const ids=Object.keys(names).sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
  const rows=useMemo(()=>[{round:0,countries:initialCountries},...rounds.filter(r=>Number.isInteger(r.round)&&r.round<=selected?.round).sort((a,b)=>a.round-b.round).map(r=>({round:r.round,countries:r.state?.countries}))],[rounds,initialCountries,selected]);
  const series=ids.map((id,i)=>({id,color:colors[i%colors.length],values:rows.map(row=>{const value=presence(row.countries,id), base=presence(initialCountries,id);return mode==='change' ? value!==null&&base!==null?value-base:null:value})}));
  const shown=series.filter(s=>!focus||s.id===focus), values=shown.flatMap(s=>s.values.filter(finite));
  const w=720,h=250,left=62,right=18,top=16,bottom=39; let min=values.length?Math.min(...values):0,max=values.length?Math.max(...values):1;
  if(max===min){const padding=Math.max(Math.abs(max)*.005,.000001);min-=padding;max+=padding}else{const padding=(max-min)*.12;min-=padding;max+=padding}
  const x=(n:number)=>left+(w-left-right)*(n/(Math.max(1,selected?.round||0)));
  const y=(v:number)=>top+(h-top-bottom)*(max-v)/(max-min);
  const tick=(v:number)=>Math.abs(v)<0.0000005?'0':Number(v.toFixed(7)).toString();
  return <section className="presence-chart"><div className="presence-chart-header"><div><h3>综合存在值变化趋势</h3><p>展示至第 {selected?.round||0} 轮；{mode==='change'?'每国以自身开局值为 0':'纵轴为存在值原值'}。纵轴自动缩放，请留意刻度。</p></div><div className="presence-chart-controls"><button className={mode==='change'?'active':''} onClick={()=>setMode('change')}>相对开局</button><button className={mode==='value'?'active':''} onClick={()=>setMode('value')}>原始数值</button></div></div>
    {values.length===0?<p className="presence-chart-empty">缺少开局或回合数据，无法绘制当前视图。</p>:<div className="presence-chart-plot"><svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`各国综合存在值趋势，${mode==='change'?'相对各自开局':'原始数值'}，纵轴自动缩放`}>
      {[0,1,2,3,4].map(i=>{const v=min+(max-min)*i/4;return <g key={i}><line x1={left} x2={w-right} y1={y(v)} y2={y(v)} className="presence-grid"/><text x={left-8} y={y(v)+4} textAnchor="end" className="presence-axis">{tick(v)}</text></g>})}
      {rows.map(row=><text key={row.round} x={x(row.round)} y={h-14} textAnchor="middle" className="presence-axis">{rows.length<=15||row.round===0||row.round===selected?.round||row.round%5===0?row.round:''}</text>)}
      {shown.map(s=>{const segments:string[]=[];let segment:string[]=[];let prev:number|null=null;rows.forEach((row,i)=>{const v=s.values[i];if(v===null||prev!==null&&row.round!==prev+1){if(segment.length)segments.push(segment.join(' '));segment=[]}if(v!==null){segment.push(`${x(row.round)},${y(v)}`);prev=row.round}else prev=null});if(segment.length)segments.push(segment.join(' '));return <g key={s.id}>{segments.map((points,i)=><polyline key={i} fill="none" stroke={s.color} strokeWidth="2.3" points={points}/>)}{rows.map((row,i)=>s.values[i]===null?null:<circle key={row.round} cx={x(row.round)} cy={y(s.values[i]!)} r="3" fill={s.color}><title>{`${names[s.id]||s.id} · ${row.round===0?'开局':`第 ${row.round} 轮`}：${mode==='change'?formatDelta(s.values[i]!):Number(s.values[i]!.toFixed(6))}`}</title></circle>)}</g>})}
      <text x={w-right} y={h-2} textAnchor="end" className="presence-axis">回合</text></svg></div>}
    <div className="presence-legend"><button className={!focus?'active':''} onClick={()=>setFocus(null)}>全部国家</button>{series.map(s=><button key={s.id} className={focus===s.id?'active':''} onClick={()=>setFocus(focus===s.id?null:s.id)}><i style={{background:s.color}}/>{names[s.id]||s.id}</button>)}</div><p className="presence-chart-note">悬停数据点可查看具体数值；缺失记录会断开折线，点击国家可单独查看。</p>
  </section>;
}
