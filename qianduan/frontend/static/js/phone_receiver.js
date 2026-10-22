(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const map=window.createRoadSenseMap('phone-location-map',{source:'phone'});
  let loading=false,entryUrl='',qrObjectUrl=null;
  const fmt=x=>Number.isFinite(x)?x.toFixed(6):'--.------';
  function statusMessage(msg){$('phone-qr-loading').textContent=msg;$('phone-qr-loading').hidden=false;}
  async function configureEntry(){
    try {
      const links=await window.roadSenseFetch('/api/mobile_access');
      entryUrl=links.entry_url||'';
      const link=$('phone-upload-link');link.textContent=entryUrl||'未找到可供手机访问的网址';
      if(entryUrl)link.href=entryUrl; else link.removeAttribute('href');
      $('copy-phone-link').disabled=!entryUrl;
      $('phone-entry-type').textContent=links.entry_kind==='pi'?'定位节点 HTTPS':links.entry_kind==='public'?'公网 HTTPS':'本地网络';
      $('phone-access-tip').textContent=links.hint||'手机和服务端必须网络互通。';
      if(!entryUrl){statusMessage('手机入口暂不可用，请确认定位节点连接');return;}
      const res=await fetch('/api/mobile_qr',{cache:'no-store'});
      if(!res.ok){let detail=await res.json().catch(()=>({}));throw new Error(detail.message||'HTTP '+res.status);}
      const type=res.headers.get('content-type')||'';
      if(!type.includes('image/'))throw new Error('服务器未返回二维码图像');
      const url=URL.createObjectURL(await res.blob());
      const qr=$('phone-entry-qr');
      if(qrObjectUrl)URL.revokeObjectURL(qrObjectUrl);qrObjectUrl=url;
      qr.onload=()=>{$('phone-qr-loading').hidden=true;qr.hidden=false;};
      qr.onerror=()=>{qr.hidden=true;statusMessage('二维码图片无法解析，请检查 Flask 日志');};
      qr.src=url;
    }catch(error){statusMessage('二维码生成失败：'+(error.message||error));}
  }
  $('copy-phone-link').addEventListener('click',async()=>{
    if(!entryUrl)return;
    try{await navigator.clipboard.writeText(entryUrl);window.roadSenseToast('已复制手机入口地址','success');}
    catch(_){window.roadSenseToast('请长按或选中网址进行复制');}
  });
  configureEntry();
  async function poll(){
    if(loading||document.hidden)return;loading=true;
    try {
      const data=await window.roadSenseFetch('/api/location_status');
      const phone=data.phone||{},valid=phone.available===true;
      $('phone-recv-badge').textContent=valid?'手机定位已接收':'等待手机定位';
      $('phone-recv-badge').className=`badge badge-${valid?'success':'neutral'}`;
      $('phone-source-status').textContent=valid?'有效上报':'未收到有效上报';
      $('phone-received-status').textContent=valid?'正在接收手机上传的坐标':'尚未收到有效手机坐标';
      $('phone-received-note').textContent=valid?'手机坐标已到达云端；GPS 模块有效时仍优先使用模块位置':'请在手机打开二维码或网址，开始上报手机定位';
      $('phone-signal-disc').classList.toggle('is-live',valid);
      $('phone-recv-lon').textContent=valid?fmt(phone.longitude):'--.------';
      $('phone-recv-lat').textContent=valid?fmt(phone.latitude):'--.------';
      $('phone-last-received').textContent=valid?new Date(Date.now()-phone.age_seconds*1000).toLocaleTimeString('zh-CN'):'--';
      $('phone-fix-age').textContent=phone.age_seconds==null?'未上报':`${phone.age_seconds.toFixed(1)} 秒`;
      $('phone-validity').textContent=valid?'最近30秒有有效坐标':'无效或过期';
      $('phone-map-state').textContent=valid?'手机地图实时同步':'等待手机上报';
      $('phone-map-state').className=`badge badge-${valid?'success':'neutral'}`;
      $('phone-map-waiting').hidden=valid;
      $('phone-map-coords').textContent=valid?`${fmt(phone.longitude)}, ${fmt(phone.latitude)}`:'-- / --';
      if(valid)map.update(phone.longitude,phone.latitude);else map.clear();
    }catch(err){$('phone-received-status').textContent='云端定位接口未响应';}
    finally{loading=false;}
  }
  poll();setInterval(poll,2200);
})();
