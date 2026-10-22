(() => {
  'use strict';
  const el = id => document.getElementById(id);
  function updateStatus(data) {
    const badge = el('overview-device-badge');
    badge.textContent = data.online ? '设备已连接' : '设备暂未连接';
    badge.className = `badge ${data.online ? 'badge-success' : 'badge-neutral'}`;
    const connection = el('overview-connection');
    connection.textContent = data.online ? '在线' : '离线';
    connection.className = `badge ${data.online ? 'badge-success' : 'badge-neutral'}`;
    el('overview-command').textContent = ({ RUNNING:'正在巡检', PAUSED:'任务已暂停', STOP:'待机中' })[data.command] || data.command || '--';
    el('overview-session').textContent = data.session_id || '暂无运行任务';
  }
  document.addEventListener('roadsense:status', e => updateStatus(e.detail));
  if (window.roadSenseStatus) updateStatus(window.roadSenseStatus);
  const textCell = (tr, content) => { const td=document.createElement('td');td.textContent=String(content??'--');tr.appendChild(td);return td; };
  async function load() {
    try {
      const data = await window.roadSenseFetch('/api/records');
      if (!Array.isArray(data)) throw new Error('无效的数据格式');
      const counts = { '轻微':0, '中度':0, '严重':0 };
      for (const record of data) if (record && Object.hasOwn(counts, record.level)) counts[record.level]++;
      const sessions = new Set(data.map(x => x.session_id).filter(x => x && x !== '未分类任务'));
      el('stat-total').textContent = data.length;
      el('stat-severe').textContent = counts['严重'];
      el('stat-moderate').textContent = counts['中度'];
      el('stat-sessions').textContent = sessions.size;
      el('distribution-count').textContent = `共 ${data.length} 条记录`;
      for (const [key, level] of [['mild','轻微'],['moderate','中度'],['severe','严重']]) {
        el(`severity-${key}-count`).textContent = counts[level];
        el(`severity-${key}-bar`).style.width = `${data.length ? counts[level]/data.length*100 : 0}%`;
      }
      el('overview-lasttime').textContent = data[0]?.time || '暂无记录';
      const tbody = el('recent-records');tbody.replaceChildren();
      if (!data.length) { const tr=document.createElement('tr');const td=textCell(tr,'暂无巡检记录');td.colSpan=4;td.className='table-empty';tbody.appendChild(tr);return; }
      for (const r of data.slice(0,5)) {
        const tr=document.createElement('tr');textCell(tr,r.time);
        const td=document.createElement('td');const span=document.createElement('span');
        span.className=`table-tag ${{'轻微':'mild','中度':'moderate','严重':'severe'}[r.level]||'mild'}`;
        span.textContent=r.level||'未知';td.appendChild(span);tr.appendChild(td);
        textCell(tr,r.session_id || '未分类任务');
        const lon=Number(r.lon),lat=Number(r.lat);
        textCell(tr,Number.isFinite(lon)&&Number.isFinite(lat)&&lon!==0&&lat!==0 ? `${lon.toFixed(5)}, ${lat.toFixed(5)}` : '--');
        tbody.appendChild(tr);
      }
    } catch (error) {
      el('distribution-count').textContent = '同步失败';
      const tbody=el('recent-records');tbody.replaceChildren();
      const tr=document.createElement('tr');const td=textCell(tr,'记录加载失败，请检查云端服务');td.colSpan=4;td.className='table-empty';tbody.appendChild(tr);
    }
  }
  // defer 脚本：common.js 已先加载，但其初次异步状态轮询可能尚未完成
  load();
})();
