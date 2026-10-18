import { useId } from 'react';
import type { Values } from './demo';

export default function Artwork({ values, compact = false }: { values: Values; compact?: boolean }) {
  const id = useId().replace(/:/g, '');
  const cat = /猫|cat/i.test(values.character);
  const changed = values.character !== '银发赛博朋克武士';
  const warm = !values.style.includes('霓虹');
  return <svg className={`artwork ${compact ? 'compact' : ''}`} viewBox="0 0 600 350" preserveAspectRatio="xMidYMid slice" role="img" aria-label={`示意插画：${values.character}，${values.style}。非 AI 生成结果。`}>
    <defs>
      <linearGradient id={`${id}-sky`} x2="0" y2="1"><stop stopColor={warm ? '#d98a71' : '#242348'} /><stop offset="1" stopColor={warm ? '#efcfa3' : '#8c70ae'} /></linearGradient>
      <linearGradient id={`${id}-road`} x2="0" y2="1"><stop stopColor="#b19cbf" /><stop offset="1" stopColor="#27243e" /></linearGradient>
      <pattern id={`${id}-windows`} width="22" height="28" patternUnits="userSpaceOnUse"><rect x="7" y="8" width="5" height="11" fill="#f5bcad" opacity=".55" /></pattern>
    </defs>
    <rect width="600" height="350" fill={`url(#${id}-sky)`} />
    <circle cx="412" cy="95" r="52" fill="#f4ceba" opacity=".85" />
    {[0, 1, 2, 3, 4, 5, 6, 7].map((n) => <g key={n}><rect x={n * 83 - 17} y={95 + n % 3 * 25} width="65" height="220" fill={n % 2 ? '#37304c' : '#47405e'} /><rect x={n * 83 - 17} y={95 + n % 3 * 25} width="65" height="220" fill={`url(#${id}-windows)`} /></g>)}
    <path d="M230 225H370L580 350H20Z" fill={`url(#${id}-road)`} />
    <path d="M290 240L205 350M310 240L395 350" stroke="#efb3c6" strokeWidth="2" opacity=".6" />
    <path d="M0 281L600 278M0 315L600 311" stroke="#c79bb6" opacity=".3" />
    <g transform="translate(302 202)">
      {cat ? <><circle cy="-6" r="39" fill="none" stroke="#bde8ed" strokeWidth="7" opacity=".75" /><path d="M-24 -9L-26 -39L-6 -24L11 -25L29 -40L27 -6" fill="#e5a35f" /><ellipse cy="-5" rx="29" ry="26" fill="#edae68" /><path d="M-15 -9L-8 -8M10 -8L17 -9" stroke="#48354a" strokeWidth="3" /><path d="M-4 1L1 5L6 1M1 5V11" fill="none" stroke="#62424d" strokeWidth="2" /><path d="M-24 26Q0 38 24 26L39 95H-39Z" fill="#dddbe1" /><rect x="-11" y="42" width="22" height="20" rx="4" fill="#656485" /><path d="M-20 94L-24 133M21 94L26 133" stroke="#c8c6d3" strokeWidth="14" /></> : <><path d="M-15 -15L-22 -38L5 -47L27 -31L20 -8" fill={changed ? '#dcaa77' : '#d4cee4'} /><ellipse rx="19" ry="23" fill="#e3b7a7" /><path d="M-21 -15L21 -15L16 -5L-18 -4Z" fill="#524563" /><path d="M-24 25L1 15L27 28L42 103L-37 103Z" fill={changed ? '#8d7a91' : '#343147'} /><path d="M-8 24L10 28L15 101L-4 104Z" fill="#bb788e" /><path d="M-19 102L-25 136M20 102L25 136" stroke="#28253e" strokeWidth="15" /><path d="M-28 41L-44 100M29 42L41 94" stroke="#433951" strokeWidth="12" /><path d="M34 80L73 -17" stroke="#dfc6cf" strokeWidth="4" /></>}
      {values.detail && <g transform="translate(66 -39)"><ellipse rx="23" ry="7" fill="#e7af6a" /><path d="M-23 0Q0 -30 23 0" fill="#e7af6a" /><path d="M-24 5L23 5" stroke="#8cbd8a" strokeWidth="6" /><path d="M-24 11L23 11" stroke="#785245" strokeWidth="6" /><path d="M-22 16Q0 29 22 16" fill="#edba79" /></g>}
    </g>
    <rect x="26" y="26" width="3" height="25" fill="#f5e6dc" /><text x="40" y="42" fill="#f5e6dc" fontFamily="monospace" fontSize="12" letterSpacing="3">NEON / CHAPTER 01</text>
    <text x="26" y="326" fill="#fff2e8" fontFamily="sans-serif" fontSize="11" letterSpacing="2">A JOURNEY BEYOND THE FAMILIAR</text>
  </svg>;
}
