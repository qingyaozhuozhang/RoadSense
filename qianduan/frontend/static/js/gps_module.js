(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const map=window.createRoadSenseMap('gps-live-map');
  let current=null, busy=false;
  const fmt=n=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(6):'--.------';
  function badge(id,value,good){$(id).className=`badge badge-${good?'success':'neutral'}`;$(id).textContent=value;}
  function source(prefix, data){
    badge(`gps-${prefix}-status`,data.available?'有效':data.age_seconds===null?'未上报':'已过期',data.available);
    $(`gps-${prefix}-coords`).textContent=`${fmt(data.longitude)} / ${fmt(data.latitude)}`;
    $(`gps-${prefix}-detail`).textContent=data.age_seconds===null?'暂未收到有效定位':`距离最后一次上报 ${data.age_seconds.toFixed(1)} 秒 · 有效期 ${data.expires_after_seconds} 秒`;
  }
  async function refresh(){
    if(busy||document.hidden)return;
    busy=true;
    try{
      const data=await window.roadSenseFetch('/api/location_status');
      source('hw',data.hardware);source('phone',data.phone);
      const active=data.source!=='none';
      current=active?{longitude:data.longitude,latitude:data.latitude}:null;
      $('gps-active-lon').textContent=fmt(data.longitude);
      $('gps-active-lat').textContent=fmt(data.latitude);
      $('gps-active-source').textContent=active?`正在使用：${data.source_label}`:'当前暂无有效定位';
      badge('gps-active-badge',active?data.source_label:'无可用定位',active);
      badge('gps-module-badge',data.hardware.available?'GPS 模块定位正常':'等待 GPS 模块定位',data.hardware.available);
      badge('gps-map-state',active?'经纬度已同步':'等待定位数据',active);
      $('gps-map-source').textContent=data.source_label;
      $('gps-copy-active').disabled=!active;
      $('gps-map-waiting').hidden=active;
      if(active)map.update(data.longitude,data.latitude);else map.clear();
    }catch(e){badge('gps-module-badge','服务连接失败',false);}
    finally{busy=false;}
  }
  $('gps-refresh').addEventListener('click',refresh);
  $('gps-copy-active').addEventListener('click',async()=>{
    if(!current)return;
    try{await navigator.clipboard.writeText(`${fmt(current.longitude)}, ${fmt(current.latitude)}`);window.roadSenseToast('坐标已复制','success');}
    catch(_){window.roadSenseToast('复制失败，请手动选择坐标','error');}
  });
  refresh();setInterval(refresh,2000);
})();
