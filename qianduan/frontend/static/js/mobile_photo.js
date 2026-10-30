/* Embedded at the bottom of the existing RoadSense phone GPS page. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('sender-photo-section')) return;

  let selectedFile = null;
  let previewUrl = '';
  let previewId = '';
  let generation = 0;
  let archiving = false;
  const levelColors = {'严重':'#db405c','中度':'#c8781c','轻微':'#168e78','无损伤':'#657b91'};

  function status(message, error = false) {
    const label = $('sender-photo-status');
    label.textContent = message;
    label.classList.toggle('is-error', error);
  }

  function choose(file) {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      status('请选择 JPG、PNG 或 WebP 格式的图片', true);
      return;
    }
    if (archiving) { status('正在归档上一张照片，请稍后再拍摄', true); return; }
    generation += 1;
    const version = generation;
    const stalePreview = previewId;
    previewId = '';
    if (stalePreview) discard(stalePreview);
    selectedFile = file;
    $('sender-photo-filename').textContent = file.name || '手机拍摄的道路照片';
    $('sender-photo-submit').hidden = true;
    $('sender-photo-submit').disabled = true;
    $('sender-photo-archived').hidden = true;
    $('sender-photo-result').hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    $('sender-photo-preview').src = previewUrl;
    $('sender-photo-preview').hidden = false;
    $('sender-photo-placeholder').hidden = true;
    status('照片已拍摄，正在自动启动 YOLO 识别...');
    inspect(file, version);
  }

  for (const inputId of ['sender-camera-input', 'sender-album-input']) {
    $(inputId).addEventListener('change', event => choose(event.target.files?.[0]));
  }

  async function compress(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = () => reject(new Error('无法读取照片，请选择 JPG 或 PNG 图片'));
        img.src = url;
      });
      const scale = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('浏览器不支持照片处理');
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const output = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.86));
      if (!output || output.size > 8 * 1024 * 1024) throw new Error('照片处理失败或超过 8 MB');
      return output;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  async function latestPhotoLocation() {
    const native=window.roadSenseLastNativeFix;
    if(native && Date.now()-native.timestamp<10000) return {latitude:native.latitude,longitude:native.longitude};
    if (!navigator.geolocation || !window.isSecureContext) return null;
    return new Promise(resolve => {
      navigator.geolocation.getCurrentPosition(
        point => resolve({latitude: point.coords.latitude, longitude: point.coords.longitude}),
        () => resolve(null),
        {enableHighAccuracy:true, maximumAge:10000, timeout:4000}
      );
    });
  }

  function render(data) {
    const record = data.record || {};
    const result = $('sender-photo-result');
    result.hidden = false;
    $('sender-photo-level').textContent = record.level || '未知';
    $('sender-photo-level').style.color = levelColors[record.level] || '#146d91';
    $('sender-photo-count').textContent = (record.detection_count ?? 0) + ' 处';
    $('sender-photo-ratio').textContent = ((record.damage_ratio ?? 0) * 100).toFixed(2) + '%';

    // Data URI is provided by the edge relay (HTTPS->cloud HTTP); a cloud
    // browser logged in directly receives a same-origin relative image URL.
    const image = data.result_image_data_url || data.result_image_url;
    if (image) {
      const annotated = $('sender-photo-annotated');
      annotated.src = image;
      annotated.hidden = false;
    }
    const list = $('sender-photo-detections');
    list.replaceChildren();
    if (!Array.isArray(record.detections) || !record.detections.length) {
      const note = document.createElement('p');
      note.textContent = '未检测到模型可识别的道路损伤，请结合现场情况判断。';
      list.append(note);
    } else {
      for (const detection of record.detections) {
        const line = document.createElement('div');
        line.className = 'sender-photo-detection';
        const name = document.createElement('span');
        name.textContent = detection.class_name || '道路损伤';
        line.append(name);
        list.append(line);
      }
    }
    result.scrollIntoView({behavior:'smooth', block:'nearest'});
  }

  async function inspect(file, version) {
    status('正在自动识别道路损伤，首次加载云端 YOLO 模型可能稍慢');
    try {
      const photo = await compress(file);
      const body = new FormData();
      body.append('photo', photo, 'roadsense_mobile_photo.jpg');
      const location = await latestPhotoLocation();
      if (location) {
        body.append('latitude', String(location.latitude));
        body.append('longitude', String(location.longitude));
      }
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
      const response = await fetch('/api/phone/preview', {
        method:'POST', body, credentials:'same-origin', cache:'no-store',
        headers: csrf ? {'X-CSRF-Token': csrf} : {}
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401) throw new Error('登录已失效或设备令牌不匹配，请检查服务配置');
        throw new Error(data.message || ('云端返回 HTTP ' + response.status));
      }
      if (!data.record || !data.preview_id) throw new Error('云端未返回临时识别结果');
      if (version !== generation) {
        discard(data.preview_id);
        return;
      }
      previewId = data.preview_id;
      render(data);
      const button = $('sender-photo-submit');
      button.hidden = false;
      button.disabled = false;
      button.textContent = '✓ 确认归档到云端';
      status('AI 识别完成。请查看结果；只有点击“确认归档”后才会生成巡检记录。');
    } catch (error) {
      if (version === generation) status(String(error.message || '自动识别失败，请重新拍照'), true);
    }
  }

  async function discard(id) {
    try {
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
      await fetch('/api/phone/discard', {
        method:'POST', credentials:'same-origin', cache:'no-store',
        headers:{'Content-Type':'application/json', ...(csrf ? {'X-CSRF-Token':csrf} : {})},
        body:JSON.stringify({preview_id:id})
      });
    } catch (_) { /* In-memory preview expires automatically on the cloud. */ }
  }

  async function archive() {
    if (!previewId || archiving) return;
    archiving = true;
    const button = $('sender-photo-submit');
    button.disabled = true;
    button.textContent = '正在保存巡检档案...';
    status('正在将已识别的结果正式归档到阿里云');
    try {
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
      const response = await fetch('/api/phone/archive', {
        method:'POST', credentials:'same-origin', cache:'no-store',
        headers:{'Content-Type':'application/json', ...(csrf ? {'X-CSRF-Token':csrf} : {})},
        body:JSON.stringify({preview_id:previewId})
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.archived) throw new Error(data.message || ('归档失败：HTTP ' + response.status));
      previewId = '';
      button.hidden = true;
      $('sender-photo-archived').hidden = false;
      status('归档成功！中度和严重损伤记录可在云端“损伤记录档案”中点击查看照片。');
    } catch (error) {
      status(String(error.message || '归档失败，请重试'), true);
      button.disabled = false;
      button.textContent = '✓ 重试确认归档';
    } finally {
      archiving = false;
    }
  }
  $('sender-photo-submit').addEventListener('click', archive);
})();
