import { useEffect } from 'react';
import { prefersReducedMotion } from './lib/screening.js';

// Fades panels (.rv) in when they scroll into view.
export function useRevealOnScroll() {
  useEffect(() => {
    const reveal = () => document.querySelectorAll('.rv:not(.vis)').forEach((x) => { if (x.getBoundingClientRect().top < innerHeight * 0.92) x.classList.add('vis'); });
    addEventListener('scroll', reveal, { passive: true });
    const t = setTimeout(reveal, 300);
    const io = new IntersectionObserver((es) => es.forEach((x) => { if (x.isIntersecting) { x.target.classList.add('vis'); io.unobserve(x.target); } }), { threshold: 0.12 });
    document.querySelectorAll('.rv').forEach((x) => io.observe(x));
    return () => { removeEventListener('scroll', reveal); clearTimeout(t); io.disconnect(); };
  }, []);
}

// Removes the intro animation class from <body> after the intro has played.
export function useIntro() {
  useEffect(() => {
    const go = () => document.body.classList.remove('intro');
    if (prefersReducedMotion()) { go(); return; }
    const t = setTimeout(go, 2800);
    return () => clearTimeout(t);
  }, []);
}
