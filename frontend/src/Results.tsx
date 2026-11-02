import './Results.css';

const nuclear = new Set(['有限核武器使用', '升级性核行动', '扩大核打击行动', '战略核战争']);
const military = new Set(['军事示威', '有限军事试探', '升级边境军事行动', '常规军事施压', '大规模常规军事行动', '纵深常规打击', '最大规模常规作战']);
const majorCombat = new Set(['大规模常规军事行动', '纵深常规打击', '最大规模常规作战', '战略核战争']);
const fieldNames: Record<string, string> = {global_presence: '综合存在值', economic_presence: '经济存在', military_presence: '军事存在', soft_presence: '软实力', conventional: '常规力量', nuclear: '核力量', alive: '在场状态'};
const format = (value: unknown) => typeof value === 'number' ? Number(value.toFixed(3)).toString() : typeof value === 'boolean' ? (value ? '在场' : '退场') : value == null ? '无' : typeof value === 'object' ? JSON.stringify(value) : String(value);

/** 结算事实以 pairs 为准：其中包括自动反击，而 effective_decisions 只保存有效的提交决策。 */
export function Results({selected, names, calls = []}: {selected: any; names: Record<string, string>; calls?: any[]}) {
  if (!selected) return null;
  const settlement = selected.settlement;
  const pairs = Array.isArray(settlement?.pairs) ? settlement.pairs : null;
  const name = (id: string) => names[id] ? `${names[id]}（${id}）` : id;
  const actions: {actor: string; target: string; action: string; automatic: boolean; pair: any}[] = [];
  for (const pair of pairs || []) {
    const parties = Array.isArray(pair.parties) ? pair.parties : [];
    if (parties.length !== 2) continue;
    for (const [actor, target] of [[parties[0], parties[1]], [parties[1], parties[0]]]) {
      const action = pair.actions?.[actor];
      if (!nuclear.has(action) && !military.has(action)) continue;
      const automatic = (settlement.automatic_second_strikes || []).some((s: any) => s.source === actor && s.target === target && s.action_id === action);
      actions.push({actor, target, action, automatic, pair});
    }
  }
  const cancellations = Array.isArray(settlement?.settlement_fallbacks) ? settlement.settlement_fallbacks : [];
  const failedCalls = calls.filter(call => call.round === selected.round && call.status !== 'started' && call.status !== 'completed');
  const changes = (Array.isArray(selected.state_changes) ? selected.state_changes : []).filter((item: any) => {
    const parts = String(item.path || '').split('/');
    return parts.length === 3 && parts[0] === 'countries' && fieldNames[parts[2]];
  });
  return <section className="round-results" aria-label="本轮结算">
    <header className="results-header"><div><span>ROUND {selected.round}</span><h2>本轮结算与重大事件</h2></div><small>基于程序结算记录</small></header>
    {!pairs ? <p className="results-unknown">此记录没有可用的逐对结算数据，无法确认本轮军事或核行动是否执行。</p> : <>
      <div className="results-summary"><span className={actions.some(x => nuclear.has(x.action)) ? 'result-badge nuclear' : 'result-badge'}>核武器使用：{actions.filter(x => nuclear.has(x.action)).length} 项</span><span className={actions.some(x => majorCombat.has(x.action)) ? 'result-badge military' : 'result-badge'}>大规模作战／核战争：{actions.filter(x => majorCombat.has(x.action)).length} 项</span><span className={actions.some(x => military.has(x.action)) ? 'result-badge military' : 'result-badge'}>常规军事行动：{actions.filter(x => military.has(x.action)).length} 项</span><span className={cancellations.length ? 'result-badge canceled' : 'result-badge'}>结算时取消：{cancellations.length} 项</span></div>
      {actions.length ? <div className="major-events">{actions.map((item, i) => <article className={nuclear.has(item.action) ? 'major-event nuclear-event' : 'major-event'} key={`${item.actor}-${item.target}-${i}`}>
        <strong>{nuclear.has(item.action) ? '核武器使用' : '常规军事行动'}{item.automatic ? ' · 自动二次打击' : ''}</strong>
        <p>{name(item.actor)} → {name(item.target)}：{item.action}</p>
        <small>结算强度 {format(item.pair.intensity)}{Object.entries(item.pair.losses || {}).map(([id, loss]: [string, any]) => ` · ${name(id)}损失：常规 ${format(loss?.conventional ?? 0)}、核 ${format(loss?.nuclear ?? 0)}`).join('')}</small>
      </article>)}</div> : <p className="results-empty">本轮结算未记录常规军事行动或实际核武器使用。核威胁、核示威等不计为核武器使用。</p>}
      {cancellations.length > 0 && <div className="canceled-events"><h3>提交后未执行的行动</h3>{cancellations.map((item: any, i: number) => <p key={i}><strong>{name(item.country_id)}</strong> 对 {name(item.target_id)} 的“{item.submitted_action?.action_id || '行动'}”被取消：{item.reason || '结算条件未满足'}{item.failed_dependencies?.length ? `（未履约借调：${item.failed_dependencies.join('、')}）` : ''}。这属于结算取消，不表示模型调用失败。</p>)}</div>}
      <details className="executed-actions"><summary>查看全部有效行动（含非军事行动）</summary>{Object.entries(settlement.effective_decisions || {}).map(([id, decision]: [string, any]) => <p key={id}><strong>{name(id)}：</strong>{decision.actions?.length ? decision.actions.map((a: any) => `对 ${name(a.target_id)}：${a.action_id}`).join('；') : '无有效提交行动'}</p>)}<p className="results-note">自动二次打击以重大事件和结算明细为准，未必出现在有效提交行动中。</p></details>
    </>}
    <details className="changes"><summary>国家状态变化（{changes.length} 项）</summary>{changes.length ? <div className="changes-list">{changes.map((item: any, i: number) => {const [, id, field] = item.path.split('/'); const delta = typeof item.before === 'number' && typeof item.after === 'number' ? item.after - item.before : null; return <div key={i}><span>{name(id)} · {fieldNames[field]}</span><span>{format(item.before)} → {format(item.after)}{delta !== null && delta !== 0 ? `（${delta > 0 ? '+' : ''}${format(delta)}）` : ''}</span></div>})}</div> : <p className="results-empty">没有可用的国家数值变化记录。</p>}</details>
    {failedCalls.length > 0 && <details className="call-failures"><summary>数据质量与调用记录（{failedCalls.length} 次）</summary><p className="results-note">若后续重试成功，不代表该国最终使用兜底决策。具体原因请查看运行日志；模型调用失败与结算取消分别记录。</p>{failedCalls.map((call, i) => <p key={i}><strong>{name(call.country_id)}</strong> · 第 {call.attempt} 次：{call.error ? String(call.error) : '未记录详细原因'}{call.fallback === true ? '；本轮已使用保守兜底决策' : ''}</p>)}</details>}
    {Array.isArray(selected.state_changes) && <details className="settlement-raw"><summary>查看全部状态变化明细</summary><pre>{JSON.stringify(selected.state_changes, null, 2)}</pre></details>}
    {settlement && <details className="settlement-raw"><summary>查看完整结算明细</summary><pre>{JSON.stringify(settlement, null, 2)}</pre></details>}
  </section>;
}
