#!/usr/bin/env python3
"""Initialize a RoadSense administrator locally, with interactive password prompt.

Run from qianduan: python tools/create_admin.py --username <your_admin_username>
"""
import argparse
import getpass
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.auth import AccountStore
from werkzeug.security import generate_password_hash


def data_folder():
    configured = os.environ.get("ROADSENSE_DATA_DIR", "").strip()
    if configured:
        return Path(configured)
    envfile = ROOT / '.env'
    if envfile.exists():
        for line in envfile.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('ROADSENSE_DATA_DIR='):
                value = line.partition('=')[2].strip().strip('"').strip("'")
                if value:
                    return Path(value)
    return ROOT / 'data'


def main():
    parser=argparse.ArgumentParser(description='Create an initial administrator; password is not echoed')
    parser.add_argument('--username',required=True,help='Administrator login name')
    args=parser.parse_args()
    username=args.username.strip()
    if not username or len(username)>64 or any(c.isspace() for c in username):
        parser.error('Username must be 1-64 characters, without whitespace')
    password=getpass.getpass('Set a new strong administrator password: ')
    confirm=getpass.getpass('Repeat the password: ')
    if password!=confirm:
        parser.error('Passwords do not match')
    if len(password)<12 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        parser.error('Password must be at least 12 characters and include letters and numbers')
    db=AccountStore(data_folder())
    db.seed_admin_hash(username,generate_password_hash(password))
    print('Administrator initialized. Keep this credential private and do not commit runtime data.')

if __name__=='__main__':
    main()
