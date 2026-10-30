(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  let selected = null;
  let previewUrl = '';
  let busy = false;
  const severityColor = {'严重':'#e85674','中度':'#dd8b29','轻微':'#329a8b','无损伤':'#667d94'};

  function message(text, isError = false) {
    const node = byId('photo-status');
    node.textContent = text;
    node.classList.toggle('error', isError);
  }

  function selectPhoto(file) {
    if (!file) return;
    if (!file.type.startsWith('image/')) { message('请选择图片文件', true); return; }
    selected = file;
    byId('filename-label').textContent = file.name || '手机拍摄照片';
    byId('photo-placeholder').hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    const preview = byId('photo-preview');
    preview.src = previewUrl;
    preview.hidden = false;
    byId('run-detect').disabled = false;
    byId('result-card').hidden = true;
    message('照片已就绪，点击下方按钮上传云端并识别');
  }

  for (const id of ['camera-input', 'album-input']) {
    byId(id).addEventListener('change', e => selectPhoto(e.target.files?.[0]));
  }

  // Resize/compress high-resolution phone photos before transmission. The
  // server re-validates the decoded bytes; file names are never trusted.
  async function normalizedPhoto(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = () => reject(new Error('手机无法读取该照片，请改用 JPG 或 PNG 图片'));
        img.src = url;
      });
      const scale = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
      canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .88));
      if (!blob) throw new Error('图片压缩失败');
      return blob;
    } finally { URL.revokeObjectURL(url); }
  }

  async function photoLocation() {
    if (!byId('with-location').checked || !navigator.geolocation) return null;
    if (!window.isSecureContext) { message('当前为 HTTP，浏览器不会提供定位；仍可完成识别'); return null; }
    return new Promise(resolve => navigator.geolocation.getCurrentPosition(
      position => resolve(position.coords),
      () => resolve(null),
      {enableHighAccuracy:true, maximumAge:10000, timeout:4000}
    ));
  }

  function renderResult(data) {
    const r = data.record;
    byId('result-card').hidden = false;
    byId('result-photo').src = data.result_image_url + '?t=' + Date.now();
    byId('result-level').textContent = r.level;
    byId('result-severity').textContent = r.level;
    byId('result-severity').style.color = severityColor[r.level] || '#237f9b';
    byId('result-count').textContent = r.detection_count + ' 处';
    byId('result-ratio').textContent = (r.damage_ratio * 100).toFixed(2) + '%';
    byId('result-time').textContent = r.inference_ms + ' ms';
    const list = byId('result-detections');
    list.replaceChildren();
    if (!r.detections.length) {
      const empty = document.createElement('p');
      empty.textContent = '本次未检测到模型可识别的损伤目标；这不等于保证路面完全无损伤。';
      list.append(empty);
    } else {
      const heading = document.createElement('h3');
      heading.textContent = '识别目标明细';
      list.append(heading);
      for (const item of r.detections) {
        const line = document.createElement('div'); line.className = 'detection-row';
        const name = document.createElement('span');name.textContent = item.class_name;
        const confidence = document.createElement('strong');confidence.textContent = (item.confidence * 100).toFixed(1) + '% 置信度';
        line.append(name, confidence);list.append(line);
      }
    }
    byId('result-card').scrollIntoView({behavior:'smooth', block:'start'});
  }

  async function runInspection() {
    if (busy || !selected) return;
    busy = true;
    const button = byId('run-detect');
    button.disabled = true;button.textContent = '正在上传并识别...';
    message('正在准备图片并发送至阿里云，第一次加载模型可能稍慢...');
    try {
      const photo = await normalizedPhoto(selected);
      if (photo.size > 8 * 1024 * 1024) throw new Error('处理后的照片仍超过 8 MB');
      const coords = await photoLocation();
      const body = new FormData();
      body.append('photo', photo, 'roadsense_mobile_photo.jpg');
      if (coords) {
        body.append('latitude', String(coords.latitude));
        body.append('longitude', String(coords.longitude));
      }
      const response = await fetch('/api/phone/inspect', {
        method: 'POST', body, credentials:'same-origin', cache:'no-store',
        headers: {'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401) throw new Error('登录状态已失效，请重新登录');
        throw new Error(result.message || '服务器返回 HTTP ' + response.status);
      }
      renderResult(result);
      message('识别成功，结果已存入巡检档案');
      loadHistory();
    } catch (error) { message(error.message || '识别失败，请稍后重试', true); }
    finally {busy = false;button.disabled = !selected;button.textContent = '开始云端 AI 识别 →';}
  }

  async function loadHistory() {
    const container = byId('photo-history');
    try {
      const response = await fetch('/api/phone/records', {credentials:'same-origin',cache:'no-store'});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const records = await response.json();
      container.replaceChildren();
      if (!records.length) {
        const p = document.createElement('p'); p.className = 'empty-history';p.textContent = '还没有拍照识别记录，上传第一张照片开始巡检吧';container.append(p);return;
      }
      for (const record of records.slice(0, 12)) {
        const card = document.createElement('div');card.className = 'history-item';
        const img = document.createElement('img');img.src = '/images/' + encodeURIComponent(record.image);img.alt = '损伤标注图';img.loading = 'lazy';
        const meta = document.createElement('div');meta.className = 'history-meta';
        const title = document.createElement('strong');title.textContent = record.level + ' · ' + (record.detection_count || 0) + ' 处目标';
        const time = document.createElement('span');time.textContent = record.time;
        meta.append(title, time);card.append(img,meta);container.append(card);
      }
    } catch (error) { container.textContent = '识别历史加载失败：' + error.message; }
  }

  byId('run-detect').addEventListener('click', runInspection);
  byId('reload-history').addEventListener('click', loadHistory);
  loadHistory();
})();
