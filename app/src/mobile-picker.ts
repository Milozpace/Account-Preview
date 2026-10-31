import { prefersReducedMotion } from './mobile-preferences';
import {uiIcon} from '../../dashboard/src/ui-icons';

export function escapePickerHtml(value: string): string {
  const entities: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return value.replace(/[&<>"']/g, character => entities[character]);
}

export function pickerOptions(select: HTMLSelectElement): { value: string; label: string; disabled: boolean; selected: boolean }[] {
  return Array.from(select.options, option => ({
    value: option.value, label: option.label,
    disabled: option.disabled || (option.parentElement?.tagName === 'OPTGROUP' && (option.parentElement as HTMLOptGroupElement).disabled),
    selected: option.selected,
  }));
}

export function pickerTitle(select: HTMLSelectElement): string {
  return select.getAttribute('aria-label')?.trim() || select.labels?.[0]?.querySelector?.('.sr-only')?.textContent?.trim() || select.labels?.[0]?.textContent?.trim() || select.getAttribute('title')?.trim() || '请选择';
}

export function applyPickerSelection(select: HTMLSelectElement, value: string): boolean {
  if (select.disabled || select.value === value || !pickerOptions(select).some(option => option.value === value && !option.disabled)) return false;
  select.value = value;
  select.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

type Entry = { select: HTMLSelectElement; trigger: HTMLButtonElement; title: string; hidden: boolean; click: () => void; change: () => void };
type Callbacks = { onOpen: () => void; onDismiss: (after?: () => void) => void };

/** The owner handles history; this class handles only controls, modal UI and selection. */
export class MobilePickers {
  private entries = new Map<string, Entry>();
  private root?: HTMLElement;
  private openId?: string;
  private dialog?: HTMLDialogElement;
  private list?: HTMLElement;
  private heading?: HTMLElement;
  private closeButton?: HTMLButtonElement;
  private choiceButtons: HTMLButtonElement[] = [];
  private previousOverflow = '';
  private pendingDismiss = false;
  private effect?: Animation;
  private sheetId = `account-picker-${++nextPickerId}`;

  constructor(private callbacks: Callbacks) {}

  get isOpen(): boolean { return this.openId !== undefined; }

  sync(root: HTMLElement): void {
    this.root = root;
    const selects = new Map(Array.from(root.querySelectorAll<HTMLSelectElement>('select[id]'), select => [select.id, select]));
    for (const [id, entry] of this.entries) {
      if (selects.get(id) === entry.select && root.contains(entry.trigger)) continue;
      this.removeEntry(entry); this.entries.delete(id);
    }
    for (const [id, select] of selects) {
      if (!this.entries.has(id)) {
        const trigger = root.ownerDocument.createElement('button');
        trigger.type = 'button'; trigger.className = 'picker-trigger'; trigger.dataset.selectId = id;
        trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-controls', this.sheetId);
        const click = () => this.open(id);
        const change = () => { this.updateTrigger(id); if (this.openId === id && !this.pendingDismiss) this.renderOptions(); };
        // Capture before inserting into a wrapping label: its text then includes the trigger.
        const entry = { select, trigger, title: pickerTitle(select), hidden: select.hidden, click, change };
        this.entries.set(id, entry); select.hidden = true; select.after(trigger);
        trigger.addEventListener('click', click); select.addEventListener('change', change);
      }
      this.updateTrigger(id);
    }
    if (this.isOpen) {
      const target = this.target();
      if (!target || target.select.disabled) this.requestDismiss();
      else if (!this.pendingDismiss) this.renderOptions();
    }
  }

  private target(): Entry | undefined {
    const entry = this.openId === undefined ? undefined : this.entries.get(this.openId);
    return entry && this.root?.contains(entry.select) ? entry : undefined;
  }

  private updateTrigger(id: string): void {
    const entry = this.entries.get(id); if (!entry) return;
    const selected = pickerOptions(entry.select).find(option => option.selected);
    const label = entry.select.ownerDocument.createElement('span'); label.className = 'picker-trigger-label';
    const text=selected?.label || '请选择';
    label.textContent = id==='month-selector'?text.replace(/\s*·.*$/,'').replace(/\s/g,''):text; entry.trigger.replaceChildren(label);
    entry.trigger.disabled = entry.select.disabled;
    entry.trigger.setAttribute('aria-label', `${entry.title}：${selected?.label || '请选择'}`);
    entry.trigger.setAttribute('aria-expanded', String(this.openId === id));
  }

  private open(id: string): void {
    if (this.isOpen) return;
    const entry = this.entries.get(id);
    if (!entry || entry.select.disabled || !this.root?.contains(entry.select)) return;
    const doc = entry.select.ownerDocument;
    const dialog = doc.createElement('dialog'); dialog.id = this.sheetId; dialog.className = 'mobile-picker';
    dialog.setAttribute('aria-modal', 'true'); dialog.setAttribute('aria-labelledby', `${this.sheetId}-title`);
    const handle = doc.createElement('div'); handle.className = 'picker-handle'; handle.setAttribute('aria-hidden', 'true');
    const header = doc.createElement('div'); header.className = 'picker-header';
    const title = doc.createElement('h2'); title.id = `${this.sheetId}-title`; title.className = 'picker-title';
    const close = doc.createElement('button'); close.type = 'button'; close.className = 'picker-close'; close.textContent = '取消';
    close.addEventListener('click', () => this.requestDismiss());
    header.append(title, close);
    const list = doc.createElement('div'); list.className = 'picker-options'; list.setAttribute('role', 'listbox');
    list.setAttribute('aria-labelledby', title.id);
    dialog.append(handle, header, list);
    dialog.addEventListener('cancel', event => { event.preventDefault(); this.requestDismiss(); });
    dialog.addEventListener('click', event => { if (event.target === dialog) this.requestDismiss(); });
    dialog.addEventListener('keydown', event => this.onKey(event));
    this.openId = id; this.pendingDismiss = false; this.dialog = dialog; this.list = list; this.heading = title; this.closeButton = close;
    this.previousOverflow = doc.body.style.overflow; doc.body.style.overflow = 'hidden';
    doc.body.append(dialog); this.renderOptions(); this.updateTrigger(id);
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    this.focusInitial();
    if (!prefersReducedMotion() && typeof dialog.animate === 'function') {
      try { this.effect = dialog.animate([{ opacity: .65, transform: 'translateY(20px)' }, { opacity: 1, transform: 'translateY(0)' }], { duration: 190, easing: 'cubic-bezier(.22,1,.36,1)' }); }
      catch { /* Unsupported animation leaves the modal visible. */ }
    }
    this.callbacks.onOpen();
  }

  private renderOptions(): void {
    const entry = this.target(); if (!entry || !this.list || !this.heading) return;
    const oldValue = this.choiceButtons.find(button => button === entry.select.ownerDocument.activeElement)?.dataset.value;
    this.heading.textContent = entry.title;
    this.choiceButtons = pickerOptions(entry.select).map(option => {
      const button = entry.select.ownerDocument.createElement('button');
      button.type = 'button'; button.className = 'picker-option'; button.dataset.value = option.value;
      button.setAttribute('role', 'option'); button.setAttribute('aria-selected', String(option.selected)); button.disabled = option.disabled;
      const label = entry.select.ownerDocument.createElement('span'); label.className = 'picker-option-label'; label.textContent = option.label;
      const check = entry.select.ownerDocument.createElement('span'); check.className = 'picker-check'; check.innerHTML = option.selected ? uiIcon('check') : ''; check.setAttribute('aria-hidden', 'true');
      button.append(label, check);
      button.addEventListener('click', () => this.choose(option.value));
      return button;
    });
    this.list.replaceChildren(...this.choiceButtons);
    if (oldValue !== undefined) this.choiceButtons.find(button => button.dataset.value === oldValue && !button.disabled)?.focus();
  }

  private choose(value: string): void {
    const entry = this.target();
    if (!entry || entry.select.disabled || !pickerOptions(entry.select).some(option => option.value === value && !option.disabled)) return;
    if (entry.select.value === value) { this.requestDismiss(); return; }
    const id = this.openId!;
    this.requestDismiss(() => {
      const latest = this.entries.get(id);
      if (latest && this.root?.contains(latest.select)) applyPickerSelection(latest.select, value);
    });
  }

  private requestDismiss(after?: () => void): void {
    if (!this.isOpen || this.pendingDismiss) return;
    this.pendingDismiss = true; this.callbacks.onDismiss(after);
  }

  private focusInitial(): void {
    const button = this.choiceButtons.find(item => !item.disabled && item.getAttribute('aria-selected') === 'true') ?? this.choiceButtons.find(item => !item.disabled) ?? this.closeButton;
    button?.focus(); button?.scrollIntoView?.({ block: 'nearest' });
  }

  private onKey(event: KeyboardEvent): void {
    if (event.key === 'Escape') { event.preventDefault(); this.requestDismiss(); return; }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const buttons = this.choiceButtons.filter(button => !button.disabled); if (!buttons.length) return;
    const index = buttons.findIndex(button => button === this.dialog?.ownerDocument.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : event.key === 'ArrowDown' ? (index + 1) % buttons.length : (index - 1 + buttons.length) % buttons.length;
    buttons[next].focus(); buttons[next].scrollIntoView?.({ block: 'nearest' });
  }

  closeFromHistory(): void {
    if (!this.isOpen) return;
    const id = this.openId!; const doc = this.dialog?.ownerDocument;
    this.effect?.cancel(); this.effect = undefined;
    if (this.dialog?.open && typeof this.dialog.close === 'function') this.dialog.close();
    this.dialog?.remove(); this.dialog = undefined; this.list = undefined; this.heading = undefined; this.closeButton = undefined; this.choiceButtons = [];
    if (doc) doc.body.style.overflow = this.previousOverflow;
    this.openId = undefined; this.pendingDismiss = false; this.updateTrigger(id);
    const trigger = this.entries.get(id)?.trigger;
    if (trigger?.isConnected && !trigger.disabled && this.root?.contains(trigger)) trigger.focus();
  }

  private removeEntry(entry: Entry): void {
    entry.select.hidden = entry.hidden; entry.select.removeEventListener('change', entry.change);
    entry.trigger.removeEventListener('click', entry.click); entry.trigger.remove();
  }

  destroy(): void {
    this.closeFromHistory();
    for (const entry of this.entries.values()) this.removeEntry(entry);
    this.entries.clear(); this.root = undefined;
  }
}

let nextPickerId = 0;
