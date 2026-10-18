import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { EditTargetPicker, type SemanticEditProposal } from '../src';
import { artifact } from './fixtures';

afterEach(cleanup);

function fixture(): SemanticEditProposal {
  return { id: 'proposal-one', format: 'aica-edit-proposal/1', source_sha256: artifact().sha256!, width: 192, height: 128,
    intent: { operation: 'replace', instruction: 'a new object', targets: ['object'] }, status: 'ready', reason: 'One target',
    candidates: [{ id: 'one', label: 'object', score: .85, mask_artifact: artifact('mask'), rect: [.2, .2, .3, .6], editable_pixel_count: 1000,
      provenance: { source: 'semantic_segmentation' } }] };
}

function loadImages(container: HTMLElement) {
  container.querySelectorAll('img').forEach(image => fireEvent.load(image));
}

describe('semantic target confirmation', () => {
  it('preselects a unique target and returns only immutable selection identities', () => {
    const confirm = vi.fn(), proposal = fixture();
    const { container } = render(<EditTargetPicker artifact={artifact()} proposal={proposal} resolveUri={item => `/images/${item.id}.png`} onConfirm={confirm} />);
    const button = screen.getByRole('button', { name: 'Confirm edit' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    loadImages(container);
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(confirm).toHaveBeenCalledWith({ proposalId: proposal.id, candidateId: 'one', sourceSha256: proposal.source_sha256 });
    expect(screen.getByText('Detector score 0.85')).toBeTruthy();
    expect(screen.queryByText(/85%/)).toBeNull();
  });

  it('requires explicit selection for ambiguous targets', () => {
    const proposal = fixture(), confirm = vi.fn();
    proposal.status = 'needs_confirmation';
    proposal.candidates.push({ ...proposal.candidates[0], id: 'two', rect: [.6, .2, .3, .6] });
    const { container } = render(<EditTargetPicker artifact={artifact()} proposal={proposal} onConfirm={confirm} />);
    loadImages(container);
    const button = screen.getByRole('button', { name: 'Confirm edit' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Select object 2' }));
    loadImages(container);
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(confirm.mock.calls[0][0].candidateId).toBe('two');
  });

  it('blocks stale proposals even when their selected mask is available', () => {
    const proposal = fixture(); proposal.source_sha256 = 'different-source';
    const { container } = render(<EditTargetPicker artifact={artifact()} proposal={proposal} onConfirm={vi.fn()} />);
    loadImages(container);
    expect(screen.getByRole('alert').textContent).toContain('another image revision');
    expect((screen.getByRole('button', { name: 'Confirm edit' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('resets selection when the proposal changes', () => {
    const proposal = fixture();
    const view = render(<EditTargetPicker artifact={artifact()} proposal={proposal} onConfirm={vi.fn()} />);
    loadImages(view.container);
    view.rerender(<EditTargetPicker artifact={artifact()} proposal={{ ...proposal, id: 'new', status: 'needs_confirmation' }} onConfirm={vi.fn()} />);
    expect((screen.getByRole('button', { name: 'Confirm edit' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('allows hiding the overlay and labels placement separately from semantic masks', () => {
    const proposal = fixture(); proposal.candidates[0].score = null; proposal.candidates[0].provenance.source = 'placement_proposal';
    const { container } = render(<EditTargetPicker artifact={artifact()} proposal={proposal} onConfirm={vi.fn()} />);
    expect(screen.getByText('Placement proposal')).toBeTruthy();
    expect(container.querySelector('.aica-edit-mask')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Mask' }));
    expect(container.querySelector('.aica-edit-mask')).toBeNull();
  });

  it('disables confirmation when mask loading fails', () => {
    const { container } = render(<EditTargetPicker artifact={artifact()} proposal={fixture()} onConfirm={vi.fn()} />);
    loadImages(container);
    fireEvent.error(container.querySelector('.aica-edit-mask-probe')!);
    expect((screen.getByRole('button', { name: 'Confirm edit' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each(['not_found', 'unavailable', 'needs_target'] as const)('never executes status %s', status => {
    const proposal = fixture(); proposal.status = status; proposal.candidates = [];
    render(<EditTargetPicker artifact={artifact()} proposal={proposal} onConfirm={vi.fn()} />);
    expect((screen.getByRole('button', { name: 'Confirm edit' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
