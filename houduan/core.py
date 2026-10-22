"""
道路损伤识别与定位核心模块 (边缘端智能控制版 - 面积占比评估升级版)
特性: 状态机按需拉起推理 / 模型懒加载 / 异步心跳上报 / 基于面积占比的严重度判断
"""

import math
import os
import threading
import time
from typing import Optional, Tuple
from location_state import PositionManager

import cv2
import requests
import numpy as np
import onnxruntime as ort
import ast
from obb_inference import decode_predictions, draw_detections
from video_pipeline import LatestJpeg, make_preview_server

try:
    import serial
    from rpi.gps_rmc_reader import parse_rmc_from_line

    RPI_GPS_AVAILABLE = True
except ImportError:
    RPI_GPS_AVAILABLE = False
    serial = None
    parse_rmc_from_line = None

# ==========================================
# ⚙️ 第一部分：全局配置区
# ==========================================

# Optional Pi-specific .env. Prefer exported environment variables when present.
from pathlib import Path as _ConfigPath
_pi_env = _ConfigPath(__file__).resolve().parent / '.env'
if _pi_env.exists():
    for _line in _pi_env.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            if _k.strip() in {'AMAP_JS_KEY','AMAP_JS_SECRET','CLOUD_BASE_URL','PI_LAN_HOST'}:
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Default to local server: never accidentally upload test data to production.
CLOUD_BASE_URL = os.environ.get('CLOUD_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')
CLOUD_SERVER_URL = f"{CLOUD_BASE_URL}/upload"
CLOUD_STREAM_URL = f"{CLOUD_BASE_URL}/update_stream"
CLOUD_COMMAND_URL = f"{CLOUD_BASE_URL}/api/command"
REPORT_URL = f"{CLOUD_BASE_URL}/api/report_pi_url"
PHONE_RELAY_URL = f"{CLOUD_BASE_URL}/api/phone_location"
DEVICE_TOKEN = os.environ.get('DEVICE_TOKEN','')
HTTP_HEADERS = {'X-RoadSense-Token': DEVICE_TOKEN} if DEVICE_TOKEN else {}


HEARTBEAT_INTERVAL = 1.0
# Capture, browser preview and network upload have independent frame rates.
# A 20-25 FPS browser feed feels far smoother than the previous fixed 8 FPS.
VIDEO_RENDER_FPS = max(5.0, min(float(os.environ.get('VIDEO_RENDER_FPS', '25')), 40.0))
VIDEO_UPLOAD_FPS = max(1.0, min(float(os.environ.get('VIDEO_UPLOAD_FPS', '20')), 30.0))
VIDEO_JPEG_QUALITY = max(30, min(int(os.environ.get('VIDEO_JPEG_QUALITY', '50')), 85))
VIDEO_OUTPUT_WIDTH = max(320, min(int(os.environ.get('VIDEO_OUTPUT_WIDTH','640')), 1280))
# Direct preview is a *local-development* fast path; the cloud /video_feed remains available.
DIRECT_VIDEO_ENABLED = os.environ.get('DIRECT_VIDEO_ENABLED', '1').strip().lower() not in ('0','false','no')
DIRECT_VIDEO_BIND = os.environ.get('DIRECT_VIDEO_BIND','127.0.0.1').strip()
DIRECT_VIDEO_PORT = int(os.environ.get('DIRECT_VIDEO_PORT','8091'))
DIRECT_VIDEO_PUBLIC_HOST = os.environ.get('DIRECT_VIDEO_PUBLIC_HOST','').strip()


MAP_FILENAME = "map_result.png"
MAP_WIDTH, MAP_HEIGHT = 800, 600
ZOOM_LEVEL = 16
ZONE_RADIUS = 20

RPI_GPS_PORT = os.environ.get("GPS_PORT", "/dev/ttyUSB0")
RPI_GPS_BAUD = int(os.environ.get("GPS_BAUD", "9600"))
RPI_GPS_TIMEOUT = 1.0
CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "2"))
CAMERA_SCAN_INTERVAL = float(os.environ.get("CAMERA_SCAN_INTERVAL", "5"))
MODEL_PATH = os.environ.get("YOLO_MODEL", "best.onnx")
SAVE_DIR = os.environ.get("SAVE_DIR", "severe_cracks")

os.makedirs(SAVE_DIR, exist_ok=True)

THRES_MILD = 0.02
THRES_MODERATE = 0.1

# ==========================================
# 💾 第二部分：全局状态共享区
# ==========================================
lock = threading.Lock()

