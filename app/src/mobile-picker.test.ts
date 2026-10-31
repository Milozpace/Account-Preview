import { afterEach, describe, expect, it, vi } from 'vitest';
import { MobilePickers, applyPickerSelection, escapePickerHtml, pickerOptions, pickerTitle } from './mobile-picker';

const options = () => [
  { value: 'a', label: '当前 <月份>', disabled: false, selected: true, parentElement: null },
  { value: 'b', label: '其他月份', disabled: false, selected: false, parentElement: null },
  { value: 'c', label: '禁用月份', disabled: true, selected: false, parentElement: null },
];

// Small DOM substitute: no jsdom or new dependency is needed for lifecycle tests.
class Node {
  tagName: string; id = ''; className = ''; textContent = ''; hidden = false; disabled = false;
  type = ''; value = ''; open = false; style = { overflow: '' }; dataset: Record<string, string> = {};
  children: Node[] = []; parentElement: Node | null = null; ownerDocument!: Doc;
  options = options(); labels: { textContent: string }[] = []; attributes = new Map<string, string>();
  listeners = new Map<string, ((event: any) => void)[]>(); changes = 0;
  constructor(tagName: string) { this.tagName = tagName.toUpperCase(); }
  get isConnected(): boolean { return this === this.ownerDocument?.body || Boolean(this.parentElement?.isConnected); }
  append(...nodes: Node[]) { nodes.forEach(node => { node.parentElement = this; node.ownerDocument = this.ownerDocument; this.children.push(node); }); }
  after(node: Node) { const parent = this.parentElement!; node.parentElement = parent; node.ownerDocument = this.ownerDocument; parent.children.splice(parent.children.indexOf(this) + 1, 0, node); }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this); this.parentElement = null; }
  replaceChildren(...nodes: Node[]) { this.children.forEach(node => { node.parentElement = null; }); this.children = []; this.append(...nodes); }
  contains(node: Node): boolean { return this === node || this.children.some(child => child.contains(node)); }
  setAttribute(name: string, value: string) { this.attributes.set(name, value); }
  getAttribute(name: string) { return this.attributes.get(name) ?? null; }
  addEventListener(name: string, fn: (event: any) => void) { this.listeners.set(name, [...this.listeners.get(name) ?? [], fn]); }
  removeEventListener(name: string, fn: (event: any) => void) { this.listeners.set(name, (this.listeners.get(name) ?? []).filter(item => item !== fn)); }
  dispatchEvent(event: any) { if (event.type === 'change') this.changes++; (this.listeners.get(event.type) ?? []).forEach(fn => fn(event)); return true; }
  click() { if (!this.disabled) this.dispatchEvent({ type: 'click', target: this }); }
  focus() { this.ownerDocument.activeElement = this; }
  scrollIntoView() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelectorAll(selector: string): Node[] {
    const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    return selector === 'select[id]' ? all.filter(node => node.tagName === 'SELECT' && node.id) : all;
  }
}
class Doc {
  body = new Node('body'); activeElement: Node | null = null;
  constructor() { this.body.ownerDocument = this; }
  createElement(tag: string) { const node = new Node(tag); node.ownerDocument = this; return node; }
}
function fixture() {
  const doc = new Doc(); const root = doc.createElement('main'); doc.body.append(root);
  const select = doc.createElement('select'); select.id = 'bill-category'; select.value = 'a';
  select.labels = [{ textContent: '分类' }]; root.append(select);
  const onOpen = vi.fn(); const afters: (() => void | undefined)[] = [];
  const onDismiss = vi.fn((after?: () => void) => { if (after) afters.push(after); });
  const pickers = new MobilePickers({ onOpen, onDismiss });
  pickers.sync(root as unknown as HTMLElement);
  const trigger = () => root.children.find(node => node.className === 'picker-trigger')!;
  const dialog = () => doc.body.children.find(node => node.tagName === 'DIALOG')!;
  const choices = () => dialog().querySelectorAll('*').filter(node => node.getAttribute('role') === 'option');
  const close = () => { pickers.closeFromHistory(); afters.splice(0).forEach(fn => fn()); };
  return { doc, root, select, pickers, onOpen, onDismiss, afters, trigger, dialog, choices, close };
}

afterEach(() => vi.unstubAllGlobals());

