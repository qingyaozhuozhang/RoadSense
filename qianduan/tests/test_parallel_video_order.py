"""Regression test for parallel edge JPEG transport (run with Flask deps installed)."""
import io
import tempfile
import unittest
from pathlib import Path
from server.app import create_app

JPEG = Path(__file__).parent / 'fixtures' / 'legacy_damage.jpg'

class ParallelVideoOrderTests(unittest.TestCase):
    def test_old_http_frame_does_not_override_newer_one(self):
        with tempfile.TemporaryDirectory() as data_dir:
            app = create_app({'TESTING':True, 'DATA_DIR':data_dir, 'DEVICE_TOKEN':''})
            client=app.test_client()
            image=JPEG.read_bytes()
            def send(session,seq):
                return client.post('/update_stream', data={
                    'video_session':session, 'video_seq':str(seq),
                    'frame':(io.BytesIO(image),'live.jpg')}, content_type='multipart/form-data')
            self.assertEqual(send('session-a',8).status_code,200)
            received=client.get('/api/video_status').get_json()['frames_received']
            self.assertEqual(send('session-a',7).status_code,200)
            status=client.get('/api/video_status').get_json()
            self.assertEqual(status['frames_received'],received)
            self.assertEqual(status['video_out_of_order_dropped'],1)
            self.assertEqual(send('session-a',9).status_code,200)
            self.assertEqual(client.get('/api/video_status').get_json()['frames_received'],received+1)
            self.assertEqual(send('session-b',0).status_code,200)
            self.assertEqual(client.get('/api/video_status').get_json()['frames_received'],received+2)

if __name__=='__main__':unittest.main()
