import { Fragment, useLayoutEffect, useRef, useState } from 'react';
import { useStore } from '../state/useStore.js';
import { fleet } from '../data/fleet.js';
import { drawHistoryChart } from '../lib/chart.js';
import { scr, RSEL, prefersReducedMotion, startScreening, stopScreening } from '../lib/screening.js';
import { refreshAircraft, refreshEngine, refreshPart } from '../state/server.js';
import AircraftSummary from './inspector/AircraftSummary.jsx';
import PartDetail from './inspector/PartDetail.jsx';

// Aircraft inspector panel.
//
// Behaviour kept from the original dashboard:
//  - the panel body is rebuilt on every update (new Fragment key = fresh DOM, so highlight classes reset)
//  - when the aircraft/part changes, the numbers "read sensors": they scramble and lock one by one
//    (lib/screening.js edits the DOM text directly, so React output is frozen while it runs)
//  - after a normal update, values that changed flash
export default function Inspector() {
  const app = useStore();
  const e = fleet[app.sel];
  const key = app.sel + ':' + app.tgt + ':' + app.cur;

  const panelRef = useRef(null);
  const sideRef = useRef(null);
  const cache = useRef({ el: null });
  const seq = useRef(0);
  const reused = useRef(false);
  const sideKey = useRef('');
  const inTimer = useRef(0);
  const [scanning, setScanning] = useState(false);

  let body;
  if (scr.on && scr.key === key && cache.current.el) {
    body = cache.current.el;     // scan in progress for this view: keep the exact same element so React leaves the DOM alone
    reused.current = true;
  } else {
    reused.current = false;
    body = <Fragment key={++seq.current}>{app.tgt ? <PartDetail e={e} /> : <AircraftSummary e={e} />}</Fragment>;
    cache.current.el = body;
  }

  // Runs after every render, like the original side() did after every render().
  useLayoutEffect(() => {
    if (reused.current) return;
    const s = sideRef.current;

    // slide-in animation when the view changes
    if (key !== sideKey.current) {
      sideKey.current = key;
      s.classList.add('in');
      clearTimeout(inTimer.current);
      inTimer.current = setTimeout(() => s.classList.remove('in'), 1000);
    }
    drawHistoryChart(e);

    const fresh = key !== scr.key;
    scr.key = key;
    if (fresh && !prefersReducedMotion()) {
      startScreening(s, {
        onStart: () => { setScanning(true); panelRef.current.classList.add('scn'); },
        onDone: () => { setScanning(false); panelRef.current.classList.remove('scn'); },
      });
    } else {
      const els = [...s.querySelectorAll(RSEL)], c = els.map((x) => x.textContent);
      if (!fresh && scr.prev.length == c.length) els.forEach((x, i) => { if (c[i] != scr.prev[i]) x.classList.add('chg'); });
      scr.prev = c;
    }
  });

  useLayoutEffect(() => () => { stopScreening(); clearTimeout(inTimer.current); }, []);

  // Per-aircraft detail: part health and RUL, component health, sensor attribution, the
  // engine spare and the 60-cycle history. These are separate endpoints from the
  // fleet-wide poll, so they are fetched for whatever is actually selected rather than
  // for all eight aircraft.
  useLayoutEffect(() => {
    if (!e) return;
    refreshAircraft(e.id);
    refreshEngine(e.id);
    if (app.tgt) refreshPart(e.id, app.cur);
  }, [app.sel, app.tgt, app.cur, e && e.id]);

  return (
    <section className="pn s12 rv" id="detail" ref={panelRef}>
      <div className="hd"><h2>Aircraft inspector</h2><span id="rd">{scanning ? 'Reading sensors' : 'Live'}</span></div>
      <div id="side" ref={sideRef}>{body}</div>
      <i className="scan"></i>
    </section>
  );
}
