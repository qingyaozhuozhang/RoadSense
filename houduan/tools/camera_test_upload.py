"""No YOLO needed: upload webcam frames to Flask to test /update_stream and UI.
Run from houduan: python tools/camera_test_upload.py --server http://127.0.0.1:5000 --seconds 40
"""
import argparse
import os
import time
import cv2
import requests


def main():
    cli=argparse.ArgumentParser(description='RoadSense 纯摄像头视频上报联调')
    cli.add_argument('--server',default=os.environ.get('CLOUD_BASE_URL','http://127.0.0.1:5000'))
    cli.add_argument('--camera',type=int,default=int(os.environ.get('CAMERA_INDEX','2')))
    cli.add_argument('--seconds',type=int,default=45)
    args=cli.parse_args()
    cap=None;selected=None
    for idx in dict.fromkeys([args.camera,0,1,2]):
        print(f'[摄像头测试] 正在尝试设备序号 {idx} ...',flush=True)
        for backend in (cv2.CAP_V4L2,cv2.CAP_ANY):
            trial=cv2.VideoCapture(idx,backend)
            if not trial.isOpened():trial.release();continue
            ok,img=trial.read()
            if ok and img is not None and img.size:
                cap=trial;selected=idx;break
            trial.release()
        if cap is not None:break
    if cap is None:
        print('ERROR: 已检查 CAMERA_INDEX 优先项及 0、1、2，未发现可读摄像头；请检查 /dev/video*、权限及占用。')
        return 2
    print('摄像头已打开：',selected,'; 云端:',args.server)
    print('打开 /detection 查看视频。无定位时监控页会显示定位检查弹窗，请先注入手机/GPS坐标。')
    start=time.monotonic();count=0
    headers={}
    if os.environ.get('DEVICE_TOKEN'):
        headers['X-RoadSense-Token']=os.environ['DEVICE_TOKEN']
    try:
        while time.monotonic()-start < args.seconds:
            ok,frame=cap.read()
            if not ok or frame is None:
                print('[摄像头] 读取失败');time.sleep(0.5);continue
            cv2.putText(frame,f'Camera test / frame {count}',(20,40),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,255,180),2)
            ok,buf=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,65])
            if not ok: print('JPEG encode failed');continue
            try:
                resp=requests.post(args.server.rstrip('/')+'/update_stream',files={'frame':('camera.jpg',buf.tobytes(),'image/jpeg')},data={'location_source':'none','camera_index':str(selected),'camera_signal':'ok','frame_brightness':str(round(float(cv2.mean(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY))[0]),1))},headers=headers,timeout=5)
                print(f'[frame {count}] HTTP {resp.status_code} bytes {len(buf)}')
            except requests.RequestException as ex: print('上传失败：',str(ex)[:160])
            count+=1;time.sleep(0.7)
    except KeyboardInterrupt:pass
    finally:cap.release()
    return 0
if __name__=='__main__':raise SystemExit(main())