describe('mobile picker option semantics', () => {
  it('preserves the current option and marks disabled options including optgroups', () => {
    const select = { value: 'a', options: options() } as unknown as HTMLSelectElement;
    expect(pickerOptions(select)).toMatchObject([{ value: 'a', selected: true }, { value: 'b', selected: false }, { value: 'c', disabled: true }]);
    (select.options[1] as any).parentElement = { tagName: 'OPTGROUP', disabled: true };
    expect(pickerOptions(select)[1].disabled).toBe(true);
  });
  it('escapes every HTML delimiter in dynamic values', () => {
    expect(escapePickerHtml(`<img title="x" data-v='&'>`)).toBe('&lt;img title=&quot;x&quot; data-v=&#39;&amp;&#39;&gt;');
  });
  it('marks only the actual current option when values are duplicated', () => {
    const select = { value: 'a', options: [{ ...options()[0] }, { ...options()[0], selected: false }] } as unknown as HTMLSelectElement;
    expect(pickerOptions(select).map(option => option.selected)).toEqual([true, false]);
  });
  it('uses accessible labels and a concise fallback title', () => {
    const f = fixture(); expect(pickerTitle(f.select as unknown as HTMLSelectElement)).toBe('分类');
    f.select.setAttribute('aria-label', '查看月份'); expect(pickerTitle(f.select as unknown as HTMLSelectElement)).toBe('查看月份');
    f.select.attributes.clear(); f.select.labels = []; expect(pickerTitle(f.select as unknown as HTMLSelectElement)).toBe('请选择');
  });
  it('rejects nonexistent, disabled, and unchanged selections without a change event', () => {
    const f = fixture();
    for (const value of ['missing', 'c', 'a']) expect(applyPickerSelection(f.select as unknown as HTMLSelectElement, value)).toBe(false);
    expect(f.select.changes).toBe(0);
    f.select.disabled = true; expect(applyPickerSelection(f.select as unknown as HTMLSelectElement, 'b')).toBe(false);
  });
  it('dispatches one bubbling change only for a valid changed selection', () => {
    const f = fixture(); const changed = vi.fn(); f.select.addEventListener('change', changed);
    expect(applyPickerSelection(f.select as unknown as HTMLSelectElement, 'b')).toBe(true);
    expect(f.select.value).toBe('b'); expect(changed).toHaveBeenCalledOnce(); expect(changed.mock.calls[0][0].bubbles).toBe(true);
  });
  it('rejects options in a disabled optgroup at commit time', () => {
    const f = fixture(); (f.select.options[1] as any).parentElement = { tagName: 'OPTGROUP', disabled: true };
    expect(applyPickerSelection(f.select as unknown as HTMLSelectElement, 'b')).toBe(false);
    expect(f.select.value).toBe('a'); expect(f.select.changes).toBe(0);
  });
});

