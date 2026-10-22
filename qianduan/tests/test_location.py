import unittest
from server.location_state import PositionManager, valid_coordinates
from server.coordinate_utils import wgs84_to_gcj02

class LocationTests(unittest.TestCase):
    def test_gps_priority_failover_and_recover(self):
        t=[100.]
        p=PositionManager(clock=lambda:t[0])
        p.submit(43.85,87.73,'phone')
        self.assertEqual(p.snapshot()['source'],'phone')
        p.submit(43.86,87.74,'hardware')
        self.assertEqual(p.snapshot()['source'],'hardware')
        t[0]+=6
        self.assertEqual(p.snapshot()['source'],'phone')
        p.submit(43.87,87.75,'hardware')
        self.assertEqual(p.snapshot()['source'],'hardware')
        t[0]+=31
        self.assertEqual(p.snapshot()['source'],'none')
    def test_invalid_coordinates(self):
        for lat,lon in [('',87),(91,60),('NaN',10),(0,0),('abc',None)]:
            self.assertIsNone(valid_coordinates(lat,lon))
        self.assertEqual(valid_coordinates(0,88),(0.,88.))
    def test_wgs84_keeps_world_coords_outside_china(self):
        self.assertEqual(wgs84_to_gcj02(103.851959,1.290270),(103.851959,1.290270))
        self.assertNotEqual(wgs84_to_gcj02(87.73,43.84),(87.73,43.84))
if __name__=='__main__': unittest.main()
