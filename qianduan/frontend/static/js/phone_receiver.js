(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const map=window.createRoadSenseMap('phone-location-map',{source:'phone'});
  let loading = false;
  const fmt = x => Number.isFinite(x) ? x.toFixed(6) : '--.------';
  async function poll(){
    if(loading||document.hidden)return;loading=true;
    try {
      const data=await window.roadSenseFetch('/api/location_status');
      const phone=data.phone||{},valid=phone.available===true;
      $('phone-recv-badge').textContent=valid?'手机定位已接收':'等待手机定位';
      $('phone-recv-badge').className=`badge badge-${valid?'success':'neutral'}`;
      $('phone-source-status').textContent=valid?'有效上报':'未收到有效上报';
      $('phone-received-status').textContent=valid?'正在接收手机上传的坐标':'尚未收到有效手机坐标';
      $('phone-received-note').textContent=valid?'手机坐标已到达云端；GPS 模块有效时仍优先使用模块位置':'请在 RoadSense Android App 内点击开始定位上报';
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
