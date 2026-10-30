"""End-to-end HTTP contract of the new RoadSense edge client and cloud server."""

import io
import tempfile
import unittest
from pathlib import Path

from server.app import create_app


IMAGE = Path(__file__).parent / 'fixtures' / 'legacy_damage.jpg'


class NewRoadSenseEdgeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.app = create_app({
            'TESTING': True,
            'DATA_DIR': self.data.name,
            'DEVICE_TOKEN': '',
            'CONTROL_TOKEN': '',
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.data.cleanup()

    def test_split_heartbeat_video_and_control(self):
        heartbeat = self.client.post('/update_stream', data={
            'camera_capture_fps': '25.0',
            'preview_render_fps': '24.0',
            'latitude': '43.846764',
            'longitude': '87.731124',
            'location_source': 'hardware',
            'camera_signal': 'connected',
            'camera_index': '2',
        })
        self.assertEqual(heartbeat.status_code, 200)
        frame = self.client.post('/update_stream', data={
            'camera_signal': 'connected',
            'camera_index': '2',
            'frame': (io.BytesIO(IMAGE.read_bytes()), 'live.jpg'),
        })
        self.assertEqual(frame.status_code, 200)

        video = self.client.get('/api/video_status').get_json()
        self.assertTrue(video['cloud_available'])
        self.assertEqual(video['camera_index'], 2)
        self.assertEqual(video['camera_capture_fps'], 25.0)
        self.assertEqual(video['preview_render_fps'], 24.0)
        self.assertEqual(self.client.get('/api/latest_frame').status_code, 200)

        self.assertEqual(self.client.post('/api/control', json={'command': 'START'}).status_code, 200)
        self.assertEqual(self.client.get('/api/command').get_json()['command'], 'RUNNING')

    def test_phone_relay_and_phone_link(self):
        fix = self.client.post('/api/receive_location', json={
            'latitude': 43.846764,
            'longitude': 87.731124,
            'accuracy': 8,
        })
        self.assertEqual(fix.status_code, 200)
        self.assertTrue(self.client.get('/api/phone_location').get_json()['available'])

        # The edge client reports its HTTPS /phone endpoint to the cloud.
        self.assertEqual(self.client.post('/update_stream', data={}).status_code, 200)
        self.assertEqual(self.client.post('/api/report_pi_url', json={
            'url': 'https://192.168.10.5:8080/phone',
        }).status_code, 200)
        self.assertEqual(
            self.client.get('/api/mobile_access').get_json()['entry_url'],
            'https://192.168.10.5:8080/phone',
        )


if __name__ == '__main__':
    unittest.main()
