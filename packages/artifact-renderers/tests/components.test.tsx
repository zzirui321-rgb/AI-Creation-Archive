import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ArchiveDiffViewer, ArtifactRenderer, ExploreArchiveCard, ProvenanceAndLinksBar, ReuseRatioBar } from '../src';
import { artifact, fixture, run } from './fixtures';

afterEach(cleanup);

describe('artifact components', () => {
  it('distinguishes reusable scene layers from separate comic panels', () => {
    const archive = fixture(), composite = artifact('scene');
    archive.artifacts = [artifact('background'), artifact('subject'), composite];
    render(<ArtifactRenderer artifact={composite} archive={archive} metadata={{ composition_mode: 'layer_stack', regions: [
      { artifact_id: 'background', role: 'background', rect: { x: 0, y: 0, width: 768, height: 512 } },
      { artifact_id: 'subject', role: 'subject', rect: { x: 200, y: 80, width: 320, height: 400 } },
    ] }} />);
    expect(screen.queryByRole('button', { name: 'Individual panels' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Individual layers' }));
    expect(screen.getByRole('button', { name: 'background: background' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'subject: subject' })).toBeTruthy();
  });
  it('uses a host resolver and labels local substitutes honestly', () => {
    const item = artifact(); item.uri = 'file:private.png';
    render(<ArtifactRenderer artifact={item} resolveUri={() => '/served/image.png'} previewMode="draft"
      metadata={{ is_substitute: true, source: 'local_fallback' }} />);
    expect(screen.getByRole('img').getAttribute('src')).toBe('/served/image.png');
    expect(screen.getByText('Local Fallback')).toBeTruthy();
    expect(screen.getByText('Draft Preview')).toBeTruthy();
  });
  it('shows missing artifacts and failed image loads', () => {
    const view = render(<ArtifactRenderer artifact={{ ...artifact(), availability: 'missing' }} />);
    expect(screen.getByRole('alert').textContent).toContain('missing');
    view.rerender(<ArtifactRenderer artifact={artifact()} />);
    fireEvent.error(screen.getByRole('img'));
    expect(screen.getByRole('alert').textContent).toContain('Unable to load');
  });
  it('does not relabel a known Draft artifact as HQ', () => {
    render(<ArtifactRenderer artifact={artifact()} previewMode="high_quality" metadata={{ stage: 'draft' }} />);
    expect(screen.getByText('Draft Preview')).toBeTruthy();
    expect(screen.queryByText('High-Quality')).toBeNull();
  });
  it('loads JSON without injecting markup', async () => {
    render(<ArtifactRenderer artifact={artifact('json', 'application/json')}
      loadText={async () => '{"html":"<script>bad()</script>"}'} />);
    await waitFor(() => expect(screen.getByText(/<script>bad/)).toBeTruthy());
    expect(document.querySelector('script')).toBeNull();
  });
  it('renders Markdown and uses a sandbox for Web artifacts', async () => {
    const view = render(<ArtifactRenderer artifact={artifact('markdown', 'text/markdown')}
      loadText={async () => '# A heading\n\n<script>bad()</script>'} />);
    await waitFor(() => expect(screen.getByRole('heading').textContent).toBe('A heading'));
    expect(document.querySelector('script')).toBeNull();
    view.rerender(<ArtifactRenderer artifact={artifact('web', 'text/html')} />);
    expect(document.querySelector('iframe')?.getAttribute('sandbox')).toBe('');
  });
  it('supports audio and video controls', () => {
    const view = render(<ArtifactRenderer artifact={artifact('audio', 'audio/wav')} />);
    expect(document.querySelector('audio')?.controls).toBe(true);
    view.rerender(<ArtifactRenderer artifact={artifact('video', 'video/mp4')} />);
    expect(document.querySelector('video')?.controls).toBe(true);
  });
  it('renders SVG through the image renderer', () => {
    render(<ArtifactRenderer artifact={artifact('svg', 'image/svg+xml')} />);
    expect(screen.getByRole('img')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
  });
  it.each([['live_api', 'Live API'], ['prewarmed_cache', 'Prewarmed Cache'], ['local_fallback', 'Local Fallback']])(
    'labels the actual %s source', (source, label) => {
      render(<ArtifactRenderer artifact={artifact()} metadata={{ source }} />);
      expect(screen.getByText(label)).toBeTruthy();
    },
  );
  it('renders an accessible reuse ratio without counting a dead-end', () => {
    const archive = fixture();
    archive.graph.branches.push({ id: 'trial', parent_id: 'main', state: 'dead_end', reason: 'Rejected' });
    archive.graph.nodes.push({ ...archive.graph.nodes[0], id: 'rejected', branch_id: 'trial' });
    archive.runs = [run('source', 'CACHE_HIT'), run('image', 'RECOMPUTE'), run('layout', 'REBUILD')];
    render(<ReuseRatioBar archive={archive} previewMode="draft" />);
    expect(screen.getByText('1/3 nodes reused')).toBeTruthy();
    expect(screen.getByRole('progressbar').getAttribute('aria-valuemax')).toBe('3');
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('1');
  });
  it('shows current child-node cache state and prompt details', () => {
    const archive = fixture(), composite = { ...artifact('composite'), producer_node_id: 'layout', producer_run_id: 'run-layout' };
    archive.artifacts = [artifact('child'), composite];
    archive.runs = [run('image', 'CACHE_HIT', ['child']), run('layout', 'REBUILD', ['composite'])];
    const select = vi.fn();
    render(<ArtifactRenderer artifact={composite} archive={archive} onPanelSelect={select} metadata={{
      regions: [{ artifact_id: 'child', node_id: 'image', prompt: 'A measured change',
        rect: { x: 12, y: 32, width: 160, height: 100 }, caption: 'First panel', cache_decision: 'RECOMPUTE' }],
    }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Individual panels' }));
    expect(screen.getByText('CACHE HIT')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /First panel/ }));
    expect(screen.getByText('A measured change')).toBeTruthy();
    expect(select).toHaveBeenCalledWith(archive.artifacts[0]);
  });
  it('shows reuse statistics and variable differences', () => {
    const original = fixture(), fork = fixture();
    fork.runs = [run('source', 'CACHE_HIT'), run('image', 'RECOMPUTE'), run('layout', 'REBUILD')];
    fork.variables.theme.value = 'new theme';
    render(<ArchiveDiffViewer originalArchive={original} forkedArchive={fork} />);
    expect(screen.getByText('reused')).toBeTruthy();
    expect(screen.getByText('"new theme"')).toBeTruthy();
    expect(screen.getByText('CACHE HIT')).toBeTruthy();
  });
  it('includes removed nodes in an archive comparison', () => {
    const original = fixture(), fork = fixture();
    fork.graph.nodes = fork.graph.nodes.filter(node => node.id !== 'image');
    render(<ArchiveDiffViewer originalArchive={original} forkedArchive={fork} />);
    expect(screen.getByText('Removed node')).toBeTruthy();
  });
  it('exposes card actions and filters unsafe external links', () => {
    const archive = fixture();
    archive.metadata.external_links.push('javascript:alert(1)');
    const open = vi.fn(), fork = vi.fn();
    const view = render(<ExploreArchiveCard archive={archive} onOpen={open} onFork={fork} />);
    fireEvent.click(screen.getByRole('button', { name: /Open/ }));
    fireEvent.click(screen.getByRole('button', { name: /Fork/ }));
    expect(open).toHaveBeenCalledWith(archive);
    expect(fork).toHaveBeenCalledWith(archive);
    expect(screen.getByRole('link', { name: /GitHub/ }).getAttribute('href')).toMatch(/^https:/);
    expect(document.querySelector('[href^="javascript:"]')).toBeNull();
    view.rerender(<ProvenanceAndLinksBar archive={archive} showEvidence />);
    expect(screen.getByText('Recorded · 3')).toBeTruthy();
    expect(screen.getByText('Evidence (1)')).toBeTruthy();
  });
});
