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
      const headers = new Headers(options.headers || {});
      if (options.method && options.method.toUpperCase() !== 'GET') {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
        if (csrf) headers.set('X-CSRF-Token', csrf);
      }
      // Propagate selected, server-authorized device ID to device-scoped APIs.
      const chosen = new URLSearchParams(location.search).get('device_id');
      let target = url;
      if (chosen && /^\/api\/(status|control|command|video_status|latest_frame|location_status|map_position|phone\/records|records|overview|get_pi_url|receive_location)/.test(url)) {
        const parsed = new URL(url, window.location.origin);
        parsed.searchParams.set('device_id', chosen);
        target = parsed.pathname + parsed.search;
      }
      const response = await fetch(target, { ...options, headers, signal: options.signal || controller.signal, cache: 'no-store' });
      if (!response.ok) { let detail=''; try{const body=await response.json();detail=body.message||'';}catch(_){} throw new Error(detail||`HTTP ${response.status}`); }
      return await response.json();
    } finally { clearTimeout(timer); }
  };
  // The selector is populated from an access-controlled server endpoint.
  (async () => {
    const picker = q('#rs-device-picker');
    if (!picker) return;
    try {
      const response=await fetch('/api/devices',{credentials:'same-origin',cache:'no-store'});
      if(!response.ok)return;
      const data=await response.json();
      picker.replaceChildren();
      const selected=new URLSearchParams(location.search).get('device_id')||data.selected_device_id;
      if (!data.devices.length) {
        const option=new Option('暂无绑定设备', ''); picker.add(option);picker.disabled=true;return;
      }
      for (const dev of data.devices) {
        picker.add(new Option(`${dev.name} (${dev.device_id}) ${dev.online?'●':'○'}`,dev.device_id));
      }
      picker.value=selected;
      picker.addEventListener('change',()=>{
        const next=new URL(window.location.href);
        next.searchParams.set('device_id',picker.value);
        window.location.href=next.href;
      });
      if(selected){
        document.querySelectorAll('.sidebar-nav a[href],.sidebar-info a[href]').forEach(a=>{
          try{const next=new URL(a.href);next.searchParams.set('device_id',selected);a.href=next.href;}catch(_){}
        });
      }
    }catch(_) { picker.replaceChildren(new Option('设备接口不可用','')); }
  })();
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
