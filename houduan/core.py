"""
道路损伤识别与定位核心模块 (边缘端智能控制版 - 面积占比评估升级版)
特性: 状态机按需拉起推理 / 模型懒加载 / 异步心跳上报 / 基于面积占比的严重度判断
"""

import math
import os
import threading
import time
import uuid
import concurrent.futures
from typing import Optional, Tuple
from location_state import PositionManager, valid_coordinates

import cv2
import requests
import numpy as np
import onnxruntime as ort
import ast
from obb_inference import decode_predictions, draw_detections, draw_severity_hud
from video_pipeline import LatestJpeg, make_preview_server
from stereo_distance import StereoDistanceEstimator, weighted_damage_ratio

try:
    import serial
    from rpi.neo_f10n_simple import parse_rmc

    RPI_GPS_AVAILABLE = True
except ImportError:
    RPI_GPS_AVAILABLE = False
    serial = None
    parse_rmc = None

# ==========================================
# ⚙️ 第一部分：全局配置区
# ==========================================

# Optional edge-device .env. Explicitly exported environment variables take priority.
from pathlib import Path as _ConfigPath
_pi_env = _ConfigPath(__file__).resolve().parent / '.env'
if _pi_env.exists():
    for _line in _pi_env.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            if _k.strip() in {
                'AMAP_JS_KEY', 'AMAP_JS_SECRET', 'CLOUD_BASE_URL', 'PI_LAN_HOST',
                'DEVICE_TOKEN', 'DEVICE_ID', 'CAMERA_INDEX', 'CAMERA_SCAN_INTERVAL',
                'GPS_PORT', 'GPS_BAUD', 'GPS_UPLOAD_INTERVAL', 'PHONE_POLL_INTERVAL', 'YOLO_MODEL', 'SAVE_DIR',
                'ORT_PROVIDER', 'ORT_THREADS', 'VIDEO_RENDER_FPS',
                'VIDEO_UPLOAD_FPS', 'VIDEO_UPLOAD_CONCURRENCY', 'VIDEO_JPEG_QUALITY', 'VIDEO_OUTPUT_WIDTH',
                'DIRECT_VIDEO_ENABLED', 'DIRECT_VIDEO_BIND',
                'DIRECT_VIDEO_PORT', 'DIRECT_VIDEO_PUBLIC_HOST',
                'PI_SSL_CERT_FILE', 'PI_SSL_KEY_FILE',
            }:
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Default to local server: never accidentally upload test data to production.
CLOUD_BASE_URL = os.environ.get('CLOUD_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')
CLOUD_SERVER_URL = f"{CLOUD_BASE_URL}/upload"
CLOUD_STREAM_URL = f"{CLOUD_BASE_URL}/update_stream"
CLOUD_COMMAND_URL = f"{CLOUD_BASE_URL}/api/command"
REPORT_URL = f"{CLOUD_BASE_URL}/api/report_pi_url"
PHONE_RELAY_URL = f"{CLOUD_BASE_URL}/api/phone_location"
DEVICE_TOKEN = os.environ.get('DEVICE_TOKEN','')
DEVICE_ID = os.environ.get('DEVICE_ID','pi-main').strip()
HTTP_HEADERS = {'X-RoadSense-Token': DEVICE_TOKEN, 'X-RoadSense-Device-ID': DEVICE_ID} if DEVICE_TOKEN else {}


# GPS/position state is uploaded via /update_stream on this interval.  This is
# the reporting interval, not a promise of GNSS sensor sample frequency.
HEARTBEAT_INTERVAL = max(0.2, min(float(os.environ.get('GPS_UPLOAD_INTERVAL', '0.5')), 5.0))
PHONE_POLL_INTERVAL = max(0.2, min(float(os.environ.get('PHONE_POLL_INTERVAL', '0.5')), 5.0))
# Capture, browser preview and network upload have independent frame rates.
# A 20-25 FPS browser feed feels far smoother than the previous fixed 8 FPS.
VIDEO_RENDER_FPS = max(5.0, min(float(os.environ.get('VIDEO_RENDER_FPS', '25')), 40.0))
VIDEO_UPLOAD_FPS = max(1.0, min(float(os.environ.get('VIDEO_UPLOAD_FPS', '20')), 30.0))
# Each HTTP POST takes one whole RTT. A small pool avoids the old 5FPS limit.
VIDEO_UPLOAD_CONCURRENCY = max(1, min(int(os.environ.get('VIDEO_UPLOAD_CONCURRENCY', '4')), 6))
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