global_lon: Optional[float] = None
global_lat: Optional[float] = None
last_hardware_gps_time: float = 0.0
location_manager = PositionManager()

latest_frame: Optional[np.ndarray] = None
active_camera = None
latest_detections = []
latest_prediction_time = 0.0
latest_infer_ms = None
jpeg_hub = LatestJpeg()
current_camera_index: Optional[int] = None
current_camera_brightness: Optional[float] = None
current_camera_signal: str = "missing"
latest_detection_count: int = 0
latest_map: Optional[np.ndarray] = None

global_command = "STOP"
global_session_id = ""


# ==========================================
# 🛠️ 第三部分：基础工具与通信网络
# ==========================================

def set_location(latitude: float, longitude: float, source: str = "hardware") -> None:
    """Both sources cached; hardware <=5s old takes priority over phone <=30s."""
    global global_lon, global_lat, last_hardware_gps_time
    if location_manager.submit(latitude, longitude, source, origin='raspberry_pi'):
        chosen = location_manager.snapshot()
        with lock:
            global_lat, global_lon = chosen['latitude'], chosen['longitude']
            if source == 'hardware':
                last_hardware_gps_time = time.time()


def get_location() -> Tuple[Optional[float], Optional[float]]:
    snapshot = location_manager.snapshot()
    return snapshot['longitude'], snapshot['latitude']


def get_location_source() -> str:
    return location_manager.snapshot()['source']


def lonlat_to_pixel(target_lon, target_lat, center_lon, center_lat, zoom, width, height):
    n = 2.0 ** zoom
    cx = ((center_lon + 180.0) / 360.0) * n * 256.0
    lat_rad_c = math.radians(center_lat)
    cy = (1.0 - math.log(math.tan(lat_rad_c) + (1 / math.cos(lat_rad_c))) / math.pi) / 2.0 * n * 256.0
    tx = ((target_lon + 180.0) / 360.0) * n * 256.0
    lat_rad_t = math.radians(target_lat)
    ty = (1.0 - math.log(math.tan(lat_rad_t) + (1 / math.cos(lat_rad_t))) / math.pi) / 2.0 * n * 256.0
    return int(width / 2 + (tx - cx)), int(height / 2 + (ty - cy))


def upload_to_cloud_async(frame: np.ndarray, lon: float, lat: float, severity: int) -> None:
    def _upload():
        try:
            data = {'longitude': lon, 'latitude': lat, 'severity': severity, 'timestamp': time.time(),
                    'session_id': global_session_id, 'location_source': get_location_source()}
            if severity >= 2:
                success, encoded_image = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                if not success: return
                files = {'image': ('damage.jpg', encoded_image.tobytes(), 'image/jpeg')}
                requests.post(CLOUD_SERVER_URL, files=files, data=data, headers=HTTP_HEADERS, timeout=5)
            else:
                requests.post(CLOUD_SERVER_URL, data=data, headers=HTTP_HEADERS, timeout=5)
        except Exception:
            pass

    threading.Thread(target=_upload, daemon=True).start()


def cleanup_old_records(directory: str, max_files: int = 1000) -> None:
    try:
        files = [os.path.join(directory, f) for f in os.listdir(directory) if f.endswith('.jpg')]
        if len(files) > max_files:
            files.sort(key=os.path.getmtime)
            for i in range(len(files) - max_files + 10):
                if os.path.exists(files[i]): os.remove(files[i])
    except Exception:
        pass


# ==========================================
# 🤖 第四部分：硬件与 AI 引擎
# ==========================================

