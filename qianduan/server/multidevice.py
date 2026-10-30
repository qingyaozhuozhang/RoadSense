"""Per-device runtime isolation and SQLite-backed enrollment/ownership."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from contextlib import contextmanager
import threading
import time
from collections.abc import MutableMapping
from pathlib import Path

from flask import g, has_request_context
try:
    from .location_state import PositionManager
    from .video_rate import RollingFrameRate
except ImportError:
    from location_state import PositionManager
    from video_rate import RollingFrameRate

DEVICE_ID_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{2,47}$')


class DeviceRegistry:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'devices.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                owner_id INTEGER NOT NULL, token_sha256 TEXT NOT NULL,
                created_at INTEGER NOT NULL)''')
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=20000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, device_id, name, owner_id, token=None):
        device_id = str(device_id or '').strip()
        if not DEVICE_ID_RE.fullmatch(device_id):
            raise ValueError('设备编号需要3–48位字母、数字、下划线或短横线')
        token = token or secrets.token_urlsafe(40)
        if len(token) < 24:
            raise ValueError('设备密钥至少24位')
        with self._connect() as db:
            if db.execute('SELECT 1 FROM devices WHERE owner_id=? LIMIT 1', (int(owner_id),)).fetchone():
                raise ValueError('该账号已绑定一台设备，请先重新分配现有设备；每个账号最多归属一台设备')
            db.execute('INSERT INTO devices(device_id,name,owner_id,token_sha256,created_at) VALUES(?,?,?,?,?)',
                (device_id, str(name or device_id)[:100], int(owner_id),
                 hashlib.sha256(token.encode()).hexdigest(), int(time.time())))
        return token

    def bootstrap(self, device_id, token, owner_id):
        if token and not self.get(device_id):
            return self.create(device_id, 'RoadSense 主设备', owner_id, token)
        return None

    def get(self, device_id):
        with self._connect() as db:
            row = db.execute('SELECT device_id,name,owner_id,created_at FROM devices WHERE device_id=?',
                             (str(device_id),)).fetchone()
            return dict(row) if row else None

    def authenticate(self, device_id, token):
        if not device_id or not token:
            return None
        with self._connect() as db:
            row = db.execute('SELECT * FROM devices WHERE device_id=?', (device_id,)).fetchone()
        if row and hmac.compare_digest(row['token_sha256'], hashlib.sha256(str(token).encode()).hexdigest()):
            return {'device_id':row['device_id'], 'name':row['name'], 'owner_id':row['owner_id']}
        return None

    def visible(self, user):
        if not user:
            return []
        with self._connect() as db:
            if user['role'] == 'admin':
                rows = db.execute('SELECT device_id,name,owner_id,created_at FROM devices ORDER BY device_id').fetchall()
            else:
                rows = db.execute('SELECT device_id,name,owner_id,created_at FROM devices WHERE owner_id=? ORDER BY device_id', (user['id'],)).fetchall()
        return [dict(x) for x in rows]

    def owned(self, user):
        if not user: return []
        with self._connect() as db:
            rows = db.execute('SELECT device_id,name,owner_id,created_at FROM devices WHERE owner_id=? ORDER BY created_at,device_id LIMIT 1', (user['id'],)).fetchall()
        return [dict(row) for row in rows]

    def assign(self, device_id, owner_id):
        with self._connect() as db:
            exists = db.execute('SELECT device_id FROM devices WHERE owner_id=? AND device_id!=? LIMIT 1', (int(owner_id),str(device_id))).fetchone()
            if exists:
                raise ValueError('目标账号已绑定设备 ' + exists['device_id'] + '，请先修改原设备的归属')
            cur = db.execute('UPDATE devices SET owner_id=? WHERE device_id=?', (int(owner_id), str(device_id)))
        return cur.rowcount == 1

    def rotate_token(self, device_id):
        token = secrets.token_urlsafe(40)
        with self._connect() as db:
            cur = db.execute('UPDATE devices SET token_sha256=? WHERE device_id=?',
                             (hashlib.sha256(token.encode()).hexdigest(), device_id))
        return token if cur.rowcount else None


class Runtime:
    def __init__(self):
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.location = PositionManager()
        self.received_rate = RollingFrameRate()
        self.state = dict(command='STOP', session_id='', last_heartbeat=0.0, frame=None,
            pi_url='', pi_url_updated_at=0.0, phone_fix=None, last_uploaded_image=None,
            device_kind='physical', last_frame_at=0.0, frame_count=0,
            camera_signal='unknown', video_fps=0.0, video_stream_session='',
            video_frame_seq=-1, video_out_of_order_dropped=0, frame_brightness=None,
            camera_index=None, direct_video_url='', preview_render_fps=0.0,
            camera_capture_fps=0.0, last_operator_id=None)


class RuntimePool:
    def __init__(self):
        self._mutex = threading.Lock()
        self._items = {}
        self.default = Runtime()

    def get(self, device_id):
        if not device_id:
            return self.default
        with self._mutex:
            if device_id not in self._items:
                self._items[device_id] = Runtime()
            return self._items[device_id]


class RuntimeProxy:
    def __init__(self, pool, attr):
        self.pool = pool
        self.attr = attr
    def target(self):
        obj = getattr(g, 'rs_runtime', None) if has_request_context() else None
        return getattr(obj or self.pool.default, self.attr)
    def __getattr__(self, name):
        return getattr(self.target(), name)
    def __enter__(self):
        return self.target().__enter__()
    def __exit__(self, *a):
        return self.target().__exit__(*a)


class StateProxy(MutableMapping, RuntimeProxy):
    def __init__(self, pool): RuntimeProxy.__init__(self, pool, 'state')
    def __getitem__(self, key): return self.target()[key]
    def __setitem__(self, key, value): self.target()[key] = value
    def __delitem__(self, key): del self.target()[key]
    def __iter__(self): return iter(self.target())
    def __len__(self): return len(self.target())
