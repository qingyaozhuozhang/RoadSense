(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  let online=false,sending=false,locationReady=false,videoLoading=false,videoConnected=false;
  let currentVideoUrl="", directRetryAfter=0;
  const map=window.createRoadSenseMap('live-map',{source:'active'});
  const labels={STOP:'待机中',PAUSED:'暂停中',RUNNING:'巡检中'};
  const gate=$('location-gate');
  function showGate(show){gate.hidden=!show;document.body.classList.toggle('location-gated',show);}
  async function refreshPosition(){
    try{
      const d=await window.roadSenseFetch('/api/location_status');
      locationReady=d.source!=='none'&&Number.isFinite(d.longitude)&&Number.isFinite(d.latitude);
      showGate(!locationReady);
      $('map-waiting').hidden=locationReady;
      $('map-state').className=`badge badge-${locationReady?'success':'neutral'}`;
      $('map-state').textContent=locationReady?d.source_label:'暂无有效定位';
      $('detection-coords').textContent=locationReady?`${d.longitude.toFixed(6)}, ${d.latitude.toFixed(6)}`:'经度 -- / 纬度 --';
      if(locationReady)map.update(d.longitude,d.latitude);else map.clear();
      if(!locationReady)$('gate-refresh-note').textContent='尚未找到有效位置数据，页面每 2 秒自动重新检测。';
      renderCommands(window.roadSenseStatus||{});
    }catch(_){locationReady=false;showGate(true);$('map-state').textContent='定位服务未响应';}
  }
  function renderCommands(data){
    document.querySelectorAll('.cmd-btn').forEach(btn=>{
      btn.disabled=sending||!online||(btn.dataset.command==='START'&&!locationReady)||
        (btn.dataset.command==='PAUSE'&&data.command!=='RUNNING')||
        (btn.dataset.command==='STOP'&&data.command==='STOP');
    });
  }
  function update(data){
    online=Boolean(data.online);
    $('status-badge').className=`badge badge-${online?'success':'danger'}`;
    $('status-badge').textContent=online?'边缘设备在线':'边缘设备离线';
    $('video-connection').textContent=online?'巡检摄像头':'设备未上线';
    $('detail-connection').textContent=online?'在线':'离线';
    const cmd=labels[data.command]||data.command||'未知';
    $('session-display').textContent=`当前任务：${cmd}`;
    $('detail-command').textContent=cmd;
    $('detail-session').textContent=data.session_id||'--';
    $('command-helper').textContent=data.session_id?`巡检批次：${data.session_id}`:'通过云端指令控制边缘设备';
    renderCommands(data);
  }
  // The LAN fast path never waits for cloud uploads. If the direct MJPEG link
  // is unavailable (different machine, firewall, HTTPS mixed-content), fall back
  // to the original /video_feed stream without reloading the entire page.
  function connectVideo(url){
    if(videoConnected && currentVideoUrl===url)return;
    const el=$('video-stream');
    videoConnected=true;currentVideoUrl=url;
    el.src=url;
  }
  $('video-stream').addEventListener('error',()=>{
    if(currentVideoUrl && currentVideoUrl.includes(':8091/live.mjpg')){
      directRetryAfter=Date.now()+30000;
      connectVideo(`/video_feed?t=${Date.now()}`);
      $('video-connection').textContent='直连不可达，已自动切回云端视频';
    }else{
      videoConnected=false;
      currentVideoUrl='';
    }
  });
  async function refreshVideo(){
    if(videoLoading || document.hidden)return;
    videoLoading=true;
    try{
      const status=await window.roadSenseFetch('/api/video_status');
      const available=Boolean(status.available)&&status.camera_signal!=='dark'&&status.camera_signal!=='missing';
      const dark=Boolean(status.available)&&status.camera_signal==='dark';
      const direct=(window.location.protocol==='http:' && Date.now()>directRetryAfter)
        ? String(status.direct_video_url||'') : '';
      const liveMode=direct ? '局域网低延时直连' : '云端 MJPEG';
      $('offline-mask').hidden=available;
      $('video-state').textContent=available?`摄像头 ${status.camera_index??'?'} · ${liveMode}`:
        (dark?'摄像头画面过暗 · 请检查镜头/设备':(status.device_online?'设备在线 · 摄像头暂无有效画面':'等待边缘设备摄像头'));
      $('video-label').textContent=available?`CAM ${String(status.camera_index??'?').padStart(2,'0')} · LIVE`:'摄像头 · 等待连接';
      $('video-connection').textContent=available?
        `${liveMode} · 摄像头 ${Number(status.camera_capture_fps||0).toFixed(1)} / 编码 ${Number(status.preview_render_fps||0).toFixed(1)} / 云端 ${Number(status.video_fps||0).toFixed(1)} FPS`:
        (dark?`摄像头 ${status.camera_index??'?'} 原始亮度异常`:(status.camera_signal==='missing'?'没有检测到摄像头 · 请检查 0/1/2':'等待有效视频'));
      $('video-state').className=`badge badge-${available?'success':'neutral'}`;
      if(!available){
        if(videoConnected){$('video-stream').removeAttribute('src');videoConnected=false;currentVideoUrl='';}
        $('offline-mask').querySelector('strong').textContent=dark?'摄像头已连接，但画面几乎全黑':'等待摄像头接入';
        $('offline-mask').querySelector('span').textContent=dark?'请检查镜头盖、供电、光线':'程序默认序号 2，失败后自动巡检 0、1、2';
        return;
      }
      if(direct){
        connectVideo(direct);
      }else if(!videoConnected || currentVideoUrl.includes(':8091/live.mjpg')){
        connectVideo(`/video_feed?t=${Date.now()}`);
      }
    }catch(_){
      $('offline-mask').hidden=false;
      $('video-state').textContent='无法连接视频接口';
      if(videoConnected){$('video-stream').removeAttribute('src');videoConnected=false;currentVideoUrl='';}
    }finally{videoLoading=false;}
  }
  $('gate-retry').addEventListener('click',refreshPosition);
  $('refresh-status').addEventListener('click',()=>{window.refreshRoadSenseStatus?.();refreshPosition();refreshVideo();});
  document.addEventListener('roadsense:status',ev=>update(ev.detail));
  document.addEventListener('roadsense:status-error',()=>update({online:false,command:'未知'}));
  if(window.roadSenseStatus)update(window.roadSenseStatus);
  document.querySelectorAll('.cmd-btn').forEach(btn=>btn.addEventListener('click',async()=>{
    if(sending)return;
    if(btn.dataset.command==='START'&&!locationReady){showGate(true);return;}
    if(!online){window.roadSenseToast('边缘设备离线，无法发送控制指令','error');return;}
    sending=true;renderCommands(window.roadSenseStatus||{});
    try{
      const data=await window.roadSenseFetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:btn.dataset.command})});
      window.roadSenseToast('控制指令已发送','success');
      update({...window.roadSenseStatus,online:true,command:data.command,session_id:data.session_id});
      window.refreshRoadSenseStatus?.();
    }catch(e){window.roadSenseToast(`指令发送失败：${e.message}`,'error');await refreshPosition();}
    finally{sending=false;renderCommands(window.roadSenseStatus||{});}
  }));
  refreshPosition();refreshVideo();
  setInterval(refreshPosition,2000);setInterval(refreshVideo,2000);
})();