class CameraStream:
    """优先 CAMERA_INDEX (默认 2)，失败后依次巡检 0、1、2，并自动重试热插拔。"""

    def __init__(self, src=CAMERA_INDEX):
        self.preferred = int(src)
        # 无法使用指定设备时，从 0 到 2 巡检，不依赖设备枚举顺序。
        self.fallback = tuple(range(0, 3))
        self.cap = None
        self.active_index = None
        self.ret = False
        self.frame = None
        self.lock = threading.RLock()
        self.failures = 0
        self.camera_fps = 0.0
        self._last_capture_at = 0.0
        self._stop = threading.Event()
        self._next_scan_at = 0.0
        self._scan_for_device()
        threading.Thread(target=self.update, name='RoadSenseCamera', daemon=True).start()

    def _try_open(self, index):
        # MJPG often enables 30 FPS over USB where YUYV is limited to 5-10.
        # However, stereo cameras may only support raw YUYV: always retry with
        # unmodified default FourCC before deciding that a device is unusable.
        for backend in (cv2.CAP_V4L2, cv2.CAP_ANY):
            for prefer_mjpg in (True, False):
                cap = cv2.VideoCapture(index, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                if prefer_mjpg:
                    cap.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))
                cap.set(cv2.CAP_PROP_FPS, 30)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                for _ in range(7):
                    ok, img = cap.read()
                    if ok and img is not None and img.size > 0:
                        lum=float(np.mean(cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)))
                        if lum >= 6.0:
                            fourcc=int(cap.get(cv2.CAP_PROP_FOURCC))
                            codec=''.join(chr((fourcc >> (8*k)) & 0xff) for k in range(4))
                            fps=cap.get(cv2.CAP_PROP_FPS)
                            print(f'[摄像头] 已找到 {index}，编码={codec!r}，设备报告 {fps:.1f}FPS，'
                                  f'模式={"MJPEG优先" if prefer_mjpg else "默认"}',flush=True)
                            return cap, img
                    time.sleep(0.04)
                cap.release()
            print(f'[摄像头] 设备 {index} 后端={backend} 无有效帧，尝试其他后端/设备',flush=True)
        return None, None

    def _scan_for_device(self):
        # 优先2；失败再完整巡检0、1、2。日志中保留尝试过程。
        targets = list(dict.fromkeys([self.preferred] + list(self.fallback)))
        print(f'[摄像头] 优先 CAMERA_INDEX={self.preferred}；不可用则自动巡检 0 → 1 → 2', flush=True)
        for index in targets:
            print(f'[摄像头] 正在检测视频设备 {index} ...', flush=True)
            cap, img = self._try_open(index)
            if cap is not None:
                with self.lock:
                    self.cap = cap
                    self.active_index = index
                    self.ret = True
                    self.frame = img
                    self.failures = 0
                print(f'[摄像头] 已连接序列号 {index}，视频 {img.shape[1]}×{img.shape[0]}，准备上报到 /update_stream', flush=True)
                return True
        with self.lock:
            self.cap = None
            self.active_index = None
            self.ret = False
            self.frame = None
        self._next_scan_at = time.monotonic() + max(2.0, CAMERA_SCAN_INTERVAL)
        print(f'[摄像头] 未找到可读摄像头；{CAMERA_SCAN_INTERVAL:g} 秒后自动重新搜索，网页显示双目摄像头占位图。', flush=True)
        return False

    def update(self):
        while not self._stop.is_set():
            with self.lock:
                cap = self.cap
            if cap is None:
                if time.monotonic() >= self._next_scan_at:
                    self._scan_for_device()
                else:
                    time.sleep(0.3)
                continue
            ok, img = cap.read()
            if ok and img is not None and img.size > 0:
                captured = time.monotonic()
                with self.lock:
                    if self._last_capture_at:
                        dt = captured - self._last_capture_at
                        if dt > 0:
                            measured = min(120.0, 1.0/dt)
                            self.camera_fps = (measured if not self.camera_fps else
                                               0.8*self.camera_fps + 0.2*measured)
                    self._last_capture_at = captured
                    self.ret, self.frame, self.failures = True, img, 0
                continue
            with self.lock:
                self.ret, self.frame = False, None
                self.camera_fps = 0.0
                self.failures += 1
                failures = self.failures
            if failures >= 8:
                print(f'[摄像头] 设备 {self.active_index} 连续读取失败，释放并重新巡检 0~2。', flush=True)
                with self.lock:
                    if self.cap is cap:
                        self.cap = None
                        self.active_index = None
                cap.release()
                self._next_scan_at = time.monotonic()
            time.sleep(0.10)

    def read(self):
        with self.lock:
            if not self.ret or self.frame is None:
                return False, None
            return True, self.frame.copy()

    def status(self):
        with self.lock:
            return self.active_index, bool(self.ret and self.frame is not None)

    def close(self):
        self._stop.set()
        with self.lock:
            cap, self.cap = self.cap, None
        if cap is not None:
            cap.release()


