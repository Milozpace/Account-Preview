export type MotionPreference = 'system' | 'reduce';
export function loadMotionPreference(): MotionPreference { return 'system'; }
export function saveMotionPreference(_value: MotionPreference): void {}
export function prefersReducedMotion(_preference?: MotionPreference): boolean { return false; }
