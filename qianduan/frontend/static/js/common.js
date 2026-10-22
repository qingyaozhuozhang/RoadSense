/* 所有页面共享：侧边栏、设备连接状态、通知、时间 */
(() => {
  'use strict';
  const q = (selector) => document.querySelector(selector);
  const sidebar = q('#sidebar');
  const toggle = q('#menu-toggle');
  const scrim = q('#mobile-scrim');
  const closeMenu = () => {
    sidebar?.classList.remove('sidebar-open');
    scrim?.classList.remove('visible');
    toggle?.setAttribute('aria-expanded', 'false');
  };
  toggle?.addEventListener('click', () => {
    const open = !sidebar.classList.contains('sidebar-open');
    sidebar.classList.toggle('sidebar-open', open);
    scrim.classList.toggle('visible', open);
    toggle.setAttribute('aria-expanded', String(open));
  });
  scrim?.addEventListener('click', closeMenu);
  window.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });

  const clock = q('#topbar-clock');
  const updateClock = () => {
    if (clock) clock.textContent = new Intl.DateTimeFormat('zh-CN', { hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false }).format(new Date());
  };
  updateClock();
  setInterval(updateClock, 1000);

  window.roadSenseToast = (message, kind = 'info') => {
    const zone = q('#toast-area');
    if (!zone) return;
    const el = document.createElement('div');
    el.className = `toast toast-${kind}`;
    el.textContent = message;
    zone.appendChild(el);
    setTimeout(() => { el.classList.add('toast-exit'); setTimeout(() => el.remove(), 250); }, 3500);
  };
  window.roadSenseFetch = async (url, options = {}) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 7500);
    try {
      const response = await fetch(url, { ...options, signal: options.signal || controller.signal, cache: 'no-store' });
      if (!response.ok) { let detail=''; try{const body=await response.json();detail=body.message||'';}catch(_){} throw new Error(detail||`HTTP ${response.status}`); }
      return await response.json();
    } finally { clearTimeout(timer); }
  };
  window.roadSenseStatus = null;
  let fetching = false;
  async function pollGlobalStatus() {
    if (fetching || document.hidden) return;
    fetching = true;
    try {
      const status = await window.roadSenseFetch('/api/status');
      window.roadSenseStatus = status;
      const text = q('#sidebar-status');
      const dot = q('#sidebar-dot');
      if (text) text.textContent = status.online ? '边缘设备已连接' : '等待边缘设备连接';
      if (dot) dot.classList.toggle('is-online', Boolean(status.online));
      document.dispatchEvent(new CustomEvent('roadsense:status', {detail: status}));
    } catch (_) {
      const text = q('#sidebar-status');
      if (text) text.textContent = '云端接口暂不可用';
      q('#sidebar-dot')?.classList.remove('is-online');
      document.dispatchEvent(new CustomEvent('roadsense:status-error'));
    } finally { fetching = false; }
  }
  pollGlobalStatus();
  setInterval(pollGlobalStatus, 4000);
  window.refreshRoadSenseStatus = pollGlobalStatus;
})();
