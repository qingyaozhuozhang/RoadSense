(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const out = $('device-result');
  let lastDeviceEnv = '';
  let lastDeviceId = '';
  const downloadButton = $('download-device-config');
  const cell = (tr, value) => { const td = tr.insertCell(); td.textContent = String(value ?? '—'); return td; };
  // Administrator-only image preview, with the same authenticated /images/ gate as archives.
  let adminPhotoOverlay;
  function previewArchivePhoto(record){
    if (!record.image) return;
    if (!adminPhotoOverlay){
      adminPhotoOverlay=document.createElement('div');
      adminPhotoOverlay.className='rs-admin-photo-overlay';
      adminPhotoOverlay.hidden=true;
      const dialog=document.createElement('section');
      dialog.className='rs-admin-photo-dialog';
      dialog.setAttribute('role','dialog');
      dialog.setAttribute('aria-modal','true');
      const header=document.createElement('div');
      header.className='rs-admin-photo-heading';
      const heading=document.createElement('strong');heading.className='rs-admin-photo-name';
      const close=document.createElement('button');close.type='button';close.textContent='关闭 ✕';
      close.onclick=()=>{adminPhotoOverlay.hidden=true;document.body.style.overflow='';};
      header.append(heading,close);
      const pic=document.createElement('img');pic.alt='道路损伤归档照片';
      const err=document.createElement('small');err.textContent='图片加载失败，请检查云端图片文件';err.hidden=true;
      pic.onerror=()=>{err.hidden=false;};
      dialog.append(header,pic,err);adminPhotoOverlay.append(dialog);document.body.append(adminPhotoOverlay);
      adminPhotoOverlay.addEventListener('click',e=>{if(e.target===adminPhotoOverlay)close.click();});
      document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!adminPhotoOverlay.hidden)close.click();});
    }
    adminPhotoOverlay.querySelector('.rs-admin-photo-name').textContent=record.record_name||'归档照片';
    adminPhotoOverlay.querySelector('small').hidden=true;
    adminPhotoOverlay.querySelector('img').src='/images/'+encodeURIComponent(record.image);
    adminPhotoOverlay.hidden=false;
    document.body.style.overflow='hidden';
  }
  const authFetch = async (path, options = {}) => {
    // This page is loaded after common.js, but fetch directly if a cache or third-party
    // script has replaced the global helper. Always send the CSRF meta token.
    if (typeof window.roadSenseFetch === 'function') return window.roadSenseFetch(path, options);
    const headers = new Headers(options.headers || {});
    if (options.method && options.method !== 'GET') {
      headers.set('X-CSRF-Token', document.querySelector('meta[name="csrf-token"]')?.content || '');
    }
    const response = await fetch(path, {...options, headers, credentials: 'same-origin', cache: 'no-store'});
    const body = await response.json();
    if (!response.ok) throw Error(body.message || `HTTP ${response.status}`);
    return body;
  };
  // The server returns the complete houduan/.env (including the AMap settings)
  // only when the admin issues a new device token. No secrets in static JS.
  const configText = data => {
    if (typeof data.device_env !== 'string' ||
        !data.device_env.includes('DEVICE_ID=') ||
        !data.device_env.includes('AMAP_JS_KEY=')) {
      throw Error('服务器未返回完整 .env，请先部署新版云端补丁');
    }
    return data.device_env;
  };
  const offerDownload = () => {
    if (!lastDeviceEnv || !lastDeviceId) return;
    // Browser-local download: the device secret is never sent to another endpoint.
    const blob = new Blob([lastDeviceEnv], {type: 'text/plain;charset=utf-8'});
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = '.env';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1500);
    out.textContent = `设备 ${lastDeviceId} 的 .env 已触发下载。\n下载文件名就是 .env，可直接覆盖对应树莓派 houduan/.env。覆盖前请备份；检查该设备摄像头编号和 GPS 串口。\n设备令牌不可在列表中再次查看，丢失后请轮换密钥。`;
  };
  async function mutation(path, payload, method = 'POST') {
    try {
      const response = await authFetch(path, {
        method, headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload || {})
      });
      if (response.device_token) {
        try { lastDeviceEnv = configText(response); } catch (error) {
          out.textContent = error.message; lastDeviceEnv=''; lastDeviceId='';
          downloadButton.hidden=true; return response;
        }
        lastDeviceId = response.device_id;
        out.textContent = `已生成设备 ${lastDeviceId} 的完整 houduan/.env。点击下载后可以直接覆盖该设备现有 .env（请先备份）。每台设备必须使用自己的配置。`;
        downloadButton.hidden = false;
      } else {
        out.textContent = response.message || '操作成功';
        lastDeviceEnv = ''; lastDeviceId = ''; downloadButton.hidden = true;
      }
      await refresh();
      return response;
    } catch (error) { out.textContent = '操作失败：' + error.message; return null; }
  }
  async function refresh() {
    const endpoints = ['/api/devices', '/api/admin/users', '/api/admin/records', '/api/admin/audit'];
    const [devices, users, records, audits] = await Promise.allSettled(endpoints.map(url => authFetch(url)));
    const problems = [];
    if (devices.status === 'fulfilled') {
      const tbody = $('device-table').tBodies[0]; tbody.replaceChildren();
      (devices.value.devices || []).forEach(d => {
        const tr = tbody.insertRow();
        cell(tr, d.device_id); cell(tr, d.name); cell(tr, d.owner_username || d.owner_id); cell(tr, d.online ? '在线' : '离线');
        const td = tr.insertCell(); const rotate = document.createElement('button');
        rotate.textContent = '轮换密钥';
        rotate.onclick = () => { if (confirm('旧令牌会立即失效；必须同步修改该设备的 .env。确定继续？')) mutation('/api/admin/devices/' + encodeURIComponent(d.device_id) + '/rotate', {}); };
        td.append(rotate);
      });
    } else problems.push('设备列表：' + devices.reason.message);
    if (users.status === 'fulfilled') {
      const tbody = $('user-table').tBodies[0]; tbody.replaceChildren();
      users.value.forEach(u => {
        const tr = tbody.insertRow(); cell(tr, u.id); cell(tr, u.username); cell(tr, u.role === 'admin' ? '管理员' : '普通用户');
        const rightCell = tr.insertCell();
        rightCell.textContent = u.role === 'admin' ? '始终允许' : (u.mobile_location_granted ? '已授权' : '未授权（无设备时）');
        const td = tr.insertCell();
        if (u.role === 'user') {
          const toggle = document.createElement('button');
          toggle.type = 'button';
          toggle.textContent = u.mobile_location_granted ? '撤销独立定位权限' : '允许独立手机定位';
          toggle.onclick = () => {
            const allowed = !Boolean(u.mobile_location_granted);
            if (confirm(`确定${allowed ? '允许' : '撤销'}账号 ${u.username} 在未分配设备时使用手机定位和归档？`)) {
              mutation('/api/admin/users/' + u.id + '/mobile-location', {allowed});
            }
          };
          td.append(toggle);
          const btn = document.createElement('button'); btn.textContent = '升为管理员';
          btn.onclick = () => { if(confirm(`确认将账号 ${u.username} 升级为管理员？升级后可查看所有设备并修改数据。`)) mutation('/api/admin/users/promote', {username:u.username}); };
          td.append(btn);
          const reset = document.createElement('button'); reset.textContent='重置密码'; reset.style.marginLeft='6px';
          reset.onclick=async()=>{ if(!confirm(`确认重置 ${u.username} 的密码？原密码会立即失效。`))return;
            const data=await mutation('/api/admin/users/'+u.id+'/reset-password', {});
            if(data?.temporary_password) alert('新密码（只显示此次，请立即安全交付用户）：\n'+data.temporary_password);
          };
          const del=document.createElement('button'); del.textContent='删除用户'; del.style.marginLeft='6px';
          del.onclick=()=>{if(confirm(`确认删除普通用户 ${u.username}？有关联设备或损伤记录时服务器会拒绝删除。`))mutation('/api/admin/users/'+u.id,{},'DELETE');};
          td.append(reset,del);
        } else { td.textContent = '管理员不可在此删除'; }
      });
    } else problems.push('用户列表：' + users.reason.message);
    if (records.status === 'fulfilled') {
      const rows = Array.isArray(records.value) ? records.value : [];
      $('admin-record-summary').textContent = `数据库中共有 ${rows.length} 条损伤记录 · 本页显示最近 200 条`;
      const zone = $('record-table'); zone.replaceChildren();
      if (rows.length === 0) { const p = document.createElement('p'); p.textContent = '暂无数据库记录。若档案有数据但这里没有，请检查 .env 的 ROADSENSE_DATA_DIR 是否指向同一 data 目录。'; zone.append(p); }
      const table = document.createElement('table'); table.className = 'rs-table';
      const header = table.createTHead().insertRow();
      ['记录名称', '图片', '来源设备', '关联/上传账号', '损伤等级', '检测时间', '操作'].forEach(x => { const th = document.createElement('th'); th.textContent = x; header.append(th); });
      const body = table.createTBody();
      rows.slice(0,200).forEach(r => {
        const tr = body.insertRow();
        const name=cell(tr, r.record_name || '未命名记录');name.className='rs-admin-record-name';
        const imageCell=tr.insertCell();
        if(r.image){
          const preview=document.createElement('button');preview.type='button';
          preview.className='rs-admin-thumb-button';preview.title='点击查看原始尺寸照片';
          const image=document.createElement('img');image.loading='lazy';image.alt='查看 '+(r.record_name||'道路损伤')+' 图片';
          image.src='/images/'+encodeURIComponent(r.image);preview.append(image);
          preview.onclick=()=>previewArchivePhoto(r);imageCell.append(preview);
        }else{imageCell.textContent='无照片';}
        cell(tr, r.device_id); cell(tr, r.uploaded_by_username || '历史记录'); cell(tr, r.level); cell(tr, r.time);
        const td = tr.insertCell();
        const edit = document.createElement('button'); edit.textContent='编辑';
        edit.onclick=()=>{ const notes=prompt('修改备注（保留其他字段）',r.notes||''); if(notes!==null)mutation('/api/admin/records/'+encodeURIComponent(r.id),{notes},'PATCH'); };
        const del = document.createElement('button'); del.textContent='删除'; del.style.marginLeft='8px';
        del.onclick=()=>{ if(confirm('永久删除数据库中的这一条记录？原照片暂时保留。'))mutation('/api/admin/records/'+encodeURIComponent(r.id),{},'DELETE'); };
        td.append(edit,del);
      });
      zone.append(table);
    } else problems.push('损伤记录：' + records.reason.message);
    if (audits.status === 'fulfilled') {
      $('audit-view').textContent = audits.value.map(a => `${a.performed_at}  ${a.actor_username||'system'}  ${a.action}  ${a.target_id}`).join('\n') || '暂无操作日志';
    } else problems.push('审计日志：' + audits.reason.message);
    if (problems.length) out.textContent = '以下接口加载失败：\n' + problems.join('\n');
  }
  $('dev-create').onclick=()=>mutation('/api/admin/devices',{
    device_id:$('dev-id').value.trim(),name:$('dev-name').value.trim(),owner_username:$('dev-owner').value.trim()
  });
  $('dev-assign').onclick=()=>mutation('/api/admin/devices/'+encodeURIComponent($('assign-id').value.trim())+'/assign',{
    owner_username:$('assign-owner').value.trim()
  });
  $('promote-user').onclick=()=>{ const username=$('promote-username').value.trim();
    if (!username) {out.textContent='请输入待升级的账号';return;}
    if(confirm(`确认将 ${username} 升级为管理员？`)) mutation('/api/admin/users/promote',{username});
  };
  downloadButton.onclick = offerDownload;
  refresh().catch(error => {out.textContent = '管理页初始化失败：'+error.message;});
})();
