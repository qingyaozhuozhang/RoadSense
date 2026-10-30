"""The cloud must not report an instantaneous FPS spike from concurrent POSTs."""
import unittest
from server.video_rate import RollingFrameRate

class MeterTests(unittest.TestCase):
    def test_parallel_http_burst_is_not_hundreds_of_fps(self):
        meter=RollingFrameRate()
        for i in range(20):
            meter.accept(100 + i*0.002)
        self.assertLessEqual(meter.rate(100.038,24.2,25),24.2)
    def test_real_20fps_window(self):
        meter=RollingFrameRate()
        for i in range(80):
            meter.accept(100+i*.05)
        self.assertGreaterEqual(meter.rate(103.95,25,25),19.0)
        self.assertLessEqual(meter.rate(103.95,25,25),21)
    def test_stale_stream_is_zero(self):
        meter=RollingFrameRate();meter.accept(100)
        self.assertEqual(meter.rate(104,30,30),0)
    def test_cap_by_source_fps_and_encoder_fps(self):
        meter=RollingFrameRate()
        for i in range(100):meter.accept(100+i*.01)
        self.assertLessEqual(meter.rate(100.99,12.1,20),12.1)
        self.assertLessEqual(meter.rate(100.99,30,18),18)
    def test_new_session_clears_history(self):
        meter=RollingFrameRate()
        for i in range(22):meter.accept(100+i*.1)
        meter.reset();self.assertEqual(meter.rate(103,30,30),0)

if __name__=='__main__':unittest.main()
