"""Build the complete per-device houduan/.env on successful enrollment/rotation.

Never put the per-device token or AMap security code into public static JS.
The response is only returned by admin-only endpoints at the instant a token is issued.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / 'deploy' / 'houduan.env.template'
DEVICE_ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{2,47}$')


def render_houduan_env(device_id: str, device_token: str, *,
                       cloud_url: str, amap_key: str = '', amap_secret: str = '') -> str:
    """Render ALL houduan env keys; only identity and cloud-provided map keys change."""
    if not DEVICE_ID_PATTERN.fullmatch(str(device_id)):
        raise ValueError('设备编号不符合要求')
    url = urlsplit(cloud_url)
    if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password:
        raise ValueError('云端地址无效')
    values = dict(CLOUD_BASE_URL=cloud_url.rstrip('/'), DEVICE_ID=device_id,
                  DEVICE_TOKEN=device_token, AMAP_JS_KEY=amap_key,
                  AMAP_JS_SECRET=amap_secret)
    # Defend against accidentally writing extra directives via configuration values.
    for value in values.values():
        if any(c in str(value) for c in ('\n', '\r', '\x00')):
            raise ValueError('配置值含不允许的换行符')
    text = TEMPLATE.read_text(encoding='utf-8')
    output = []
    seen = set()
    for line in text.splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, _ = line.split('=', 1)
            key = key.strip()
            if key in values:
                line = f'{key}={values[key]}'
                seen.add(key)
        output.append(line)
    if seen != set(values):
        raise ValueError('缺少必须的设备 .env 模板字段')
    return '\n'.join(output) + '\n'
