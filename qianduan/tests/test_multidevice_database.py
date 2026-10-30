"""Run with: python -m unittest discover -s tests -p 'test_multidevice_database.py' -v
These tests need Python stdlib only; Flask/werkzeug are stubbed if unavailable.
"""
from __future__ import annotations
import hashlib
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import flask  # noqa
except ImportError:
    fake=types.ModuleType('flask')
    fake.g=types.SimpleNamespace()
    fake.has_request_context=lambda: False
    sys.modules['flask']=fake

try:
    import werkzeug.security  # noqa
except ImportError:
    werkzeug=types.ModuleType('werkzeug')
    security=types.ModuleType('werkzeug.security')
    def check_password_hash(stored, password):
        method,salt,expected=stored.split('$')
        count=int(method.split(':')[-1])
        return hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),count).hex()==expected
    def generate_password_hash(p):
        import secrets
        salt=secrets.token_hex(8)
        return f'pbkdf2:sha256:600000${salt}${hashlib.pbkdf2_hmac("sha256",p.encode(),salt.encode(),600000).hex()}'
    security.check_password_hash=check_password_hash
    security.generate_password_hash=generate_password_hash
    werkzeug.security=security
    sys.modules['werkzeug']=werkzeug
    sys.modules['werkzeug.security']=security

# PIL import required by AccountStore, but image CAPTCHA is not in these tests.
try: import PIL.Image  # noqa
except ImportError:
    pil=types.ModuleType('PIL')
    for x in ('Image','ImageDraw','ImageFont'):
        mod=types.ModuleType('PIL.'+x)
        setattr(pil,x,mod)
        sys.modules['PIL.'+x]=mod
    sys.modules['PIL']=pil

from server.auth import AccountStore
from server.multidevice import DeviceRegistry, RuntimePool, StateProxy
from server.storage import RecordStore

class MultiDeviceDataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dir=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_admin_passwords_are_hashed_and_roles(self):
        store=AccountStore(self.dir)
        admin_username='test_admin'
        admin_password='OnlyForTests12345'
        from werkzeug.security import generate_password_hash
        stored_hash=generate_password_hash(admin_password)
        self.assertNotEqual(stored_hash, admin_password)
        store.seed_admin_hash(admin_username, stored_hash)
        self.assertEqual(store.authenticate(admin_username, admin_password)['role'], 'admin')
        self.assertIsNone(store.authenticate(admin_username, 'wrong-password'))
        ordinary, error=store.register('RoadUser123', 'Testpass123')
        self.assertIsNone(error)
        self.assertEqual(store.find_user(ordinary['id'])['role'], 'user')
        with self.assertRaises(RuntimeError):
            store.seed_admin_hash('RoadUser123', stored_hash)

    def test_devices_scope_and_independent_token(self):
        acc=AccountStore(self.dir)
        a,_=acc.register('user_one','Hello1234')
        b,_=acc.register('user_two','Hello1234')
        reg=DeviceRegistry(self.dir)
        t1=reg.create('cam-one','CAM 1',a['id'])
        t2=reg.create('cam-two','CAM 2',b['id'])
        self.assertNotEqual(t1,t2)
        self.assertEqual(reg.authenticate('cam-one',t1)['owner_id'],a['id'])
        self.assertIsNone(reg.authenticate('cam-two',t1))
        self.assertEqual([x['device_id'] for x in reg.visible(acc.find_user(a['id']))],['cam-one'])
        # Current policy: one ordinary account owns at most one Pi.
        with self.assertRaisesRegex(ValueError, '最多|已绑定'):
            reg.assign('cam-two',a['id'])
        self.assertEqual([x['device_id'] for x in reg.visible(acc.find_user(a['id']))],['cam-one'])
        newer=reg.rotate_token('cam-one')
        self.assertIsNone(reg.authenticate('cam-one',t1))
        self.assertTrue(reg.authenticate('cam-one',newer))
        self.assertNotIn(t1,reg.path.read_bytes().decode('utf-8',errors='ignore'))

    def test_records_migrate_only_once_and_isolated(self):
        original=[{'id':'old','time':'2026-10-01 00:00:00','level':'严重','image':'old.jpg'}]
        (self.dir/'records.json').write_text(json.dumps(original),encoding='utf-8')
        store=RecordStore(self.dir)
        self.assertEqual(len(store.all()),1)
        self.assertEqual(store.all(['pi-main'])[0]['upload_identity_type'],'legacy_unknown')
        store.add({'id':'new','time':'2026-10-28 00:00:00','device_id':'cam-one',
                   'owner_id':7,'uploaded_by_username':'user_one','level':'中度','image':'new.jpg'})
        self.assertEqual(len(store.all(['pi-main'])),1)
        self.assertEqual(len(store.all(['cam-one'])),1)
        self.assertEqual(store.by_image('new.jpg')['device_id'],'cam-one')
        self.assertIsNone(store.by_image('other.jpg'))
        self.assertEqual(store.update('new',{'notes':'调查中'})['notes'],'调查中')
        with self.assertRaises(ValueError):store.update('new',{'owner_id':999})
        store.audit({'id':1,'username':'admin'},'record_deleted','new')
        self.assertEqual(store.recent_audit()[0]['action'],'record_deleted')
        store.delete('old')
        reopened=RecordStore(self.dir)
        self.assertIsNone(reopened.get('old'))  # legacy import cannot resurrect deletion

    def test_independent_runtime_objects(self):
        pool=RuntimePool()
        x=pool.get('one');y=pool.get('two')
        x.state['frame']=b'111'
        x.state['command']='RUNNING'
        self.assertIsNone(y.state['frame'])
        self.assertEqual(y.state['command'],'STOP')
        self.assertIsNot(x.location,y.location)

if __name__=='__main__':unittest.main()