describe('mobile picker modal lifecycle', () => {
  it('replaces selects with a trigger and restores the original hidden state on destroy', () => {
    const f = fixture(); expect(f.select.hidden).toBe(true); expect(f.trigger().dataset.selectId).toBe('bill-category');
    f.pickers.sync(f.root as unknown as HTMLElement); expect(f.root.children).toHaveLength(2);
    f.pickers.destroy(); expect(f.select.hidden).toBe(false); expect(f.root.children).toEqual([f.select]);
  });
  it('opens one modal, locks scrolling, and cancels through history without changing the value', () => {
    const f = fixture(); f.doc.body.style.overflow = 'auto'; f.trigger().click(); f.trigger().click();
    expect(f.pickers.isOpen).toBe(true); expect(f.onOpen).toHaveBeenCalledOnce(); expect(f.dialog().open).toBe(true);
    expect(f.dialog().getAttribute('aria-modal')).toBe('true'); expect(f.doc.body.style.overflow).toBe('hidden');
    const preventDefault = vi.fn(); f.dialog().dispatchEvent({ type: 'cancel', preventDefault });
    expect(preventDefault).toHaveBeenCalledOnce(); expect(f.onDismiss).toHaveBeenCalledOnce(); expect(f.select.value).toBe('a');
    f.close(); expect(f.pickers.isOpen).toBe(false); expect(f.doc.body.style.overflow).toBe('auto'); expect(f.doc.activeElement).toBe(f.trigger());
  });
  it('defers a valid selection until history closes and resolves the latest select by id', () => {
    const f = fixture(); f.trigger().click(); f.choices()[1].click();
    expect(f.onDismiss).toHaveBeenCalledOnce(); expect(f.select.value).toBe('a');
    const replacement = f.doc.createElement('select'); replacement.id = f.select.id; replacement.value = 'a'; replacement.labels = f.select.labels;
    f.select.remove(); f.root.append(replacement); f.pickers.sync(f.root as unknown as HTMLElement);
    expect(f.select.hidden).toBe(false); expect(f.root.children.filter(node => node.className === 'picker-trigger')).toHaveLength(1);
    f.close(); expect(replacement.value).toBe('b'); expect(replacement.changes).toBe(1); expect(f.select.changes).toBe(0);
  });
  it('same-value selection dismisses without dispatching change', () => {
    const f = fixture(); f.trigger().click(); f.choices()[0].click(); f.close();
    expect(f.onDismiss).toHaveBeenCalledOnce(); expect(f.select.changes).toBe(0);
  });
  it('keeps a stable title when its wrapping label later includes trigger text', () => {
    const f = fixture(); f.select.labels[0].textContent = '分类当前 <月份>';
    f.pickers.sync(f.root as unknown as HTMLElement); f.trigger().click();
    expect(f.dialog().querySelectorAll('*').find(node => node.className === 'picker-title')!.textContent).toBe('分类');
  });
  it('can reopen after cancellation and accept a subsequent selection once', () => {
    const f = fixture(); f.trigger().click(); f.dialog().dispatchEvent({ type: 'cancel', preventDefault: vi.fn() }); f.close();
    f.trigger().click(); f.choices()[1].click(); f.close();
    expect(f.onOpen).toHaveBeenCalledTimes(2); expect(f.onDismiss).toHaveBeenCalledTimes(2); expect(f.select.changes).toBe(1); expect(f.select.value).toBe('b');
  });
  it('revalidates a pending selection against disabled options in the replacement select', () => {
    const f = fixture(); f.trigger().click(); f.choices()[1].click();
    f.select.options[1].disabled = true; f.close(); expect(f.select.value).toBe('a'); expect(f.select.changes).toBe(0);
  });
  it.each(['backdrop', 'close'])('cancels through the %s without changing the value', action => {
    const f = fixture(); f.trigger().click();
    if (action === 'backdrop') f.dialog().dispatchEvent({ type: 'click', target: f.dialog() });
    else f.dialog().querySelectorAll('*').find(node => node.className === 'picker-close')!.click();
    f.close(); expect(f.onDismiss).toHaveBeenCalledOnce(); expect(f.select.value).toBe('a'); expect(f.select.changes).toBe(0);
  });
  it('renders malicious labels as text and preserves a preexisting hidden select on destroy', () => {
    const f = fixture(); f.pickers.destroy(); f.select.hidden = true; f.select.options[0].label = '<img src=x onerror=alert(1)>';
    f.pickers.sync(f.root as unknown as HTMLElement); f.trigger().click();
    expect(f.choices()[0].children[0].textContent).toBe('<img src=x onerror=alert(1)>');
    expect(f.trigger().children[0].textContent).toBe('<img src=x onerror=alert(1)>');
    f.pickers.destroy(); expect(f.select.hidden).toBe(true);
  });
  it('is immediately visible when showModal or animation APIs are absent', () => {
    const f = fixture(); const create = f.doc.createElement.bind(f.doc);
    f.doc.createElement = tag => { const node = create(tag); if (tag === 'dialog') (node as any).showModal = undefined; return node; };
    f.trigger().click(); expect(f.dialog().getAttribute('open')).toBe(''); expect(f.pickers.isOpen).toBe(true);
    f.pickers.destroy(); expect(f.doc.body.style.overflow).toBe('');
  });
  it.each(['disabled', 'removed'])('requests dismissal once when the target becomes %s', condition => {
    const f = fixture(); f.trigger().click(); if (condition === 'disabled') f.select.disabled = true; else f.select.remove();
    f.pickers.sync(f.root as unknown as HTMLElement); f.pickers.sync(f.root as unknown as HTMLElement);
    expect(f.onDismiss).toHaveBeenCalledOnce(); f.close(); expect(f.select.changes).toBe(0);
  });
  it('uses arrow and Home/End keys to focus only enabled options; Escape requests dismissal', () => {
    const f = fixture(); f.trigger().click(); const key = (value: string) => f.dialog().dispatchEvent({ type: 'keydown', key: value, preventDefault: vi.fn() });
    key('End'); expect(f.doc.activeElement).toBe(f.choices()[1]); key('Home'); expect(f.doc.activeElement).toBe(f.choices()[0]);
    key('ArrowDown'); expect(f.doc.activeElement).toBe(f.choices()[1]); key('ArrowUp'); expect(f.doc.activeElement).toBe(f.choices()[0]);
    key('Escape'); expect(f.onDismiss).toHaveBeenCalledOnce();
  });
  it('destroy closes the modal and never requests a history mutation', () => {
    const f = fixture(); f.trigger().click(); f.pickers.destroy(); f.pickers.destroy();
    expect(f.pickers.isOpen).toBe(false); expect(f.doc.body.style.overflow).toBe(''); expect(f.onDismiss).not.toHaveBeenCalled();
  });
});