RPI_GPS_PORT = os.environ.get("GPS_PORT", "/dev/serial0")
RPI_GPS_BAUD = int(os.environ.get("GPS_BAUD", "38400"))
RPI_GPS_TIMEOUT = 1.0
CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "0"))
CAMERA_SCAN_INTERVAL = float(os.environ.get("CAMERA_SCAN_INTERVAL", "5"))
CAMERA_MAX_INDEX = max(2, min(int(os.environ.get('CAMERA_MAX_INDEX', '5')), 20))
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
latest_damage_ratio = 0.0
latest_weighted_ratio = 0.0
latest_damage_level = 0
latest_distance_m = None
latest_distance_source = 'mono'
jpeg_hub = LatestJpeg()
current_camera_index: Optional[int] = None
current_camera_brightness: Optional[float] = None
current_camera_signal: str = "missing"
latest_detection_count: int = 0
latest_detection_event = None  # A fresh inference snapshot captured WITH a valid GPS fix.
latest_detection_reference = None
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


def upload_to_cloud_sync(frame: np.ndarray, lon: float, lat: float, severity: int,
                         session_id: str, location_source: str) -> bool:
    """Upload one geolocated damage frame. A failed upload is NEVER marked archived.

    This runs in the archive worker (not the camera or inference threads).
    """
    if valid_coordinates(lat, lon) is None or location_source not in ('hardware', 'phone'):
        return False
    try:
        success, encoded_image = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not success:
            print('[归档] JPEG 编码失败，未保存', flush=True)
            return False
        data = {'longitude': lon, 'latitude': lat, 'severity': severity,
                'timestamp': time.time(), 'session_id': session_id,
                'location_source': location_source}
        files = {'image': ('damage.jpg', encoded_image.tobytes(), 'image/jpeg')}
        response = requests.post(CLOUD_SERVER_URL, files=files, data=data,
                                 headers=HTTP_HEADERS, timeout=(2.5, 6.0))
        if not response.ok:
            print(f'[归档] 云端拒绝保存 HTTP {response.status_code}: {response.text[:180]}',flush=True)
            return False
        body = response.json()
        if body.get('status') != 'success':
            print(f'[归档] 云端返回失败：{str(body)[:180]}', flush=True)
            return False
        print(f'[归档] 已确认入库：损伤等级={severity}，定位来源={location_source}，'
              f'经纬度=({lat:.6f},{lon:.6f})', flush=True)
        return True
    except (requests.RequestException, ValueError) as exc:
        print(f'[归档] 网络或响应错误，未入库：{exc}',flush=True)
        return False

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
    """自动选 1280x480 双目；没有双目时保留普通单目及热插拔回退。"""

    def __init__(self, src=CAMERA_INDEX):
        self.preferred = int(src)
        self.fallback = tuple(range(CAMERA_MAX_INDEX + 1))
        self.cap = None
        self.active_index = None
        self.mode = 'missing'
        self.right_rectified = None
        self._pending_stereo = None
        self.ret = False
        self.frame = None
        self.lock = threading.RLock()
        self.failures = 0
        self.camera_fps = 0.0
        self.capture_sequence = 0
        self._last_capture_at = 0.0
        self._stop = threading.Event()
        self._next_scan_at = 0.0
        try:
            self.stereo = StereoDistanceEstimator()
        except (ValueError, cv2.error, OSError) as exc:
            self.stereo = None
            print(f'[双目] 标定/初始化失败，仅使用单目：{exc}', flush=True)
        self._scan_for_device()
        threading.Thread(target=self.update, name='RoadSenseCamera', daemon=True).start()
        threading.Thread(target=self._probe_stereo_loop, name='RoadSenseStereoHotplug', daemon=True).start()

    def _try_open(self, index, stereo_only=False):
        # Probe actual dimensions: cap.set() does not guarantee that the camera
        # accepted the requested format. Try MJPEG and the device's native mode.
        widths = (1280,) if stereo_only else (1280, 640)
        if self.stereo is None:
            widths = (640,)
        for requested_width in widths:
            for backend in (cv2.CAP_V4L2, cv2.CAP_ANY):
                for prefer_mjpg in (True, False):
                    cap = cv2.VideoCapture(index, backend)
                    if not cap.isOpened():
                        cap.release()
                        continue
                    if prefer_mjpg:
                        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, requested_width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    found = None
                    for _ in range(5):
                        ok, img = cap.read()
                        if ok and img is not None and img.size > 0 and img.ndim == 3:
                            h, w = img.shape[:2]
                            if h == 480 and w == 1280 and self.stereo is not None:
                                found = (cap, img, 'stereo')
                                break
                            if not stereo_only and requested_width == 640 and h > 0 and w > 0:
                                found = (cap, img, 'mono')
                                break
                        time.sleep(0.03)
                    if found:
                        print(f'[摄像头] 设备{index}：{found[2]} {found[1].shape[1]}×'
                              f'{found[1].shape[0]}，格式={"MJPG" if prefer_mjpg else "默认"}', flush=True)
                        return found
                    cap.release()
        return None, None, None

    def _prepare_capture(self, image, mode):
        if mode == 'stereo':
            return self.stereo.rectify_pair(image)
        return image, None

    def _scan_for_device(self):
        # The first open mono device is a fallback, not a reason to stop
        # looking for a calibrated stereo stream on another /dev/videoN.
        targets = list(dict.fromkeys([self.preferred] + list(self.fallback)))
        print(f'[摄像头] 巡检 {targets}，优先选择 1280x480 双目，单目备用', flush=True)
        chosen = None
        mono_fallback = None
        for index in targets:
            cap, img, mode = self._try_open(index)
            if cap is None:
                continue
            if mode == 'stereo':
                chosen = (cap, img, mode, index)
                break
            if mono_fallback is None:
                mono_fallback = (cap, img, mode, index)
            else:
                cap.release()
        if chosen is None:
            chosen, mono_fallback = mono_fallback, None
        if chosen is not None:
            cap, img, mode, index = chosen
            try:
                display, right = self._prepare_capture(img, mode)
            except (ValueError, cv2.error) as exc:
                print(f'[摄像头] 双目帧校正失败：{exc}；改用单目备用', flush=True)
                cap.release()
                if mono_fallback is None:
                    self._next_scan_at = time.monotonic() + max(2.0, CAMERA_SCAN_INTERVAL)
                    return False
                cap, img, mode, index = mono_fallback
                display, right = img, None
                mono_fallback = None
            if mono_fallback is not None:
                mono_fallback[0].release()
            with self.lock:
                self.cap, self.active_index, self.mode = cap, index, mode
                self.ret, self.frame, self.right_rectified = True, display, right
                self.capture_sequence += 1
                self.failures = 0
            print(f'[摄像头] 选中设备 {index}，模式={mode}，YOLO 使用左目/单目画面', flush=True)
            return True
        with self.lock:
            self.cap = None
            self.active_index = None
            self.mode = 'missing'
            self.right_rectified = None
            self.ret = False
            self.frame = None
        self._next_scan_at = time.monotonic() + max(2.0, CAMERA_SCAN_INTERVAL)
        print(f'[摄像头] 未找到摄像头；{CAMERA_SCAN_INTERVAL:g} 秒后重试', flush=True)
        return False

    def _probe_stereo_loop(self):
        """Check for a newly plugged stereo device without freezing mono FPS."""
        while not self._stop.wait(15.0):
            if self.stereo is None:
                continue
            with self.lock:
                mode, active_index = self.mode, self.active_index
                pending = self._pending_stereo is not None
            if mode != 'mono' or pending:
                continue
            for index in dict.fromkeys([self.preferred] + list(self.fallback)):
                if index == active_index or self._stop.is_set():
                    continue
                cap, img, found_mode = self._try_open(index, stereo_only=True)
                if cap is None:
                    continue
                with self.lock:
                    if self.mode == 'mono' and self._pending_stereo is None:
                        self._pending_stereo = (cap, img, index)
                        cap = None  # ownership transfers to capture thread
                if cap is not None:
                    cap.release()
                break

    def update(self):
        while not self._stop.is_set():
            with self.lock:
                pending, self._pending_stereo = self._pending_stereo, None
                if pending is not None and self.mode == 'mono':
                    old_cap = self.cap
                    new_cap, first_frame, new_index = pending
                    self.cap, self.active_index, self.mode = new_cap, new_index, 'stereo'
                    self.failures = 0
                    print(f'[摄像头] 发现新双目设备 {new_index}，自动切换到双目测距', flush=True)
                else:
                    old_cap = None
                    if pending is not None:
                        pending[0].release()
                cap = self.cap
                mode = self.mode
            if old_cap is not None:
                old_cap.release()
            if cap is None:
                if time.monotonic() >= self._next_scan_at:
                    self._scan_for_device()
                else:
                    time.sleep(0.3)
                continue
            ok, img = cap.read()
            if ok and img is not None and img.size > 0:
                try:
                    display, right = self._prepare_capture(img, mode)
                except (ValueError, cv2.error) as exc:
                    print(f'[摄像头] 图像校正失败：{exc}', flush=True)
                    ok = False
                else:
                    captured = time.monotonic()
                    with self.lock:
                        if self._last_capture_at:
                            dt = captured - self._last_capture_at
                            if dt > 0:
                                measured = min(120.0, 1.0/dt)
                                self.camera_fps = (measured if not self.camera_fps else
                                                   0.8*self.camera_fps + 0.2*measured)
                        self._last_capture_at = captured
                        self.ret, self.frame, self.right_rectified, self.failures = True, display, right, 0
                        self.capture_sequence += 1
                    continue
            with self.lock:
                self.ret, self.frame, self.right_rectified = False, None, None
                self.camera_fps = 0.0
                self.failures += 1
                failures = self.failures
            if failures >= 8:
                print(f'[摄像头] 设备 {self.active_index} 连续读取失败，重新选择双目或单目', flush=True)
                with self.lock:
                    if self.cap is cap:
                        self.cap = None
                        self.active_index = None
                        self.mode = 'missing'
                cap.release()
                self._next_scan_at = time.monotonic()
            time.sleep(0.10)

    def read(self):
        with self.lock:
            if not self.ret or self.frame is None:
                return False, None
            return True, self.frame.copy()

    def read_for_inference(self):
        """Return a synchronized rectified left frame and optional right eye."""
        with self.lock:
            if not self.ret or self.frame is None:
                return False, None, None, 'missing'
            right = None if self.right_rectified is None else self.right_rectified.copy()
            return True, self.frame.copy(), right, self.mode

    def read_with_sequence(self):
        """Use this in video encoder to avoid sending the same captured frame twice."""
        with self.lock:
            if not self.ret or self.frame is None:
                return False, None, self.capture_sequence
            return True, self.frame.copy(), self.capture_sequence

    def status(self):
        with self.lock:
            return self.active_index, bool(self.ret and self.frame is not None)

    def close(self):
        self._stop.set()
        with self.lock:
            cap, self.cap = self.cap, None
            pending, self._pending_stereo = self._pending_stereo, None
        if cap is not None:
            cap.release()
        if pending is not None:
            pending[0].release()


