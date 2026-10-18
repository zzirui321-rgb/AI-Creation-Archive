import { useEffect, useRef } from 'react';

export default function ParticleField() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !parent || !ctx) return;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const pointer = { x: -1000, y: -1000 };
    let width = 0, height = 0, frame = 0, visible = true;
    let points: { x: number; y: number; phase: number; radius: number }[] = [];
    const draw = (_time: number) => {
      ctx.clearRect(0, 0, width, height);
      const t = 0;
      const live = points.map(point => ({ ...point, x: point.x + Math.sin(t + point.phase) * 12, y: point.y + Math.cos(t * .8 + point.phase) * 9 }));
      for (let i = 0; i < live.length; i++) {
        const a = live[i];
        const distance = Math.hypot(a.x - pointer.x, a.y - pointer.y);
        const near = distance < 180;
        ctx.fillStyle = near ? `rgba(139,232,255,${.5 + (180 - distance) / 360})` : 'rgba(129,171,204,.35)';
        ctx.beginPath(); ctx.arc(a.x, a.y, near ? 2 : a.radius, 0, Math.PI * 2); ctx.fill();
        for (let j = i + 1; j < live.length; j++) {
          const b = live[j]; const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d < 130) { ctx.strokeStyle = `rgba(92,164,198,${(1 - d / 130) * (near ? .45 : .1)})`; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
        }
        if (near && !motion.matches) { ctx.strokeStyle = `rgba(103,213,243,${(1 - distance / 180) * .28})`; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(pointer.x, pointer.y); ctx.stroke(); }
      }

    };
    const resize = () => {
      const rect = parent.getBoundingClientRect(); width = rect.width; height = rect.height;
      const scale = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = width * scale; canvas.height = height * scale; ctx.setTransform(scale, 0, 0, scale, 0, 0);
      const count = Math.min(90, Math.floor(width * height / 11000));
      points = Array.from({ length: count }, (_, i) => ({ x: ((i * 137.508 + 31) % 997) / 997 * width, y: ((i * 83.71 + 53) % 991) / 991 * height, phase: i * 1.3, radius: i % 3 ? .9 : 1.5 }));
      cancelAnimationFrame(frame); frame = requestAnimationFrame(draw);
    };
    const move = (event: PointerEvent) => {
      if (event.pointerType === 'touch' || motion.matches) return;
      const rect = parent.getBoundingClientRect(); pointer.x = event.clientX - rect.left; pointer.y = event.clientY - rect.top;
      parent.style.setProperty('--spotlight-opacity', '1'); parent.style.setProperty('--mouse-x', `${pointer.x}px`); parent.style.setProperty('--mouse-y', `${pointer.y}px`);
      cancelAnimationFrame(frame); if (visible) frame = requestAnimationFrame(draw);
    };
    const leave = () => { pointer.x = -1000; pointer.y = -1000; parent.style.setProperty('--spotlight-opacity', '0'); parent.style.setProperty('--mouse-x', '-1000px'); cancelAnimationFrame(frame); frame = requestAnimationFrame(draw); };
    const observer = new ResizeObserver(resize); observer.observe(parent);
    const visibility = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; cancelAnimationFrame(frame); if (visible) frame = requestAnimationFrame(draw); }); visibility.observe(parent);
    const motionChange = () => { cancelAnimationFrame(frame); if (motion.matches) draw(0); else if (visible) frame = requestAnimationFrame(draw); };
    motion.addEventListener('change', motionChange);
    parent.addEventListener('pointermove', move); parent.addEventListener('pointerleave', leave);
    resize();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); visibility.disconnect(); motion.removeEventListener('change', motionChange); parent.removeEventListener('pointermove', move); parent.removeEventListener('pointerleave', leave); };
  }, []);
  return <canvas className="particle-field" ref={canvasRef} aria-hidden="true" />;
}
