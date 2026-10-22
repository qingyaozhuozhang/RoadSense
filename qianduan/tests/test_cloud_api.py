"""Run with python -m unittest discover -s cloud_web/tests -p 'test_cloud_api.py'
Requires Flask installed from qianduan/requirements.txt.
"""
import io
import tempfile
import unittest
try:
    from server.app import create_app
except ModuleNotFoundError as exc:
    if exc.name != 'flask':
        raise
    create_app = None

@unittest.skipUnless(create_app is not None, 'Flask 尚未安装，需 pip install -r qianduan/requirements.txt')
class CloudAPI(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app({'TESTING':True,'DATA_DIR':self.tmp.name})
        self.client=self.app.test_client()
    def tearDown(self): self.tmp.cleanup()
    def test_pages_legacy_routes_and_health(self):
        for route in ('/','/map-demo','/detection','/severe','/gps','/location','/phone','/api/status','/api/command','/api/records','/api/health','/api/location_status','/api/video_status'):
            self.assertEqual(self.client.get(route).status_code,200,route)
    def test_device_control(self):
        self.assertEqual(self.client.post('/api/control',json={'command':'FAIL'}).status_code,400)
        self.assertEqual(self.client.post('/api/control',json={'command':'START'}).status_code,409)
        self.client.post('/api/receive_location',json={'latitude':43.8467,'longitude':87.7311})
        start=self.client.post('/api/control',json={'command':'START'}).json
        self.assertEqual(start['command'],'RUNNING')
        self.assertTrue(start['session_id'].startswith('Task_'))
        self.assertEqual(self.client.get('/api/command').json['command'],'RUNNING')
    def test_location_arbitration(self):
        self.client.post('/api/receive_location',json={'latitude':43.8467,'longitude':87.7311})
        self.assertEqual(self.client.get('/api/location_status').json['source'],'phone')
        self.client.post('/update_stream',data={'latitude':'43.8468','longitude':'87.7312','location_source':'hardware'})
        self.assertEqual(self.client.get('/api/location_status').json['source'],'hardware')
        self.assertTrue(self.client.get('/api/status').json['online'])
        self.assertTrue(self.client.get('/api/phone_location').json['available'])
    def test_record_persistence_and_file(self):
        payload={'latitude':'43.846764','longitude':'87.731124','severity':'3','session_id':'TEST','location_source':'hardware'}
        example=b'\xff\xd8\xff\xd9'
        response=self.client.post('/upload',data={**payload,'image':(io.BytesIO(example),'test.jpg')})
        self.assertEqual(response.status_code,200,response.data)
        rows=self.client.get('/api/records').json
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['level'],'严重')
        self.assertEqual(self.client.get('/images/'+rows[0]['image']).status_code,200)
    def test_video_frame_only_when_real_frame_arrives(self):
        self.assertFalse(self.client.get('/api/video_status').json['available'])
        self.client.post('/update_stream', data={'latitude':'43.8467','longitude':'87.7311'})
        self.assertFalse(self.client.get('/api/video_status').json['available'])
        self.assertEqual(self.client.get('/api/latest_frame').status_code,204)
        self.client.post('/update_stream',data={'latitude':'43.8467','longitude':'87.7311','frame':(io.BytesIO(b'\xff\xd8\xff\xd9'),'frame.jpg')})
        self.assertTrue(self.client.get('/api/video_status').json['available'])
        self.assertEqual(self.client.get('/api/latest_frame').status_code,200)
    def test_phone_receiver_no_browser_location(self):
        page=self.client.get('/location').data.decode()
        self.assertNotIn('gps.js',page)
        self.assertNotIn('navigator.geolocation',page)
        self.assertIn('phone_receiver.js',page)
        mobile=self.client.get('/phone').data.decode()
        self.assertIn('phone_sender.js',mobile)
    def test_qr_and_mobile_links(self):
        # Insecure HTTP is not advertised as a valid GPS entry; no USB route.
        data=self.client.get('/api/mobile_access',headers={'Host':'192.168.1.8:5000'}).json
        self.assertIsNone(data['entry_url'])
        self.assertEqual(self.client.get('/api/mobile_qr').status_code,409)
        self.client.post('/api/report_pi_url',json={'url':'https://192.168.1.26:8080/phone'})
        self.client.post('/update_stream',data={'camera_signal':'missing'})
        data=self.client.get('/api/mobile_access').json
        self.assertEqual(data['entry_url'],'https://192.168.1.26:8080/phone')
        self.assertEqual(data['entry_kind'],'pi')
        response=self.client.get('/api/mobile_qr')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'image/png')
        self.assertTrue(response.data.startswith(b'\x89PNG'))
    def test_amap_map_metadata(self):
        response=self.client.get('/api/map_provider')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json['needs_key'])
        self.assertIn('WGS84',response.json['coordinate_system'])
        self.assertEqual(self.client.get('/map_feed').status_code,410)
        self.assertEqual(self.client.get('/api/map_position').status_code,200)
    def test_no_phone_ttl_refresh_via_edge_heartbeat(self):
        self.client.post('/api/receive_location',json={'latitude':43.8467,'longitude':87.7311})
        original=self.client.get('/api/location_status').json['phone']['age_seconds']
        import time
        time.sleep(0.25)
        self.client.post('/update_stream',data={'latitude':'43.8467','longitude':'87.7311','location_source':'phone'})
        current=self.client.get('/api/location_status').json['phone']['age_seconds']
        self.assertGreaterEqual(current,original+0.15)
    def test_bad_upload(self):
        self.assertEqual(self.client.post('/upload',data={'severity':'abc'}).status_code,400)
        self.assertEqual(self.client.post('/upload',data={'severity':'4'}).status_code,400)
        self.assertEqual(self.client.post('/api/receive_location',json={'latitude':91,'longitude':1}).status_code,400)
if __name__=='__main__': unittest.main()
