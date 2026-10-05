import { app, createWorkOrder } from '../../state/store.js';
import { ENG, PARTS } from '../../data/parts.js';
import { C, hc, rk } from '../../lib/colors.js';
import { ph, partRisk, rul, partDetail, comp, subH, weakestModule } from '../../lib/health.js';
import SubBar from './SubBar.jsx';
import SensorTable from './SensorTable.jsx';

const ENG_MODULES = ['fan', 'hpc', 'hpt', 'lpt'];

// Inspector while a part is zoomed in: health, sub-components, records, spares, agency,
// sensors, work order.
//
// Everything below the health figure now comes from GET /aircraft/{code}/parts/{part}:
// the technical records are real maintenance records rather than two hardcoded strings
// with an invented cycle number, the spare is the stocked part reference with its real
// lead time, and back-in-service is the API's own slot + turnaround + lead-time
// arithmetic. Previously all three were fabricated locally, so the inspector could quote
// an agency and a lead time the work order it creates was never booked with.
export default function PartDetail({ e }) {
  const k = app.cur;
  const p = PARTS[k];
  const h = ph(e, k);
  const risk = partRisk(e, k) || (h == null ? null : rk(h));
  const d = partDetail(e, k);
  const eng = k === 'eng';
  const woKey = `${e.idx}:${k}`;
  const wo = app.WO[woKey];

  const sp = d?.spare || (eng ? e.engineSpare : null);
  const ag = d?.agency;
  const bis = d?.back_in_service_breakdown;
  const s = sp?.stock;

  return (
    <>
      <div className="blk">
        <h2>Health</h2>
        <div className="kpi" style={{ color: hc(h ?? 1) }}>
          {h == null ? '--' : Math.round(h * 100) + '%'}
          <small>
            {risk ? risk + (eng && e.rul != null ? ' - ' + rul(e) + ' cycles left' : '') : 'awaiting data'}
          </small>
        </div>
      </div>

      {eng
        ? ENG_MODULES.map((c) => <SubBar key={c} name={ENG[c]} h={comp(e, c)} />)
        : p.subs.map((n, i) => <SubBar key={n} name={n} h={subH(e, k, i)} />)}

      {eng ? <canvas id="hc" width="560" height="180"></canvas> : null}

      {d?.action && risk !== 'healthy' ? (
        <div className="card"><h3>Recommended action</h3><p>{d.action}</p></div>
      ) : null}

      <div className="card">
        <h3>Technical records</h3>
        {d?.records?.length
          ? d.records.map((r) => (
              <p key={r.id}>
                {`${new Date(r.date).toISOString().slice(0, 10)}: ${r.type || 'event'}`}
                {r.fault ? ` - ${r.fault}` : ''}
                {r.action ? `. ${r.action}` : ''}
                {r.cycles_at_event != null ? ` (cycle ${r.cycles_at_event})` : ''}
              </p>
            ))
          : <p className="note">{d ? 'No maintenance records for this part.' : 'Loading records…'}</p>}
      </div>

      <div className="card">
        <h3>Spares</h3>
        {sp ? (
          <>
            <p>{sp.item_name + ': '}<b style={{ color: s > 0 ? 'inherit' : C.bad }}>{s > 0 ? s + ' in stock' : 'out of stock'}</b></p>
            <p className="note">
              {`${sp.part_ref_id}${sp.criticality ? ' · criticality ' + sp.criticality : ''}` +
                (sp.lead_time_days ? ` · ${sp.lead_time_days} day lead time if ordered` : '')}
              {sp.minimum_stock ? ` · minimum stock ${sp.minimum_stock}` : ''}
              {sp.supplier ? ` · ${sp.supplier}` : ''}
            </p>
          </>
        ) : <p className="note">Loading spare…</p>}
      </div>

      <div className="card">
        <h3>Maintenance agency</h3>
        {ag ? (
          <>
            <p>{`${ag.name}: free slot in ${ag.free_slot_days} days`}</p>
            <p className="note">
              {`${ag.specialisation}${ag.location ? ' · ' + ag.location : ''}. Turnaround ${ag.turnaround_days} days.` +
                (d?.back_in_service_days != null ? ` Back in service in about ${d.back_in_service_days} days.` : '')}
            </p>
            {bis ? (
              <p className="note">
                {`${bis.slot_days} slot + ${bis.turnaround_days} turnaround` +
                  (bis.lead_time_applied ? ` + ${bis.lead_time_days} lead time` : ' (part in stock)')}
              </p>
            ) : null}
          </>
        ) : <p className="note">Loading agency…</p>}
      </div>

      {eng ? <SensorTable e={e} /> : null}

      {risk && risk !== 'healthy' ? (
        <button className="wo" onClick={() => createWorkOrder(woKey)}>
          {wo ? wo + ' created' : 'Create work order'}
        </button>
      ) : null}

      <p className="note">
        {eng
          ? <>Health from the ML model{e.modelInfo?.fallback ? ' (deterministic fallback)' : ''}. Part model is illustrative.</>
          : <>Health derived from maintenance burden, not the engine model. Part model is illustrative.</>}
      </p>
    </>
  );
}
