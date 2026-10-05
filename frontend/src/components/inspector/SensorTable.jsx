import { ENG } from '../../data/parts.js';
import { hc } from '../../lib/colors.js';

// Engine sensors, biggest real deviation first.
//
// These values used to be synthesised in the browser: each sensor was its hardcoded
// baseline scaled by the component's locally-computed health, so the "live sensors"
// table had no relationship to the telemetry being replayed or to the model reading it.
// The backend already reports this properly — /aircraft/{code}/engine returns
// `top_sensors` with the measured robust z-score, the model's gain contribution, the
// resulting health impact, and which module each sensor belongs to.
export default function SensorTable({ e }) {
  const rows = (e.topSensors || []).slice().sort(
    (x, y) => Math.abs(y.z ?? 0) - Math.abs(x.z ?? 0),
  );

  if (!rows.length) {
    return (
      <div className="card sx">
        <h3>Engine sensors</h3>
        <p className="note">Waiting for sensor attribution from the engine endpoint…</p>
      </div>
    );
  }

  return (
    <div className="card sx">
      <h3>Engine sensors, largest deviation first</h3>
      <table className="st">
        <thead>
          <tr><th>Sensor</th><th>Module</th><th>z-score</th><th>Health impact</th><th>Gain</th></tr>
        </thead>
        <tbody>
          {rows.map((x) => {
            const impact = x.health_impact ?? 0;
            const q = hc(1 - impact);
            return (
              <tr key={x.sensor}>
                <td>{x.sensor} <span className="note">{x.label}</span></td>
                <td>{ENG[x.component] || x.component || '-'}</td>
                <td className="mono">
                  <span className="dv" style={{ color: hc(1 - Math.min(1, Math.abs(x.z ?? 0) / 8)) }}>
                    {(x.z >= 0 ? '+' : '') + (x.z ?? 0).toFixed(2)}
                  </span>
                </td>
                <td className="mono">
                  <span className="dv" style={{ color: q }}>{(impact * 100).toFixed(1)}%</span>
                  <div className="bar"><i style={{ width: Math.min(100, impact * 100) + '%', background: q }}></i></div>
                </td>
                <td className="mono">{(x.contribution ?? 0).toFixed(4)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="note">
        Robust z-scores against the per-regime healthy baseline, from the model that
        produced this cycle&apos;s RUL
        {e.modelInfo?.version ? ` (${e.modelInfo.version})` : ''}
        {e.modelInfo?.fallback ? ' — currently served by the deterministic fallback' : ''}.
      </p>
    </div>
  );
}
