#!/usr/bin/env python3
"""只读检查管理员账号是否存在且密码正确。不会显示或保存输入的密码。"""
from __future__ import annotations
import getpass
import os
import sqlite3
from pathlib import Path
from werkzeug.security import check_password_hash

ROOT = Path(__file__).resolve().parents[1]
env = ROOT / '.env'
settings = {}
if env.exists():
    for line in env.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if '=' in stripped and not stripped.startswith('#'):
            key, value = stripped.split('=', 1)
            settings[key.strip()] = value.strip().strip('\"').strip("'")
data_dir = Path(os.getenv('ROADSENSE_DATA_DIR') or settings.get('ROADSENSE_DATA_DIR') or (ROOT/'data'))
dbfile = data_dir/'accounts.sqlite3'
if not dbfile.is_file():
    raise SystemExit(f'未找到账号数据库: {dbfile}；先确认部署路径并启动云端服务。')
username = input('请输入需要检查的账号: ').strip()
password = getpass.getpass('请输入要验证的密码（输入不回显）: ')
with sqlite3.connect(str(dbfile)) as db:
    result = db.execute('SELECT role,password_hash FROM users WHERE username=?',(username,)).fetchone()
if not result:
    print('该账号在当前数据库中不存在。')
else:
    role, hashed = result
    print('数据库中的角色:', role)
    print('密码验证:', '通过' if check_password_hash(hashed,password) else '失败')