class YOLO_ONNX_Engine:
    """ONNX YOLOv8 OBB and standard Detect; infer task from model metadata."""
    def __init__(self, onnx_model: str, confidence_thres: float = 0.3, iou_thres: float = 0.5):
        self.confidence_thres = confidence_thres
        self.iou_thres = iou_thres
        provider_choice = os.environ.get('ORT_PROVIDER','cpu').strip().lower()
        providers=['CPUExecutionProvider']
        if provider_choice == 'cuda' and 'CUDAExecutionProvider' in ort.get_available_providers():
            providers=['CUDAExecutionProvider','CPUExecutionProvider']
        opts=ort.SessionOptions()
        opts.log_severity_level=3
        threads=int(os.environ.get('ORT_THREADS',str(max(1,min(2,(os.cpu_count() or 2)-1)))))
        if threads>0:opts.intra_op_num_threads=threads
        opts.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            self.session=ort.InferenceSession(onnx_model,sess_options=opts,providers=providers)
        except Exception as exc:
            if providers[0] != 'CUDAExecutionProvider':raise
            print('[推理] GPU 初始化失败，回退 CPU:',exc,flush=True)
            self.session=ort.InferenceSession(onnx_model,sess_options=opts,providers=['CPUExecutionProvider'])
        md=self.session.get_modelmeta().custom_metadata_map or {}
        self.rotated=str(md.get('task','')).lower()=='obb'
        output_shape=self.session.get_outputs()[0].shape
        channel=output_shape[1] if len(output_shape)==3 else None
        if not isinstance(channel,int):
            raise ValueError(f'无法从输出 {output_shape} 获取通道，请检查 ONNX 模型')
        self.class_count=channel-4-int(self.rotated)
        if self.class_count<=0:raise ValueError(f'错误的模型通道: {output_shape}')
        try:
            names=ast.literal_eval(md.get('names','{}'))
            if isinstance(names,dict):self.classes=[str(names.get(i,names.get(str(i),i))) for i in range(self.class_count)]
            else:self.classes=[str(i) for i in range(self.class_count)]
        except (ValueError,SyntaxError,TypeError):
            self.classes=[str(i) for i in range(self.class_count)]
        shape=self.session.get_inputs()[0].shape
        self.input_height=int(shape[2]) if isinstance(shape[2],int) else 640
        self.input_width=int(shape[3]) if isinstance(shape[3],int) else 640
        self.model_inputs=self.session.get_inputs()
        self.last_detections=[]
        self.last_infer_ms=0
        print(f'[推理] 模型任务={"OBB旋转检测" if self.rotated else "普通检测"}，类别={self.classes}，'
              f'输出={output_shape}，执行后端={self.session.get_providers()}',flush=True)

    def preprocess(self, img):
        height,width=img.shape[:2]
        r=min(self.input_width/width,self.input_height/height)
        nw,nh=int(round(width*r)),int(round(height*r))
        # Correct width / height order and letterbox padding.
        resized=cv2.resize(img,(nw,nh),interpolation=cv2.INTER_LINEAR)
        dw=(self.input_width-nw)/2;dh=(self.input_height-nh)/2
        left,right=int(round(dw-.1)),int(round(dw+.1))
        top,bottom=int(round(dh-.1)),int(round(dh+.1))
        padded=cv2.copyMakeBorder(resized,top,bottom,left,right,cv2.BORDER_CONSTANT,value=(114,114,114))
        rgb=cv2.cvtColor(padded,cv2.COLOR_BGR2RGB)
        blob=np.ascontiguousarray(rgb.transpose(2,0,1)[None],dtype=np.float32)
        blob/=255.0
        return blob,r,(left,top)

    def predict(self,frame):
        start=time.perf_counter()
        blob,ratio,padding=self.preprocess(frame)
        raw=self.session.run(None,{self.model_inputs[0].name:blob})[0]
        detections=decode_predictions(raw,self.class_count,self.rotated,ratio,padding,frame.shape[:2],
                                     self.confidence_thres,self.iou_thres)
        self.last_detections=detections
        self.last_infer_ms=(time.perf_counter()-start)*1000
        annotated=draw_detections(frame,detections,self.classes)
        area=sum(d['area'] for d in detections)
        fraction=area/max(1,frame.shape[0]*frame.shape[1])
        level=0 if not detections else (1 if fraction<=THRES_MILD else 2 if fraction<=THRES_MODERATE else 3)
        name=['None','Mild','Moderate','Severe'][level]
        color=[(0,255,0),(0,255,255),(0,165,255),(0,0,255)][level]
        cv2.putText(annotated,f'Severity: {name} ({fraction:.2%})',
                    (20,40),cv2.FONT_HERSHEY_SIMPLEX,.75,color,2,cv2.LINE_AA)
        return annotated,level


# ==========================================
# 🔄 第五部分：核心后台工作线程
# ==========================================

