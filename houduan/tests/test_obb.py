import sys
from pathlib import Path
import unittest
import numpy as np
import cv2
SRC=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SRC))
from obb_inference import decode_predictions,draw_detections

class OBBEngineTests(unittest.TestCase):
    def test_rotated_30_degrees(self):
        raw=np.zeros((1,7,8),np.float32)
        raw[0,0,0]=320;raw[0,1,0]=240;raw[0,2,0]=110;raw[0,3,0]=44
        raw[0,4,0]=.90;raw[0,5,0]=.10;raw[0,6,0]=np.pi/6
        detected=decode_predictions(raw,2,True,1,(0,0),(480,640),.30,.5)
        self.assertEqual(len(detected),1)
        p=detected[0]['corners']
        self.assertEqual(p.shape,(4,2))
        self.assertGreater(abs(int(p[1,1])-int(p[0,1])),10)
        self.assertEqual(detected[0]['class_id'],0)
        drawn=draw_detections(np.zeros((480,640,3),dtype=np.uint8),detected,['Crack','Hole'])
        self.assertGreater(drawn.sum(),0)

    def test_two_obb_classes_and_nonclass_angle(self):
        raw=np.zeros((1,7,3),np.float32)
        raw[0,0,:]=[100,200,300]
        raw[0,1,:]=[100,200,300]
        raw[0,2,:]=40;raw[0,3,:]=20
        raw[0,4,:]=[.9,.1,.1]
        raw[0,5,:]=[.1,.8,.1]
        raw[0,6,:]=[2.5,2.5,100.0] # large angle is not a confidence score
        det=decode_predictions(raw,2,True,1,(0,0),(480,640),.3,.5)
        self.assertEqual(len(det),2)
        self.assertEqual(sorted(d['class_id'] for d in det),[0,1])

    def test_detect_non_rotated(self):
        raw=np.zeros((1,5,1),np.float32)
        raw[0,:,0]=[100,120,40,20,.9]
        det=decode_predictions(raw,1,False,1,(0,0),(480,640))
        self.assertEqual(len(det),1)
        self.assertAlmostEqual(float(det[0]['area']),800,delta=1)

    def test_letterbox_scale(self):
        # model x=350,y=300 and padding=(30,20) with scale=2
        raw=np.zeros((1,7,1),np.float32)
        raw[0,:,0]=[350,300,80,40,.8,.0,.2]
        det=decode_predictions(raw,2,True,2,(30,20),(480,640))
        self.assertEqual(len(det),1)
        p=det[0]['corners']
        self.assertLess(abs(float(p[:,0].mean())-160),2)
        self.assertLess(abs(float(p[:,1].mean())-140),2)

if __name__=='__main__':unittest.main()
