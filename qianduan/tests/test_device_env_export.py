"""Pure-Python tests for per-device downloadable full houduan/.env."""
from pathlib import Path
import sys
import unittest

SERVER_DIR = Path(__file__).resolve().parents[1] / 'server'
sys.path.insert(0, str(SERVER_DIR))
from device_env_export import render_houduan_env


class DownloadedDeviceEnvTest(unittest.TestCase):
    def test_download_has_all_original_houduan_env_fields(self):
        sample = render_houduan_env('pi-test-02', 'generated-unique-test-token',
                                    cloud_url='http://192.0.2.10:5000',
                                    amap_key='sample-amap-key', amap_secret='sample-amap-secret')
        expected_keys = {
            'CLOUD_BASE_URL', 'DEVICE_ID', 'DEVICE_TOKEN', 'AMAP_JS_KEY', 'AMAP_JS_SECRET',
            'CAMERA_INDEX', 'CAMERA_SCAN_INTERVAL', 'YOLO_MODEL', 'SAVE_DIR',
            'ORT_PROVIDER', 'ORT_THREADS', 'VIDEO_RENDER_FPS', 'VIDEO_UPLOAD_FPS',
            'VIDEO_UPLOAD_CONCURRENCY', 'VIDEO_JPEG_QUALITY', 'VIDEO_OUTPUT_WIDTH',
            'DIRECT_VIDEO_ENABLED', 'DIRECT_VIDEO_BIND', 'DIRECT_VIDEO_PORT',
            'GPS_PORT', 'GPS_BAUD', 'GPS_UPLOAD_INTERVAL', 'PHONE_POLL_INTERVAL', 'PI_LAN_HOST', 'DIRECT_VIDEO_PUBLIC_HOST',
            'PI_SSL_CERT_FILE', 'PI_SSL_KEY_FILE'
        }
        lines = {line.split('=', 1)[0]: line.split('=', 1)[1] for line in sample.splitlines()
                 if '=' in line and not line.startswith('#')}
        self.assertEqual(set(lines), expected_keys)
        self.assertEqual(lines['DEVICE_ID'], 'pi-test-02')
        self.assertEqual(lines['DEVICE_TOKEN'], 'generated-unique-test-token')
        self.assertEqual(lines['CLOUD_BASE_URL'], 'http://192.0.2.10:5000')
        self.assertEqual(lines['AMAP_JS_KEY'], 'sample-amap-key')
        self.assertEqual(lines['AMAP_JS_SECRET'], 'sample-amap-secret')
        self.assertEqual(lines['GPS_PORT'], '/dev/serial0')
        self.assertEqual(lines['GPS_BAUD'], '38400')
        self.assertEqual(lines['GPS_UPLOAD_INTERVAL'], '0.5')
        self.assertEqual(lines['PHONE_POLL_INTERVAL'], '0.5')
        self.assertEqual(lines['VIDEO_UPLOAD_CONCURRENCY'], '4')
        self.assertTrue(sample.endswith('\n'))

    def test_rejects_malformed_id(self):
        with self.assertRaises(ValueError):
            render_houduan_env('bad\nTOKEN=attacker', 'ok',
                                cloud_url='http://192.0.2.10:5000')

    def test_rejects_newline_in_field(self):
        with self.assertRaises(ValueError):
            render_houduan_env('pi-test-02', 'token\nINJECT=1',
                                cloud_url='http://192.0.2.10:5000')


if __name__ == '__main__':
    unittest.main()