def run_gps_reader() -> None:
    if not RPI_GPS_AVAILABLE: return
    try:
        ser = serial.Serial(RPI_GPS_PORT, RPI_GPS_BAUD, timeout=RPI_GPS_TIMEOUT)
        while True:
            line_bytes = ser.readline()
            if not line_bytes: continue
            fix = parse_rmc_from_line(line_bytes.decode("ascii", errors="ignore"))
            if fix and fix.status == "A" and fix.latitude_deg is not None and fix.longitude_deg is not None:
                set_location(fix.latitude_deg, fix.longitude_deg, source="hardware")
    except Exception as exc:
        print(f'[硬件GPS] 串口读取失败：{exc}。请检查 GPS_PORT={RPI_GPS_PORT}、权限、波特率和设备连接。',flush=True)


def run_command_polling() -> None:
    global global_command, global_session_id
    print("[边缘端] 监听网络指令线程已启动...")
    while True:
        try:
            resp = requests.get(CLOUD_COMMAND_URL, headers=HTTP_HEADERS, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                global_command = data.get("command", "STOP")
                global_session_id = data.get("session_id", "")
        except Exception:
            pass
        time.sleep(2.0)


def run_phone_location_polling() -> None:
    """Fetch phone GPS; repeat polling does not extend TTL after phone disconnects."""
    last_remote_fix_time = None
    while True:
        try:
            response = requests.get(PHONE_RELAY_URL, headers=HTTP_HEADERS, timeout=5)
            if response.ok:
                data = response.json()
                if data.get('available') and data.get('updated_at') != last_remote_fix_time:
                    last_remote_fix_time = data.get('updated_at')
                    set_location(data['latitude'], data['longitude'], source='phone')
        except (requests.RequestException, ValueError, KeyError):
            pass
        time.sleep(2)


def run_heartbeat_loop() -> None:
    """Device/location heartbeat only. Video is sent on a separate channel."""
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        lon,lat=get_location()
        with lock:
            camidx=current_camera_index
            cam=active_camera
            camsignal=current_camera_signal
            brightness=current_camera_brightness
        local_direct_host = DIRECT_VIDEO_PUBLIC_HOST or ('127.0.0.1' if DIRECT_VIDEO_BIND in ('127.0.0.1','localhost') else get_local_ip())
        direct_url = (f'http://{local_direct_host}:{DIRECT_VIDEO_PORT}/live.mjpg'
                      if DIRECT_VIDEO_ENABLED else '')
        capture_fps=0.0
        if cam is not None:
            with cam.lock: capture_fps=cam.camera_fps
        data={'camera_capture_fps':f'{capture_fps:.1f}',
              'direct_video_url':direct_url,
              'preview_render_fps':f"{jpeg_hub.info()['render_fps']:.1f}",
              'longitude':lon if lon is not None else '', 
              'latitude':lat if lat is not None else '',
              'location_source':get_location_source(),
              'camera_signal':camsignal,
              'camera_index':str(camidx) if camidx is not None else '',
              'frame_brightness':f'{brightness:.1f}' if brightness is not None else ''}
        try:
            resp=requests.post(CLOUD_STREAM_URL,data=data,headers=HTTP_HEADERS,timeout=3)
            if resp.status_code!=200:print('[心跳] HTTP',resp.status_code,flush=True)
        except requests.RequestException as exc:
            print('[心跳] 网络故障:',str(exc)[:120],flush=True)


def run_video_render_loop():
    """Fast lane: capture and render at ~25 FPS independently of AI and HTTP.

    Never wait for upload; even when the network hangs, direct browser preview
    continues updating. Draw the last recent OBB results on a fresh camera frame.
    """
    period = 1.0 / VIDEO_RENDER_FPS
    last_notice = 0.0
    next_tick = time.monotonic()
    while True:
        start = time.monotonic()
        with lock:
            cam = active_camera
            detections = list(latest_detections)
            detected_at = latest_prediction_time
            inference_ms = latest_infer_ms
            command = global_command
        ok, frame = cam.read() if cam is not None else (False, None)
        if ok and frame is not None:
            # Never freeze the display while waiting for expensive AI inference.
            # Only overlay detections while they are recent (and AI is running).
            if (command == 'RUNNING' and detections and detected_at
                and start - detected_at < 2.0):
                frame = draw_detections(frame, detections, getattr(cam, 'class_names', []))
            if inference_ms is not None and command == 'RUNNING':
                cv2.putText(frame, f'AI: {inference_ms:.0f}ms', (12,frame.shape[0]-15),
                            cv2.FONT_HERSHEY_SIMPLEX,.52,(52,225,136),2,cv2.LINE_AA)
            h, w = frame.shape[:2]
            if w > VIDEO_OUTPUT_WIDTH:
                new_size = (VIDEO_OUTPUT_WIDTH, round(h * VIDEO_OUTPUT_WIDTH / w))
                frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)
            ok_encode, jpeg = cv2.imencode('.jpg', frame,
                             [int(cv2.IMWRITE_JPEG_QUALITY), VIDEO_JPEG_QUALITY])
            if ok_encode:
                jpeg_hub.put(jpeg.tobytes())
        elif start-last_notice > 10:
            print('[视频] 摄像头暂无可用图像，等待自动重连。',flush=True)
            last_notice = start
        next_tick += period
        remaining = next_tick - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        else:
            # Frames are dropped, not queued: camera picture is always current.
            next_tick = time.monotonic()


