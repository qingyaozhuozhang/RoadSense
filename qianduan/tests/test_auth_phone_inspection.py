"""Authentication and cloud photo inspection integration tests.

Use an injected inference function for HTTP contract tests. A real ONNX smoke
test is documented separately because it requires native cloud dependencies.
"""
import io
import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.app import create_app


IMAGE = Path(__file__).parent / 'fixtures' / 'legacy_damage.jpg'


def fake_inference(contents):
    assert contents.startswith(b'\xff\xd8')
    return {
        'original_jpeg': contents,
        'result_jpeg': contents,
        'level': '中度',
        'damage_ratio': .07,
        'inference_ms': 124,
        'detections': [{'class_name':'crack', 'confidence':.91,
                        'polygon':[[3,4],[13,4],[13,20],[3,20]]}],
    }


class RoadSenseAuthAndPhoneTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            'TESTING':True, 'AUTH_ENABLED':True,
            'DATA_DIR':self.directory.name,
            'SECRET_KEY':'testing-road-sense-secret',
            'PHONE_INFER_FN':fake_inference,
            'DEVICE_TOKEN':'edge-test-token',
        })
        self.client = self.app.test_client()
        self.base = {'base_url':'https://localhost'}

    def tearDown(self):
        self.directory.cleanup()

    def get_captcha_and_csrf(self):
        response = self.client.get('/api/auth/captcha', **self.base)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/png')
        with self.client.session_transaction() as sess:
            challenge = sess['captcha_id']
            csrf = sess['csrf_token'] if 'csrf_token' in sess else None
        if csrf is None:
            self.client.get('/login', **self.base)
            with self.client.session_transaction() as sess:
                csrf = sess['csrf_token']
        with sqlite3.connect(Path(self.directory.name) / 'accounts.sqlite3') as db:
            answer = db.execute('SELECT answer FROM captchas WHERE id=?', (challenge,)).fetchone()[0]
        return answer, csrf

    def register(self, username='RoadUser', password='Pass12345'):
        self.client.get('/register', **self.base)
        answer, csrf = self.get_captcha_and_csrf()
        return self.client.post('/register', data={
            'username':username, 'password':password, 'captcha':answer,
            'csrf_token':csrf,
        }, **self.base)

    def test_guest_blocked_dashboard_but_edge_still_works(self):
        response = self.client.get('/', **self.base)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)
        self.assertEqual(self.client.get('/api/records', **self.base).status_code, 401)
        self.assertEqual(self.client.get('/api/latest_frame', **self.base).status_code, 401)
        self.assertEqual(self.client.get('/phone', **self.base).status_code, 200)
        self.assertEqual(self.client.post('/update_stream', data={
            'camera_signal':'ok'}, headers={'X-RoadSense-Token':'edge-test-token'}, **self.base).status_code, 200)
        self.assertEqual(self.client.get('/api/command', headers={
            'X-RoadSense-Token':'edge-test-token'}, **self.base).status_code, 200)
        self.assertEqual(self.client.get('/api/command', **self.base).status_code, 401)

    def test_login_visual_assets_are_served(self):
        login = self.client.get('/login', **self.base)
        self.assertEqual(login.status_code, 200)
        self.assertIn(b'auth-redesign.css', login.data)
        self.assertIn(b'INTELLIGENT ROAD INSPECTION', login.data)
        css = self.client.get('/static/css/auth-redesign.css', **self.base)
        self.assertEqual(css.status_code, 200)
        self.assertIn(b'road-login-hero.png', css.data)
        css.close()
        image = self.client.get('/static/img/road-login-hero.png', **self.base)
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.data.startswith(b'\x89PNG\r\n\x1a\n'))
        image.close()

    def test_http_registration_and_login_keep_browser_session(self):
        # session_transaction() uses localhost; preserve one cookie host.
        self.base = {'base_url': 'http://localhost:5000'}
        register_page = self.client.get('/register', **self.base)
        self.assertEqual(register_page.status_code, 200)
        self.assertNotIn('auth-https-warning', register_page.get_data(as_text=True))
        self.assertIn('session=', register_page.headers.get('Set-Cookie', ''))
        self.assertNotIn('Secure', register_page.headers.get('Set-Cookie', ''))

        registered = self.register()
        self.assertEqual(registered.location, '/login')
        self.client.get('/login', **self.base)
        answer, csrf = self.get_captcha_and_csrf()
        logged_in = self.client.post('/login', data={
            'username': 'RoadUser', 'password': 'Pass12345',
            'captcha': answer, 'csrf_token': csrf,
        }, follow_redirects=True, **self.base)
        self.assertEqual(logged_in.status_code, 200)
        self.assertIn('登录成功', logged_in.get_data(as_text=True))
        self.assertIn('道路巡检总览', logged_in.get_data(as_text=True))

    def test_closing_browser_does_not_create_week_long_login_cookie(self):
        self.register()
        self.client.get('/login', **self.base)
        answer, csrf = self.get_captcha_and_csrf()
        login = self.client.post('/login', data={
            'username':'RoadUser', 'password':'Pass12345',
            'captcha':answer, 'csrf_token':csrf,
        }, **self.base)
        self.assertEqual(login.status_code, 302)
        self.assertIn('rs_tab_ticket=', login.location)
        cookie = login.headers.get('Set-Cookie','')
        self.assertNotIn('Max-Age=', cookie)
        self.assertNotIn('Expires=', cookie)
        dashboard = self.client.get(login.location, **self.base).get_data(as_text=True)
        self.assertIn("sessionStorage.setItem(key, 'active')", dashboard)
        self.assertIn('true', dashboard)
        # A newly opened tab is routed to fresh login by the early head script.
        self.client.get('/login?fresh=1', **self.base)
        self.assertEqual(self.client.get('/api/auth/me', **self.base).status_code, 401)

    def test_mobile_auto_recognizes_then_requires_one_archive_confirmation(self):
        markup = self.client.get('/phone', **self.base).get_data(as_text=True)
        self.assertIn('id="sender-photo-submit" hidden disabled', markup)
        self.assertIn('确认归档到云端', markup)
        js_response = self.client.get('/static/js/mobile_photo.js', **self.base)
        script = js_response.get_data(as_text=True)
        js_response.close()
        self.assertIn('inspect(file, version)', script)
        self.assertIn("fetch('/api/phone/preview'", script)
        self.assertIn("fetch('/api/phone/archive'", script)
        self.assertNotIn("addEventListener('click', inspect)", script)

    def test_secure_cookie_can_be_restored_for_https(self):
        self.app.config['SESSION_COOKIE_SECURE'] = True
        response = self.client.get('/register', **self.base)
        self.assertIn('Secure', response.headers.get('Set-Cookie', ''))

    def test_photo_inspection_is_not_a_separate_desktop_sidebar_entry(self):
        self.app.config['AUTH_ENABLED'] = False
        page = self.client.get('/', **self.base).get_data(as_text=True)
        self.assertNotIn('href="/phone/inspect"', page)

    def test_registration_success_returns_to_login_with_visible_success_message(self):
        response = self.register()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/login')
        self.assertEqual(self.client.get('/', **self.base).status_code, 302)
        login = self.client.get('/login', **self.base)
        page = login.get_data(as_text=True)
        self.assertIn('注册成功', page)
        self.assertIn('auth-alert-success', page)

    def test_failed_registration_and_login_show_visible_error_messages(self):
        self.client.get('/register', **self.base)
        answer, csrf = self.get_captcha_and_csrf()
        failed_registration = self.client.post('/register', data={
            'username':'RoadUser', 'password':'short', 'captcha':answer,
            'csrf_token':csrf,
        }, follow_redirects=True, **self.base)
        self.assertIn('密码长度需要在 8–128 位之间', failed_registration.get_data(as_text=True))
        self.assertIn('auth-alert-error', failed_registration.get_data(as_text=True))

        self.register()
        self.client.get('/login', **self.base)
        answer, csrf = self.get_captcha_and_csrf()
        failed_login = self.client.post('/login', data={
            'username':'RoadUser', 'password':'Wrong12345', 'captcha':answer,
            'csrf_token':csrf,
        }, follow_redirects=True, **self.base)
        self.assertIn('账号或密码错误', failed_login.get_data(as_text=True))
        self.assertIn('auth-alert-error', failed_login.get_data(as_text=True))

    def test_login_success_opens_dashboard_with_success_notice(self):
        self.register()
        self.client.get('/login', **self.base)
        answer, csrf = self.get_captcha_and_csrf()
        response = self.client.post('/login', data={
            'username':'RoadUser', 'password':'Pass12345', 'captcha':answer,
            'csrf_token':csrf,
        }, follow_redirects=True, **self.base)
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('道路巡检总览', page)
        self.assertIn('登录成功', page)
        self.assertIn('workspace-notice-success', page)

    def test_register_login_captcha_single_use_and_logout(self):
        response = self.register()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/login')
        self.assertEqual(self.client.get('/', **self.base).status_code, 302)
        self.assertEqual(self.client.get('/api/auth/me', **self.base).status_code, 401)
        # One-off challenge cannot be replayed.
        self.client.get('/login', **self.base)
        answer, csrf = self.get_captcha_and_csrf()
        ok = self.client.post('/login', data={
            'username':'RoadUser', 'password':'Pass12345', 'captcha':answer,
            'csrf_token':csrf}, **self.base)
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(self.client.get('/api/records', **self.base).status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me', **self.base).json['user']['username'], 'RoadUser')
        csrf = self.client.get('/api/auth/me', **self.base).json['csrf_token']
        self.assertEqual(self.client.post('/logout', data={'csrf_token':csrf}, **self.base).status_code, 302)
        self.assertEqual(self.client.get('/api/records', **self.base).status_code, 401)

    def test_photo_upload_requires_csrf_and_is_archived(self):
        self.register()
        self.client.get('/login', **self.base)
        answer, csrf = self.get_captcha_and_csrf()
        self.assertEqual(self.client.post('/login', data={
            'username':'RoadUser', 'password':'Pass12345', 'captcha':answer,
            'csrf_token':csrf,
        }, **self.base).status_code, 302)
        payload = {'photo':(io.BytesIO(IMAGE.read_bytes()), 'photo.jpg')}
        missing_csrf = self.client.post('/api/phone/inspect', data=payload, **self.base)
        self.assertEqual(missing_csrf.status_code, 403)
        csrf = self.client.get('/api/auth/me', **self.base).json['csrf_token']
        response = self.client.post('/api/phone/inspect', data={
            'photo':(io.BytesIO(IMAGE.read_bytes()), 'photo.jpg'),
            'latitude':'43.8468', 'longitude':'87.7311',
        }, headers={'X-CSRF-Token':csrf}, **self.base)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        result = response.json
        self.assertEqual(result['record']['level'], '中度')
        self.assertEqual(result['record']['source'], 'phone_photo')
        self.assertEqual(result['record']['detection_count'], 1)
        self.assertEqual(len(self.client.get('/api/phone/records', **self.base).json), 1)
        self.assertEqual(len(self.client.get('/api/records', **self.base).json), 1)
        image_response = self.client.get(result['result_image_url'], **self.base)
        self.assertEqual(image_response.status_code, 200)
        image_response.close()

    def test_phone_location_page_contains_photo_tool_at_bottom(self):
        for page in ('/phone',):
            html = self.client.get(page, **self.base).get_data(as_text=True)
            self.assertIn('id="sender-photo-section"', html)
            self.assertIn('id="sender-photo-submit"', html)
            self.assertGreater(html.index('id="sender-photo-section"'), html.index('03 / CONNECTION STATUS'))
            self.assertIn('js/mobile_photo.js', html)

    def test_edge_photo_requires_valid_device_token_and_archives(self):
        denied = self.client.post('/api/edge/phone/inspect', data={
            'photo': (io.BytesIO(IMAGE.read_bytes()), 'test.jpg'),
        }, **self.base)
        self.assertEqual(denied.status_code, 401)
        result = self.client.post('/api/edge/phone/inspect', data={
            'photo': (io.BytesIO(IMAGE.read_bytes()), 'test.jpg'),
            'latitude': '43.8', 'longitude': '87.7',
        }, headers={'X-RoadSense-Token': 'edge-test-token'}, **self.base)
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        payload = result.json
        self.assertIn('result_image_data_url', payload)
        self.assertTrue(payload['result_image_data_url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(base64.b64decode(payload['result_image_data_url'].split(',', 1)[1]), IMAGE.read_bytes())
        self.assertEqual(payload['record']['source'], 'phone_photo')
        self.assertIsNone(payload['record']['owner_id'])
        self.assertEqual(len(self.app.extensions['roadsense']['store'].all()), 1)

    def test_edge_photo_requires_configured_token(self):
        self.app.config['DEVICE_TOKEN'] = ''
        result = self.client.post('/api/edge/phone/inspect', data={
            'photo': (io.BytesIO(IMAGE.read_bytes()), 'test.jpg'),
        }, **self.base)
        self.assertEqual(result.status_code, 503)

    def test_edge_auto_preview_does_not_archive_until_confirmed(self):
        auth = {'X-RoadSense-Token':'edge-test-token'}
        photo = IMAGE.read_bytes()
        preview = self.client.post('/api/edge/phone/preview', data={
            'photo':(io.BytesIO(photo),'mobile.jpg'),
            'latitude':'43.8468','longitude':'87.7311',
        }, headers=auth, **self.base)
        self.assertEqual(preview.status_code, 200, preview.get_data(as_text=True))
        self.assertFalse(preview.json['archived'])
        self.assertEqual(preview.json['record']['level'], '中度')
        self.assertTrue(preview.json['result_image_data_url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(self.app.extensions['roadsense']['store'].all(), [])
        self.assertEqual(list(self.app.extensions['roadsense']['store'].image_dir.glob('*.jpg')), [])

        pending = preview.json['preview_id']
        saved = self.client.post('/api/edge/phone/archive', json={'preview_id':pending},
                                 headers=auth, **self.base)
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        self.assertTrue(saved.json['archived'])
        record = saved.json['record']
        self.assertRegex(record['image'], r'^中度_\d{8}_\d{6}_[0-9a-f]{8}\.jpg$')
        self.assertTrue((self.app.extensions['roadsense']['store'].image_dir / record['image']).is_file())
        self.assertTrue((self.app.extensions['roadsense']['store'].image_dir / record['original_image']).is_file())
        self.assertEqual(len(self.app.extensions['roadsense']['store'].all()), 1)
        duplicate = self.client.post('/api/edge/phone/archive', json={'preview_id':pending},
                                     headers=auth, **self.base)
        self.assertEqual(duplicate.status_code, 410)

    def test_preview_can_be_discarded_without_archiving(self):
        auth = {'X-RoadSense-Token':'edge-test-token'}
        denied = self.client.post('/api/edge/phone/preview', data={
            'photo':(io.BytesIO(IMAGE.read_bytes()),'photo.jpg')}, **self.base)
        self.assertEqual(denied.status_code, 401)
        pending = self.client.post('/api/edge/phone/preview', data={
            'photo':(io.BytesIO(IMAGE.read_bytes()),'photo.jpg')}, headers=auth, **self.base).json['preview_id']
        discarded = self.client.post('/api/edge/phone/discard', json={'preview_id':pending},
                                     headers=auth, **self.base)
        self.assertEqual(discarded.status_code, 200)
        self.assertEqual(self.app.extensions['roadsense']['store'].all(), [])
        self.assertEqual(self.client.post('/api/edge/phone/archive',
                         json={'preview_id':pending},headers=auth, **self.base).status_code,410)


if __name__ == '__main__':
    unittest.main()
