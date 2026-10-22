"""Read MJPEG multipart data and measure actual delivered frames per second.

Usage: python tools/video_fps_check.py --url http://127.0.0.1:8091/live.mjpg
       python tools/video_fps_check.py --url http://127.0.0.1:5000/video_feed
No camera is opened by this script; run core.py and Flask first.
"""
import argparse
import time
import requests


def measure(url, seconds):
    count=0
    started=time.monotonic()
    with requests.get(url,stream=True,timeout=(4, 12)) as response:
        response.raise_for_status()
        payload=response.raw
        while time.monotonic()-started < seconds:
            header=payload.readline()
            if not header:break
            if not header.startswith(b'--frame'):continue
            size=None
            while True:
                line=payload.readline()
                if not line or line in (b'\r\n',b'\n'):break
                if line.lower().startswith(b'content-length:'):
                    size=int(line.split(b':',1)[1].strip())
            if size is None:
                raise ValueError('流没有 Content-Length，无法可靠测 FPS')
            frame=payload.read(size)
            if len(frame)!=size:
                break
            if frame.startswith(b'\xff\xd8'):
                count+=1
    elapsed=time.monotonic()-started
    return count,elapsed,count/elapsed if elapsed else 0


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--url',default='http://127.0.0.1:8091/live.mjpg')
    p.add_argument('--seconds',type=float,default=8)
    args=p.parse_args()
    try:
        n,sec,fps=measure(args.url,args.seconds)
        print(f'视频地址: {args.url}\n采样 {sec:.1f}s，成功解码帧头 {n} 帧，实际接收 {fps:.1f} FPS')
        if fps < 12:print('诊断：不足12FPS，有明显跳帧风险。请比较直连端口8091与云端5000的数值。')
        elif fps < 20:print('诊断：12~20FPS，仍可能看到轻微跳动。建议使用直连并检查相机自身帧率。')
        else:print('诊断：20FPS以上，帧率较流畅；若仍顿挫，检查摄像头原始帧间隔与浏览器渲染。')
    except (requests.RequestException,ValueError,OSError) as e:
        print('视频流检测失败：',e)
        raise SystemExit(1)
