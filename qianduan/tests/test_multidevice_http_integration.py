"""HTTP authorization regression suite. Requires Flask + project dependencies.

Run on an installed environment:
  cd qianduan
  python -m unittest discover -s tests -p 'test_multidevice_http_integration.py' -v
"""
import io
import tempfile
import unittest

try:
    import flask
    from server.app import create_app
    HAVE_FLASK = True
except ImportError:
    HAVE_FLASK = False


@unittest.skipUnless(HAVE_FLASK, 'Flask dependencies are not installed here')
class RoleAndDeviceHttpTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'AUTH_ENABLED': True,
            'SKIP_ADMIN_BOOTSTRAP': True, 'DATA_DIR': self.folder.name,
            'SECRET_KEY': 'test-only-session-secret', 'DEVICE_TOKEN': ''})
        ctx = self.app.extensions['roadsense']
        self.users=ctx['accounts'];self.devices=ctx['devices'];self.records=ctx['store']
        self.admin,_=self.users.register('admin_test','SecurePass123')
        self.a,_=self.users.register('ordinary_alpha','SecurePass123')
        self.b,_=self.users.register('ordinary_bravo','SecurePass123')
        # Setting role is test-only; production has no public privilege upgrade.
        with self.users._connect() as db:
            db.execute('UPDATE users SET role=? WHERE id=?',('admin',self.admin['id']))
        self.t1=self.devices.create('device-001','Campus 1',self.a['id'])
        self.t2=self.devices.create('device-002','Campus 2',self.b['id'])
        self.client=self.app.test_client()

    def tearDown(self):self.folder.cleanup()

    def sign_in(self,user):
        with self.client.session_transaction() as session:
            session['user_id']=user['id']
            session['csrf_token']='test-csrf'

    def test_upload_device_identity_and_records_scope(self):
        for device_id,token in [('device-001',self.t1),('device-002',self.t2)]:
            response=self.client.post('/upload', data={'severity':'1'},headers={
                'X-RoadSense-Device-ID':device_id,'X-RoadSense-Token':token})
            self.assertEqual(response.status_code,200,response.get_json())
        self.sign_in(self.a)
        rows=self.client.get('/api/records').get_json()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['device_id'],'device-001')
        self.assertEqual(rows[0]['uploaded_by_username'],'ordinary_alpha')
        self.assertEqual(self.client.get('/api/records?device_id=device-002').status_code,403)
        self.assertEqual(self.client.get('/video_feed?device_id=device-002').status_code,403)
        self.sign_in(self.admin)
        self.assertEqual(len(self.client.get('/api/records').get_json()),2)

    def test_distinct_video_buckets(self):
        for did, token, frame in [('device-001',self.t1,b'\xff\xd8one'),('device-002',self.t2,b'\xff\xd8two')]:
            response=self.client.post('/update_stream',data={'frame':(io.BytesIO(frame),'frame.jpg'),
                'video_session':'a','video_seq':'1'},
                headers={'X-RoadSense-Device-ID':did,'X-RoadSense-Token':token})
            self.assertEqual(response.status_code,200)
        self.sign_in(self.admin)
        one=self.client.get('/api/latest_frame?device_id=device-001')
        two=self.client.get('/api/latest_frame?device_id=device-002')
        self.assertEqual(one.data,b'\xff\xd8one')
        self.assertEqual(two.data,b'\xff\xd8two')

    def test_reject_wrong_device_token(self):
        response=self.client.post('/upload',data={'severity':'1'},headers={
            'X-RoadSense-Device-ID':'device-001','X-RoadSense-Token':self.t2})
        self.assertEqual(response.status_code,401)
        self.assertEqual(self.client.post('/upload',data={'severity':'1'}).status_code,401)

    def test_only_admin_may_modify_records(self):
        r={'id':'ins-001','time':'2026-10-28 12:00:00','device_id':'device-001',
           'level':'中度','image':None,'owner_id':self.a['id']}
        self.records.add(r)
        self.sign_in(self.a)
        url='/api/admin/records/ins-001'
        self.assertEqual(self.client.patch(url,json={'notes':'hack'},headers={'X-CSRF-Token':'test-csrf'}).status_code,403)
        self.sign_in(self.admin)
        self.assertEqual(self.client.patch(url,json={'notes':'checked'},headers={'X-CSRF-Token':'test-csrf'}).status_code,200)
        self.assertEqual(self.records.get('ins-001')['notes'],'checked')
        self.assertEqual(self.client.delete(url,headers={'X-CSRF-Token':'test-csrf'}).status_code,200)
        self.assertIsNone(self.records.get('ins-001'))

if __name__=='__main__':unittest.main()
