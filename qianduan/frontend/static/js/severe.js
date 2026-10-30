(() => {
  'use strict';
  const $ = id=>document.getElementById(id);
  const severityClasses={'轻微':'mild','中度':'moderate','严重':'severe'};
  const colors={'轻微':'#f2b650','中度':'#ed9560','严重':'#ea6878'};
  let allRecords=[],filtered=[],activeLevel='all';
  let photoRecord=null;
  const map=window.createRoadSenseMap('map-container',{zoom:15});
  const photoOverlay=$('archive-photo-overlay');
  const photoImage=$('archive-photo-image');
  const photoTabAnnotated=$('archive-photo-annotated');
  const photoTabOriginal=$('archive-photo-original');
  let photoReturnFocus=null;
  function showPhotoType(kind){
    if(!photoRecord)return;
    const filename=kind==='original'&&photoRecord.original_image?photoRecord.original_image:photoRecord.image;
    photoTabAnnotated.setAttribute('aria-pressed',String(kind!=='original'));
    photoTabOriginal.setAttribute('aria-pressed',String(kind==='original'));
    $('archive-photo-error').hidden=true;
    photoImage.hidden=false;
    photoImage.src='/images/'+encodeURIComponent(filename);
    photoImage.alt=(kind==='original'?'原始路面照片：':'YOLO 标注照片：')+photoRecord.level+'道路损伤';
  }
  function openPhoto(record){
    if(!record.image)return false;
    photoRecord=record;
    photoReturnFocus=document.activeElement;
    $('archive-photo-title').textContent=(record.record_name||'未命名记录')+' · '+record.level+'损伤';
    $('archive-photo-info').textContent='检测时间：'+(record.time||'未知时间')+' · 巡检批次：'+(record.session_id||'未分类任务');
    photoTabOriginal.hidden=!record.original_image;
    photoOverlay.hidden=false;
    document.body.style.overflow='hidden';
    showPhotoType('annotated');
    $('archive-photo-close').focus();
    return true;
  }
  function closePhoto(){
    if(photoOverlay.hidden)return;
    photoOverlay.hidden=true;
    photoRecord=null;
    photoImage.removeAttribute('src');
    document.body.style.overflow='';
    photoReturnFocus?.focus?.();
  }
  $('archive-photo-close').addEventListener('click',closePhoto);
  photoOverlay.addEventListener('click',event=>{if(event.target===photoOverlay)closePhoto();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')closePhoto();});
  photoTabAnnotated.addEventListener('click',()=>showPhotoType('annotated'));
  photoTabOriginal.addEventListener('click',()=>showPhotoType('original'));
  photoImage.addEventListener('error',()=>{$('archive-photo-error').hidden=false;photoImage.hidden=true;});
  const coordinates = record => {
    if (record.lat==null||record.lon==null||String(record.lat).trim()===''||String(record.lon).trim()==='') return null;
    const lat=Number(record.lat),lon=Number(record.lon);
    if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180||(lat===0&&lon===0))return null;
    return [lat,lon];
  };
  function emptyState(title,subtitle){
    const div=document.createElement('div');div.className='archive-empty';
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.classList.add('icon');
    const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#i-layers');icon.appendChild(use);
    const strong=document.createElement('strong');strong.textContent=title;
    const span=document.createElement('span');span.textContent=subtitle;
    div.append(icon,strong,span);return div;
  }
  function escapeFilenamePart(value){return String(value).replace(/[^\w\u4e00-\u9fa5-]/g,'_').slice(0,45);}
  function tag(record){const span=document.createElement('span');span.className=`table-tag ${severityClasses[record.level]||'mild'}`;span.textContent=record.level||'未分类';return span;}
  function recordCard(record,index){
    const button=document.createElement('button');button.type='button';button.className='record-card';button.setAttribute('aria-label',`查看第 ${index+1} 条${record.level||''}损伤记录${record.image?'的道路照片':'的位置'}`);
    const thumb=document.createElement('div');thumb.className='record-thumb';
    if(record.image){const img=document.createElement('img');img.loading='lazy';img.alt=`${record.level||'损伤'}损伤图像`;img.src=`/images/${encodeURIComponent(record.image)}`;img.addEventListener('error',()=>{thumb.textContent='图片不可用';});thumb.appendChild(img);}
    else{const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.classList.add('icon');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#i-camera');icon.appendChild(use);thumb.appendChild(icon);}
    const body=document.createElement('div');body.className='record-body';
    const top=document.createElement('div');top.className='record-top';const clock=document.createElement('span');clock.className='record-timestamp';clock.textContent=record.time||'未知时间';top.append(tag(record),clock);
    const title=document.createElement('strong');title.className='record-title';title.textContent=record.record_name||`${record.level||'未知'}道路损伤`;
    const task=document.createElement('span');task.className='record-meta';task.textContent=`${record.level||'未知'}损伤 · 批次：${record.session_id||'未分类任务'}`;
    const coord=document.createElement('span');coord.className='record-coord';const xy=coordinates(record);coord.textContent=xy?`GPS ${xy[1].toFixed(6)}, ${xy[0].toFixed(6)}`:'暂无有效 GPS 坐标';
    body.append(top,title,task,coord);button.append(thumb,body);
    button.addEventListener('click',()=>{
      document.querySelectorAll('.record-card').forEach(b=>b.classList.remove('selected'));button.classList.add('selected');
      if(openPhoto(record)){
        if(xy)map.focus(record);
        return;
      }
      if(!xy){window.roadSenseToast('该条记录没有可用的 GPS 坐标');return;}
      map.focus(record);
      if(window.matchMedia('(max-width:1100px)').matches)$('map-container')?.scrollIntoView({behavior:'smooth',block:'center'});
    });return button;
  }
  function updateMap(){
    const valid=filtered.filter(r=>coordinates(r));
    const yes=valid.length>0;
    $('archive-map-empty').hidden=yes;
    $('map-container').hidden=!yes;
    $('marker-count').textContent=`${valid.length} 个有效坐标`;
    if(yes)map.setPoints(valid);else map.setPoints([]);
  }
  function applyFilters(){
    const session=$('session-filter').value;const search=$('record-search').value.trim().toLocaleLowerCase();
    filtered=allRecords.filter(r=>(session==='all'||r.session_id===session)&&(activeLevel==='all'||r.level===activeLevel)&&(!search||[r.record_name,r.uploaded_by_username,r.session_id,r.time,r.lon,r.lat,r.level].some(value=>String(value??'').toLocaleLowerCase().includes(search))));
    $('filtered-count').textContent=`${filtered.length} / ${allRecords.length} 条`;
    const container=$('records-container');container.replaceChildren();
    if(!filtered.length)container.append(emptyState('没有符合条件的记录','试试修改批次、关键词或损伤等级'));
    else {const fragment=document.createDocumentFragment();filtered.forEach((r,i)=>fragment.appendChild(recordCard(r,i)));container.append(fragment);}
    updateMap();
  }
  function updateStats(){
    $('archive-total').textContent=allRecords.length;
    [['轻微','archive-mild'],['中度','archive-moderate'],['严重','archive-severe']].forEach(([l,id])=>$(id).textContent=allRecords.filter(r=>r.level===l).length);
  }
  async function loadRecords(){
    const refresh=$('refresh-records');refresh.disabled=true;
    try{
      const response=await window.roadSenseFetch('/api/records');
      if(!Array.isArray(response))throw new Error('服务器数据格式错误');
      allRecords=response.filter(x=>x&&typeof x==='object');
      const previous=$('session-filter').value;
      const select=$('session-filter');select.replaceChildren();
      const all=document.createElement('option');all.value='all';all.textContent='全部巡检批次';select.append(all);
      const sessions=[...new Set(allRecords.map(r=>r.session_id).filter(Boolean))];
      sessions.forEach(session=>{const opt=document.createElement('option');opt.value=session;opt.textContent=session;select.append(opt);});
      select.value=sessions.includes(previous)?previous:'all';
      updateStats();applyFilters();
    }catch(error){
      $('records-container').replaceChildren(emptyState('记录加载失败',error.message||'请检查服务器连接'));
      window.roadSenseToast('无法加载巡检记录','error');
    }finally{refresh.disabled=false;}
  }
  $('session-filter').addEventListener('change',applyFilters);
  $('record-search').addEventListener('input',applyFilters);
  document.querySelectorAll('.filter-tab').forEach(tab=>tab.addEventListener('click',()=>{
    activeLevel=tab.dataset.level;
    document.querySelectorAll('.filter-tab').forEach(t=>t.setAttribute('aria-pressed',String(t===tab)));
    applyFilters();
  }));
  $('refresh-records').addEventListener('click',loadRecords);
  $('export-records').addEventListener('click',()=>{
    if(!filtered.length){window.roadSenseToast('当前筛选下没有可导出的记录');return;}
    const keys=['record_name','time','level','session_id','uploaded_by_username','device_id','lon','lat','image'];
    const escapeCell=value=>'"'+String(value??'').replace(/"/g,'""')+'"';
    const lines=[['记录名称','检测时间','损伤等级','巡检批次','上传账号','来源设备','经度','纬度','图像文件'],...filtered.map(r=>keys.map(k=>r[k]))];
    const blob=new Blob(['\uFEFF'+lines.map(row=>row.map(escapeCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
    const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`RoadSense_巡检档案_${escapeFilenamePart(new Date().toISOString().slice(0,10))}.csv`;
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    window.roadSenseToast(`已导出 ${filtered.length} 条记录`,'success');
  });
  loadRecords();
})();
