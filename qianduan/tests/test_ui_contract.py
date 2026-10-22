"""Source-level UI contracts, no Flask install required."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class UIContracts(unittest.TestCase):
    def test_phone_accuracy_is_private(self):
        for file in (ROOT/'frontend/templates/phone_sender.html', ROOT.parent/'houduan/phone_ui/templates/phone_sender.html'):
            if file.exists(): self.assertNotIn('sender-accuracy',file.read_text(encoding='utf-8'))
        for file in (ROOT/'frontend/static/js/phone_sender.js', ROOT.parent/'houduan/phone_ui/static/js/phone_sender.js'):
            if file.exists(): self.assertNotIn("$('sender-accuracy')",file.read_text(encoding='utf-8'))
    def test_mobile_isolation(self):
        receiver=(ROOT/'frontend/static/js/phone_receiver.js').read_text()
        sender=(ROOT/'frontend/static/js/phone_sender.js').read_text()
        self.assertNotIn('navigator.geolocation',receiver)
        self.assertIn('navigator.geolocation.watchPosition',sender)
        self.assertIn('/api/receive_location',sender)
    def test_dual_camera_placeholder_and_qr(self):
        self.assertTrue((ROOT/'frontend/static/img/dual-camera.svg').exists())
        self.assertIn('dual-camera.svg',(ROOT/'frontend/templates/detection.html').read_text())
        self.assertIn('/api/mobile_qr', (ROOT/'frontend/static/js/phone_receiver.js').read_text())
    def test_camera_and_amap(self):
        stream=(ROOT/'frontend/static/js/detection.js').read_text()
        server=(ROOT/'server/app.py').read_text()
        self.assertIn('/api/video_status',stream)
        self.assertIn('/video_feed',stream)
        self.assertIn('register_blueprint(amap_blueprint)',server)
    def test_empty_record_map_preview(self):
        self.assertTrue((ROOT/'frontend/static/img/archive-aerial-preview.jpg').exists())
        self.assertIn('archive-aerial-preview.jpg',(ROOT/'frontend/templates/severe.html').read_text())
        self.assertIn('archive-map-empty', (ROOT/'frontend/templates/severe.html').read_text())
        self.assertIn('map-container', (ROOT/'frontend/templates/severe.html').read_text())
if __name__=='__main__':unittest.main()
