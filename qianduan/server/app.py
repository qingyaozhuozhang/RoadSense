"""Flask server for device control, location, video streams and inspection records."""
from __future__ import annotations

import os
import base64
import secrets
import socket
import ipaddress
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import requests
from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for, stream_with_context)

try:
    from .location_state import PositionManager, PHONE_TTL, valid_coordinates
    from .storage import RecordStore
    from .amap_proxy import amap_blueprint
    from .auth import AccountStore, get_session_secret
    from .phone_inference import PhoneInference, InferenceError
    from .multidevice import DeviceRegistry, RuntimePool, RuntimeProxy, StateProxy
    from .admin_seeds import ADMINS
    from .device_env_export import render_houduan_env
    from .record_naming import archive_name
except ImportError:  # Allow direct execution with python server/app.py
    from location_state import PositionManager, PHONE_TTL, valid_coordinates
    from storage import RecordStore
    from amap_proxy import amap_blueprint
    from auth import AccountStore, get_session_secret
    from phone_inference import PhoneInference, InferenceError
    from multidevice import DeviceRegistry, RuntimePool, RuntimeProxy, StateProxy
    from admin_seeds import ADMINS
    from device_env_export import render_houduan_env
    from record_naming import archive_name

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
               'AMAP_JS_KEY', 'AMAP_JS_SECRET', 'ROADSENSE_SECRET_KEY',
               'REGISTRATION_ENABLED', 'PHONE_YOLO_MODEL', 'SESSION_COOKIE_SECURE',
               'BOOTSTRAP_DEVICE_ID', 'ALLOW_LEGACY_DEVICE_AUTH',
               'ROADSENSE_TRUST_PROXY', 'DEVICE_CLOUD_BASE_URL'):
        os.environ.setdefault(key,value.strip().strip('\"').strip("'"))
APP_START = time.time()


