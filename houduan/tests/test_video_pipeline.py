import sys
import threading
import unittest
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_pipeline import LatestJpeg, make_preview_server

class TestVideoPipeline(unittest.TestCase):
    def test_latest_replaces_old_no_backlog(self):
        hub = LatestJpeg()
        self.assertTrue(hub.put(b'\xff\xd8first'))
        self.assertTrue(hub.put(b'\xff\xd8latest'))
        seq,jpg,_ = hub.wait_after(-1, timeout=.01)
        self.assertEqual(seq, 2)
        self.assertEqual(jpg, b'\xff\xd8latest')
    def test_invalid_jpeg_cannot_overwrite(self):
        hub=LatestJpeg()
        self.assertFalse(hub.put(b'bad'))
        self.assertEqual(hub.info()['frames_encoded'], 0)
    def test_preview_endpoint_has_live_frame(self):
        hub=LatestJpeg()
        server=make_preview_server(hub,'127.0.0.1',0)
        t=threading.Thread(target=server.serve_forever,daemon=True)
        t.start()
        try:
            port=server.server_address[1]
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as response:
                self.assertIn(b'frames_encoded',response.read())
            hub.put(b'\xff\xd8test-image')
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/live.mjpg',timeout=2) as response:
                self.assertIn(b'multipart/x-mixed-replace',response.headers.get('Content-Type','').encode())
                self.assertEqual(response.readline().strip(), b'--frame')
                self.assertIn(b'image/jpeg',response.readline())
                self.assertIn(b'Content-Length:',response.readline())
        finally:
            server.shutdown()
            server.server_close()

if __name__ == '__main__': unittest.main()
