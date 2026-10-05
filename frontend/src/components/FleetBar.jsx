import { useState } from 'react';
import { useStore } from '../state/useStore.js';
import { selectAircraft, setForecast } from '../state/store.js';
import { fleet } from '../data/fleet.js';
import { C } from '../lib/colors.js';
import { ready, rul } from '../lib/health.js';

const FORECASTS = [[0, 'Now'], [10, '+10'], [20, '+20'], [30, '+30']];

export default function FleetBar() {
  const app = useStore();
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('id');

  const q = query.trim().toLowerCase();
  const rows = fleet.map((e, i) => [e, i]).filter(([e]) => e.id.toLowerCase().includes(q));
  if (sort == 'rul') rows.sort((a, b) => rul(a[0]) - rul(b[0]));

  return (
    <div className="fbar">
      <div id="fleet">
        {rows.map(([e, i]) => (
          <div key={e.id} className={'ac ' + (i == app.sel ? 'on' : '')} data-i={i} onClick={() => selectAircraft(i)}>
            <span><span className="dot" style={{ background: ready(e) ? C.ok : C.bad }}></span>{e.id}</span>
            <span className="num">{rul(e)}</span>
          </div>
        ))}
      </div>
      <div className="ft">
        <input id="fq" placeholder="Search aircraft" value={query} onChange={(ev) => setQuery(ev.target.value)} />
        <select id="fs" value={sort} onChange={(ev) => setSort(ev.target.value)}>
          <option value="id">Sort: ID</option>
          <option value="rul">Sort: RUL</option>
        </select>
        <div id="fc" className="seg">
          <span>Forecast</span>
          {FORECASTS.map(([f, label]) => (
            <button key={f} data-f={f} className={app.fc === f ? 'on' : ''} onClick={() => setForecast(f)}>{label}</button>
          ))}
        </div>
      </div>
    </div>
  );
}
