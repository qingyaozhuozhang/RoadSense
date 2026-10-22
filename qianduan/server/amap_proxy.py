"""RoadSense AMap JS API 2.0 security proxy (keeps securityJsCode server-side).

See https://developer.amap.com/api/javascript-api-v2/guide/abc/jscode
"""
import os
import re
from urllib.parse import urlencode
import requests
from flask import Blueprint, Response, jsonify, request

amap_blueprint = Blueprint('roadsense_amap', __name__)


def client_settings():
    key = os.environ.get('AMAP_JS_KEY', '').strip()
    secret = os.environ.get('AMAP_JS_SECRET', '').strip()
    return {'key': key, 'configured': bool(key and secret),
            'service_host': '/_AMapService', 'provider': '高德 JS API 2.0',
            'coordinate_system': 'WGS84 (原始), GCJ-02 (展示)',
            'online_required': True, 'needs_key': True}


@amap_blueprint.route('/api/map_config')
def amap_client_config():
    # AMap JS key is public by design; securityJsCode MUST NOT be returned.
    return jsonify(client_settings())


@amap_blueprint.route('/api/map_provider')
def amap_map_provider():
    result = client_settings().copy()
    result.pop('key', None)
    return jsonify(result)


@amap_blueprint.route('/api/amap_check')
def amap_check():
    cfg=client_settings()
    return jsonify({'status':'configured' if cfg['configured'] else 'missing_config',
                    'provider':'高德地图 JS API 2.0',
                    'js_key_present':bool(cfg['key']),
                    'js_security_code_present':bool(os.environ.get('AMAP_JS_SECRET','').strip()),
                    'proxy_path':'/_AMapService',
                    'note':'这是配置检查，不证明 Key 已生效；请通过 /gps 或 /severe 的实际地图加载验证。'})


@amap_blueprint.route('/_AMapService/<path:resource>', methods=['GET', 'POST'])
def amap_reverse_proxy(resource):
    secret = os.environ.get('AMAP_JS_SECRET', '').strip()
    if not secret:
        return jsonify({'status': 'error', 'message': '缺少 AMAP_JS_SECRET'}), 503
    # Prevent traversal and exotic paths even though the remote host is fixed.
    if not re.fullmatch(r'[A-Za-z0-9._/-]{1,180}', resource) or '..' in resource:
        return jsonify({'status': 'error', 'message': 'invalid AMap path'}), 400
    # As required by AMap JS API security proxy: style endpoint uses webapi,
    # remaining endpoints use restapi. NEVER construct a remote host from requests.
    if resource == 'v4/map/styles' or resource.startswith('v4/map/styles/'):
        base = 'https://webapi.amap.com/'
    else:
        base = 'https://restapi.amap.com/'
    params = [(key, value) for key, value in request.args.items(multi=True)
              if key.lower() != 'jscode']
    params.append(('jscode', secret))
    try:
        response = requests.request(request.method, base + resource, params=params,
            data=request.get_data() if request.method == 'POST' else None,
            headers={'Content-Type': request.content_type or 'application/octet-stream'} if request.method == 'POST' else {},
            timeout=12)
    except requests.RequestException:
        return jsonify({'status':'error','message':'高德上游接口不可达'}),502
    # Response may be a JSON error, image, styles, etc. Preserve content type.
    return Response(response.content,status=response.status_code,
        content_type=response.headers.get('Content-Type','application/octet-stream'),
        headers={'Cache-Control': 'public, max-age=120' if response.ok else 'no-store'})