def run_video_upload_loop():
    """Cloud fallback: always post latest encoded JPEG, never wait on inference.

    The browser may use the direct preview URL on a trusted LAN; if unavailable,
    the normal Flask MJPEG relay continues to work with no endpoint changes.
    """
    period = 1.0 / VIDEO_UPLOAD_FPS
    session = requests.Session()
    last_seq = -1
    last_log = 0.0
    next_send = time.monotonic()
    sent = 0
    last_report = time.monotonic()
    while True:
        seq, jpeg, jpeg_at = jpeg_hub.wait_after(last_seq, timeout=0.5)
        now = time.monotonic()
        if seq == last_seq or not jpeg or now - jpeg_at > 1.0:
            continue
        if now < next_send:
            # Wait for the newest frame instead of sending older queued frames.
            time.sleep(min(next_send - now, .025))
            continue
        last_seq = seq
        next_send = time.monotonic() + period
        with lock:
            camera_idx = current_camera_index
            camera_signal = current_camera_signal
        try:
            response = session.post(CLOUD_STREAM_URL,
                   files={'frame':('live.jpg', jpeg, 'image/jpeg')},
                   data={'camera_signal':camera_signal,
                         'camera_index':camera_idx if camera_idx is not None else ''},
                   headers=HTTP_HEADERS, timeout=(1.5, 2.0))
            if response.status_code == 200:
                sent += 1
            elif now-last_log > 5:
                print('[视频] HTTP',response.status_code,flush=True);last_log=now
        except requests.RequestException as exc:
            if now-last_log > 5:
                print('[视频] 网络上传失败（本地预览仍可连续显示）:',str(exc)[:100],flush=True)
                last_log=now
        if now-last_report > 8:
            metrics = jpeg_hub.info()
            print(f'[视频] 编码 {metrics["render_fps"]:.1f} FPS | 云端上传 {sent/(now-last_report):.1f} FPS '
                  f'| AI {latest_infer_ms or 0:.0f}ms',flush=True)
            sent = 0
            last_report = now


def run_direct_preview_server():
    if not DIRECT_VIDEO_ENABLED:
        return
    try:
        server = make_preview_server(jpeg_hub, DIRECT_VIDEO_BIND, DIRECT_VIDEO_PORT)
    except OSError as exc:
        print(f'[视频直连] 端口无法监听，将自动使用 Flask 视频：{exc}',flush=True)
        return
    print(f'[视频直连] MJPEG 已启动：http://{DIRECT_VIDEO_BIND}:{DIRECT_VIDEO_PORT}/live.mjpg',flush=True)
    server.serve_forever(poll_interval=.2)


