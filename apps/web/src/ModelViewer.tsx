import { createElement, useEffect, useState } from 'react';
import { usePlatformMedia } from './platform-media';

export default function ModelViewer({ uri }: { uri: string }) {
  const source = usePlatformMedia(uri);
  const [failed, setFailed] = useState(false);
  useEffect(() => { let disposed = false; void import('@google/model-viewer').catch(() => { if (!disposed) setFailed(true); }); return () => { disposed = true; }; }, []);
  return <div className="rodin-viewer">{source && !failed ? createElement('model-viewer', {
    key: source, src: source, alt: 'Hyper3D 生成的三维模型', 'camera-controls': true,
    'touch-action': 'pan-y', 'shadow-intensity': '1', exposure: '1', 'auto-rotate': true, 'camera-orbit': '0deg 75deg 110%',
    style: { width: '100%', height: 'clamp(240px, 44vh, 420px)', background: '#edf0ef', borderRadius: 16 },
    onError: () => setFailed(true),
  }) : <p>{failed ? '模型预览失败，可下载 GLB 后查看。' : '正在读取 3D 模型…'}</p>}<small>拖动旋转 · 滚轮缩放 · 双指平移</small>{source && <a className="button" href={source} download="aica-rodin.glb">下载 GLB 模型</a>}</div>;
}
