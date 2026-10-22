"""RoadSense cloud service. Run `python server/app.py` from cloud_web.

Maintains all routes from the uploaded Flask app.py for legacy edge compatibility.
Server and front-end live in separate folders, but Flask serves both at one origin
for easy local testing and to avoid requiring a Node build tool or permissive CORS.
"""
from __future__ import annotations

import os
import socket
import ipaddress
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

try:
    from .location_state import PositionManager, PHONE_TTL, valid_coordinates
    from .storage import RecordStore
    from .amap_proxy import amap_blueprint
except ImportError:  # python server/app.py
    from location_state import PositionManager, PHONE_TTL, valid_coordinates
    from storage import RecordStore
    from amap_proxy import amap_blueprint

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / 'frontend'
DEFAULT_DATA = ROOT / 'data'
ALLOWED_COMMANDS = {'START', 'STOP', 'PAUSE'}
ALLOWED_SOURCE = {'hardware', 'phone'}
# Lightweight local config, no extra package required. Copy .env.example to .env.
# Environment variables always override this file.
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines() if (ROOT / '.env').exists() else ():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line: continue
    key, value = line.split('=',1)
    key = key.strip()
    if key in ('HOST', 'PORT', 'DEVICE_TOKEN', 'CONTROL_TOKEN',
               'ROADSENSE_DATA_DIR', 'SSL_CERT_FILE', 'SSL_KEY_FILE',
               'PHONE_PUBLIC_URL', 'LAN_HOST', 'MAX_PHONE_ACCURACY_M',
               'AMAP_JS_KEY', 'AMAP_JS_SECRET'):
        os.environ.setdefault(key,value.strip().strip('\"').strip("'"))
APP_START = time.time()