def run_map_loop() -> None:
    global latest_map, global_lon, global_lat
    # Edge local coordinate canvas is just for spatial bucketing; visual map
    # is rendered in the browser using configured AMap JS API. No map fetching in this loop.
    while True:
        center_lon, center_lat = get_location()
        if center_lon is not None and center_lat is not None:
            break
        time.sleep(1)
    display_center_lon, display_center_lat = center_lon, center_lat
    base_map_img=np.full((MAP_HEIGHT,MAP_WIDTH,3), (232,239,245),dtype=np.uint8)
    zones = {}
    saved_severe_locations = set()

    while True:
        current_lon, current_lat = get_location()
        with lock:
            current_severity = latest_detection_count
            frame_to_save = latest_frame.copy() if latest_frame is not None else None

        if current_lon is not None and current_lat is not None and current_severity > 0:
            display_lon, display_lat = current_lon,current_lat
            px, py = lonlat_to_pixel(display_lon, display_lat, display_center_lon, display_center_lat, ZOOM_LEVEL, MAP_WIDTH,
                                     MAP_HEIGHT)
            if 0 <= px < MAP_WIDTH and 0 <= py < MAP_HEIGHT:
                my_zone = None
                min_dist = float("inf")
                for (cx, cy) in zones:
                    dist = math.hypot(cx - px, cy - py)
                    if dist <= ZONE_RADIUS and dist < min_dist:
                        min_dist, my_zone = dist, (cx, cy)

                if my_zone is None:
                    my_zone = (px, py)
                    zones[my_zone] = current_severity
                else:
                    zones[my_zone] = max(zones[my_zone], current_severity)

                severity = zones[my_zone]
                if severity >= 1:
                    if my_zone not in saved_severe_locations and frame_to_save is not None:
                        upload_to_cloud_async(frame_to_save, current_lon, current_lat, severity)
                        if severity >= 2:
                            save_path = os.path.join(SAVE_DIR,
                                                     f"severe_lon{current_lon:.6f}_lat{current_lat:.6f}_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
                            cv2.imwrite(save_path, frame_to_save)
                            cleanup_old_records(SAVE_DIR)
                        saved_severe_locations.add(my_zone)
                    if severity >= 3:
                        cv2.circle(base_map_img, my_zone, radius=12, color=(0, 0, 255), thickness=-1)
                    elif severity == 2:
                        cv2.circle(base_map_img, my_zone, radius=9, color=(0, 165, 255), thickness=-1)
                    else:
                        cv2.circle(base_map_img, my_zone, radius=6, color=(0, 255, 255), thickness=-1)

        with lock:
            latest_map = base_map_img.copy() if base_map_img is not None else None
        time.sleep(0.20)


def run_detection_loop() -> None:
    global latest_frame, latest_detection_count
    global current_camera_index, current_camera_brightness, current_camera_signal
    global active_camera,latest_detections,latest_prediction_time,latest_infer_ms
    cam = CameraStream()
    with lock: active_camera=cam
    model = None

    print("[边缘端] 硬件初始化完成，进入待命状态...")

    while True:
        success, frame = cam.read()
        index, connected = cam.status()
        if not success or frame is None:
            with lock:
                latest_frame = None
                latest_detection_count = 0
                current_camera_index = None
                current_camera_signal = 'missing'
                current_camera_brightness = None
            time.sleep(0.12)
            continue
        frame_brightness = float(np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
        camera_signal = 'dark' if frame_brightness < 6.0 else 'ok'
        if camera_signal == 'dark' and int(time.monotonic()) % 20 == 0:
            # 有效图像但过暗：告警，不将其错误判断为没有摄像头。
            pass

        if global_command == "RUNNING":
            if model is None:
                print("[系统] 接收到执行指令，正在向内存载入 YOLO 权重...")
                model = YOLO_ONNX_Engine(MODEL_PATH)

            annotated_frame, severity_level = model.predict(frame)
            with lock:
                latest_detections=list(model.last_detections)
                latest_prediction_time=time.monotonic()
                latest_infer_ms=model.last_infer_ms
                cam.class_names=model.classes

        else:
            with lock:
                latest_detections=[]
                latest_prediction_time=0.0
            annotated_frame = frame.copy()
            severity_level = 0

            status_text = "STANDBY (READY)" if global_command == "STOP" else "PAUSED"
            cv2.putText(annotated_frame, status_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2,
                        cv2.LINE_AA)
            time.sleep(0.05)

        with lock:
            latest_frame = annotated_frame
            latest_detection_count = severity_level
            current_camera_index = index
            current_camera_signal = camera_signal
            current_camera_brightness = frame_brightness


def get_local_ip() -> str:
    import socket, subprocess, ipaddress
    explicit=os.environ.get('PI_LAN_HOST','').strip()
    if explicit:return explicit
    try:
        lines=subprocess.check_output(['ip','-o','-4','addr','show'],stderr=subprocess.DEVNULL,text=True,timeout=2).splitlines()
        found=[]
        for line in lines:
            p=line.split()
            if len(p)<4 or p[2]!='inet':continue
            iface=p[1].split('@')[0]
            ip=p[3].split('/')[0]
            if iface.startswith(('docker','br-','veth','lo','tun','tap','wg','tailscale','zt','virbr')):continue
            try:
                if not ipaddress.ip_address(ip).is_private:continue
            except ValueError:continue
            weight=20 if iface.startswith(('wlan','wlp','eth','eno','enp','ens')) else 2
            weight+=12 if ip.startswith('192.168.') else 8 if ip.startswith('172.') else 1
            found.append((weight,ip))
        if found:return max(found)[1]
    except (OSError,subprocess.SubprocessError):pass
    # Last fallback may be VPN IP; explicitly set PI_LAN_HOST if phone cannot reach.
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        s.connect(('192.0.2.1',9))
        ip=s.getsockname()[0];s.close();return ip
    except OSError:return '127.0.0.1'

# ==========================================
# 🚀 第六部分：系统启动主入口
# ==========================================
if __name__ == "__main__":
    import logging

    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    local_ip = get_local_ip()
    if local_ip.startswith('127.'):
        print('[手机入口] 无法识别真实局域网 IP，请配置 PI_LAN_HOST=树莓派无线网卡IP',flush=True)
    pi_url = f"https://{local_ip}:8080/phone"
    print(f"[网络] 本机副驾驶定位网址为: {pi_url}")


    def report_url_to_cloud():
        while True:
            try:
                response=requests.post(REPORT_URL, json={"url": pi_url}, headers=HTTP_HEADERS, timeout=5)
                response.raise_for_status()
                print("[手机入口] 已上报树莓派 HTTPS 地址，电脑端可生成同网址二维码。", flush=True)
                time.sleep(10)
                continue
            except Exception as e:
                print(f"[网络] ⚠️ 汇报失败，原因: {e} \n(1秒后重试...)")
                time.sleep(5)


    threading.Thread(target=report_url_to_cloud, daemon=True).start()

    # 删除了一行重复的硬件线程启动代码
    threading.Thread(target=run_gps_reader, daemon=True).start()

    from flask import Flask, request, jsonify

    flask_app = Flask(__name__)
    from amap_proxy import amap_blueprint
    flask_app.register_blueprint(amap_blueprint)

    # Original phone node remains at https://PI_LAN_IP:8080/ ; share the polished UI
    # assets with the independent cloud /phone page, no separate JS implementation.
    from pathlib import Path
    from flask import render_template, send_from_directory, Response
    PHONE_UI_ROOT = Path(__file__).resolve().parent / 'phone_ui'
    flask_app.template_folder = str(PHONE_UI_ROOT / 'templates')
    flask_app.static_folder = str(PHONE_UI_ROOT / 'static')
    flask_app.static_url_path = '/static'

    @flask_app.route('/')
    @flask_app.route('/phone')
    def index():
        return render_template('phone_sender.html')

    @flask_app.route('/api/receive_location',methods=['POST'])
    def phone_gps():
        data=request.get_json(silent=True) or {}
        try:
            lat=float(data.get('latitude'))
            lon=float(data.get('longitude'))
            if not (math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180) or (lat==0 and lon==0):
                raise ValueError('坐标无效')
        except (TypeError,ValueError):
            return jsonify({'status':'error','message':'invalid coordinate'}),400
        try:
            r=requests.post(f'{CLOUD_BASE_URL}/api/receive_location',json=data,headers=HTTP_HEADERS,timeout=5)
            if not r.ok:
                return jsonify({'status':'error','message':'云端拒绝了定位上报：'+str(r.status_code)+' '+r.text[:100]}),502
            set_location(lat,lon,source='phone')
            result=r.json()
            return jsonify(result)
        except (requests.RequestException,ValueError) as exc:
            return jsonify({'status':'error','message':'位置未能上传至云端：'+str(exc)[:160]}),502

    # Use a trusted certificate when PI_SSL_CERT_FILE/PI_SSL_KEY_FILE are provided.
    pi_cert=os.environ.get('PI_SSL_CERT_FILE') or str(Path(__file__).resolve().parent/'certs/lan.crt')
    pi_key=os.environ.get('PI_SSL_KEY_FILE') or str(Path(__file__).resolve().parent/'certs/lan.key')
    if not (Path(pi_cert).is_file() and Path(pi_key).is_file()):
        pi_cert=pi_key=None
        print('[手机 HTTPS] 未找到树莓派持久证书，将使用临时自签名证书；手机定位可能因证书不受信任而被拒绝。建议先执行 tools/create_lan_cert.py',flush=True)
    pi_tls=(pi_cert,pi_key) if pi_cert and pi_key else 'adhoc'
    threading.Thread(
        target=lambda: flask_app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False, ssl_context=pi_tls),
        daemon=True).start()

    threading.Thread(target=run_command_polling, daemon=True).start()
    threading.Thread(target=run_phone_location_polling, daemon=True).start()
    threading.Thread(target=run_map_loop, daemon=True).start()
    threading.Thread(target=run_heartbeat_loop, daemon=True).start()
    threading.Thread(target=run_video_render_loop, name='RoadSenseVideoRender', daemon=True).start()
    threading.Thread(target=run_video_upload_loop, name='RoadSenseCloudUpload', daemon=True).start()
    threading.Thread(target=run_direct_preview_server, name='RoadSenseDirectMJPEG', daemon=True).start()

    try:
        run_detection_loop()
    except KeyboardInterrupt:
        pass
