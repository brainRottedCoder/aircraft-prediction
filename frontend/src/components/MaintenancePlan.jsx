import { useStore } from '../state/useStore.js';
import { C } from '../lib/colors.js';
import { PLAN_HEADERS, planRows, downloadPlanCsv } from '../lib/plan.js';

export default function MaintenancePlan() {
  useStore();
  return (
    <section className="pn s12 rv" id="plan">
      <div className="hd">
        <h2>Maintenance plan</h2>
        <p>What to do, by when, and where the spare comes from</p>
        <button className="wo" id="csv" onClick={downloadPlanCsv}>Export CSV</button>
      </div>
      <div className="scroll">
        <table className="pl">
          <thead>
            <tr>{PLAN_HEADERS.map((h) => <th key={h}>{h}</th>)}</tr>
          </thead>
          <tbody id="sched">
            {planRows().map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.part}</td>
                <td><span className="pill" style={{ color: r.color, borderColor: r.color }}>{r.risk}</span></td>
                <td>{r.action}{r.wo ? <>{' '}<span className="pill">{r.wo}</span></> : null}</td>
                <td>{r.doBy}</td>
                <td style={{ color: r.spareBad ? C.bad : 'inherit' }}>{r.spare}</td>
                <td>{r.agency}</td>
                <td>{r.back}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
