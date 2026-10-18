import { usePlatformMedia } from './platform-media';
import { useState } from 'react';
import { FileImage, FileText, Film } from 'lucide-react';
import LocalEditPanel from './LocalEditPanel';
import type { LocalEdit } from './workspace';
import { safeMedia, type ModuleArtifact } from './workflow';
import type { ReactNode } from 'react';
import ModelViewer from './ModelViewer';

export function ResultThumbnail({ artifacts, text }: { artifacts: ModuleArtifact[]; text?: string }) {
  const [failed, setFailed] = useState(false);
  const artifact = artifacts[0];
  const mediaUrl = usePlatformMedia(artifact?.uri);
  return <div className="result-thumbnail">
    {artifact && !failed && safeMedia(artifact.uri, artifact.mime) && artifact.mime.startsWith('image/') ? <img src={mediaUrl || undefined} alt="此节点当时生成的结果" loading="lazy" onError={() => setFailed(true)} /> : artifact && !failed && safeMedia(artifact.uri, artifact.mime) && artifact.mime.startsWith('video/') ? <><video src={mediaUrl || undefined} muted playsInline preload="metadata" onError={() => setFailed(true)} /><Film size={24} /></> : text ? <p>{text}</p> : <span><FileImage size={25} />{artifact?.uri ? '打开查看生成文件' : '结果已记录，文件暂不可用'}</span>}
    {artifacts.length > 1 && <small>{artifacts.length} 个生成结果</small>}
  </div>;
}

export default function ResultGallery({ artifacts, text, pending, onLocalEdit, localModel, localError, renderGenerationActions }: { artifacts: ModuleArtifact[]; text?: string; pending?: boolean; onLocalEdit?: (edit: LocalEdit) => void; localModel?: string; localError?: string; renderGenerationActions?: (artifact: ModuleArtifact) => ReactNode }) {
  const [editing, setEditing] = useState(false);
  const [index, setIndex] = useState(0), [failed, setFailed] = useState('');
  const artifact = artifacts[index] ?? artifacts[0];
  const mediaUrl = usePlatformMedia(artifact?.uri);
  const accessible = artifact && safeMedia(artifact.uri, artifact.mime);
  return <section className="node-result-gallery" aria-label="此节点的历史生成结果"><header><strong>此节点的生成结果</strong><small>保留当时结果，新的预览不会覆盖</small></header>
    {artifact && accessible && /^(image|model)\//.test(artifact.mime) && renderGenerationActions?.(artifact)}
    {pending && <p className="inline-note">生成文件已记录。提示词与执行环境的关联待确认；候选提示词仅按文件名称匹配，不代表已验证的调用记录。</p>}
    {artifact && editing && onLocalEdit && mediaUrl ? <LocalEditPanel key={artifact.uri} sourceUri={mediaUrl} model={localModel} onClose={() => setEditing(false)} onSubmit={edit => onLocalEdit({ ...edit, sourceUri: artifact.uri })} /> : artifact && <div className="node-result-media">{accessible && artifact.mime === 'model/gltf-binary' ? <ModelViewer key={artifact.uri} uri={artifact.uri} /> : accessible && failed !== artifact.uri && artifact.mime.startsWith('image/') ? <img key={artifact.uri} src={mediaUrl || undefined} alt="节点历史生成图片" loading="lazy" onError={() => setFailed(artifact.uri)} /> : accessible && failed !== artifact.uri && artifact.mime.startsWith('video/') ? <video key={artifact.uri} src={mediaUrl || undefined} controls playsInline preload="metadata" onError={() => setFailed(artifact.uri)} /> : <div className="media-unavailable"><FileText size={25} /><p>{accessible ? artifact.mime === 'application/pdf' ? 'PDF 创作结果' : '生成文件，可通过下方链接查看' : '结果已记录，文件暂不可用'}{failed === artifact.uri ? ' · 无法加载' : ''}</p></div>}</div>}
    {artifact && accessible && artifact.mime.startsWith('image/') && onLocalEdit && !editing && <button type="button" className="button primary local-edit-entry" onClick={() => setEditing(true)}>局部修改此图</button>}
    {editing && localError && <p className="inline-error" role="alert">{localError}<a href="#/settings">前往模型设置</a></p>}
    {artifacts.length > 1 && <div className="node-result-choices" role="group" aria-label="选择本次生成结果">{artifacts.map((a, i) => <button key={a.id} aria-pressed={index === i} onClick={() => { setIndex(i); setEditing(false); }}>结果 {i + 1}</button>)}</div>}
    {artifact && accessible && <a className="button" href={mediaUrl || undefined} download target="_blank" rel="noreferrer">打开／下载结果 {artifacts.length > 1 ? index + 1 : ''}</a>}
    {text && <pre className="node-result-text">{text}</pre>}
    {!artifact && !text && <p className="media-unavailable">文件内容未包含在当前档案中。</p>}
  </section>;
}
