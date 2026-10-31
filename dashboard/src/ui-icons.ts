import './ui-icons.css';

const paths = {
  export: '<path d="M12 3v12m-4-4 4 4 4-4M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/>',
  upload: '<path d="M12 15V3m-4 4 4-4 4 4M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/>',
  sync: '<path d="M4 10a8 8 0 0 1 13-4l3 3M20 4v5h-5M20 14a8 8 0 0 1-13 4l-3-3M4 20v-5h5"/>',
  settings: '<path d="m10 3-.7 2.2-2 .9-2.1-.5-2 3.5 1.5 1.7v2.4l-1.5 1.7 2 3.5 2.1-.5 2 .9.7 2.2h4l.7-2.2 2-.9 2.1.5 2-3.5-1.5-1.7v-2.4l1.5-1.7-2-3.5-2.1.5-2-.9L14 3z"/><circle cx="12" cy="12" r="3"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.1"/>',
  right: '<path d="m9 5 7 7-7 7"/>',
  left: '<path d="m15 5-7 7 7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  upRight: '<path d="M6 18 18 6M6 6h12v12"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  warning: '<path d="M12 8v5m0 4v.1"/><path d="m10.3 4-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0Z"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18"/>',
  document: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6ZM14 3v6h6M8 13h8M8 17h5"/>',
} as const;

/** Decorative only; the containing control owns its accessible name. */
export function uiIcon(name: keyof typeof paths): string {
  return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">${paths[name]}</svg>`;
}
