import { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Map as MapIcon } from 'lucide-react';
import './StrategicMap.css';
import { formatDelta, roundDeltas } from './presence';
import { PresenceHighlights, PresenceTrend } from './PresenceTrend';

type MapAction = {source:string; target:string; action:string; kind:Kind; cancelled:boolean; automatic:boolean; reason?:string};
type Kind = 'nuclear'|'military'|'economic'|'other'|'cancelled';
const nuclear = new Set(['有限核武器使用', '升级性核行动', '扩大核打击行动', '战略核战争']);
const military = new Set(['军事示威','有限军事试探','升级边境军事行动','常规军事施压','大规模常规军事行动','纵深常规打击','最大规模常规作战']);
const economic = new Set(['经济施压', '扩大经济战']);
const classify = (action:string):Kind => nuclear.has(action) ? 'nuclear' : military.has(action) ? 'military' : economic.has(action) ? 'economic' : 'other';

/** Only pair results are authoritative for execution; neutral placeholders are not actions. */
export function mapActions(selected:any): MapAction[] | null {
  const settlement = selected?.settlement;
  if (!Array.isArray(settlement?.pairs)) return null;
  const actions:MapAction[] = [];
  for (const pair of settlement.pairs) {
    if (!Array.isArray(pair.parties) || pair.parties.length !== 2) continue;
    const [a,b] = pair.parties;
    if (typeof a !== 'string' || typeof b !== 'string') continue;
    for (const [source,target] of [[a,b],[b,a]]) {
      const action = pair.actions?.[source];
      if (typeof action !== 'string' || !action || action === '保持不变') continue;
      const automatic = Array.isArray(settlement.automatic_second_strikes) && settlement.automatic_second_strikes.some((item:any) => item.source===source && item.target===target && item.action_id===action);
      actions.push({source,target,action,kind:classify(action),cancelled:false,automatic});
    }
  }
  if (Array.isArray(settlement.settlement_fallbacks)) for (const item of settlement.settlement_fallbacks) {
    if (typeof item.country_id !== 'string' || typeof item.target_id !== 'string') continue;
    actions.push({source:item.country_id,target:item.target_id,action:item.submitted_action?.action_id || '未知行动',kind:'cancelled',cancelled:true,automatic:false,reason:item.reason || '结算条件未满足'});
  }
  return actions;
}

