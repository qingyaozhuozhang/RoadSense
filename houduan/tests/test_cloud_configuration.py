"""Lightweight checks for the complete RoadSense local-edge deployment package."""

import ast
import unittest
from pathlib import Path


EDGE_DIR = Path(__file__).resolve().parents[1]


class CloudConfigurationTests(unittest.TestCase):
    def test_edge_env_targets_existing_aliyun_port(self):
        settings = {}
        for line in (EDGE_DIR / '.env').read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                settings[key.strip()] = value.strip().strip('"').strip("'")
        self.assertEqual(settings.get('CLOUD_BASE_URL'), 'http://192.0.2.10:5000')

    def test_edge_reuses_new_cloud_api_endpoints(self):
        tree = ast.parse((EDGE_DIR / 'core.py').read_text(encoding='utf-8'))
        target_names = {
            'CLOUD_SERVER_URL', 'CLOUD_STREAM_URL', 'CLOUD_COMMAND_URL',
            'REPORT_URL', 'PHONE_RELAY_URL',
        }
        configured_names = {
            target.id for stmt in tree.body if isinstance(stmt, ast.Assign)
            for target in stmt.targets if isinstance(target, ast.Name)
        }
        self.assertTrue(target_names.issubset(configured_names))
        self.assertTrue((EDGE_DIR / 'best.onnx').is_file())
        self.assertGreater((EDGE_DIR / 'best.onnx').stat().st_size, 1_000_000)


if __name__ == '__main__':
    unittest.main()
