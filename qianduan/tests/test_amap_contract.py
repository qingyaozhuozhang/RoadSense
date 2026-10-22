"""Amap map integration tests that run without network or installed Flask."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
EDGE=ROOT.parent/'houduan'

class AMapContract(unittest.TestCase):
    def test_amap_loader_and_coordinates(self):
        js=(ROOT/'frontend/static/js/live_map.js').read_text(encoding='utf-8')
        self.assertIn('webapi.amap.com/loader.js',js)
        self.assertIn("version:'2.0'",js)
        self.assertIn('RoadSenseWgs84ToGcj02',js)
        self.assertIn('setPoints',js)
        self.assertNotIn('maplibregl',js)
        self.assertNotIn('tiles.openfreemap.org',js)

    def test_security_code_never_embedded_in_html_or_js(self):
        text='\n'.join(p.read_text(encoding='utf-8') for p in (
            ROOT/'frontend/static/js/live_map.js',
            ROOT/'frontend/templates/base.html',
            ROOT/'frontend/templates/phone_sender.html'))
        self.assertNotIn('securityJsCode:',text)
        self.assertIn('serviceHost',text)
        proxy=(ROOT/'server/amap_proxy.py').read_text(encoding='utf-8')
        self.assertIn("os.environ.get('AMAP_JS_SECRET'",proxy)
        self.assertIn('https://restapi.amap.com/',proxy)
        self.assertIn('https://webapi.amap.com/',proxy)
        self.assertNotIn("'AMAP_JS_SECRET':",proxy)

    def test_archive_marker_only_for_real_records(self):
        source=(ROOT/'frontend/static/js/severe.js').read_text(encoding='utf-8')
        self.assertIn('map.setPoints(valid)',source)
        self.assertIn("$('archive-map-empty').hidden=yes",source)
        self.assertTrue((ROOT/'frontend/static/img/archive-aerial-preview.jpg').exists())

    def test_shared_pi_phone_ui(self):
        self.assertEqual((ROOT/'frontend/static/js/live_map.js').read_bytes(),
                         (EDGE/'phone_ui/static/js/live_map.js').read_bytes())
        self.assertTrue((EDGE/'amap_proxy.py').exists())
        self.assertIn('register_blueprint(amap_blueprint)',(EDGE/'core.py').read_text(encoding='utf-8'))

    def test_key_not_committed(self):
        env=(ROOT/'.env').read_text(encoding='utf-8')
        self.assertIn('AMAP_JS_KEY=',env)
        self.assertIn('AMAP_JS_SECRET=',env)
        self.assertFalse(any(v.strip() for k,v in [line.split('=',1) for line in env.splitlines() if line.startswith('AMAP_JS_')]))

if __name__=='__main__':
    unittest.main()
