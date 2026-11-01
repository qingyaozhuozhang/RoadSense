import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('new_neo_f10n', ROOT/'rpi'/'neo_f10n_simple.py')
neo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(neo)


def nmea(payload):
    value = 0
    for ch in payload:
        value ^= ord(ch)
    return f'${payload}*{value:02X}'

class NeoF10NIntegrationTests(unittest.TestCase):
    def test_valid_rmc(self):
        line = nmea('GNRMC,104525.00,A,4350.7250,N,08743.9000,E,0.0,0.0,011126,,,A')
        result = neo.parse_rmc(line)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result[0], 43 + 50.7250/60, places=7)
        self.assertAlmostEqual(result[1], 87 + 43.9000/60, places=7)

    def test_bad_checksum_rejected(self):
        line = nmea('GNRMC,104525.00,A,4350.7250,N,08743.9000,E,0.0,0.0,011126,,,A')
        self.assertIsNone(neo.parse_rmc(line[:-2] + '00'))

    def test_invalid_fix_rejected(self):
        self.assertIsNone(neo.parse_rmc(nmea('GNRMC,104525.00,V,4350.7250,N,08743.9000,E,0,0,011126,,,N')))

    def test_old_parser_removed_and_cadence_configured(self):
        self.assertFalse((ROOT/'rpi'/'gps_rmc_reader.py').exists())
        text = (ROOT/'core.py').read_text(encoding='utf-8')
        self.assertIn('from rpi.neo_f10n_simple import parse_rmc', text)
        self.assertIn('GPS_UPLOAD_INTERVAL', text)
        self.assertIn('time.sleep(PHONE_POLL_INTERVAL)', text)
        env = (ROOT/'.env').read_text(encoding='utf-8')
        self.assertIn('GPS_UPLOAD_INTERVAL=0.5', env)
        self.assertIn('PHONE_POLL_INTERVAL=0.5', env)

if __name__ == '__main__': unittest.main()
