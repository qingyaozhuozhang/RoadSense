"""Edge/cloud integration contracts testable without hardware or Flask."""
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class EdgeContracts(unittest.TestCase):
    def test_damage_upload_is_not_disabled_without_amap(self):
        text=(ROOT/'houduan/core.py').read_text(encoding='utf-8')
        section=text.split('def run_map_loop()',1)[1].split('def run_detection_loop()',1)[0]
        self.assertNotIn('if not WEB_SERVICE_KEY:\n        return', section)
        self.assertIn('upload_to_cloud_async(',section)
        self.assertIn('base_map_img=np.full(',section)
    def test_camera_scan_and_mobile_https(self):
        text=(ROOT/'houduan/core.py').read_text(encoding='utf-8')
        self.assertIn('CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "2"))',text)
        self.assertIn('self.fallback = tuple(range(0, 3))',text)
        self.assertIn("@flask_app.route('/phone')",text)
        self.assertIn("/api/receive_location",text)
    def test_pi_mobile_assets_included(self):
        p=ROOT/'houduan/phone_ui'
        for item in ('templates/phone_sender.html','static/js/phone_sender.js','static/js/live_map.js','static/css/mobile_sender.css'):
            self.assertTrue((p/item).is_file(),item)
if __name__=='__main__': unittest.main()
