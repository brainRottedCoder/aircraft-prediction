import { useEffect, useRef } from 'react';

// Floating tooltip for any element with a data-tip attribute (use "\n" for line breaks).
export default function Tooltip() {
  const ref = useRef(null);
  useEffect(() => {
    const tip = ref.current;
    const move = (e) => {
      const t = e.target.closest && e.target.closest('[data-tip]');
      if (!t) { tip.style.opacity = 0; return; }
      tip.innerHTML = t.dataset.tip.replace(/\n/g, '<br>');
      tip.style.opacity = 1;
      tip.style.transform = 'translate(' + Math.max(8, Math.min(innerWidth - tip.offsetWidth - 8, e.clientX + 14)) + 'px,' + Math.min(innerHeight - tip.offsetHeight - 8, e.clientY + 16) + 'px)';
    };
    document.addEventListener('pointermove', move);
    return () => document.removeEventListener('pointermove', move);
  }, []);
  return <div id="tip" ref={ref}></div>;
}
