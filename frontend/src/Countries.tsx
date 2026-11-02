import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import './Countries.css';

type Stakeholders = { allies?: string[]; adversaries?: string[]; others?: string[]; description?: string };
export type Country = {
  name: string; strategic_culture: string; ideology_focus: number;
  economic_presence: number; military_presence: number; soft_presence: number;
  conventional: number; nuclear: number; profile?: string; alive?: boolean;
  tags?: { stakeholders?: Stakeholders };
};
export type Config = {
  countries: Record<string, Country>; world_context: string; rounds: number;
  provider: string; model: string; base_url: string; output_parent: string;
  deepseek_key_configured?: boolean;
};

const fields = [
  ['economic_presence', '经济存在', '经济规模及对外影响力'],
  ['military_presence', '军事存在', '军事能力的国际存在'],
  ['soft_presence', '软实力存在', '文化、制度与外交影响'],
  ['conventional', '常规力量', '可用于常规行动与防御的力量'],
  ['nuclear', '核力量', '当前核力量决定可用核行动权限'],
] as const;
const cultures = ['国际主义', '民族主义', '进步主义', '现实主义'];

/** Front-end checks mirror the scenario fields edited here; the engine remains authoritative. */
export function validateCountries(countries: Record<string, Country>): string[] {
  const entries = Object.entries(countries);
  const errors: string[] = [];
  if (entries.length < 2) errors.push('至少需要两个国家。');
  for (const [id, c] of entries) {
    const name = `${c.name?.trim() || id}（${id}）`;
    if (!c.name?.trim() || Array.from(c.name).length > 200) errors.push(`${id}：国家名称不能为空，且不能超过 200 字。`);
    if (!cultures.includes(c.strategic_culture)) errors.push(`${name}：请选择有效的战略文化。`);
    if (!Number.isInteger(c.ideology_focus) || c.ideology_focus < 1 || c.ideology_focus > 10) errors.push(`${name}：意识形态侧重必须是 1–10 的整数。`);
    for (const [key, label] of fields) {
      if (!Number.isFinite(c[key]) || c[key] < 0 || c[key] > 100) errors.push(`${name}：${label}必须是 0–100 的数字。`);
    }
    if (c.conventional < 40 && c.nuclear > 0) errors.push(`${name}：开局常规力量低于 40 时，核力量必须为 0。`);
    if (Number.isFinite(c.economic_presence) && Number.isFinite(c.military_presence) && Number.isFinite(c.soft_presence) &&
        0.43 * c.economic_presence + 0.21 * c.military_presence + 0.36 * c.soft_presence < 0.0000005) errors.push(`${name}：初始全球存在指数必须大于 0，请至少填写一项存在值。`);
    if (c.profile && Array.from(c.profile).length > 10000) errors.push(`${name}：私人画像不能超过 10000 字。`);
  }
  return errors;
}

export function Countries({config, setConfig}: {config: Config; setConfig: (config: Config) => void}) {
  const entries = Object.entries(config.countries);
  const [selectedId, setSelectedId] = useState(entries[0]?.[0] || '');
  const id = config.countries[selectedId] ? selectedId : entries[0]?.[0];
  const current = id ? config.countries[id] : undefined;
  const update = <K extends keyof Country>(key: K, value: Country[K]) => {
    if (!id) return;
    setConfig({...config, countries: {...config.countries, [id]: {...current!, [key]: value}}});
  };
  const add = () => {
    let n = 1; while (config.countries[`C${n}`]) n++;
    const newId = `C${n}`;
    setConfig({...config, countries: {...config.countries, [newId]: {name: `国家${n}`, strategic_culture: '现实主义', ideology_focus: 5, economic_presence: 50, military_presence: 50, soft_presence: 50, conventional: 50, nuclear: 0, profile: '', alive: true}}});
    setSelectedId(newId);
  };
  const remove = () => {
    if (!id || !window.confirm(`确定删除 ${current?.name || id}（${id}）？其他国家对该国的关系标签也会移除。`)) return;
    const countries = Object.fromEntries(entries.filter(([cid]) => cid !== id).map(([cid, country]) => {
      const stakeholders = country.tags?.stakeholders;
      if (!stakeholders) return [cid, country];
      const next = {...stakeholders};
      for (const key of ['allies', 'adversaries', 'others'] as const) {
        if (next[key]) next[key] = next[key]!.filter(target => target !== id);
      }
      return [cid, {...country, tags: {...country.tags, stakeholders: next}}];
    }));
    setConfig({...config, countries});
    setSelectedId(Object.keys(countries)[0] || '');
  };
  const errors = validateCountries(config.countries);
  return <div className="country-editor">
    <div className="country-editor-intro"><div><h2>国家与开局条件</h2><p>选择国家编辑公开状态和仅该国可见的私人画像。至少需要两个国家。</p></div><button className="primary" onClick={add}><Plus/>添加国家</button></div>
    {errors.length > 0 && <div className="country-errors" role="alert"><strong>请先修正 {errors.length} 处配置</strong><ul>{errors.map((error, index) => <li key={index}>{error}</li>)}</ul></div>}
    <div className="country-editor-layout"><div className="country-picker" aria-label="国家列表">
      {entries.map(([cid, country]) => <button key={cid} className={id === cid ? 'selected' : ''} onClick={() => setSelectedId(cid)}><span>{country.name || '未命名国家'}</span><small>{cid} · {country.strategic_culture}</small></button>)}
    </div>
    {current && <section className="country-detail"><div className="country-detail-head"><div><h3>{current.name || '未命名国家'}</h3><span>{id}</span></div><button className="country-delete" onClick={remove}><Trash2/>删除国家</button></div>
      <div className="country-grid"><label>国家名称<input maxLength={200} value={current.name} onChange={e => update('name', e.target.value)} /><small>必填，最多 200 字。</small></label>
      <label>战略文化<select value={current.strategic_culture} onChange={e => update('strategic_culture', e.target.value)}>{cultures.map(culture => <option key={culture}>{culture}</option>)}</select><small>影响国家如何理解局势，不直接提供数值加成。</small></label>
      <label>意识形态侧重<input type="number" min="1" max="10" step="1" value={Number.isFinite(current.ideology_focus) ? current.ideology_focus : ''} onChange={e => update('ideology_focus', e.target.valueAsNumber)} /><small>1–10 的整数；越高越强调意识形态原则。</small></label></div>
      <h4>存在值与力量</h4><p className="country-hint">每项为 0–100。全球存在指数由经济 × 0.43、军事 × 0.21、软实力 × 0.36 计算，开局必须大于 0。开局常规力量低于 40 时不能拥有核力量。</p>
      <div className="country-grid">{fields.map(([key, label, description]) => <label key={key}>{label}<input type="number" min="0" max="100" step="any" value={Number.isFinite(current[key]) ? current[key] : ''} onChange={e => update(key, e.target.valueAsNumber)} /><small>{description}</small></label>)}</div>
      <label className="country-profile">私人画像<textarea maxLength={10000} value={current.profile || ''} onChange={e => update('profile', e.target.value)} placeholder="描述本国背景、利益、国内压力和战略考量……"/><small>仅在该国的决策上下文中使用；可留空，最多 10000 字。请勿填写真实敏感信息。</small></label>
    </section>}</div>
  </div>;
}
