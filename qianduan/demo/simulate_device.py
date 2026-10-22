"""Standard-library-only edge simulation for local browser end-to-end tests.

Sample images in demo/ are user-supplied examples, not new AI detections.
"""
import argparse
import json
import math
import mimetypes
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
IMAGE = (BASE / 'sample_damage.jpg').read_bytes()


def post(url, path, fields=None, image=None, key='image', token=''):
    fields = fields or {}
    if image is None:
        body=json.dumps(fields).encode('utf-8')
        content='application/json'
    else:
        boundary = uuid.uuid4().hex
        parts = []
        for k,v in fields.items():
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="demo.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()+image+b'\r\n')
        parts.append((f'--{boundary}--\r\n').encode())
        body=b''.join(parts)
        content=f'multipart/form-data; boundary={boundary}'
    req=Request(url.rstrip('/') + path, data=body, headers={'Content-Type':content,**({'X-RoadSense-Token':token} if token else {})},method='POST')
    with urlopen(req,timeout=7) as resp:
        return resp.status, resp.read()[:100]


def get(url,path,token=''):
    headers={'X-RoadSense-Token':token} if token else {}
    with urlopen(Request(url.rstrip('/')+path,headers=headers),timeout=7) as resp:
        return json.load(resp)


def run():
    parser=argparse.ArgumentParser(description='RoadSense 本地模拟树莓派定位与损伤数据')
    parser.add_argument('--server',default='http://127.0.0.1:5000')
    parser.add_argument('--scenario',choices=['hardware','phone','both','failover','recover','damage'],default='both')
    parser.add_argument('--seconds',type=int,default=35,help='运行时间，默认35秒；failover/recover建议40秒')
    parser.add_argument('--records',type=int,default=3,help='最多生成多少条模拟损伤记录；需点击页面的开始巡检')
    parser.add_argument('--auto-start', action='store_true', help='自动发送 START 指令以快速测试记录')
    parser.add_argument('--token',default=os.environ.get('DEVICE_TOKEN',''))
    args=parser.parse_args()
    if args.seconds < 1 or args.records < 0: parser.error('seconds 必须大于0，records 不能为负数')
    print('模拟测试数据：并非真实的 GPS/YOLO 结果。打开 http://127.0.0.1:5000/detection 和 /gps')
    print(f'场景={args.scenario}，时长={args.seconds}s，损伤记录={args.records}。Ctrl+C 退出')
    try:
        print('服务器:',get(args.server,'/api/health'))
    except (URLError,HTTPError,ValueError) as exc:
        print('无法连接 Flask 服务：',exc,file=sys.stderr);return 1
    sent=0
    requested_start=False
    for i in range(args.seconds):
        lon=87.731124 + i*.00012
        lat=43.846764 + math.sin(i*.15)*.00018
        # Standalone phone test: browser sends /api/receive_location,
        # while physical simulator POSTs /update_stream heartbeat without GPS.
        hardware = args.scenario in ('hardware','both','damage')
        if args.scenario=='failover':hardware=i<8
        if args.scenario=='recover':hardware=i>=15
        phone = args.scenario in ('phone','both','failover','recover')
        try:
            if phone:
                post(args.server,'/api/receive_location',{'latitude':lat+.0005,'longitude':lon+.0005})
            # Heartbeat with image and hardware position if present
            post(args.server,'/update_stream',{
                'latitude':lat if hardware else '', 'longitude':lon if hardware else '',
                'location_source':'hardware' if hardware else 'none', 'device_kind':'simulator',
            },IMAGE,key='frame',token=args.token)
            if args.auto_start and not requested_start:
                print('首次定位/心跳上报后自动开始巡检:',post(args.server,'/api/control',{'command':'START'}))
                requested_start=True
            command=get(args.server,'/api/command',token=args.token)
            if command.get('command')=='RUNNING' and sent<args.records:
                post(args.server,'/upload',{
                    'latitude':lat if hardware else lat+.0005,
                    'longitude':lon if hardware else lon+.0005,
                    'severity':(sent%3)+1,'location_source':'hardware' if hardware else 'phone',
                    'session_id':command.get('session_id','SIM_LOCAL_TEST'), 'device_kind':'simulator'
                },IMAGE,key='image',token=args.token)
                sent+=1
            if i%3 == 0:
                p=get(args.server,'/api/location_status',token=args.token)
                print(f'[{i:02d}s] 硬件={hardware} 手机={phone} -> 当前定位: {p["source_label"]} · {p["longitude"]}, {p["latitude"]}')
        except (URLError,HTTPError,ValueError) as exc:
            print(f'第{i}秒发送失败：{exc}',file=sys.stderr)
        time.sleep(1)
    return 0


if __name__=='__main__':
    try:sys.exit(run())
    except KeyboardInterrupt:print('\n已结束本地模拟。')
