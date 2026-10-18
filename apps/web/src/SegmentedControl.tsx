import { useEffect, useRef, type ReactNode } from 'react';

export default function SegmentedControl({ value, options, onChange, label }: { value: string; options: { id: string; label: string; icon?: ReactNode }[]; onChange: (value: string) => void; label: string }) {
  const root = useRef<HTMLDivElement>(null);
  const indicator = useRef<HTMLSpanElement>(null);
  const spring = useRef({ x: 0, velocity: 0, initialized: false });
  const index = Math.max(0, options.findIndex(option => option.id === value));
  useEffect(() => {
    const element = root.current; const track = indicator.current;
    if (!element || !track) return;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0, last = 0, target = 0;
    const paint = () => { track.style.transform = `translate3d(${spring.current.x}px,0,0)`; };
    const step = (time: number) => {
      const dt = Math.min((time - (last || time - 16)) / 1000, .032); last = time;
      const current = spring.current;
      current.velocity += ((target - current.x) * 420 - current.velocity * 41) * dt;
      current.x += current.velocity * dt;
      if (Math.abs(target - current.x) < .05 && Math.abs(current.velocity) < .05) { current.x = target; current.velocity = 0; paint(); return; }
      paint(); frame = requestAnimationFrame(step);
    };
    const measure = () => {
      cancelAnimationFrame(frame); last = 0;
      target = index * (element.getBoundingClientRect().width - 8) / options.length;
      if (!spring.current.initialized || media.matches) { spring.current.x = target; spring.current.velocity = 0; spring.current.initialized = true; paint(); }
      else frame = requestAnimationFrame(step);
    };
    const observer = new ResizeObserver(measure); observer.observe(element); measure(); media.addEventListener('change', measure);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); media.removeEventListener('change', measure); };
  }, [index, options.length]);
  return <div ref={root} className="segmented-control" role="group" aria-label={label}>
    <span ref={indicator} className="segmented-indicator" style={{ width: `calc((100% - 8px) / ${options.length})` }} aria-hidden="true" />
    {options.map((option, optionIndex) => <button type="button" key={option.id} aria-pressed={value === option.id} onClick={() => onChange(option.id)} onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (optionIndex + (event.key === 'ArrowRight' ? 1 : -1) + options.length) % options.length;
      onChange(options[next].id); root.current?.querySelectorAll('button')[next]?.focus();
    }}>{option.icon}{option.label}</button>)}
  </div>;
}