def create_app(config=None):
    app = Flask(__name__, static_folder=str(FRONTEND / 'static'), template_folder=str(FRONTEND / 'templates'))
    # Enable only when Gunicorn listens on 127.0.0.1 behind our trusted Nginx.
    if os.environ.get('ROADSENSE_TRUST_PROXY', '0').lower() in ('1', 'true', 'yes', 'on'):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        DATA_DIR=os.environ.get('ROADSENSE_DATA_DIR', str(DEFAULT_DATA)),
        DEVICE_TOKEN=os.environ.get('DEVICE_TOKEN', ''),
        CONTROL_TOKEN=os.environ.get('CONTROL_TOKEN', ''),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        AUTH_ENABLED=True,
        REGISTRATION_ENABLED=os.environ.get('REGISTRATION_ENABLED', '1').lower() not in ('0','false','no'),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        # HTTP demo mode by default. Set SESSION_COOKIE_SECURE=1 once the
        # public login endpoint is HTTPS; never send credentials over HTTP
        # on an untrusted network.
        SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '0').lower() in ('1', 'true', 'yes', 'on'),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if config:
        app.config.update(config)
    # Keep old edge/HTTP compatibility tests unchanged; security tests opt back in.
    if app.config['TESTING'] and (not config or 'AUTH_ENABLED' not in config):
        app.config['AUTH_ENABLED'] = False
    app.secret_key = app.config.get('SECRET_KEY') or get_session_secret(app.config['DATA_DIR'])
    app.register_blueprint(amap_blueprint)
    store = RecordStore(app.config['DATA_DIR'])
    accounts = AccountStore(app.config['DATA_DIR'])
    photo_engine = PhoneInference(app.config.get('PHONE_YOLO_MODEL'))
    # One device gets one independent video frame buffer, command, map, and FPS meter.
    # Run ONE Gunicorn worker with multiple threads: runtime buffers are in memory.
    registry = DeviceRegistry(app.config['DATA_DIR'])
    if not app.config.get('SKIP_ADMIN_BOOTSTRAP'):
        for username, password_hash in ADMINS:
            accounts.seed_admin_hash(username, password_hash)
    default_device_id = os.environ.get('BOOTSTRAP_DEVICE_ID','pi-main')
    initial_admin = accounts.find_by_username(ADMINS[0][0])
    if initial_admin and app.config.get('DEVICE_TOKEN'):
        registry.bootstrap(default_device_id, app.config['DEVICE_TOKEN'], initial_admin['id'])
    pool = RuntimePool()
    pool._items[default_device_id] = pool.default
    state = StateProxy(pool)
    location = RuntimeProxy(pool, 'location')
    lock = RuntimeProxy(pool, 'lock')
    frame_condition = RuntimeProxy(pool, 'condition')
    received_rate = RuntimeProxy(pool, 'received_rate')
    app.extensions['roadsense'] = {'store':store, 'location':location, 'state':state,
                                   'accounts':accounts, 'devices':registry, 'runtime_pool':pool,
                                   'photo_engine':photo_engine}

    access_attempts = {}
    attempts_lock = threading.Lock()
    mobile_location_lock = threading.Lock()
    mobile_locations = {}  # user_id -> latest phone GPS; independent of any Pi device
    pending_photos = {}
    pending_lock = threading.Lock()
    preview_ttl = 600  # Preview images live only in memory until explicitly archived.

    def too_many_attempts():
        # Small single-process throttle; production Nginx can add IP rate limiting.
        remote_ip = request.remote_addr or 'unknown'
        now = time.monotonic()
        with attempts_lock:
            history = [t for t in access_attempts.get(remote_ip, []) if now-t < 600]
            if len(history) >= 15:
                access_attempts[remote_ip] = history
                return True
            history.append(now)
            access_attempts[remote_ip] = history
        return False

    def current_user():
        user_id = session.get('user_id')
        return accounts.find_user(user_id) if isinstance(user_id, int) else None

    def mobile_position_allowed(user):
        """An owned Pi grants phone access; unassigned ordinary users require admin approval."""
        return bool(user and (user['role'] == 'admin' or registry.owned(user) or
                              accounts.mobile_location_granted(user['id'])))

    def require_mobile_position(user):
        if not app.config['AUTH_ENABLED']:
            return None
        if not user:
            return err('请先登录', 401)
        if not mobile_position_allowed(user):
            return err('当前账号暂无定位权限，请联系管理员开通', 403)
        return None

    def csrf_token():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_urlsafe(32)
        return session['csrf_token']

    app.context_processor(lambda: {'current_user':current_user(), 'csrf_token':csrf_token(),
                                    'registration_enabled':app.config['REGISTRATION_ENABLED'],
                                    'tab_activated':getattr(g, 'road_tab_activated', False)})

    def safe_next(value):
        value = str(value or '/').strip()
        return value if value.startswith('/') and not value.startswith('//') and '\\' not in value else '/'

    edge_endpoints = {'update_stream', 'upload', 'get_command', 'phone_location_for_pi',
                      'report_pi_url', 'phone_photo_inspect_from_edge',
                      'phone_photo_preview_from_edge', 'phone_photo_archive_from_edge',
                      'phone_photo_discard_from_edge'}
    public_endpoints = {'static', 'health', 'login_page', 'register_page', 'auth_captcha', 'forgot_password'}

    @app.before_request
    def authorize_browser_request():
        ep = request.endpoint or ''
        # Every edge request must prove its OWN device identity (no shared global
        # authentication that lets device A impersonate B).
        if ep in edge_endpoints:
            device_id = request.headers.get('X-RoadSense-Device-ID','').strip()
            token = request.headers.get('X-RoadSense-Token','')
            device = registry.authenticate(device_id, token)
            if device is None:
                if app.config['TESTING'] and not app.config['AUTH_ENABLED']:
                    device_id = device_id or default_device_id
                    device = registry.get(device_id) or {'device_id':device_id, 'owner_id':None}
                else:
                    return jsonify({'status':'error','message':'设备身份无效：检查 DEVICE_ID / DEVICE_TOKEN'}), 401
            g.edge_device = device
            g.device_id = device_id
            g.rs_runtime = pool.get(device_id)
            return None
        if ep in public_endpoints:
            return None
        user = current_user()
        if app.config['AUTH_ENABLED'] and not user:
            if request.path.startswith(('/api/', '/images/', '/_AMapService/')) or request.path == '/video_feed':
                return jsonify({'status':'error','message':'请先登录 RoadSense'}), 401
            return redirect(url_for('login_page', next=safe_next(request.full_path.rstrip('?'))))
        if user:
            allowed = registry.visible(user)
            wanted = (request.args.get('device_id') or request.form.get('device_id') or '').strip()
            if not wanted and ep in ('control_device','receive_location') and request.is_json and request.method in ('POST','PUT','PATCH','DELETE'):
                wanted = str((request.get_json(silent=True) or {}).get('device_id','')).strip()
            ids = {d['device_id'] for d in allowed}
            if wanted and wanted not in ids:
                return jsonify({'status':'error','message':'无权访问该设备'}), 403
            independent_mobile = (ep in ('receive_location', 'phone_photo_preview_for_browser', 'phone_photo_archive_for_browser', 'phone_photo_discard_for_browser', 'phone_photo_inspect') and request.headers.get('X-RoadSense-Mobile') == 'android' and not wanted)
            g.device_id = None if independent_mobile else (wanted or (allowed[0]['device_id'] if allowed else None))
            g.rs_runtime = pool.get(g.device_id)
            g.visible_devices = allowed
        elif app.config['TESTING'] and not app.config['AUTH_ENABLED']:
            g.device_id = request.args.get('device_id') or default_device_id
            g.rs_runtime = pool.get(g.device_id)
        # Protected requests cannot see another user's device via query parameter.
        ticket = request.args.get('rs_tab_ticket', '')
        expected = session.get('rs_tab_ticket', '')
        g.road_tab_activated = bool(ticket and expected and secrets.compare_digest(ticket, expected))
        if g.road_tab_activated:
            session.pop('rs_tab_ticket', None)
        if request.method in ('POST','PUT','PATCH','DELETE') and app.config['AUTH_ENABLED']:
            # The native Android foreground service has an authenticated WebView
            # session cookie; its special header avoids exposing CSRF to it.
            native_location = (ep == 'receive_location' and
                               request.headers.get('X-RoadSense-Mobile') == 'android' and user and
                               not request.headers.get('Origin'))
            if not native_location:
                provided = request.form.get('csrf_token', '') or request.headers.get('X-CSRF-Token', '')
                if not secrets.compare_digest(provided, csrf_token()):
                    return jsonify({'status':'error','message':'CSRF 校验失败，请刷新重试'}), 403
        return None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        if request.endpoint not in ('static',):
            response.headers.setdefault('Cache-Control', 'no-store')
        return response

    @app.route('/api/auth/captcha')
    def auth_captcha():
        challenge_id, picture = accounts.issue_captcha()
        session['captcha_id'] = challenge_id
        return Response(picture, mimetype='image/png', headers={'Cache-Control':'no-store'})

    def validate_auth_form():
        if not secrets.compare_digest(request.form.get('csrf_token',''), csrf_token()):
            return '页面验证已失效，请刷新后重试'
        if too_many_attempts():
            return '操作过于频繁，请十分钟后再试'
        challenge_id = session.pop('captcha_id', None)
        if not accounts.consume_captcha(challenge_id, request.form.get('captcha','')):
            return '验证码错误或已过期，请点击图片刷新'
        return None

    @app.route('/login', methods=['GET','POST'])
    def login_page():
        destination = safe_next(request.values.get('next','/'))
        if request.method == 'GET':
            if request.args.get('fresh') == '1':
                session.clear()
            if current_user(): return redirect(destination)
            return render_template('auth.html', mode='login', next_url=destination)
        failure = validate_auth_form()
        if not failure:
            user = accounts.authenticate(request.form.get('username'), request.form.get('password'))
            if user:
                session.clear()
                session['user_id'] = user['id']
                session.permanent = False
                session['rs_tab_ticket'] = secrets.token_urlsafe(24)
                flash('登录成功，欢迎回到 RoadSense 工作台！', 'success')
                separator = '&' if '?' in destination else '?'
                return redirect(destination + separator + 'rs_tab_ticket=' + session['rs_tab_ticket'])
            failure = '账号或密码错误'
        flash(failure, 'error')
        return redirect(url_for('login_page', next=destination))

    @app.route('/register', methods=['GET','POST'])
    def register_page():
        destination = safe_next(request.values.get('next','/'))
        if not app.config['REGISTRATION_ENABLED']:
            abort(404)
        if request.method == 'GET':
            if current_user(): return redirect('/')
            return render_template('auth.html', mode='register', next_url=destination)
        failure = validate_auth_form()
        if not failure:
            if request.form.get('password') != request.form.get('password_confirm'):
                failure = '两次输入的密码不一致'
            else:
                user, failure = accounts.register(request.form.get('username'), request.form.get('password'))
            if failure:
                user = None
            if user:
                # Registration and authentication are separate actions: let the
                # new account owner sign in explicitly on the login screen.
                flash('注册成功！请使用新账号和密码登录。', 'success')
                return redirect(url_for('login_page', next=destination))
        flash(failure, 'error')
        return redirect(url_for('register_page', next=destination))

    @app.route('/forgot-password')
    def forgot_password():
        return render_template('password_help.html')

    @app.route('/logout', methods=['POST'])
    def logout():
        session.clear()
        return redirect('/login')

    @app.route('/api/auth/me')
    def auth_me():
        return jsonify({'user':current_user(), 'csrf_token':csrf_token()})

    @app.route('/account/security', methods=['GET', 'POST'])
    def account_security():
        user = current_user()
        if not user: return redirect('/login')
        if request.method == 'POST':
            if request.form.get('new_password') != request.form.get('new_password_confirm'):
                flash('两次输入的新密码不一致', 'error')
                return redirect('/account/security')
            ok, failure = accounts.change_password(user['id'], request.form.get('current_password'),
                                                   request.form.get('new_password'))
            if ok:
                store.audit(user, 'password_changed', user['username'])
                flash('密码修改成功，请妥善保管新密码。', 'success')
            else: flash(failure, 'error')
            return redirect('/account/security')
        return render_template('account_security.html', active_page='security')

    @app.route('/api/auth/change-password', methods=['POST'])
    def change_password():
        user = current_user()
        if not user: return jsonify({'status':'error','message':'请先登录'}), 401
        data = request.get_json(silent=True) or {}
        success, msg = accounts.change_password(user['id'], data.get('current_password'), data.get('new_password'))
        if not success:return jsonify({'status':'error','message':msg}), 400
        store.audit(user,'password_changed',user['username'])
        return jsonify({'status':'success','message':'密码修改成功'})

    def err(message, status=400):
        return jsonify({'status':'error', 'message':message}), status

    def require_token(key):
        token = app.config.get(key, '')
        if key == 'DEVICE_TOKEN':
            return None if getattr(g, 'edge_device', None) is not None else err('unauthorized', 401)
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

    @app.route('/gps')
    def gps(): return render_template('gps.html')

    @app.route('/location')
    def location_page(): return render_template('location_page.html')

    @app.route('/phone')
    def phone_sender(): return render_template('phone_sender.html')

    @app.route('/app/ready')
    def android_app_ready():
        """Post-login rendezvous. App detects this URL and switches to native UI."""
        return render_template('android_ready.html')

    @app.route('/app/map')
    def android_app_map():
        """Map canvas only: Android's surrounding controls remain fully native."""
        return render_template('android_map.html')

    @app.route('/phone/inspect')
    def phone_inspect():
        return render_template('phone_inspect.html')

    @app.route('/api/phone/inspect', methods=['POST'])
    def phone_photo_inspect():
        """Upload a phone photograph, run cloud ONNX detection, archive both images."""
        current = current_user()
        blocked = require_mobile_position(current)
        if blocked is not None: return blocked
        return process_phone_photo(current['id'] if current else None)

    @app.route('/api/edge/phone/inspect', methods=['POST'])
    def phone_photo_inspect_from_edge():
        """Token-authenticated HTTPS-edge to HTTP-cloud relay; never requires a browser cookie."""
        # Unlike legacy upload, a missing device token must not expose an
        # expensive public YOLO inference endpoint.
        if not app.config.get('DEVICE_TOKEN'):
            return err('服务器未配置 DEVICE_TOKEN，无法启用手机照片中转', 503)
        blocked = require_token('DEVICE_TOKEN')
        if blocked is not None:
            return blocked
        return process_phone_photo(getattr(g,'edge_device',{}).get('owner_id'), include_preview_data=True)

    @app.route('/api/edge/phone/preview', methods=['POST'])
    def phone_photo_preview_from_edge():
        """Recognize an image without creating a permanent archive record."""
        if not app.config.get('DEVICE_TOKEN'):
            return err('服务器未配置 DEVICE_TOKEN，无法使用手机识别', 503)
        blocked = require_token('DEVICE_TOKEN')
        if blocked is not None:
            return blocked
        return preview_phone_photo(getattr(g,'edge_device',{}).get('owner_id'))

    @app.route('/api/phone/preview', methods=['POST'])
    def phone_photo_preview_for_browser():
        current = current_user()
        if app.config['AUTH_ENABLED'] and not current:
            return err('请先登录后再上传识别照片', 401)
        return preview_phone_photo(current['id'] if current else None)

    def preview_phone_photo(owner_id):
        prepared = prepare_phone_photo()
        if prepared[2] is not None:
            return prepared[2]
        result, location_value, _ = prepared
        preview_id = secrets.token_urlsafe(32)
        with pending_lock:
            now = time.monotonic()
            for key in list(pending_photos):
                if now - pending_photos[key]['created_at'] > preview_ttl:
                    pending_photos.pop(key, None)
            if len(pending_photos) >= 10:
                oldest = min(pending_photos, key=lambda key: pending_photos[key]['created_at'])
                pending_photos.pop(oldest, None)
            pending_photos[preview_id] = {'created_at':now, 'result':result,
                                          'location':location_value, 'owner_id':owner_id, 'device_id':getattr(g,'device_id',None)}
        return jsonify({
            'status':'success', 'archived':False, 'preview_id':preview_id,
            'record':phone_photo_summary(result, location_value),
            'result_image_data_url':'data:image/jpeg;base64,' +
                                    base64.b64encode(result['result_jpeg']).decode('ascii'),
        })

    @app.route('/api/edge/phone/archive', methods=['POST'])
    def phone_photo_archive_from_edge():
        if not app.config.get('DEVICE_TOKEN'):
            return err('服务器未配置 DEVICE_TOKEN，无法保存手机记录', 503)
        blocked = require_token('DEVICE_TOKEN')
        if blocked is not None:
            return blocked
        return confirm_pending_phone_photo(getattr(g,'edge_device',{}).get('owner_id'))

    @app.route('/api/phone/archive', methods=['POST'])
    def phone_photo_archive_for_browser():
        current = current_user()
        blocked = require_mobile_position(current)
        if blocked is not None: return blocked
        return confirm_pending_phone_photo(current['id'] if current else None)

    def confirm_pending_phone_photo(owner_id):
        preview_id = str((request.get_json(silent=True) or {}).get('preview_id', ''))
        with pending_lock:
            preview = pending_photos.get(preview_id)
            if preview and preview['owner_id'] == owner_id and preview['device_id'] == getattr(g,'device_id',None):
                pending_photos.pop(preview_id, None)
            else:
                preview = None
        if preview is None or time.monotonic() - preview['created_at'] > preview_ttl:
            return err('临时识别结果已失效，请重新拍照识别', 410)
        return archive_phone_photo(preview['result'], preview['location'], owner_id,
                                   include_preview_data=False)

    @app.route('/api/edge/phone/discard', methods=['POST'])
    def phone_photo_discard_from_edge():
        if not app.config.get('DEVICE_TOKEN'):
            return err('服务器未配置 DEVICE_TOKEN', 503)
        blocked = require_token('DEVICE_TOKEN')
        if blocked is not None:
            return blocked
        return discard_pending_phone_photo(getattr(g,'edge_device',{}).get('owner_id'))

    @app.route('/api/phone/discard', methods=['POST'])
    def phone_photo_discard_for_browser():
        current = current_user()
        if app.config['AUTH_ENABLED'] and not current:
            return err('请先登录', 401)
        return discard_pending_phone_photo(current['id'] if current else None)

    def discard_pending_phone_photo(owner_id):
        preview_id = str((request.get_json(silent=True) or {}).get('preview_id', ''))
        with pending_lock:
            if preview_id in pending_photos and pending_photos[preview_id]['owner_id'] == owner_id and pending_photos[preview_id]['device_id'] == getattr(g,'device_id',None):
                pending_photos.pop(preview_id, None)
        return jsonify({'status':'success', 'archived':False})

    def phone_photo_summary(result, location_value):
        lat, lon = location_value if location_value else (None, None)
        return {'level':result['level'], 'lat':lat, 'lon':lon,
                'damage_ratio':result['damage_ratio'],
                'inference_ms':result['inference_ms'],
                'detection_count':len(result['detections']),
                'detections':result['detections']}

    def prepare_phone_photo():
        image = request.files.get('photo')
        if image is None:
            return None, None, err('请选择或拍摄一张道路照片')
        contents = image.read(8 * 1024 * 1024 + 1)
        if not contents or len(contents) > 8 * 1024 * 1024:
            return None, None, err('照片不能为空，且不能超过 8 MB', 413)
        try:
            analyzer = app.config.get('PHONE_INFER_FN') or photo_engine.analyze
            result = analyzer(contents)
        except ValueError as exc:
            return None, None, err(str(exc), 400)
        except (InferenceError, ImportError, OSError) as exc:
            app.logger.error('Phone photo inference unavailable: %s', exc)
            return None, None, err('云端 YOLO 模型暂不可用：' + str(exc), 503)
        except Exception:
            app.logger.exception('Phone photo inference failed')
            return None, None, err('图片推理失败，请检查云端日志、模型及 ONNX 依赖', 500)
        location_value = valid_coordinates(request.form.get('latitude'), request.form.get('longitude'))
        if (request.form.get('latitude') or request.form.get('longitude')) and location_value is None:
            return None, None, err('无效的图片定位坐标')
        return result, location_value, None

    def process_phone_photo(owner_id, include_preview_data=False):
        result, location_value, error = prepare_phone_photo()
        if error is not None:
            return error
        return archive_phone_photo(result, location_value, owner_id, include_preview_data)

    def archive_phone_photo(result, location_value, owner_id, include_preview_data=False):
        # A phone archive requires a CURRENT valid fix, not only a past screenshot.
        actor = current_user()
        if actor and app.config['AUTH_ENABLED']:
            blocked = require_mobile_position(actor)
            if blocked is not None: return blocked
            with mobile_location_lock:
                recent_fix = dict(mobile_locations.get(actor['id'], {}))
            valid_fix = valid_coordinates(recent_fix.get('latitude'), recent_fix.get('longitude'))
            if (not valid_fix or time.time() - recent_fix.get('updated_at', 0) > PHONE_TTL or
                    recent_fix.get('device_id') != getattr(g, 'device_id', None)):
                return err('请先取得有效的实时定位，再确认归档', 422)
            # If the preview was made before positioning started, use the live fix.
            if location_value is None:
                location_value = valid_fix
        # Never store a damage record without valid coordinates.
        if not location_value or valid_coordinates(*location_value) is None:
            return err('尚无有效定位，无法归档损伤记录', 422)
        lat, lon = location_value
        now = datetime.now()
        stamp = now.strftime('%Y%m%d_%H%M%S')
        identifier = uuid.uuid4().hex
        device = registry.get(getattr(g, 'device_id', None)) if getattr(g, 'device_id', None) else None
        actor = current_user()
        physical_owner_id = device['owner_id'] if device else owner_id
        uploader_id = actor['id'] if actor else physical_owner_id
        uploader = accounts.find_user(uploader_id) if uploader_id else None
        filename_stem = archive_name('phone_photo', (uploader or {}).get('username'), now, identifier[:12])
        original_name = f'{filename_stem}_original.jpg'
        annotated_name = f'{filename_stem}.jpg'
        (store.image_dir / original_name).write_bytes(result['original_jpeg'])
        (store.image_dir / annotated_name).write_bytes(result['result_jpeg'])
        record = {
            'id':identifier,
            'record_name':filename_stem,
            'time':now.strftime('%Y-%m-%d %H:%M:%S'),
            'lon':lon, 'lat':lat,
            'level':result['level'],
            'image':annotated_name,
            'original_image':original_name,
            'source':'phone_photo',
            'session_id':filename_stem,
            'location_source':'phone' if location_value else 'none',
            'owner_id':physical_owner_id,
            'device_id':getattr(g,'device_id',None),
            'uploaded_by_user_id':uploader_id,
            'uploaded_by_username': (uploader or {}).get('username'),
            'upload_identity_type':'device_owner_proxy' if getattr(g,'edge_device',None) else 'authenticated_session',
            'damage_ratio':result['damage_ratio'],
            'inference_ms':result['inference_ms'],
            'detection_count':len(result['detections']),
            'detections':result['detections'],
        }
        store.add(record)
        response = {'status':'success', 'archived':True, 'record':record,
                    'result_image_url':url_for('images', filename=annotated_name),
                    'original_image_url':url_for('images', filename=original_name)}
        if include_preview_data:
            # Pi page uses HTTPS; data: preview avoids blocked HTTP mixed content.
            response['result_image_data_url'] = 'data:image/jpeg;base64,' + base64.b64encode(result['result_jpeg']).decode('ascii')
        return jsonify(response)

    @app.route('/api/phone/records')
    def phone_photo_records():
        current = current_user()
        if not current and app.config['AUTH_ENABLED']:
            return err('请先登录', 401)
        allowed_ids = [d['device_id'] for d in registry.visible(current)] if current else None
        rows = store.all(allowed_ids)
        if current:
            if current['role'] == 'admin':
                rows.extend([r for r in store.all() if not r.get('device_id')])
            else:
                rows.extend(store.unbound_phone_records(current['id']))
        rows = [r for r in rows if r.get('source') == 'phone_photo' and
                (not app.config['AUTH_ENABLED'] or current['role']=='admin' or r.get('owner_id') == current['id'])]
        return jsonify(sorted(rows, key=lambda row:row['time'], reverse=True)[:50])

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
        # Legacy up/core.py serves its phone page at /, while RoadSense's edge
        # node reports /phone. Preserve the reported entry path for both.
        if pi_url:
            parsed=urlsplit(pi_url)
            if parsed.scheme=='https' and parsed.hostname and parsed.port==8080 and parsed.path.rstrip('/') in ('','/phone'):
                entry_url=pi_url.rstrip('/') + ('/' if not parsed.path.rstrip('/') else '')
                return dict(entry_url=entry_url,entry_kind='pi',ready=True,
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
                state['last_operator_id'] = (current_user() or {}).get('id')
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
                # Parallel HTTP uploads may finish out of order. Accept only newest
                # frame within the same edge process; reset on a new edge session.
                video_session = str(request.form.get('video_session','')).strip()[:72]
                try:
                    video_seq = int(request.form.get('video_seq','-1'))
                except (ValueError,TypeError):
                    video_seq = -1
                with lock:
                    if video_session and video_seq >= 0:
                        if video_session != state['video_stream_session']:
                            state['video_stream_session'] = video_session
                            state['video_frame_seq'] = -1
                            received_rate.reset()
                        if video_seq <= state['video_frame_seq']:
                            state['video_out_of_order_dropped'] += 1
                            return 'OK', 200
                        state['video_frame_seq'] = video_seq
                    state['frame'] = frame
                    state['last_frame_at'] = time.monotonic()
                    state['frame_count'] += 1
                    received_rate.accept(state['last_frame_at'])
                    state['video_fps'] = received_rate.rate(
                        state['last_frame_at'], state['camera_capture_fps'],
                        state['preview_render_fps'])
                    frame_condition.notify_all()
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
        if latlon is None:
            return err('尚无有效定位，无法归档损伤记录', 422)
        lat, lon = latlon
        submit_coords(request.form)
        session = str(request.form.get('session_id','未分类任务')).strip()[:100] or '未分类任务'
        level = {1:'轻微',2:'中度',3:'严重'}[severity]
        now = datetime.now()
        identifier = uuid.uuid4().hex
        device_owner = g.edge_device.get('owner_id')
        device_username = (accounts.find_user(device_owner) or {}).get('username') if device_owner else None
        record_name = archive_name('edge_camera', device_username, now, identifier[:12])
        record = {
            'id':identifier,
            'record_name':record_name,
            'time':now.strftime('%Y-%m-%d %H:%M:%S'),
            'lon':lon, 'lat':lat,'level':level, 'image':None,
            'session_id':session,
            'location_source':request.form.get('location_source','hardware'),
            'device_id':g.device_id,
            'owner_id':g.edge_device.get('owner_id'),
            'uploaded_by_user_id':g.edge_device.get('owner_id'),
            'uploaded_by_username':device_username,
            'upload_identity_type':'device_owner_proxy',
            'initiated_by_user_id':state['last_operator_id'],
        }
        file = request.files.get('image')
        if severity >= 2 and file:
            contents = file.read()
            if contents.startswith(b'\xff\xd8') and len(contents) <= 8 * 1024 * 1024:
                name = f'{record_name}.jpg'
                (store.image_dir / name).write_bytes(contents)
                record['image'] = name
                with lock: state['last_uploaded_image'] = name
            else:
                return err('image must be JPEG and under 8 MB')
        store.add(record)
        return jsonify({'status':'success','record':record}), 200

    def scoped_records():
        user = current_user()
        if not user and not app.config['AUTH_ENABLED']:
            return store.all()
        ids = [d['device_id'] for d in registry.visible(user)] if user else []
        requested = request.args.get('device_id')
        if requested:
            ids = [requested] if requested in ids else []
        rows = store.all(ids)
        # Mobile photos may belong directly to a user without any Pi assignment.
        if user and (not requested):
            if user['role'] == 'admin':
                rows.extend(r for r in store.all() if not r.get('device_id'))
            else:
                rows.extend(store.unbound_phone_records(user['id']))
        return sorted(rows, key=lambda row:row.get('time',''), reverse=True)

    @app.route('/api/records')
    def records():
        return jsonify(scoped_records())

    @app.route('/api/overview')
    def overview():
        rows = scoped_records()
        counts = {level:sum(r.get('level') == level for r in rows) for level in ('轻微','中度','严重')}
        return jsonify({'total':len(rows),'counts':counts,'sessions':len({r.get('session_id') for r in rows if r.get('session_id') and r.get('session_id')!='未分类任务'}),'latest_time':max((r.get('time','') for r in rows),default='')})

    @app.route('/images/<path:filename>')
    def images(filename):
        # Do not rely on the UI hiding URLs: prevent direct cross-account image access.
        record = store.by_image(filename)
        if not record:
            abort(404)
        user = current_user()
        if app.config['AUTH_ENABLED']:
            allowed = bool(user and (record.get('device_id') in
                {d['device_id'] for d in registry.visible(user)} or
                (not record.get('device_id') and record.get('owner_id') == user['id'])))
            if user and user['role'] == 'admin': allowed = True
            if not allowed: abort(403)
        return send_from_directory(store.image_dir, filename)

    @app.route('/api/receive_location', methods=['POST'])
    def receive_location():
        actor = current_user()
        blocked = require_mobile_position(actor)
        if blocked is not None: return blocked
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
        # Unassigned accounts are allowed only after explicit admin approval.
        with mobile_location_lock:
            if actor:
                mobile_locations[actor['id']] = {'latitude':lat,'longitude':lon,'accuracy':accuracy,
                     'updated_at':time.time(),'username':actor['username'],
                     'device_id':getattr(g,'device_id',None)}
        # Device positioning is updated ONLY when the user has selected an authorized Pi.
        if getattr(g,'device_id',None):
            location.submit(lat, lon, 'phone', origin='mobile_node')
            with lock:
                state['phone_fix'] = {'latitude':lat,'longitude':lon,'accuracy':accuracy,'updated_at':time.time(),
                                      'user_id':actor['id'] if actor else None}
            winner = location.snapshot()['source']
        else: winner = 'personal_phone'
        return jsonify({'status':'success','active_source':winner,
                        'used_for_position':winner in ('phone','personal_phone'),'received_at':time.time()})

    @app.route('/api/mobile/permission')
    def mobile_location_permission():
        user = current_user()
        if not user: return err('请先登录',401)
        return jsonify({'status':'success', 'allowed':mobile_position_allowed(user),
                        'message':'定位服务可用' if mobile_position_allowed(user) else '当前账号暂无定位权限，请联系管理员开通'})

    @app.route('/api/mobile/location')
    def mobile_location_for_account():
        user = current_user()
        if not user: return err('请先登录', 401)
        with mobile_location_lock: fix = dict(mobile_locations.get(user['id'], {}))
        return jsonify({'status':'success','location':fix or None})

    @app.route('/api/phone_location')
    def phone_location_for_pi():
        blocked = require_token('DEVICE_TOKEN')
        if blocked: return blocked
        with lock: fix = dict(state['phone_fix']) if state['phone_fix'] else None
        # A phone fix is usable only by the device whose current owner supplied it.
        owner = getattr(g, 'edge_device', {}).get('owner_id')
        available = bool(fix and fix.get('user_id') == owner and owner is not None
                         and time.time()-fix['updated_at'] <= PHONE_TTL)
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
            # A rate is meaningful only while frames are actually arriving.
            effective_fps = received_rate.rate(time.monotonic(),
                state['camera_capture_fps'], state['preview_render_fps'])
            state['video_fps'] = effective_fps
            return jsonify({'available':cloud_available or bool(direct_url),
                            'cloud_available':cloud_available,
                            'direct_video_url':direct_url,
                            'age_seconds':round(age,2) if age is not None else None,
                            'frames_received':state['frame_count'],
                            'video_fps':effective_fps, 'video_out_of_order_dropped':state['video_out_of_order_dropped'], 'preview_render_fps':state['preview_render_fps'], 'camera_capture_fps':state['camera_capture_fps'], 'device_online':online(),
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
        stream_device_id = getattr(g,'device_id',None)
        stream_user_id = (current_user() or {}).get('id')
        def frames():
            last_seq=-1
            auth_checked=0.0
            while True:
                if app.config['AUTH_ENABLED'] and time.monotonic()-auth_checked > 2:
                    watcher=accounts.find_user(stream_user_id) if stream_user_id else None
                    if not watcher or stream_device_id not in {d['device_id'] for d in registry.visible(watcher)}:
                        return
                    auth_checked=time.monotonic()
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
        return Response(stream_with_context(frames()),mimetype='multipart/x-mixed-replace; boundary=frame',
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


    @app.route('/api/devices')
    def list_my_devices():
        user = current_user()
        devices = (registry.owned(user) if request.args.get('scope') == 'owned' else registry.visible(user)) if user else []
        now = time.monotonic()
        return jsonify({'devices':[
            {**device, 'owner_username':(accounts.find_user(device['owner_id']) or {}).get('username'),
             'online':now-pool.get(device['device_id']).state['last_heartbeat']<15,
             'camera':pool.get(device['device_id']).state['camera_index']}
            for device in devices], 'selected_device_id':getattr(g,'device_id',None),
            'role':user['role'] if user else 'user'})

    @app.route('/devices')
    def devices_gallery():
        user = current_user()
        if app.config['AUTH_ENABLED'] and (not user or user['role'] != 'admin'):
            return redirect(url_for('detection'))
        rows = registry.visible(user)
        now = time.monotonic()
        for row in rows:
            row['online'] = now-pool.get(row['device_id']).state['last_heartbeat'] < 15
        return render_template('devices.html', active_page='devices', devices=rows)

    def admin_only():
        user = current_user()
        if not user or user['role'] != 'admin':
            return err('仅管理员可以操作', 403)
        return None

    @app.route('/admin')
    def admin_page():
        blocked = admin_only()
        if blocked is not None: return blocked
        return render_template('admin.html', active_page='admin')

    @app.route('/api/admin/users')
    def admin_users():
        blocked = admin_only()
        if blocked is not None: return blocked
        return jsonify(accounts.list_users())

    @app.route('/api/admin/users/promote', methods=['POST'])
    def admin_promote_user():
        blocked = admin_only()
        if blocked is not None: return blocked
        data = request.get_json(silent=True) or {}
        username = str(data.get('username') or '').strip()
        if not username:
            return err('请填写用户账号')
        promoted, error = accounts.promote_to_admin(username)
        if error:
            return err(error, 404 if '不存在' in error else 409)
        store.audit(current_user(), 'account_promoted', str(promoted['id']),
                    {'username': promoted['username'], 'role': 'admin'})
        return jsonify({'status': 'success', 'user': promoted,
                        'message': '权限升级成功，该用户重新登录后可进入管理员控制中心'})

    @app.route('/api/admin/users/<int:user_id>/mobile-location', methods=['POST'])
    def admin_mobile_location_permission(user_id):
        blocked = admin_only()
        if blocked is not None: return blocked
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload.get('allowed'), bool):
            return err('请选择是否允许定位', 400)
        success, failure = accounts.set_mobile_location_granted(user_id, payload['allowed'])
        if not success: return err(failure, 409)
        store.audit(current_user(), 'mobile_location_permission_changed', str(user_id),
                    {'allowed':payload['allowed']})
        return jsonify({'status':'success', 'allowed':payload['allowed'], 'message':'手机定位权限已更新'})

    @app.route('/api/admin/users/<int:user_id>/reset-password', methods=['POST'])
    def admin_reset_user_password(user_id):
        blocked = admin_only()
        if blocked is not None: return blocked
        temporary, failure = accounts.reset_password_for_user(user_id)
        if failure: return err(failure, 403)
        store.audit(current_user(), 'user_password_reset', str(user_id))
        response = jsonify({'status':'success', 'temporary_password':temporary,
                            'message':'已生成新密码，仅此一次显示。请通过可信方式交给用户，并要求其登录后修改。'})
        response.headers['Cache-Control']='no-store'
        return response

    @app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
    def admin_delete_normal_user(user_id):
        blocked = admin_only()
        if blocked is not None: return blocked
        user = accounts.find_user(user_id)
        if not user: return err('账号不存在', 404)
        if user['role'] != 'user': return err('管理员账号不能在此删除', 403)
        if registry.owned(user): return err('该用户仍绑定设备，请先变更设备归属', 409)
        if any(r.get('owner_id') == user_id or r.get('uploaded_by_user_id') == user_id for r in store.all()):
            return err('该账号仍有关联损伤记录，需先处理数据归属；系统不会孤立历史记录', 409)
        success, failure = accounts.delete_normal_user(user_id)
        if not success: return err(failure, 409)
        store.audit(current_user(), 'user_deleted', str(user_id), {'username':user['username']})
        return jsonify({'status':'success','message':'用户已删除'})

    @app.route('/api/admin/records')
    def admin_all_records():
        blocked = admin_only()
        if blocked is not None: return blocked
        # Do not apply the selected device_id query filter on the admin table.
        # Otherwise the archive can contain entries while this table appears empty.
        return jsonify(store.all())

    @app.route('/api/admin/devices', methods=['POST'])
    def admin_create_device():
        blocked = admin_only()
        if blocked is not None: return blocked
        data = request.get_json(silent=True) or {}
        if not str(data.get('device_id') or '').strip():
            data['device_id'] = 'pi-' + secrets.token_hex(4)
        owner = accounts.find_by_username(data.get('owner_username'))
        if not owner: return err('账号不存在：请先注册普通用户', 404)
        # Preflight: do not commit the device or issue a one-time token if the
        # complete downloadable config cannot be generated.
        try:
            render_houduan_env(
                data['device_id'], 'preflight-token',
                cloud_url=os.environ.get('DEVICE_CLOUD_BASE_URL', 'http://39.102.84.218:5000'),
                amap_key=os.environ.get('AMAP_JS_KEY', ''),
                amap_secret=os.environ.get('AMAP_JS_SECRET', ''))
        except (ValueError, OSError) as exc:
            return err('设备配置模板错误：' + str(exc), 500)
        try:
            token = registry.create(data.get('device_id'), data.get('name'), owner['id'])
        except ValueError as exc:
            return err(str(exc))
        except __import__('sqlite3').IntegrityError:
            return err('设备编号已存在', 409)
        store.audit(current_user(),'device_created',data.get('device_id'),{'owner':owner['username']})
        device_env = render_houduan_env(
            data['device_id'], token,
            cloud_url=os.environ.get('DEVICE_CLOUD_BASE_URL', 'http://39.102.84.218:5000'),
            amap_key=os.environ.get('AMAP_JS_KEY', ''),
            amap_secret=os.environ.get('AMAP_JS_SECRET', ''))
        response = jsonify({'status':'success','device_id':data['device_id'],
                            'device_token':token, 'device_env':device_env,
                            'message':'完整设备配置仅在本次生成，请立即下载并妥善保管'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 201

    @app.route('/api/admin/devices/<device_id>/assign', methods=['POST'])
    def admin_assign_device(device_id):
        blocked = admin_only()
        if blocked is not None: return blocked
        data=request.get_json(silent=True) or {}
        owner=accounts.find_by_username(data.get('owner_username'))
        if not owner: return err('目标用户不存在', 404)
        try:
            assigned = registry.assign(device_id,owner['id'])
        except ValueError as exc: return err(str(exc),409)
        if not assigned: return err('设备不存在',404)
        store.audit(current_user(),'device_assigned',device_id,{'new_owner':owner['username']})
        return jsonify({'status':'success','owner_username':owner['username']})

    @app.route('/api/admin/devices/<device_id>/rotate', methods=['POST'])
    def admin_rotate_device(device_id):
        blocked = admin_only()
        if blocked is not None: return blocked
        if not registry.get(device_id): return err('设备不存在',404)
        try:
            render_houduan_env(
                device_id, 'preflight-token',
                cloud_url=os.environ.get('DEVICE_CLOUD_BASE_URL', 'http://39.102.84.218:5000'),
                amap_key=os.environ.get('AMAP_JS_KEY', ''),
                amap_secret=os.environ.get('AMAP_JS_SECRET', ''))
        except (ValueError, OSError) as exc:
            return err('设备配置模板错误：' + str(exc), 500)
        token=registry.rotate_token(device_id)
        if not token: return err('设备不存在',404)
        store.audit(current_user(),'device_token_rotated',device_id)
        device_env = render_houduan_env(
            device_id, token,
            cloud_url=os.environ.get('DEVICE_CLOUD_BASE_URL', 'http://39.102.84.218:5000'),
            amap_key=os.environ.get('AMAP_JS_KEY', ''),
            amap_secret=os.environ.get('AMAP_JS_SECRET', ''))
        response = jsonify({'status':'success','device_id':device_id,
                            'device_token':token, 'device_env':device_env,
                            'message':'旧密钥已失效；请立即下载完整 .env 并覆盖对应设备配置'})
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.route('/api/admin/audit')
    def admin_audit():
        blocked = admin_only()
        if blocked is not None: return blocked
        return jsonify(store.recent_audit())

    @app.route('/api/admin/records/<record_id>',methods=['PATCH','DELETE'])
    def admin_record_write(record_id):
        blocked = admin_only()
        if blocked is not None: return blocked
        if request.method == 'DELETE':
            record=store.delete(record_id)
            if record: store.audit(current_user(),'record_deleted',record_id,{'before':record})
            return jsonify({'status':'success','deleted':record_id}) if record else err('记录不存在',404)
        data=request.get_json(silent=True) or {}
        try:
            record=store.update(record_id,data)
        except ValueError as exc:
            return err(str(exc))
        if record: store.audit(current_user(),'record_updated',record_id,{'changes':data})
        return jsonify({'status':'success','record':record}) if record else err('记录不存在',404)

    return app


app = create_app()

if __name__ == '__main__':
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT','5000'))
    cert, key = os.environ.get('SSL_CERT_FILE'), os.environ.get('SSL_KEY_FILE')
    tls = (cert, key) if cert and key else None
    print(f'电脑端：http{"s" if tls else ""}://127.0.0.1:{port}/location  |  手机节点必须使用有效 HTTPS 或树莓派 8080 HTTPS')
    app.run(host=host,port=port,threaded=True,debug=False,ssl_context=tls)
