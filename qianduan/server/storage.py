"""SQLite persistence of inspection records, with one-time legacy JSON import."""
import json
import os
import sqlite3
from contextlib import contextmanager
import threading
import uuid
from pathlib import Path

try:
    from .record_naming import legacy_archive_name, record_source
except ImportError:  # python server/app.py
    from record_naming import legacy_archive_name, record_source

class RecordStore:
    def __init__(self, data_dir):
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.image_dir = self.directory / 'images'
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'records.sqlite3'
        self.lock = threading.RLock()
        with self._connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, time TEXT NOT NULL,
                device_id TEXT, owner_id INTEGER,
                uploaded_by_user_id INTEGER, uploaded_by_username TEXT,
                upload_identity_type TEXT NOT NULL DEFAULT 'legacy_unknown',
                level TEXT, image TEXT, original_image TEXT,
                payload TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_records_device ON records(device_id, time DESC)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_records_owner ON records(owner_id, time DESC)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_records_images ON records(image, original_image)')
            db.execute('''CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, performed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actor_id INTEGER, actor_username TEXT, action TEXT NOT NULL,
                target_id TEXT, details TEXT NOT NULL DEFAULT '{}')''')
        try: self.path.chmod(0o600)
        except OSError: pass
        self._ensure_record_names()
        self._migrate_legacy()

    def _ensure_record_names(self):
        # In-place, idempotent metadata migration. Never rename/delete archived images.
        with self.lock, self._connect() as db:
            cols = {r['name'] for r in db.execute('PRAGMA table_info(records)')}
            if 'record_name' not in cols:
                db.execute('ALTER TABLE records ADD COLUMN record_name TEXT')
            rows = db.execute('SELECT id, record_name, payload FROM records').fetchall()
            for row in rows:
                try:
                    record = json.loads(row['payload'])
                except (ValueError, TypeError):
                    continue
                old_name = record.get('record_name')
                name = old_name or legacy_archive_name(record)
                # Old phone rows used a non-descriptive literal as batch name.
                if record_source(record) == 'phone_photo' and record.get('session_id') == '手机辅助拍照识别':
                    record['session_id'] = name
                if old_name != name or row['record_name'] != name:
                    record['record_name'] = name
                    db.execute('UPDATE records SET record_name=?, payload=? WHERE id=?',
                               (name, json.dumps(record, ensure_ascii=False), row['id']))

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

    def _migrate_legacy(self):
        old = self.directory / 'records.json'
        marker = self.directory / '.records_json_imported'
        if marker.exists() or not old.exists(): return
        with self.lock:
            try:
                records = json.loads(old.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                return
            if not isinstance(records, list): return
            with self._connect() as db:
                for row in records:
                    if not isinstance(row, dict): continue
                    row.setdefault('id', uuid.uuid4().hex)
                    row.setdefault('device_id', 'pi-main')
                    row.setdefault('upload_identity_type', 'legacy_unknown')
                    self._insert(db, row, ignore=True)
            # Keep JSON as a backup; never re-import records an admin deletes later.
            marker.write_text('Imported into records.sqlite3. Original records.json is kept as backup.\n',encoding='utf-8')

    def _insert(self, db, row, ignore=False):
        record_name = row.setdefault('record_name', legacy_archive_name(row))
        if record_source(row) == 'phone_photo' and row.get('session_id') == '手机辅助拍照识别':
            row['session_id'] = record_name
        verb = 'INSERT OR IGNORE' if ignore else 'INSERT'
        db.execute(f'''{verb} INTO records
            (id,time,device_id,owner_id,uploaded_by_user_id,uploaded_by_username,
             upload_identity_type,level,image,original_image,payload,record_name)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (str(row['id']), str(row.get('time','')), row.get('device_id'), row.get('owner_id'),
             row.get('uploaded_by_user_id'), row.get('uploaded_by_username'),
             row.get('upload_identity_type','legacy_unknown'), row.get('level'),
             row.get('image'), row.get('original_image'), json.dumps(row,ensure_ascii=False), record_name))

    def add(self, record):
        with self.lock, self._connect() as db:
            self._insert(db, record)
            count = db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        return count

    def all(self, device_ids=None):
        with self._connect() as db:
            if device_ids is None:
                rows = db.execute('SELECT payload FROM records ORDER BY time DESC').fetchall()
            elif not device_ids:
                return []
            else:
                marks = ','.join('?' for _ in device_ids)
                rows = db.execute(f'SELECT payload FROM records WHERE device_id IN ({marks}) ORDER BY time DESC', tuple(device_ids)).fetchall()
        return [json.loads(r['payload']) for r in rows]

    def unbound_phone_records(self, owner_id):
        """Account-owned phone photos made before any Pi device assignment."""
        with self._connect() as db:
            rows=db.execute("SELECT payload FROM records WHERE device_id IS NULL AND owner_id=? ORDER BY time DESC", (int(owner_id),)).fetchall()
        return [json.loads(row['payload']) for row in rows]

    def get(self, record_id):
        with self._connect() as db:
            row=db.execute('SELECT payload FROM records WHERE id=?',(str(record_id),)).fetchone()
        return json.loads(row['payload']) if row else None

    def by_image(self, filename):
        with self._connect() as db:
            row=db.execute('SELECT payload FROM records WHERE image=? OR original_image=?',(filename,filename)).fetchone()
        return json.loads(row['payload']) if row else None

    def update(self, record_id, changes):
        allowed = {'level','notes','lat','lon','session_id'}
        if not changes or any(k not in allowed for k in changes):
            raise ValueError('只能修改损伤等级、备注、经纬度和任务编号')
        with self.lock, self._connect() as db:
            row=db.execute('SELECT payload FROM records WHERE id=?',(str(record_id),)).fetchone()
            if not row: return None
            data=json.loads(row['payload'])
            data.update(changes)
            db.execute('UPDATE records SET level=?,payload=? WHERE id=?',
                       (data.get('level'),json.dumps(data,ensure_ascii=False),str(record_id)))
            return data

    def audit(self, actor, action, target_id, details=None):
        with self._connect() as db:
            db.execute('INSERT INTO audit_events(actor_id,actor_username,action,target_id,details) VALUES(?,?,?,?,?)',
                (actor.get('id') if actor else None, actor.get('username') if actor else None,
                 str(action),str(target_id),json.dumps(details or {},ensure_ascii=False)))

    def recent_audit(self, limit=100):
        with self._connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT ?',
                (max(1,min(int(limit),500)),)).fetchall()]

    def delete(self, record_id):
        with self.lock, self._connect() as db:
            row=db.execute('SELECT payload FROM records WHERE id=?',(str(record_id),)).fetchone()
            if not row:return None
            value=json.loads(row['payload'])
            db.execute('DELETE FROM records WHERE id=?',(str(record_id),))
            # Physical images are intentionally retained for audit/recovery.
            return value
