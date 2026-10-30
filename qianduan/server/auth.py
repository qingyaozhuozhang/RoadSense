"""Persistent account and single-use image CAPTCHA storage for RoadSense."""
from __future__ import annotations

import hmac
import io
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from werkzeug.security import check_password_hash, generate_password_hash


REGISTER_USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
CAPTCHA_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def get_session_secret(data_dir):
    configured = os.environ.get("ROADSENSE_SECRET_KEY", "").strip()
    if configured:
        return configured
    secret_path = Path(data_dir) / ".flask_session_secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(secret_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(secrets.token_hex(32))
    return secret_path.read_text(encoding="ascii").strip()


class AccountStore:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "accounts.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )""")
            cols = {r[1] for r in db.execute("PRAGMA table_info(users)")}
            if 'role' not in cols:
                db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            if 'mobile_location_granted' not in cols:
                db.execute("ALTER TABLE users ADD COLUMN mobile_location_granted INTEGER NOT NULL DEFAULT 0")
            db.execute("""CREATE TABLE IF NOT EXISTS captchas (
                id TEXT PRIMARY KEY,
                answer TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )""")
        try:
            os.chmod(self.path, 0o600)
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

    def register(self, username, password):
        username = str(username or "").strip()
        password = str(password or "")
        if not REGISTER_USERNAME_PATTERN.fullmatch(username):
            return None, "普通用户注册账号须为 4–32 位、字母开头（仅字母、数字和下划线）；数字管理员账号请直接登录"
        if len(password) < 8 or len(password) > 128:
            return None, "密码长度需要在 8–128 位之间"
        if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
            return None, "密码需要同时包含字母和数字"
        try:
            with self._connect() as db:
                cursor = db.execute(
                    "INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
                    (username, generate_password_hash(password), int(time.time())),
                )
                return {"id": cursor.lastrowid, "username": username, "role": "user"}, None
        except sqlite3.IntegrityError:
            return None, "该用户名已注册"

    def authenticate(self, username, password):
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (str(username or "").strip(),)).fetchone()
        if row and check_password_hash(row["password_hash"], str(password or "")):
            return {"id": row["id"], "username": row["username"], "role": row["role"]}
        return None

    def find_user(self, user_id):
        with self._connect() as db:
            row = db.execute("SELECT id, username, role FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def mobile_location_granted(self, user_id):
        with self._connect() as db:
            row = db.execute('SELECT role, mobile_location_granted FROM users WHERE id=?', (user_id,)).fetchone()
        return bool(row and (row['role'] == 'admin' or row['mobile_location_granted']))

    def set_mobile_location_granted(self, user_id, allowed):
        with self._connect() as db:
            row = db.execute('SELECT role FROM users WHERE id=?', (user_id,)).fetchone()
            if not row:
                return False, '账号不存在'
            if row['role'] != 'user':
                return False, '管理员始终允许定位，无需单独授权'
            db.execute('UPDATE users SET mobile_location_granted=? WHERE id=?',
                       (1 if allowed else 0, user_id))
        return True, None

    def change_password(self, user_id, current_password, new_password):
        if len(str(new_password or '')) < 10 or len(str(new_password)) > 128:
            return False, '新密码需要至少10位，且不超过128位'
        if not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):
            return False, '新密码需同时包含字母和数字'
        with self._connect() as db:
            row = db.execute('SELECT password_hash FROM users WHERE id=?',(user_id,)).fetchone()
            if not row or not check_password_hash(row['password_hash'],str(current_password or '')):
                return False, '原密码错误'
            db.execute('UPDATE users SET password_hash=? WHERE id=?',
                       (generate_password_hash(new_password), user_id))
            return True, None

    def find_by_username(self, username):
        with self._connect() as db:
            row = db.execute("SELECT id,username,role FROM users WHERE username=?", (str(username).strip(),)).fetchone()
        return dict(row) if row else None

    def list_users(self):
        with self._connect() as db:
            rows = db.execute('SELECT id,username,role,created_at,mobile_location_granted FROM users ORDER BY id').fetchall()
        return [dict(row) for row in rows]

    def promote_to_admin(self, username):
        """Promote a previously registered user; caller MUST be an authenticated admin."""
        username = str(username or '').strip()
        if not username:
            return None, '请填写要升级的用户名'
        with self._connect() as db:
            row = db.execute('SELECT id,username,role FROM users WHERE username=?', (username,)).fetchone()
            if not row:
                return None, '账号不存在，请先让该用户注册'
            if row['role'] == 'admin':
                return None, '该账号已经是管理员'
            db.execute("UPDATE users SET role='admin' WHERE id=? AND role='user'", (row['id'],))
            return {'id': row['id'], 'username': row['username'], 'role': 'admin'}, None

    def reset_password_for_user(self, user_id):
        """Generate a replacement credential. Old password cannot be recovered from its hash."""
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
        temporary = secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ') + secrets.choice('23456789') + ''.join(secrets.choice(alphabet) for _ in range(18))
        with self._connect() as db:
            row = db.execute('SELECT id,username,role FROM users WHERE id=?', (int(user_id),)).fetchone()
            if not row or row['role'] == 'admin':
                return None, '仅能重置普通用户密码；管理员请使用独立恢复流程'
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (generate_password_hash(temporary), int(user_id)))
        return temporary, None

    def delete_normal_user(self, user_id):
        with self._connect() as db:
            row = db.execute('SELECT id,role,username FROM users WHERE id=?', (int(user_id),)).fetchone()
            if not row: return False, '用户不存在'
            if row['role'] != 'user': return False, '禁止直接删除管理员账号'
            db.execute('DELETE FROM users WHERE id=? AND role=?', (int(user_id),'user'))
        return True, None

    def seed_admin_hash(self, username, password_hash):
        """First-run only. Never upgrade an existing untrusted account to admin."""
        with self._connect() as db:
            row = db.execute('SELECT role FROM users WHERE username=?',(username,)).fetchone()
            if row:
                if row['role'] != 'admin':
                    raise RuntimeError('Administrator username already belongs to a normal user: ' + username)
                return
            db.execute('INSERT INTO users(username,password_hash,created_at,role) VALUES(?,?,?,?)',
                       (username,password_hash,int(time.time()),'admin'))

    def issue_captcha(self):
        challenge_id = secrets.token_urlsafe(24)
        answer = "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(5))
        with self._connect() as db:
            db.execute("DELETE FROM captchas WHERE created_at<?", (int(time.time()) - 300,))
            db.execute("INSERT INTO captchas(id,answer,created_at) VALUES(?,?,?)",
                       (challenge_id, answer, int(time.time())))
        return challenge_id, self._render_captcha(answer)

    def consume_captcha(self, challenge_id, answer):
        if not challenge_id:
            return False
        with self._connect() as db:
            row = db.execute("SELECT answer,created_at FROM captchas WHERE id=?", (challenge_id,)).fetchone()
            db.execute("DELETE FROM captchas WHERE id=?", (challenge_id,))
        return bool(row and time.time() - row["created_at"] <= 300 and
                    hmac.compare_digest(row["answer"], str(answer or "").strip().upper()))

    @staticmethod
    def _render_captcha(answer):
        # Pillow is already installed by qrcode[pil]. No third-party SMS account needed.
        img = Image.new("RGB", (174, 54), "#eef5ff")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
        except OSError:
            font = ImageFont.load_default()
        for _ in range(12):
            x1, y1 = secrets.randbelow(174), secrets.randbelow(54)
            x2, y2 = secrets.randbelow(174), secrets.randbelow(54)
            draw.line((x1, y1, x2, y2), fill=(169, 195, 220), width=1)
        for i, char in enumerate(answer):
            # Draw at 2x size before resampling for more legible text.
            draw.text((12 + i * 31, 7 + secrets.randbelow(7)), char,
                      font=font, fill=(19, 52 + secrets.randbelow(60), 108))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
