/* RoadSense - AMap JavaScript API 2.0 interactive maps.
 * All stored/transmitted coordinates remain WGS84. Convert ONLY for AMap rendering.
 * AMap JS Key is public; security JS code is held by Flask reverse proxy.
 */
(() => {
  'use strict';
  const valid=(lon,lat)=>Number.isFinite(+lon)&&Number.isFinite(+lat)&&Math.abs(+lon)<=180&&Math.abs(+lat)<=90&&!(+lon===0&&+lat===0);
  // The same WGS84->GCJ02 approximation as server/coordinate_utils.py.
  // Avoid converting GPS records in permanent storage.
  function position(lon,lat){
    lon=+lon;lat=+lat;
    if(lon<73.66||lon>135.05||lat<3.86||lat>53.55)return [lon,lat];
    const PI=Math.PI,x=lon-105,y=lat-35, a=6378245.0, ee=0.00669342162296594323;
    const dy=-100+2*x+3*y+.2*y*y+.1*x*y+.2*Math.sqrt(Math.abs(x))+(20*Math.sin(6*x*PI)+20*Math.sin(2*x*PI))*2/3+(20*Math.sin(y*PI)+40*Math.sin(y/3*PI))*2/3+(160*Math.sin(y/12*PI)+320*Math.sin(y*PI/30))*2/3;
    const dx=300+x+2*y+.1*x*x+.1*x*y+.1*Math.sqrt(Math.abs(x))+(20*Math.sin(6*x*PI)+20*Math.sin(2*x*PI))*2/3+(20*Math.sin(x*PI)+40*Math.sin(x/3*PI))*2/3+(150*Math.sin(x/12*PI)+300*Math.sin(x/30*PI))*2/3;
    const radlat=lat*PI/180,magic=1-ee*Math.sin(radlat)**2,sqrtmagic=Math.sqrt(magic);
    return [lon+dx*180/(a/sqrtmagic*Math.cos(radlat)*PI), lat+dy*180/((a*(1-ee))/(magic*sqrtmagic)*PI)];
  }
  window.RoadSenseWgs84ToGcj02=position;
  let loading=null;
  function loadAMap(){
    if(loading)return loading;
    loading=(async()=>{
      const response=await fetch('/api/map_config',{cache:'no-store'});
      if(!response.ok)throw new Error(`读取高德配置失败 (HTTP ${response.status})`);
      const cfg=await response.json();
      if(!cfg.configured||!cfg.key)throw new Error('地图服务暂不可用，请联系管理员。');
      // Same-origin proxy routes to official AMap services; the security JS code never reaches the browser.
      window._AMapSecurityConfig={serviceHost:new URL(cfg.service_host||'/_AMapService',window.location.origin).href};
      if(!window.AMapLoader){
        await new Promise((resolve,reject)=>{
          const script=document.createElement('script');
          script.src='https://webapi.amap.com/loader.js';script.async=true;
          script.onload=resolve;script.onerror=()=>reject(new Error('高德 JS Loader 下载失败，请检查是否能访问 webapi.amap.com'));
          document.head.append(script);
        });
      }
      if(!window.AMapLoader)throw new Error('未检测到 AMapLoader');
      return window.AMapLoader.load({key:cfg.key,version:'2.0',plugins:['AMap.Scale','AMap.ToolBar']});
    })();
    return loading;
  }
  const pin=(kind)=>{const el=document.createElement('div');el.className='rs-map-pin '+kind;return el;};
  window.createRoadSenseMap=(id,opts={})=>{
    const node=document.getElementById(id);
    if(!node)return {update(){},clear(){},setPoints(){},focus(){}};
    let map=null,AMap=null,primary=null,pointMarkers=[],points=[],current=null,follow=true,lastCenter=null;
    // This element is shared by all page types, so do not destroy it during map initialization.
    const overlay=document.createElement('div');overlay.className='rs-map-message';overlay.hidden=true;
    const description=document.createElement('span');overlay.append(description);node.append(overlay);
    const showError=(message)=>{description.textContent=message;overlay.hidden=false;};
    const hideError=()=>{overlay.hidden=true;};
    const centerButton=document.createElement('button');centerButton.type='button';centerButton.className='rs-center-button';centerButton.textContent='◎ 跟随定位';
    centerButton.onclick=()=>{follow=true;if(map&&current)map.setZoomAndCenter(18.4,current);};
    // Load lazily on first valid coordinates. Async updates can race: re-render latest state after load.
    let started=false;
    async function init(){
      if(map)return true;
      if(started)return loading;
      started=true;
      loading=(async()=>{
        try{
          AMap=await loadAMap();
          const initial=current|| (points.length?position(points[0].lon,points[0].lat):[116.397428,39.90923]);
          const mapDiv=document.createElement('div');mapDiv.className='rs-amap-canvas';node.prepend(mapDiv);
          map=new AMap.Map(mapDiv,{center:initial,zoom:opts.zoom||18.2,viewMode:'2D',resizeEnable:true,
            mapStyle:'amap://styles/normal',showLabel:true,features:['bg','road','building','point']});
          map.addControl(new AMap.Scale());
          if(AMap.ToolBar)map.addControl(new AMap.ToolBar({position:{right:'13px',top:'58px'}}));
          node.append(centerButton,overlay);
          map.on('dragstart',()=>{follow=false;});
          map.on('complete',hideError);
          if(window.ResizeObserver)new ResizeObserver(()=>{if(map&&node.offsetWidth&&node.offsetHeight)map.resize();}).observe(node);
          render();return true;
        }catch(error){started=false;loading=null;showError('高德地图未能加载：'+(error.message||String(error)));return false;}
      })();
      return loading;
    }
    function render(){
      if(!map||!AMap)return;
      if(current){
        if(!primary)primary=new AMap.Marker({map,position:current,content:pin('active'),anchor:'center',zIndex:200});
        else primary.setPosition(current);
        if(follow&&(!lastCenter||Math.abs(lastCenter[0]-current[0])>.00004||Math.abs(lastCenter[1]-current[1])>.00004)){
          map.setCenter(current);lastCenter=current;
        }
      }
      pointMarkers.forEach(m=>m.setMap(null));pointMarkers=[];
      for(const row of points){
        const coord=position(row.lon,row.lat);const kind={'严重':'severe','中度':'moderate','轻微':'mild'}[row.level]||'mild';
        const marker=new AMap.Marker({map,position:coord,content:pin(kind),anchor:'bottom-center',zIndex:110});
        const pane=document.createElement('div');pane.className='rs-map-popup';
        const title=document.createElement('strong');title.textContent=(row.level||'道路')+'损伤';
        const xy=document.createElement('div');xy.textContent=`原始 GPS：${Number(row.lon).toFixed(6)}, ${Number(row.lat).toFixed(6)}`;
        const date=document.createElement('div');date.textContent=row.time||'';
        pane.append(title,xy,date);
        marker.on('click',()=>new AMap.InfoWindow({content:pane,offset:new AMap.Pixel(0,-25)}).open(map,coord));
        pointMarkers.push(marker);
      }
      if(points.length){
        if(pointMarkers.length===1)map.setZoomAndCenter(18.1,pointMarkers[0].getPosition());
        else map.setFitView(pointMarkers,false,[50,55,50,55],18);
      }
      setTimeout(()=>map?.resize(),120);
    }
    let loading=null;
    function update(lon,lat){
      if(!valid(lon,lat))return;
      current=position(lon,lat);if(map){renderPosition();return;}void init();
    }
    function renderPosition(){
      if(!map||!current)return;
      if(!primary){primary=new AMap.Marker({map,position:current,content:pin('active'),anchor:'center',zIndex:200});}
      else primary.setPosition(current);
      if(follow&&(!lastCenter||Math.abs(lastCenter[0]-current[0])>.00004||Math.abs(lastCenter[1]-current[1])>.00004)){
        map.setCenter(current);lastCenter=current;
      }
    }
    function clear(){current=null;lastCenter=null;if(primary){primary.setMap(null);primary=null;}}
    function setPoints(records){
      points=(Array.isArray(records)?records:[]).filter(r=>r&&valid(r.lon,r.lat)).slice(0,1500);
      if(map){render();return;}
      if(points.length)void init();
    }
    function focus(row){
      if(!row||!valid(row.lon,row.lat))return;
      const xy=position(row.lon,row.lat);
      if(!map){current=xy;void init();return;}
      map.setZoomAndCenter(18.8,xy);
      const index=points.findIndex(r=>+r.lon===+row.lon&&+r.lat===+row.lat);
      if(index>=0&&pointMarkers[index])pointMarkers[index].emit('click');
    }
    return {update,clear,setPoints,focus};
  };
})();
