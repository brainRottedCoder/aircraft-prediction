import { hc } from '../../lib/colors.js';

// Labelled health bar (used for the sub-components of a part).
//
// `h` is null while the API has not answered for that component, which is a real state
// now that these come from the server. Previously it was always a number because it was
// computed locally, so the bar had nothing to represent.
export default function SubBar({ name, h }) {
  const v = h == null ? null : Math.max(0, Math.min(1, h));
  return (
    <div className="blk" style={{ margin: '0 0 10px' }}>
      <div className="row" style={{ margin: 0 }}>
        <span>{name}</span>
        <span style={{ color: hc(v ?? 1) }}>{v == null ? '--' : Math.round(v * 100) + '%'}</span>
      </div>
      <div className="bar"><i style={{ width: (v ?? 0) * 100 + '%', background: hc(v ?? 1) }}></i></div>
    </div>
  );
}
