import { useEffect, useRef } from 'react';
import { useStore } from '../state/useStore.js';
import { closePart, setView } from '../state/store.js';
import { fleet } from '../data/fleet.js';
import { PARTS } from '../data/parts.js';
import { createTwin } from '../three/index.js';

const VIEWS = [['auto', 'Auto'], ['top', 'Top'], ['side', 'Side'], ['under', 'Underside']];

// The 3D viewport. React owns the overlay UI (title, view buttons); the three.js scene is a plain module mounted into #stage.
export default function TwinStage() {
  const app = useStore();
  const stageRef = useRef(null);

  useEffect(() => {
    const twin = createTwin(stageRef.current);
    return () => twin.dispose();
  }, []);

  const e = fleet[app.sel];
  return (
    <section className="hero" id="twin">
      <div id="stage" ref={stageRef}>
        <i className="glow"></i>
        <div id="intro">
          <b>Fleet digital twin</b>
          <span>Drag to turn the aircraft. Select a label to look inside.</span>
        </div>
        <button id="back" onClick={closePart} style={{ display: app.tgt ? 'block' : 'none' }}>Back to aircraft</button>
        <div id="views">
          {VIEWS.map(([v, label]) => (
            <button key={v} data-v={v} className={app.view === v ? 'on' : ''} onClick={() => setView(v)}>{label}</button>
          ))}
        </div>
        <div id="ttl">
          <b id="t1">{app.tgt ? PARTS[app.cur].name + ' - ' + e.id : e.id}</b>
          <span id="t2">{app.status}</span>
        </div>
      </div>
    </section>
  );
}
