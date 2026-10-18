import { useEffect, useState } from 'react';
import { platform } from './platform';

export function isPlatformMedia(uri: string) {
  try {
    const url = new URL(uri, window.location.origin);
    return url.origin === window.location.origin && /^\/(api\/(repos\/|creation-parser\/|3d\/jobs\/)|runtime-api\/preview-artifacts\/)/.test(url.pathname);
  } catch { return false; }
}

export function usePlatformMedia(uri = '') {
  const protectedAsset = isPlatformMedia(uri);
  const [url, setUrl] = useState(protectedAsset ? '' : uri);
  useEffect(() => {
    if (!protectedAsset) { setUrl(uri); return; }
    const controller = new AbortController(); let objectUrl = '';
    setUrl('');
    void platform.authorizedFetch(uri, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('产物读取失败');
      const blob = await response.blob();
      if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); }
    }).catch(() => {});
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [uri, protectedAsset]);
  return url;
}
