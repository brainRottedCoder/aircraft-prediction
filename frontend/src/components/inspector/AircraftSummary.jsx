import { app, openPart } from '../../state/store.js';
import { PARTS, KEYS } from '../../data/parts.js';
import { C, hc } from '../../lib/colors.js';
import { ph, partRisk, ready, rul, spare, agency } from '../../lib/health.js';

// Inspector while the whole aircraft is shown: readiness + list of parts.
//
// Health, risk and readiness all come from GET /api/v1/aircraft and
// /aircraft/{code}; the spare and agency lines come from the part detail the API
// returns, rather than from a hardcoded table of stock levels and three invented
// depots.
export default function AircraftSummary({ e }) {
  const ok = ready(e);
  const m = e.modelInfo;
  return (
    <>
      <div className="blk">
        <h2>{e.id}</h2>
        <div className="kpi" style={{ color: ok ? C.ok : C.bad }}>
          {ok ? 'Mission-ready' : 'Not ready'}
          <small>{e.rul == null ? 'Awaiting engine data' : `Engine remaining life: ${rul(e)} cycles`}</small>
        </div>
        <p className="note">
          {[e.tailNumber, e.model, e.homeBase].filter(Boolean).join(' · ')}
          {e.cycle != null ? ` · cycle ${e.cycle}` : ''}
        </p>
      </div>
      <h2>Parts (click to inspect)</h2>
      {KEYS.map((k) => {
        const h = ph(e, k);
        const r = partRisk(e, k);
        const sp = spare(e, k);
        const ag = agency(e, k);
        return (
          <button key={k} className="prt" data-k={k} onClick={() => openPart(k)}>
            <div className="row" style={{ margin: 0 }}>
              <span><span className="dot" style={{ background: hc(h ?? 1) }}></span>{PARTS[k].name}</span>
              <span className="mono" style={{ color: hc(h ?? 1) }}>
                {h == null ? '--' : Math.round(h * 100) + '%'}
              </span>
            </div>
            <div className="bar"><i style={{ width: (h ?? 0) * 100 + '%', background: hc(h ?? 1) }}></i></div>
            <p className="note">
              {h == null ? 'Awaiting data'
                : <>
                    {r ? r[0].toUpperCase() + r.slice(1) : ''}
                    {e.partSimulated?.[k] ? ' · derived from maintenance burden' : ' · from the ML model'}
                  </>}
              {sp ? ` · spare ${sp.stock > 0 ? sp.stock + ' in stock' : 'out of stock'}` : ''}
              {ag ? ` · ${ag.name}` : ''}
            </p>
          </button>
        );
      })}
      <p className="note" style={{ marginTop: 14 }}>
        {m
          ? <>Engine life from model {m.version}
              {m.fallback ? ' — currently the deterministic fallback, not the booster' : ''}
              {m.mae != null ? ` (CV RMSE ${Number(m.mae).toFixed(1)} cycles)` : ''}.
              Other parts are derived from maintenance burden, not the engine model.</>
          : 'Connecting to the fleet API…'}
      </p>
    </>
  );
}