type Props = {rounds:any[]; selected:any; round:number; setRound:(round:number)=>void; names:Record<string,string>; initialCountries?:Record<string,any>; calls?:any[]};
export function StrategicMap({rounds, selected, round, setRound, names, initialCountries = {}, calls = []}:Props) {
  const [country,setCountry] = useState('all');
  const ids = useMemo(() => Object.keys(names).sort((a,b)=>a.localeCompare(b,undefined,{numeric:true})), [names]);
  const cols = Math.max(2, Math.ceil(Math.sqrt(ids.length * 1.6)));
  const rows = Math.max(1, Math.ceil(ids.length / cols));
  const width = Math.max(900, cols * 190), height = Math.max(480, rows * 160 + 100);
  const positions = Object.fromEntries(ids.map((id,i) => [id, {x:width*(i%cols+1)/(cols+1), y:height*(Math.floor(i/cols)+1)/(rows+1)}]));
  const actions = mapActions(selected);
  const deltas = roundDeltas(selected,rounds,initialCountries);
  const countryStates = selected ? (selected.state?.countries || {}) : initialCountries;
  const formatValue = (value:any) => typeof value === 'number' && Number.isFinite(value) ? Number(value.toFixed(1)).toString() : '未知';
  const activeCountry = country === 'all' || ids.includes(country) ? country : 'all';
  const visible = (actions||[]).filter(a => positions[a.source] && positions[a.target] && (activeCountry==='all'||a.source===activeCountry));
  const roundIndex = rounds.findIndex(r => r.round === selected?.round);
  const name = (id:string) => names[id] || id;
  const pathFor = (source:string,target:string,offset=0) => {
    const from = positions[source], to = positions[target];
    const dx=to.x-from.x, dy=to.y-from.y, length=Math.hypot(dx,dy)||1, ux=dx/length, uy=dy/length;
    const x1=from.x+ux*33, y1=from.y+uy*33, x2=to.x-ux*40, y2=to.y-uy*40;
    const bend=Math.min(38,length*.13)+offset;
    return `M ${x1} ${y1} Q ${(x1+x2)/2-uy*bend} ${(y1+y2)/2+ux*bend} ${x2} ${y2}`;
  };
  return <div className="map-page"><div className="map-toolbar"><div><span className="map-kicker">STRATEGIC OVERVIEW</span><h2>回合局势地图</h2><p>节点外环及数值表示综合存在值；位置不代表地理距离，箭头依据结算绘制。</p></div><div className="map-controls"><button aria-label="上一轮" disabled={roundIndex<=0} onClick={()=>setRound(rounds[roundIndex-1].round)}><ChevronLeft/></button><select aria-label="地图选择轮次" value={round} onChange={e=>setRound(Number(e.target.value))}><option value={0}>最新轮次</option>{rounds.map(r=><option value={r.round} key={r.round}>第 {r.round} 轮</option>)}</select><button aria-label="下一轮" disabled={roundIndex<0||roundIndex>=rounds.length-1} onClick={()=>setRound(rounds[roundIndex+1].round)}><ChevronRight/></button><select aria-label="地图筛选行动方" value={activeCountry} onChange={e=>setCountry(e.target.value)}><option value="all">全部国家</option>{ids.map(id=><option key={id} value={id}>{name(id)}（{id}）</option>)}</select></div></div>
    <div className="map-layout"><div className="map-board"><div className="map-board-head"><span><MapIcon/> {selected ? `第 ${selected.round} 轮 · 统一结算后` : '等待回合结算'}</span><span className="map-filter-hint">点击国家节点，筛选其发起的行动</span></div><div className="map-canvas"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="国家与本轮结算行动示意图">
      <defs>{(['nuclear','military','economic','other','cancelled'] as Kind[]).map(k=><marker key={k} id={`map-arrow-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" className={`marker-${k}`}/></marker>)}<pattern id="map-grid" width="48" height="48" patternUnits="userSpaceOnUse"><path d="M 48 0 L 0 0 0 48" fill="none" stroke="#ffffff" strokeOpacity=".045"/></pattern></defs>
      <rect width={width} height={height} fill="url(#map-grid)"/>
      {visible.map((a,i)=><path key={`${a.source}-${a.target}-${a.action}-${i}`} d={pathFor(a.source,a.target,a.cancelled?12:0)} className={`map-line map-line-${a.kind}`} markerEnd={`url(#map-arrow-${a.kind})`}><title>{`${name(a.source)} → ${name(a.target)}：${a.action}${a.cancelled?`（未执行：${a.reason}）`:a.automatic?'（自动二次打击）':'（已结算）'}`}</title></path>)}
      {visible.length<=40&&visible.filter(a=>!a.cancelled).map((a,i)=><circle key={`flow-${a.source}-${a.target}-${i}`} r="3" className={`map-particle marker-${a.kind}`} aria-hidden="true"><animateMotion path={pathFor(a.source,a.target)} dur="3s" begin={`${(i%5)*0.3}s`} repeatCount="indefinite"/></circle>)}
      {ids.map(id=>{const p=positions[id], c=countryStates[id], v=typeof c?.global_presence === 'number' && Number.isFinite(c.global_presence) ? Math.max(0,Math.min(100,c.global_presence)) : null;return <g key={id} className={`map-node ${activeCountry===id?'map-node-active':''}`} transform={`translate(${p.x} ${p.y})`} tabIndex={0} role="button" aria-label={`筛选 ${name(id)} 发起的行动`} onClick={()=>setCountry(activeCountry===id?'all':id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setCountry(activeCountry===id?'all':id)}}}><circle className="map-node-halo" r="33"/>{v!==null&&<circle className="map-strength-ring" r="33" pathLength="100" strokeDasharray={`${v} 100`} transform="rotate(-90)"/>}<circle className="map-node-core" r="25"/><text className="map-node-id" textAnchor="middle" dominantBaseline="central">{id}</text><text className="map-node-name" textAnchor="middle" y="53">{name(id)}</text><text className="map-node-value" textAnchor="middle" y="68">{c?.alive===false ? '已退场' : `存在值 ${formatValue(c?.global_presence)}`}</text>{deltas[id]!==undefined&&deltas[id]!==0&&<text className={deltas[id]>0?'presence-map-delta-up':'presence-map-delta-down'} textAnchor="middle" y="83">{formatDelta(deltas[id])}</text>}</g>})}
    </svg></div><div className="map-legend"><span><i className="legend-nuclear"/>实际核使用</span><span><i className="legend-military"/>常规军事</span><span><i className="legend-economic"/>经济行动</span><span><i className="legend-other"/>外交、核威慑及其他</span><span><i className="legend-cancelled"/>结算取消</span></div></div>
    <aside className="map-events">{activeCountry!=='all'&&<CountryBrief id={activeCountry} country={countryStates[activeCountry]} initial={initialCountries[activeCountry]} decision={selected?.decisions?.[activeCountry]} selected={selected} calls={calls} actions={actions} names={names}/>}<div className="map-events-head"><div><h3>行动清单</h3><p>{activeCountry==='all'?'全部行动方':`${name(activeCountry)} 发起`} · {visible.length} 项</p></div><span>{selected?`R${selected.round}`:'—'}</span></div>{!selected?<p className="map-empty">开始推演或打开历史记录后，已结算的回合会显示在这里。</p>:!actions?<p className="map-empty">此回合没有可用的逐对结算记录，无法确认行动是否执行。</p>:visible.length===0?<p className="map-empty">此筛选条件下没有需要显示的行动。</p>:<div className="map-event-list">{visible.map((a,i)=><div className={`map-event map-event-${a.kind}`} key={`${a.source}-${a.target}-${a.action}-${i}`}><div><strong>{a.action}</strong><span>{a.cancelled?'未执行':a.automatic?'自动二次打击':'已结算'}</span></div><p>{name(a.source)} → {name(a.target)}</p>{a.reason&&<small>原因：{a.reason}</small>}</div>)}</div>}<p className="map-footnote">同一轮行动统一结算，图中连线不表示行动先后。核威慑与实际核使用分开标识。</p></aside></div>
  {selected&&<><PresenceHighlights selected={selected} rounds={rounds} initialCountries={initialCountries} names={names}/><PresenceTrend selected={selected} rounds={rounds} initialCountries={initialCountries} names={names}/></>}
  </div>;
}

export function CountryBrief({id,country,initial,decision,selected,calls,actions,names}:{id:string;country:any;initial:any;decision:any;selected:any;calls:any[];actions:MapAction[]|null;names:Record<string,string>}) {
  const fmt=(n:any)=>typeof n==='number' && Number.isFinite(n) ? Number(n.toFixed(2)).toString() : '未知';
  const fallback=calls.some(c=>c.round===selected?.round && c.country_id===id && c.fallback===true);
  const executed=actions?.filter(a=>a.source===id && !a.cancelled);
  return <section className="map-country-brief"><div className="map-brief-title"><div><small>{selected ? `第 ${selected.round} 轮结算后` : '开局状态'}</small><h3>{names[id]||id}</h3></div><span>{country?.alive===false?'退场':country?'在场':'状态未知'}</span></div>
    <div className="map-metrics">{[['综合存在',country?.global_presence],['常规力量',country?.conventional],['核力量',country?.nuclear],['经济存在',country?.economic_presence],['军事存在',country?.military_presence],['软实力',country?.soft_presence]].map(([label,value])=><div key={String(label)}><small>{label}</small><strong>{fmt(value)}</strong></div>)}</div>
    <details><summary>开局国家画像（私人）</summary><p>{initial?.profile || '未提供画像'}</p></details>
    {selected&&<><details open><summary>本轮公开声明</summary><p>{decision?.declaration || '无可用声明'}</p></details><details open><summary>实际结算行动</summary>{actions===null?<p>缺少结算记录，无法确认。</p>:executed?.length?executed.map((a,i)=><p key={i}>{a.action} → {names[a.target]||a.target}{a.automatic?'（自动二次打击）':''}</p>):<p>没有已记录的行动。</p>}</details><details><summary>本轮私人理由{fallback?'（保守兜底）':''}</summary><p>{decision?.rationale || '无可用私人理由'}</p><small>该内容是模型或兜底决策的私人理由，不属于公开声明。</small></details></>}
  </section>;
}
