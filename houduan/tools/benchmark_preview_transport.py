"""Synthetic local-only MJPEG delivery benchmark, DOES NOT measure physical camera/AI FPS.

python tools/benchmark_preview_transport.py
"""
import sys
import threading
import time
from pathlib import Path
import cv2
import numpy as np
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_pipeline import LatestJpeg, make_preview_server


def run(seconds=4.0, expected_fps=25):
    hub = LatestJpeg()
    server = make_preview_server(hub,'127.0.0.1',0)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    start = time.monotonic()
    stop = threading.Event()
    def producer():
        n=0
        while not stop.is_set():
            tick=time.monotonic()
            img = np.full((360,640,3), (45, 100, 130),dtype=np.uint8)
            cv2.putText(img,f'{n}',(220,210),cv2.FONT_HERSHEY_SIMPLEX,2.0,(255,255,255),3)
            ok,jpeg = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 50])
            if ok: hub.put(jpeg.tobytes())
            n+=1
            target=start+n/expected_fps
            time.sleep(max(0,target-time.monotonic()))
    threading.Thread(target=producer,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_address[1]}/live.mjpg'
    count=0
    try:
        with requests.get(url,stream=True,timeout=(2, 6)) as response:
            response.raise_for_status()
            sock=response.raw
            while time.monotonic()-start<seconds:
                line=sock.readline()
                if not line or not line.startswith(b'--frame'):continue
                size=0
                while True:
                    line=sock.readline()
                    if line in (b'\r\n',b'\n',b''):break
                    if line.lower().startswith(b'content-length:'):
                        size=int(line.split(b':',1)[1].strip())
                if size:
                    frame=sock.read(size)
                    if len(frame)==size: count+=1
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
    duration=time.monotonic()-start
    print(f'合成测试：目标 {expected_fps} FPS，实际传送 {count} 帧/{duration:.1f}s ≈ {count/duration:.1f} FPS')
    print('说明：这是独立视频直连的传输能力测试；实际摄像头/YOLO FPS 需用真实硬件测量。')

if __name__=='__main__': run()
