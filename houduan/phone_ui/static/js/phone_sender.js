/* Runs on PHONE only, keeps desktop receiver read-only. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const map=window.createRoadSenseMap('sender-map',{source:'phone'});
  let watchId=null,active=false,uploading=false,lastUploadAt=0,last=null;
  const set=(msg,note)=>{$('sender-state').textContent=msg;$('sender-help').textContent=note;$('sender-dot').classList.toggle('active',active);};
  const controls=()=>{$('sender-start').disabled=active;$('sender-stop').disabled=!active;};
  async function upload(pos){
    const now=Date.now();
    if(!active||uploading||now-lastUploadAt<1500)return;
    uploading=true;lastUploadAt=now;
    try{
      const response=await fetch('/api/receive_location',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:pos.coords.latitude,longitude:pos.coords.longitude,accuracy:pos.coords.accuracy}),cache:'no-store'});
      if(!response.ok){const detail=await response.json().catch(()=>({}));throw new Error(detail.message||`HTTP ${response.status}`);}
      const result=await response.json();
      $('sender-result').textContent='云端接收成功';
      $('sender-source').textContent=result.active_source==='hardware'?'GPS模块定位（优先）':result.active_source==='phone'?'手机节点定位（备用）':'暂无';
      $('sender-last').textContent=new Date().toLocaleTimeString('zh-CN');
      set('持续上报中','定位数据已上传，电脑端会自动刷新坐标。请保持手机页面在前台。');
      $('sender-map-await').hidden=true;
      map.update(pos.coords.longitude,pos.coords.latitude);
    }catch(err){$('sender-result').textContent='上报失败';set('云端通信失败',String(err.message||err));}
    finally{uploading=false;}
  }
  function success(pos){
    if(!active)return;
    last=pos;$('sender-lon').textContent=pos.coords.longitude.toFixed(6);
    $('sender-lat').textContent=pos.coords.latitude.toFixed(6);
    if(pos.coords.accuracy>500){$('sender-result').textContent='位置质量不足，等待重新定位';set('等待更准确的位置','请打开手机精确定位、尽量到室外，等待位置数据稳定。');return;}
    upload(pos);
  }
  function stop(){if(watchId!==null)navigator.geolocation.clearWatch(watchId);watchId=null;active=false;controls();set('已停止上报','电脑端将在 30 秒内将手机上报视为过期');}
  $('sender-start').addEventListener('click',()=>{
    if(!window.isSecureContext){set('无法启动','无法使用当前位置，请通过安全链接重新打开定位页面。');return;}
    if(!navigator.geolocation){set('无法定位','该手机浏览器不支持定位服务');return;}
    if(active)return;
    active=true;controls();set('正在获取手机位置','请允许使用手机定位。');
    watchId=navigator.geolocation.watchPosition(success,err=>{
      set('手机定位异常',({1:'手机未授权位置权限',2:'手机无法获取卫星/网络位置',3:'手机定位超时'}[err.code]||err.message));
      if(err.code===1)stop();
    },{enableHighAccuracy:true,maximumAge:0,timeout:15000});
  });
  // Browser watchPosition can pause reporting when device is stationary. Ask for a fresh fix periodically.
  setInterval(()=>{if(active && !document.hidden)navigator.geolocation.getCurrentPosition(success,()=>{},
      {enableHighAccuracy:true,maximumAge:0,timeout:12000});},8000);
  $('sender-stop').addEventListener('click',stop);
  window.addEventListener('beforeunload',stop);
  controls();
})();
