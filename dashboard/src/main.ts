import './style.css';
import { mountDashboard, type DashboardApi } from './presentation';
import { startPageSession } from './lifecycle';

startPageSession();
const api: DashboardApi = async <T>(path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options });
  if (!response.ok) {
    let message = response.status === 404 ? '还没有可用快照，请先同步展示。' : `请求未完成（${response.status}）`;
    try { const detail = await response.json(); if (typeof detail.error === 'string') message = detail.error; } catch { /* Preserve a useful HTTP message. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
};
mountDashboard({ api });