def create_app(config=None):
    app = Flask(__name__, static_folder=str(FRONTEND / 'static'), template_folder=str(FRONTEND / 'templates'))
    app.config.update(
        DATA_DIR=os.environ.get('ROADSENSE_DATA_DIR', str(DEFAULT_DATA)),
        DEVICE_TOKEN=os.environ.get('DEVICE_TOKEN', ''),
        CONTROL_TOKEN=os.environ.get('CONTROL_TOKEN', ''),
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    )
    if config:
        app.config.update(config)
    app.register_blueprint(amap_blueprint)
    store = RecordStore(app.config['DATA_DIR'])
    location = PositionManager()
    lock = threading.RLock()
    frame_condition = threading.Condition(lock)
    state = {
        'command':'STOP', 'session_id':'', 'last_heartbeat':0.0, 'frame':None,
        'pi_url':'', 'pi_url_updated_at':0.0, 'phone_fix':None, 'last_uploaded_image':None, 'device_kind':'physical',
        'last_frame_at':0.0, 'frame_count':0, 'camera_signal':'unknown',
        'video_fps':0.0,
        'frame_brightness':None, 'camera_index':None, 'direct_video_url':'', 'preview_render_fps':0.0, 'camera_capture_fps':0.0,
    }
    app.extensions['roadsense'] = {'store':store, 'location':location, 'state':state}

    def err(message, status=400):
        return jsonify({'status':'error', 'message':message}), status

    def require_token(key):
        token = app.config.get(key, '')
        if token and request.headers.get('X-RoadSense-Token') != token:
            return err('unauthorized', 401)
        return None

    def online():
        return (time.monotonic() - state['last_heartbeat']) < 15.0

    def tick():
        with lock:
            state['last_heartbeat'] = time.monotonic()

    def submit_coords(data, default_source='hardware', origin='edge'):
        source = data.get('location_source', default_source)
        if source not in ALLOWED_SOURCE:
            return False
        # A forwarded phone fix is NOT a new phone fix. The device heartbeat must
        # never extend the 30-second freshness of a disconnected phone.
        if source == 'phone' and origin == 'edge':
            return False
        return location.submit(data.get('latitude'), data.get('longitude'), source, origin=origin)

    @app.route('/')
    def home(): return render_template('home.html')

    @app.route('/detection')
    def detection(): return render_template('detection.html')

    @app.route('/severe')
    def severe(): return render_template('severe.html')

    @app.route('/map-demo')
    def map_demo(): return render_template('amap_demo.html')

    @app.route('/gps')
    def gps(): return render_template('gps.html')

    @app.route('/location')
    def location_page(): return render_template('location_page.html')

    @app.route('/phone')
    def phone_sender(): return render_template('phone_sender.html')

    def local_ipv4():
        """Prefer Wi-Fi/Ethernet IPv4 over VPN, Docker, bridge and loopback."""
        import subprocess
        candidates=[]
        try:
            lines=subprocess.check_output(['ip','-o','-4','addr','show'],stderr=subprocess.DEVNULL,text=True,timeout=2).splitlines()
            for line in lines:
                parts=line.split()
                if len(parts)<4 or parts[2]!='inet': continue
                device=parts[1].split('@')[0]
                address=parts[3].split('/')[0]
                if any(device.startswith(x) for x in ('lo','docker','br-','veth','tun','tap','wg','tailscale','zt','virbr')):continue
                try:
                    ipv4=ipaddress.ip_address(address)
                    if not ipv4.is_private or ipv4.is_loopback: continue
                except ValueError: continue
                score=(20 if device.startswith(('wlan','wlp','wifi','eth','enp','eno','ens')) else 2)
                score+=(12 if address.startswith('192.168.') else 8 if address.startswith('172.') else 3)
                candidates.append((score,address))
        except (OSError, subprocess.SubprocessError): pass
        if candidates:return max(candidates)[1]
        try:
            for addr in socket.gethostbyname_ex(socket.gethostname())[2]:
                if addr.startswith(('192.168.','172.')) and not addr.startswith('172.17.'):return addr
        except OSError: pass
        return None

    def mobile_links():
        """Exactly one phone entry URL: Pi HTTPS if active, otherwise explicitly configured HTTPS.

        Never recommend loopback, VPN-only addresses or HTTP for mobile geolocation.
        """
        with lock:
            pi_url=state['pi_url'] if (online() and time.monotonic()-state['pi_url_updated_at']<25) else ''
        configured=(os.environ.get('PHONE_PUBLIC_URL','') or '').strip()
        lan=(os.environ.get('LAN_HOST','') or '').strip()
        # Prefer Pi: its HTTPS 8080 route mirrors the original core.py design.
        if pi_url:
            parsed=urlsplit(pi_url)
            if parsed.scheme=='https' and parsed.hostname and parsed.port==8080:
                return dict(entry_url=pi_url.rstrip('/') if parsed.path.endswith('/phone') else pi_url.rstrip('/')+'/phone',entry_kind='pi',ready=True,
                    hint='手机需与树莓派处于可互通网络，首次访问需信任树莓派 HTTPS 证书；由树莓派接收并转发坐标。')
        if configured:
            parsed=urlsplit(configured)
            if parsed.scheme=='https' and parsed.hostname and parsed.path.rstrip('/').endswith('/phone'):
                return dict(entry_url=configured,entry_kind='https',ready=True,
                    hint='手机通过 HTTPS 访问同一个 /phone 网址；证书需要被手机系统信任。')
            return dict(entry_url=None,entry_kind='unconfigured',ready=False,
                hint='PHONE_PUBLIC_URL 必须是可访问、受信任的 https://.../phone 地址。')
        if request.is_secure:
            host=lan or local_ipv4()
            # Use an explicit network interface IP rather than guessed VPN source.
            port=(request.host.rsplit(':',1)[-1] if ':' in request.host else str(os.environ.get('PORT','5000')))
            if host:
                return dict(entry_url=f'https://{host}:{port}/phone',entry_kind='local_https',ready=True,
                    hint='手机和电脑需网络互通；如果使用本地自签名证书，还必须在手机系统里信任证书。')
        return dict(entry_url=None,entry_kind='unconfigured',ready=False,
            hint='尚无可用的 HTTPS 手机入口：请启动树莓派定位服务，或配置 Flask HTTPS 与 PHONE_PUBLIC_URL。普通 HTTP 不支持手机浏览器 GPS 权限。')

    @app.route('/api/mobile_access')
    def mobile_access():
        return jsonify(mobile_links())

    @app.route('/api/mobile_qr')
    def mobile_qr():
        """QR generated server-side; URL is EXACTLY the same as /api/mobile_access."""
        from io import BytesIO
        target=mobile_links()['entry_url']
        if not target:return err('还没有 HTTPS 手机入口，无法生成可正常定位的二维码',409)
        try:
            import qrcode
            from qrcode.constants import ERROR_CORRECT_M
        except ImportError:
            return err('缺少二维码库：pip install "qrcode[pil]"',503)
        qr=qrcode.QRCode(error_correction=ERROR_CORRECT_M,box_size=10,border=3)
        qr.add_data(target);qr.make(fit=True)
        img=qr.make_image(fill_color='#122e4a',back_color='#ffffff')
        buf=BytesIO();img.save(buf,format='PNG')
        return Response(buf.getvalue(),mimetype='image/png',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.route('/api/health')
    def health():
        return jsonify({'status':'ok','service':'RoadSense Cloud','uptime_seconds':round(time.time()-APP_START,1)})

    @app.route('/api/status')
    def device_status():
        with lock:
            return jsonify({'online':online(),'command':state['command'], 'session_id':state['session_id'], 'location_source':location.snapshot()['source'], 'simulator':state['device_kind']=='simulator'})

    @app.route('/api/control', methods=['POST'])
    def control_device():
        blocked = require_token('CONTROL_TOKEN')
        if blocked: return blocked
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('command') not in ALLOWED_COMMANDS:
            return err('command must be START, PAUSE or STOP')
        cmd = data['command']
        if cmd == 'START' and location.snapshot()['source'] == 'none':
            return err('没有有效经纬度。请检查 GPS 模块，或在手机打开 /phone 开始上报定位。', 409)
        with lock:
            if cmd == 'START':
                if state['command'] == 'STOP' or not state['session_id']:
                    state['session_id'] = 'Task_' + datetime.now().strftime('%Y%m%d_%H%M%S')
                state['command'] = 'RUNNING'
            elif cmd == 'PAUSE':
                state['command'] = 'PAUSED'
            else:
                state['command'] = 'STOP'
                state['session_id'] = ''
            return jsonify({'status':'success', 'command':state['command'], 'session_id':state['session_id']})

    @app.route('/api/command')
    def get_command():
        blocked = require_token('DEVICE_TOKEN')
        if blocked: return blocked
        with lock:
            return jsonify({'command':state['command'], 'session_id':state['session_id']})

    @app.route('/update_stream', methods=['POST'])
    def update_stream():
        blocked = require_token('DEVICE_TOKEN')
        if blocked: return blocked
        tick()
        with lock: state['device_kind'] = 'simulator' if request.form.get('device_kind') == 'simulator' else 'physical'
        submit_coords(request.form)
        camera_signal = str(request.form.get('camera_signal', '')).strip()[:32]
        raw_direct_url = request.form.get('direct_video_url','').strip()
        try:
            preview_fps = float(request.form.get('preview_render_fps','0'))
            if not 0 <= preview_fps <= 60: preview_fps = 0.0
        except (ValueError,TypeError):
            preview_fps = 0.0
        if 'preview_render_fps' in request.form:
            with lock: state['preview_render_fps'] = round(preview_fps, 1)
        if 'camera_capture_fps' in request.form:
            try:
                cap_fps=float(request.form['camera_capture_fps'])
                if not 0 <= cap_fps <= 120: cap_fps=0.0
            except (ValueError,TypeError): cap_fps=0.0
            with lock: state['camera_capture_fps'] = round(cap_fps,1)
        # Only accept the known direct-preview path on a private/local IP.
        # Do not expose a general URL proxy or redirect through this field.
        if raw_direct_url:
            parsed = urlsplit(raw_direct_url)
            try:
                addr = ipaddress.ip_address(parsed.hostname or '')
                direct_url = raw_direct_url if (parsed.scheme == 'http' and
                    addr.is_private and parsed.port == 8091 and
                    parsed.path == '/live.mjpg' and not parsed.username and not parsed.password) else ''
            except (ValueError,TypeError):
                direct_url = ''
            with lock: state['direct_video_url'] = direct_url
        camera_index_raw = request.form.get('camera_index', '').strip()
        with lock:
            if camera_signal:
                state['camera_signal'] = camera_signal
            if camera_index_raw.isdigit():
                state['camera_index'] = int(camera_index_raw)
            elif camera_signal == 'missing':
                state['camera_index'] = None
                state['frame'] = None
                state['last_frame_at'] = 0.0
                state['frame_brightness'] = None
        upload = request.files.get('frame')
        if upload:
            frame = upload.read()
            if len(frame) > 0 and (frame.startswith(b'\xff\xd8') or frame.startswith(b'\x89PNG')):
                with lock:
                    previous_at=state['last_frame_at']
                    state['frame'] = frame
                    state['last_frame_at'] = time.monotonic()
                    state['frame_count'] += 1
                    frame_condition.notify_all()
                    if previous_at and 0 < state['last_frame_at']-previous_at < 2:
                        fps=1/(state['last_frame_at']-previous_at)
                        state['video_fps']=round(.8*state['video_fps']+.2*fps if state['video_fps'] else fps,1)
                    else:
                        state['video_fps']=0.0
                    state['camera_signal'] = str(request.form.get('camera_signal', 'unknown'))[:32]
                    try:
                        value = request.form.get('frame_brightness')
                        state['frame_brightness'] = round(float(value), 1) if value is not None else None
                    except (TypeError, ValueError):
                        state['frame_brightness'] = None
        return 'OK', 200

    @app.route('/upload', methods=['POST'])
    def upload():
        blocked = require_token('DEVICE_TOKEN')
        if blocked: return blocked
        tick()
        try:
            severity = int(request.form.get('severity', '1'))
        except (ValueError, TypeError):
            return err('invalid severity')
        if severity not in (1, 2, 3): return err('severity must be 1, 2, or 3')
        latlon = valid_coordinates(request.form.get('latitude'), request.form.get('longitude'))
        if latlon is None and (str(request.form.get('latitude', '')).strip() or str(request.form.get('longitude', '')).strip()):
            return err('invalid coordinates')
        lat, lon = latlon if latlon else (None, None)
        submit_coords(request.form)
        session = str(request.form.get('session_id','未分类任务')).strip()[:100] or '未分类任务'
        level = {1:'轻微',2:'中度',3:'严重'}[severity]
        record = {
            'id':str(uuid.uuid4()),
            'time':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'lon':lon, 'lat':lat,'level':level, 'image':None,
            'session_id':session,
            'location_source':request.form.get('location_source','hardware'),
        }
        file = request.files.get('image')
        if severity >= 2 and file:
            contents = file.read()
            if contents.startswith(b'\xff\xd8') and len(contents) <= 8 * 1024 * 1024:
                name = f'{level}_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:8]}.jpg'
                (store.image_dir / name).write_bytes(contents)
                record['image'] = name
                with lock: state['last_uploaded_image'] = name
            else:
                return err('image must be JPEG and under 8 MB')
        store.add(record)
        return jsonify({'status':'success','record':record}), 200

    @app.route('/api/records')
    def records():
        return jsonify(sorted(store.all(), key=lambda x:str(x.get('time','')),reverse=True))

    @app.route('/api/overview')
    def overview():
        rows = store.all()
        counts = {level:sum(r.get('level') == level for r in rows) for level in ('轻微','中度','严重')}
        return jsonify({'total':len(rows),'counts':counts,'sessions':len({r.get('session_id') for r in rows if r.get('session_id') and r.get('session_id')!='未分类任务'}),'latest_time':max((r.get('time','') for r in rows),default='')})

    @app.route('/images/<path:filename>')
    def images(filename):
        return send_from_directory(store.image_dir, filename)

    @app.route('/api/receive_location', methods=['POST'])
    def receive_location():
        data = request.get_json(silent=True) or {}
        values = valid_coordinates(data.get('latitude'), data.get('longitude'))
        if not values: return err('invalid latitude/longitude')
        lat, lon = values
        accuracy = data.get('accuracy')
        try:
            accuracy = float(accuracy) if accuracy is not None else None
        except (TypeError, ValueError):
            return err('invalid accuracy')
        if accuracy is not None:
            limit = float(os.environ.get('MAX_PHONE_ACCURACY_M', '500'))
            if not (0 <= accuracy <= limit):
                return err('手机位置数据尚未稳定，请开启精确定位或到室外重新定位。', 422)
        location.submit(lat, lon, 'phone', origin='mobile_node')
        with lock:
            state['phone_fix'] = {'latitude':lat,'longitude':lon,'accuracy':accuracy,'updated_at':time.time()}
        winner = location.snapshot()['source']
        return jsonify({'status':'success', 'active_source':winner,'used_for_position':winner=='phone', 'received_at':time.time()})

    @app.route('/api/phone_location')
    def phone_location_for_pi():
        blocked = require_token('DEVICE_TOKEN')
        if blocked: return blocked
        with lock: fix = dict(state['phone_fix']) if state['phone_fix'] else None
        available = bool(fix and time.time()-fix['updated_at'] <= PHONE_TTL)
        return jsonify({'available':available,**(fix if available else {})})

    @app.route('/api/location_status')
    def location_status():
        result = location.snapshot()
        result['online'] = online()
        return jsonify(result)

    @app.route('/api/report_pi_url', methods=['POST'])
    def report_pi_url():
        blocked = require_token('DEVICE_TOKEN')
        if blocked: return blocked
        data = request.get_json(silent=True) or {}
        url = str(data.get('url',''))[:256]
        parsed = urlsplit(url)
        if parsed.scheme not in ('https','http') or not parsed.hostname:
            return err('invalid url')
        with lock:
            state['pi_url'] = url
            state['pi_url_updated_at'] = time.monotonic()
        return jsonify({'status':'success'})

    @app.route('/api/get_pi_url')
    def get_pi_url():
        return jsonify({'pi_url':state['pi_url'] if online() and time.monotonic()-state['pi_url_updated_at']<25 and state['pi_url'] else '等待树莓派开机...'})

    @app.route('/api/video_status')
    def video_status():
        with lock:
            age = time.monotonic() - state['last_frame_at'] if state['last_frame_at'] else None
            cloud_available = age is not None and age < 6
            direct_url = state['direct_video_url'] if online() else ''
            return jsonify({'available':cloud_available or bool(direct_url),
                            'cloud_available':cloud_available,
                            'direct_video_url':direct_url,
                            'age_seconds':round(age,2) if age is not None else None,
                            'frames_received':state['frame_count'],
                            'video_fps':state['video_fps'], 'preview_render_fps':state['preview_render_fps'], 'camera_capture_fps':state['camera_capture_fps'], 'device_online':online(),
                            'device_kind':state['device_kind'],
                            'camera_signal':state['camera_signal'],
                            'camera_index':state['camera_index'],
                            'frame_brightness':state['frame_brightness']})

    @app.route('/api/latest_frame')
    def latest_frame():
        with lock:
            age = time.monotonic()-state['last_frame_at'] if state['last_frame_at'] else float('inf')
            data = state['frame'] if age < 6 else None
        if not data: return Response(status=204, headers={'Cache-Control':'no-store'})
        kind = 'image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
        return Response(data, mimetype=kind, headers={'Cache-Control':'no-store'})

    @app.route('/video_feed')
    def video_feed():
        """Condition-notified MJPEG stream: no periodic sleep or frame backlog."""
        def frames():
            last_seq=-1
            while True:
                with frame_condition:
                    frame_condition.wait_for(lambda:state['frame_count'] != last_seq,
                                             timeout=2.0)
                    age=time.monotonic()-state['last_frame_at'] if state['last_frame_at'] else float('inf')
                    seq=state['frame_count']
                    data=state['frame'] if age < 5 else None
                if data and seq != last_seq:
                    mime=b'image/png' if data.startswith(b'\x89PNG') else b'image/jpeg'
                    yield (b'--frame\r\nContent-Type: '+mime+
                           b'\r\nContent-Length: '+str(len(data)).encode()+
                           b'\r\nCache-Control: no-store\r\n\r\n'+data+b'\r\n')
                last_seq=seq
        return Response(frames(),mimetype='multipart/x-mixed-replace; boundary=frame',
                        headers={'Cache-Control':'no-store, no-cache, must-revalidate',
                                 'Pragma':'no-cache','X-Accel-Buffering':'no'})

    @app.route('/api/map_position')
    def map_position():
        """Return WGS84 GPS readings; only the map renderer converts to GCJ-02."""
        return jsonify(location.snapshot())

    @app.route('/map_feed')
    def deprecated_map_feed():
        """Compatibility endpoint retained for clients that can migrate to /api/map_position."""
        return jsonify({'status':'deprecated', 'message':'地图已升级为高德 JS API 2.0；请使用 /api/map_position 获取 WGS84 坐标。'}),410

    return app


app = create_app()

if __name__ == '__main__':
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT','5000'))
    cert, key = os.environ.get('SSL_CERT_FILE'), os.environ.get('SSL_KEY_FILE')
    tls = (cert, key) if cert and key else None
    print(f'电脑端：http{"s" if tls else ""}://127.0.0.1:{port}/location  |  手机节点必须使用有效 HTTPS 或树莓派 8080 HTTPS')
    app.run(host=host,port=port,threaded=True,debug=False,ssl_context=tls)
