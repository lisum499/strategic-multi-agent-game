/** Compare consecutive settled snapshots; absent observations stay unknown. */
export const finite = (v:unknown): v is number => typeof v==='number' && Number.isFinite(v);
export const presence = (countries:any,id:string):number|null => finite(countries?.[id]?.global_presence) ? countries[id].global_presence : null;
export const formatDelta = (value:number) => {
  const n = Math.round(value * 1_000_000) / 1_000_000;
  return `${n > 0 ? '+' : ''}${n.toFixed(6).replace(/\.?0+$/, '') || '0'}`;
};
export function roundDeltas(selected:any, rounds:any[], initialCountries:Record<string,any>) {
  const out:Record<string,number> = {};
  if (!selected || !Number.isInteger(selected.round)) return out;
  const previous = selected.round === 1 ? initialCountries : rounds.find(r=>r.round===selected.round-1)?.state?.countries;
  for (const id of Object.keys(selected.state?.countries||{})) {
    const before=presence(previous,id), after=presence(selected.state.countries,id);
    if (before!==null && after!==null) out[id]=Math.round((after-before)*1_000_000)/1_000_000;
  }
  for (const row of Array.isArray(selected.state_changes)?selected.state_changes:[]) {
    const match=/^countries\/([^/]+)\/global_presence$/.exec(row.path||'');
    if (match && finite(row.before) && finite(row.after)) out[match[1]]=Math.round((row.after-row.before)*1_000_000)/1_000_000;
  }
  return out;
}
export function extremes(deltas:Record<string,number>) {
  const entries=Object.entries(deltas); const positives=entries.filter(([,v])=>v>0); const negatives=entries.filter(([,v])=>v<0);
  const max=positives.length?Math.max(...positives.map(([,v])=>v)):null;
  const min=negatives.length?Math.min(...negatives.map(([,v])=>v)):null;
  return {gains:max===null?[]:positives.filter(([,v])=>v===max), losses:min===null?[]:negatives.filter(([,v])=>v===min)};
}