class YOLO_ONNX_Engine:
    """ONNX YOLOv8 OBB and standard Detect; infer task from model metadata."""
    def __init__(self, onnx_model: str, confidence_thres: float = 0.3, iou_thres: float = 0.5):
        self.confidence_thres = confidence_thres
        self.nms_max_candidates=max(40,min(400,int(os.environ.get('YOLO_NMS_MAX_CANDIDATES','160'))))
        self.iou_thres = iou_thres
        provider_choice = os.environ.get('ORT_PROVIDER','auto').strip().lower()
        available = ort.get_available_providers()
        providers=['CPUExecutionProvider']
        if provider_choice in ('auto','cuda') and 'CUDAExecutionProvider' in available:
            providers=['CUDAExecutionProvider','CPUExecutionProvider']
        elif provider_choice in ('auto','openvino') and 'OpenVINOExecutionProvider' in available:
            providers=['OpenVINOExecutionProvider','CPUExecutionProvider']
        elif provider_choice in ('cuda','openvino'):
            print(f'[推理] 请求的 {provider_choice} 加速器不可用，已回退 CPU；可用执行后端={available}', flush=True)
        opts=ort.SessionOptions()
        opts.log_severity_level=3
        threads=int(os.environ.get('ORT_THREADS',str(max(1,min(2,(os.cpu_count() or 2)-1)))))
        if threads>0:opts.intra_op_num_threads=threads
        opts.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            self.session=ort.InferenceSession(onnx_model,sess_options=opts,providers=providers)
        except Exception as exc:
            if providers[0] == 'CPUExecutionProvider': raise
            print('[推理] 硬件加速初始化失败，回退 CPU:',exc,flush=True)
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
        self.last_damage_ratio=0.0
        self.last_weighted_ratio=0.0
        self.last_distance_m=None
        self.last_distance_source='mono'
        self._last_stereo_error_notice=0.0
        self.last_infer_completed_at=0.0
        self.inference_counter=0
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

    def predict(self, frame, right_rectified=None, stereo_estimator=None):
        start=time.perf_counter()
        blob,ratio,padding=self.preprocess(frame)
        t0=time.perf_counter()
        raw=self.session.run(None,{self.model_inputs[0].name:blob})[0]
        t1=time.perf_counter()
        detections=decode_predictions(raw,self.class_count,self.rotated,ratio,padding,frame.shape[:2],
                                     self.confidence_thres,self.iou_thres,max_candidates=self.nms_max_candidates)
        t2=time.perf_counter()
        if detections and right_rectified is not None and stereo_estimator is not None:
            try:
                stereo_estimator.measure_detections(frame, right_rectified, detections)
            except (cv2.error, ValueError, FloatingPointError) as exc:
                if time.monotonic()-self._last_stereo_error_notice > 10:
                    print(f'[双目] 本帧深度计算失败，按原单目面积规则评估：{exc}', flush=True)
                    self._last_stereo_error_notice=time.monotonic()
                for item in detections:
                    item['distance_m']=None
        valid_distances=[d['distance_m'] for d in detections if d.get('distance_m') is not None]
        self.last_distance_m=(sum(valid_distances)/len(valid_distances)) if valid_distances else None
        self.last_distance_source=('stereo' if valid_distances else
                                   'stereo-invalid' if right_rectified is not None else 'mono')
        self.last_damage_ratio,self.last_weighted_ratio=weighted_damage_ratio(detections,frame.shape)
        fraction=self.last_weighted_ratio
        level=0 if not detections else (1 if fraction<=THRES_MILD else 2 if fraction<=THRES_MODERATE else 3)
        self.last_detections=detections
        self.last_infer_ms=(time.perf_counter()-start)*1000
        self.inference_counter += 1
        if self.inference_counter % 12 == 0:
            print(f'[AI耗时] 总计 {self.last_infer_ms:.0f}ms ≈ {1000/max(1,self.last_infer_ms):.1f}帧/秒 '
                  f'| 预处理 {(t0-start)*1000:.0f}ms '
                  f'| 模型运行 {(t1-t0)*1000:.0f}ms '
                  f'| OBB后处理 {(t2-t1)*1000:.0f}ms '
                  f'| 深度+评估 {(time.perf_counter()-t2)*1000:.0f}ms '
                  f'| 模式={self.last_distance_source} 面积={self.last_damage_ratio:.1%} '
                  f'加权={self.last_weighted_ratio:.1%} 距离={self.last_distance_m}m '
                  f'| 后端 {self.session.get_providers()[0]}', flush=True)
        self.last_infer_completed_at=time.monotonic()
        annotated=draw_detections(frame,detections,self.classes)
        annotated = draw_severity_hud(annotated, self.last_damage_ratio, level,
                                      self.last_weighted_ratio, self.last_distance_m,
                                      self.last_distance_source)
        return annotated,level


