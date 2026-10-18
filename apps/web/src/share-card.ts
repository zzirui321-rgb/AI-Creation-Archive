import type { CommunityProject } from './community';

function textLines(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, maxLines: number) {
  const lines: string[] = []; let current = '';
  for (const character of text) {
    if (ctx.measureText(current + character).width > maxWidth && current) {
      lines.push(current); current = character;
      if (lines.length === maxLines) break;
    } else current += character;
  }
  if (lines.length < maxLines && current) lines.push(current);
  if (lines.join('').length < text.length && lines.length) {
    let last = lines[lines.length - 1];
    while (ctx.measureText(last + '…').width > maxWidth) last = last.slice(0, -1);
    lines[lines.length - 1] = last + '…';
  }
  return lines;
}

export async function createShareCard(project: CommunityProject & { coverArtifact?: { mime: string } }, svg: SVGSVGElement | HTMLImageElement, link: string): Promise<Blob> {
  const canvas = document.createElement('canvas'); canvas.width = 1080; canvas.height = 1500;
  const ctx = canvas.getContext('2d'); if (!ctx) throw new Error('当前浏览器无法生成卡片，请尝试复制链接。');
  const isSVG = svg instanceof SVGSVGElement;
  const artUrl = isSVG ? URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml;charset=utf-8' })) : (svg as HTMLImageElement).src;
  try {
    const image = new Image(); if (!isSVG) image.crossOrigin = 'anonymous';
    await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(new Error('作品预览加载失败，请重试。')); image.src = artUrl; });
    const background = ctx.createLinearGradient(0, 0, 1080, 1500); background.addColorStop(0, '#1b3043'); background.addColorStop(1, '#090f1b');
    ctx.fillStyle = background; ctx.fillRect(0, 0, 1080, 1500);
    ctx.strokeStyle = '#66869c25'; ctx.lineWidth = 1;
    for (let x = 36; x < 1080; x += 48) for (let y = 36; y < 1500; y += 48) { ctx.fillStyle = '#90b5cb20'; ctx.fillRect(x, y, 2, 2); }
    ctx.font = '600 51px "Segoe UI", "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#e2f0f7'; ctx.fillText('AiCA', 68, 105);
    ctx.fillStyle = '#a7dbe5'; ctx.fillText('.', 181, 105);
    ctx.font = '17px "Segoe UI", "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#91adbf'; ctx.fillText('AI CREATION ARCHIVE', 254, 102);
    ctx.textAlign = 'right'; ctx.fillStyle = '#a5d3de'; ctx.fillText(project.category, 1008, 102); ctx.textAlign = 'left';
    ctx.strokeStyle = '#a1bdcf35'; ctx.beginPath(); ctx.moveTo(68, 144); ctx.lineTo(1012, 144); ctx.stroke();
    ctx.font = '16px "Segoe UI", sans-serif'; ctx.fillStyle = '#789aaf'; ctx.fillText('SEE HOW IT WAS MADE. MAKE IT YOURS.', 68, 185);
    ctx.save(); ctx.beginPath(); ctx.roundRect(64, 218, 952, 704, 28); ctx.clip();
    const imageWidth = image.naturalWidth || 600, imageHeight = image.naturalHeight || 760;
    const scale = Math.max(952 / imageWidth, 704 / imageHeight);
    const drawWidth = imageWidth * scale, drawHeight = imageHeight * scale;
    ctx.drawImage(image, 64 + (952 - drawWidth) / 2, 218 + (704 - drawHeight) / 2, drawWidth, drawHeight);
    const shade = ctx.createLinearGradient(0, 750, 0, 922); shade.addColorStop(0, '#06122100'); shade.addColorStop(1, '#06122199'); ctx.fillStyle = shade; ctx.fillRect(64, 750, 952, 172);
    ctx.font = '15px "Segoe UI", "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#e7f2f8'; ctx.fillText(isSVG ? '示意封面 / ARCHIVE COVER' : '确认生成结果 / MODEL OUTPUT', 91, 890); ctx.restore();
    ctx.font = '500 48px "Segoe UI", "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#e0edf5';
    const title = textLines(ctx, project.title, 944, 2); title.forEach((line, index) => ctx.fillText(line, 68, 1003 + index * 66));
    ctx.fillStyle = '#8badc2'; ctx.font = '24px "Segoe UI", "Microsoft YaHei", sans-serif'; ctx.fillText(`创作者 / ${project.author}`, 68, 1160);
    let chipX = 68; ctx.font = '18px "Segoe UI", "Microsoft YaHei", sans-serif';
    for (const tool of project.tools.slice(0, 3)) {
      const chipWidth = ctx.measureText(tool).width + 30;
      if (chipX + chipWidth > 1012) break;
      ctx.fillStyle = '#253c4d'; ctx.beginPath(); ctx.roundRect(chipX, 1193, chipWidth, 39, 8); ctx.fill(); ctx.fillStyle = '#a9c4d6'; ctx.fillText(tool, chipX + 15, 1219); chipX += chipWidth + 12;
    }
    ctx.strokeStyle = '#93b6cc38'; ctx.beginPath(); ctx.moveTo(68, 1283); ctx.lineTo(1012, 1283); ctx.stroke();
    ctx.fillStyle = '#b8dce8'; ctx.font = 'italic 27px Georgia, serif'; ctx.fillText('Archive the process, not just the result.', 68, 1345);
    ctx.fillStyle = '#718da3'; ctx.font = '17px "Segoe UI", "Microsoft YaHei", sans-serif';
    textLines(ctx, link, 944, 2).forEach((line, index) => ctx.fillText(line, 68, 1402 + index * 25));
    ctx.fillStyle = '#516f86'; ctx.font = '14px "Segoe UI", "Microsoft YaHei", sans-serif'; ctx.fillText('LOCAL PREVIEW · 链接仅对应当前运行环境', 68, 1470);
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('生成失败，请重试。')), 'image/png'));
  } finally { if (isSVG) URL.revokeObjectURL(artUrl); }
}
