#!/usr/bin/env python3
"""Local CPU/GPU ONNX OBB benchmark; does not start Flask or access a camera."""
import argparse,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from core import YOLO_ONNX_Engine

ap=argparse.ArgumentParser(description='Check model OBB task and inference latency')
ap.add_argument('--model',default=str(Path(__file__).resolve().parents[1]/'best.onnx'))
ap.add_argument('--loops',type=int,default=20)
args=ap.parse_args()
engine=YOLO_ONNX_Engine(args.model)
frame=np.zeros((480,640,3),np.uint8)
frame[:]=(65,79,90)
for _ in range(2):engine.predict(frame)
samples=[]
for i in range(args.loops):
 start=time.perf_counter()
 engine.predict(frame)
 samples.append((time.perf_counter()-start)*1000)
print(f'模型: {args.model} | OBB: {engine.rotated} | 类别: {engine.classes}')
print(f'平均延迟: {np.mean(samples):.1f} ms, p95: {np.percentile(samples,95):.1f} ms, '
      f'理论单线程上限: {1000/np.mean(samples):.1f} FPS')
print('网页画面另外由 VIDEO_UPLOAD_FPS 控制，帧率可能受摄像头、编码与网络影响。')