# ==========================================
# 🔄 第五部分：核心后台工作线程
# ==========================================

def run_gps_reader() -> None:
    """Read NEO-F10N UART using the user's strict NMEA-RMC parser.

    Retry disconnected UART automatically.  Only valid RMC fixes update the
    location manager; the cloud heartbeat publishes the latest *unexpired*
    position every GPS_UPLOAD_INTERVAL seconds.
    """
    if not RPI_GPS_AVAILABLE:
        print('[硬件GPS] 未安装 pyserial 或 NEO-F10N 驱动无法导入，手机备用定位仍可用', flush=True)
        return
    print(f'[硬件GPS] NEO-F10N: {RPI_GPS_PORT} / {RPI_GPS_BAUD}bps / 8N1', flush=True)
    last_fix_notice = 0.0
    last_wait_notice = time.monotonic()
    while True:
        try:
            with serial.Serial(RPI_GPS_PORT, RPI_GPS_BAUD, timeout=1,
                               bytesize=8, parity='N', stopbits=1) as receiver:
                print('[硬件GPS] 串口已连接，等待有效 RMC 定位', flush=True)
                while True:
                    position = parse_rmc(receiver.readline())
                    if position is not None:
                        latitude, longitude, utc_time = position
                        set_location(latitude, longitude, source='hardware')
                        now = time.monotonic()
                        if now - last_fix_notice >= 2.0:
                            print(f'[硬件GPS] WGS84 纬度={latitude:.7f} 经度={longitude:.7f} '
                                  f'GPS_UTC={utc_time}', flush=True)
                            last_fix_notice = now
                        last_wait_notice = now
                    elif time.monotonic() - last_wait_notice >= 10.0:
                        print('[硬件GPS] 暂无有效卫星定位；手机定位可作为备用', flush=True)
                        last_wait_notice = time.monotonic()
        except (serial.SerialException, OSError) as exc:
            print(f'[硬件GPS] 串口故障：{exc}；2 秒后重新连接', flush=True)
            time.sleep(2.0)


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
        time.sleep(PHONE_POLL_INTERVAL)


def run_heartbeat_loop() -> None:
    """Report latest selected WGS84 position every 0.5s (configurable).

    This is independent of the separate video upload channel.  If neither
    hardware nor phone has a fresh fix, coordinates are blank, never (0,0).
    """
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
    last_rendered_camera = None
    last_rendered_seq = -1
    next_tick = time.monotonic()
    while True:
        start = time.monotonic()
        with lock:
            cam = active_camera
            detections = list(latest_detections)
            detected_at = latest_prediction_time
            detection_ref = None if latest_detection_reference is None else latest_detection_reference.copy()
            inference_ms = latest_infer_ms
            damage_ratio = latest_damage_ratio
            weighted_ratio = latest_weighted_ratio
            damage_level = latest_damage_level
            distance_m = latest_distance_m
            distance_source = latest_distance_source
            command = global_command
        ok, frame, capture_seq = cam.read_with_sequence() if cam is not None else (False, None, -1)
        is_new_capture = ok and frame is not None and (
            cam is not last_rendered_camera or capture_seq != last_rendered_seq)
        if is_new_capture:
            last_rendered_camera, last_rendered_seq = cam, capture_seq
            # Never freeze the display while waiting for expensive AI inference.
            # Only overlay detections while they are recent (and AI is running).
            # The model can take ~900ms on CPU. Avoid painting its old OBBs onto a
            # substantially different *current* camera image (false localization).
            visually_same = True
            if detection_ref is not None:
                preview_gray=cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),(32,24),interpolation=cv2.INTER_AREA)
                visually_same = float(cv2.absdiff(preview_gray,detection_ref).mean()) <= 23.0
            if (command == 'RUNNING' and detections and detected_at
                and start - detected_at < 1.8 and visually_same):
                frame = draw_detections(frame, detections, getattr(cam, 'class_names', []))
            if command == 'RUNNING' and detected_at and start-detected_at < 1.8 and visually_same:
                frame = draw_severity_hud(frame, damage_ratio, damage_level,
                                          weighted_ratio, distance_m, distance_source)
            h, w = frame.shape[:2]
            if w > VIDEO_OUTPUT_WIDTH:
                new_size = (VIDEO_OUTPUT_WIDTH, round(h * VIDEO_OUTPUT_WIDTH / w))
                frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)
            ok_encode, jpeg = cv2.imencode('.jpg', frame,
                             [int(cv2.IMWRITE_JPEG_QUALITY), VIDEO_JPEG_QUALITY])
            if ok_encode:
                jpeg_hub.put(jpeg.tobytes())
        elif not ok and start-last_notice > 10:
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
    """Cloud MJPEG relay: bounded parallel latest-frame HTTP uploads.

    Synchronous single POST/frame previously limited receive rate to ~1/RTT
    (e.g. 180ms RTT -> 5.5 FPS). This pool caps inflight requests, sends only
    the latest frame at each tick and includes ordering metadata for the cloud.
    Frames are never queued indefinitely; if all workers are busy, skip a tick.
    """
    import requests
    period = 1.0 / VIDEO_UPLOAD_FPS
    upload_slots = threading.BoundedSemaphore(VIDEO_UPLOAD_CONCURRENCY)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=VIDEO_UPLOAD_CONCURRENCY,
                                                  thread_name_prefix='RoadSenseVideo')
    stream_session = uuid.uuid4().hex
    thread_local = threading.local()
    results_lock = threading.Lock()
    counters = {'ok': 0, 'fail': 0, 'skipped': 0, 'bytes': 0, 'last_report': time.monotonic()}
    last_error = [0.0]

    def send_one(seq, jpeg, camera_idx, camera_signal):
        try:
            session = getattr(thread_local, 'session', None)
            if session is None:
                session = requests.Session()
                thread_local.session = session
            response = session.post(CLOUD_STREAM_URL,
                 files={'frame':('live.jpg', jpeg, 'image/jpeg')},
                 data={'camera_signal':camera_signal,
                       'camera_index':camera_idx if camera_idx is not None else '',
                       'video_session': stream_session, 'video_seq':str(seq)},
                 headers=HTTP_HEADERS, timeout=(1.5, 3.0))
            with results_lock:
                if response.status_code == 200:
                    counters['ok'] += 1
                    counters['bytes'] += len(jpeg)
                else:
                    counters['fail'] += 1
                    if time.monotonic()-last_error[0] > 8:
                        print('[视频] HTTP', response.status_code, flush=True)
                        last_error[0] = time.monotonic()
        except requests.RequestException as exc:
            with results_lock:
                counters['fail'] += 1
                if time.monotonic()-last_error[0] > 8:
                    print('[视频] 云端视频上传失败:', str(exc)[:120], flush=True)
                    last_error[0] = time.monotonic()
        finally:
            upload_slots.release()

    print(f'[视频] 云端推送目标 {VIDEO_UPLOAD_FPS:.0f}FPS，并发上传 {VIDEO_UPLOAD_CONCURRENCY}，过期帧自动丢弃',flush=True)
    last_seq = -1
    next_send = time.monotonic()
    while True:
        now = time.monotonic()
        if now < next_send:
            time.sleep(min(next_send-now, .015))
            continue
        # Avoid backlog when the host cannot maintain the desired frame rate.
        next_send = max(next_send + period, now + period * .5)
        seq, jpeg, jpeg_at = jpeg_hub.wait_after(last_seq, timeout=.02)
        now = time.monotonic()
        if seq == last_seq or not jpeg or now - jpeg_at > .8:
            continue
        last_seq = seq
        if not upload_slots.acquire(blocking=False):
            with results_lock: counters['skipped'] += 1
            continue
        with lock:
            camera_idx = current_camera_index
            camera_signal = current_camera_signal
        try:
            pool.submit(send_one, seq, jpeg, camera_idx, camera_signal)
        except Exception:
            upload_slots.release()
            raise
        with results_lock:
            elapsed = now - counters['last_report']
            if elapsed >= 8:
                metrics = jpeg_hub.info()
                print(f'[视频] 编码 {metrics["render_fps"]:.1f}FPS | 云端请求 {counters["ok"]/elapsed:.1f}FPS '
                      f'| 失败 {counters["fail"]} | 跳帧 {counters["skipped"]} '
                      f'| 上行 {counters["bytes"]*8/elapsed/1e6:.2f}Mbps '
                      f'| AI {latest_infer_ms or 0:.0f}ms (~{1000/(latest_infer_ms or 1000):.1f} FPS)',flush=True)
                counters.update(ok=0,fail=0,skipped=0,bytes=0,last_report=now)


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


def _distance_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Haversine distance for suppression of repeat records at the same road spot."""
    radius = 6371000.0
    a1, a2 = math.radians(lat_a), math.radians(lat_b)
    dlat, dlon = a2-a1, math.radians(lon_b-lon_a)
    h = math.sin(dlat/2)**2 + math.cos(a1)*math.cos(a2)*math.sin(dlon/2)**2
    return 2*radius*math.asin(min(1.0, math.sqrt(h)))


def run_map_loop() -> None:
    """Archive ONLY new YOLO detections captured while a valid GPS fix existed.

    This thread is intentionally independent of capture/inference. Older detection
    results cannot be archived later merely because GPS became available.
    Records are de-duplicated within a single inspection session by 20 m location
    radius and damage severity. Failed uploads do not consume that zone.
    """
    global latest_map
    processed = None
    saved_places = []  # [(lat, lon, max_saved_severity), ...] within current session
    active_session = None
    last_attempt = 0.0
    while True:
        with lock:
            event = latest_detection_event
            command = global_command
            session_id = global_session_id
        if session_id != active_session or command != 'RUNNING':
            active_session, saved_places = session_id, []
            processed = None
        if command != 'RUNNING' or not session_id or not event:
            time.sleep(.2)
            continue
        seq = (event['session_id'],event['seq'])
        if seq == processed:
            time.sleep(.2)
            continue
        now = time.monotonic()
        if event['session_id'] != session_id or now-event['observed_at'] > 4.0:
            processed = seq
            continue
        lat, lon = event['latitude'], event['longitude']
        if valid_coordinates(lat,lon) is None:
            processed = seq
            continue
        # Both when the detection happened AND when it is committed, require
        # a current GPS fix. Never queue a frame while waiting for positioning.
        current_fix = location_manager.snapshot()
        if current_fix['source'] == 'none' or valid_coordinates(
                current_fix['latitude'], current_fix['longitude']) is None:
            processed = seq
            continue
        prior = next((spot for spot in saved_places if _distance_m(lat,lon,spot[0],spot[1]) <= 20.0),None)
        if prior and prior[2] >= event['severity']:
            processed = seq
            continue
        if now-last_attempt < 1.0:
            time.sleep(.15)
            continue
        last_attempt = now
        ok = upload_to_cloud_sync(event['frame'], lon, lat, event['severity'],
                                  session_id, event['location_source'])
        if ok:
            if prior:
                prior[2] = max(prior[2], event['severity'])
            else:
                saved_places.append([lat, lon, event['severity']])
            if event['severity'] >= 2:
                save_path = os.path.join(SAVE_DIR,
                    f"severe_lon{lon:.6f}_lat{lat:.6f}_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
                cv2.imwrite(save_path, event['frame'])
                cleanup_old_records(SAVE_DIR)
            processed = seq
        elif time.monotonic()-event['observed_at'] > 4.0:
            processed = seq
        time.sleep(.2)

def run_detection_loop() -> None:
    global latest_frame, latest_detection_count
    global current_camera_index, current_camera_brightness, current_camera_signal
    global active_camera,latest_detections,latest_prediction_time,latest_infer_ms,latest_damage_ratio,latest_weighted_ratio,latest_damage_level
    global latest_distance_m, latest_distance_source
    global latest_detection_reference, latest_detection_event
    last_missing_gps_notice = 0.0
    cam = CameraStream()
    with lock: active_camera=cam
    model = None

    print("[边缘端] 硬件初始化完成，进入待命状态...")

    while True:
        success, frame, right_rectified, camera_mode = cam.read_for_inference()
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

            annotated_frame, severity_level = model.predict(
                frame, right_rectified=right_rectified,
                stereo_estimator=cam.stereo if camera_mode == 'stereo' else None)
            # Snapshot the GPS at inference time. No GPS => no archive event.
            # A location acquired later cannot retroactively geotag an old image.
            fix = location_manager.snapshot()
            coordinates = valid_coordinates(fix['latitude'], fix['longitude'])
            event = None
            if severity_level > 0 and coordinates and fix['source'] in ('hardware','phone') and global_session_id:
                event = {'seq': model.inference_counter, 'observed_at':time.monotonic(),
                         'session_id':global_session_id, 'severity':severity_level,
                         'latitude':coordinates[0], 'longitude':coordinates[1],
                         'location_source':fix['source'], 'frame':annotated_frame.copy()}
            elif severity_level > 0 and time.monotonic()-last_missing_gps_notice >= 10.0:
                print('[归档] 等待有效定位（硬件 GPS 或手机），暂不归档',flush=True)
                last_missing_gps_notice=time.monotonic()
            with lock:
                latest_detection_event=event
                latest_detections=list(model.last_detections)
                latest_prediction_time=time.monotonic()
                latest_detection_reference=cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),(32,24),interpolation=cv2.INTER_AREA)
                latest_infer_ms=model.last_infer_ms
                latest_damage_ratio=model.last_damage_ratio
                latest_weighted_ratio=model.last_weighted_ratio
                latest_damage_level=severity_level
                latest_distance_m=model.last_distance_m
                latest_distance_source=model.last_distance_source
                cam.class_names=model.classes

        else:
            with lock:
                latest_detection_event=None
                latest_detections=[]
                latest_prediction_time=0.0
                latest_detection_reference=None
                latest_damage_ratio=0.0
                latest_weighted_ratio=0.0
                latest_damage_level=0
                latest_distance_m=None
                latest_distance_source='mono'
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

    print(f'[云端连接] {CLOUD_BASE_URL} | 设备编号 {DEVICE_ID}', flush=True)
    print('[云端接口] /upload /update_stream /api/command（Android 独立定位）', flush=True)

    # Android uses authenticated cloud APIs. No browser QR link is created.


    # Retired Pi QR URL reporting. Do not start the legacy loop.

    threading.Thread(target=run_gps_reader, daemon=True).start()

    # Android locations arrive through the authenticated cloud endpoint.
    # No local phone browser or QR/HTTPS:8080 service is started.
    threading.Thread(target=run_command_polling, daemon=True).start()
    threading.Thread(target=run_phone_location_polling, name='RoadSensePhoneGPSFallback', daemon=True).start()
    threading.Thread(target=run_map_loop, daemon=True).start()
    threading.Thread(target=run_heartbeat_loop, daemon=True).start()
    threading.Thread(target=run_video_render_loop, name='RoadSenseVideoRender', daemon=True).start()
    threading.Thread(target=run_video_upload_loop, name='RoadSenseCloudUpload', daemon=True).start()
    threading.Thread(target=run_direct_preview_server, name='RoadSenseDirectMJPEG', daemon=True).start()

    try:
        run_detection_loop()
    except KeyboardInterrupt:
        pass
