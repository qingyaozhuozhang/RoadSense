"""Cloud-side photo inspection using the same YOLO ONNX model as the edge client.

Optional native dependencies load only on first inspection, keeping the normal
dashboard and legacy device endpoints available if cloud inference is disabled.
"""
from __future__ import annotations

import ast
import io
import os
import threading
import time
from pathlib import Path

from PIL import Image, UnidentifiedImageError


DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "models" / "best.onnx"


class InferenceError(RuntimeError):
    pass


class PhoneInference:
    def __init__(self, model_path=None):
        self.model_path = Path(model_path or os.environ.get("PHONE_YOLO_MODEL") or DEFAULT_MODEL)
        self.lock = threading.RLock()
        self.session = None

    def _init_session(self):
        if self.session is not None:
            return
        if not self.model_path.is_file():
            raise InferenceError(f"阿里云未找到模型文件：{self.model_path}，请上传 qianduan/models/best.onnx")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise InferenceError("云端缺少 onnxruntime，请执行 pip install -r requirements.txt") from exc
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(2, os.cpu_count() or 1))
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(self.model_path), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        metadata = self.session.get_modelmeta().custom_metadata_map or {}
        self.rotated = str(metadata.get("task", "")).lower() == "obb"
        shape = self.session.get_outputs()[0].shape
        channel = shape[1] if len(shape) == 3 else None
        if not isinstance(channel, int):
            raise InferenceError(f"无法解析 ONNX 模型输出：{shape}")
        self.class_count = channel - 4 - int(self.rotated)
        if self.class_count <= 0:
            raise InferenceError("YOLO 模型输出通道异常")
        try:
            names = ast.literal_eval(metadata.get("names", "{}"))
            self.classes = [str(names.get(i, names.get(str(i), i)))
                            for i in range(self.class_count)] if isinstance(names, dict) else [str(i) for i in range(self.class_count)]
        except (ValueError, SyntaxError, TypeError):
            self.classes = [str(i) for i in range(self.class_count)]
        in_shape = self.session.get_inputs()[0].shape
        self.input_h = int(in_shape[2]) if isinstance(in_shape[2], int) else 640
        self.input_w = int(in_shape[3]) if isinstance(in_shape[3], int) else 640
        self.input_name = self.session.get_inputs()[0].name

    def analyze(self, contents):
        if not contents or len(contents) > 8 * 1024 * 1024:
            raise ValueError("图片不能为空，且大小不能超过 8 MB")
        try:
            with Image.open(io.BytesIO(contents)) as image:
                if image.format not in ("JPEG", "PNG", "WEBP"):
                    raise ValueError("目前支持 JPG、PNG 和 WebP 图片")
                w, h = image.size
                if w < 64 or h < 64 or w * h > 20_000_000:
                    raise ValueError("图片尺寸需至少 64×64，且不能超过 2000 万像素")
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("无法解码图片，请使用 JPG、PNG 或 WebP 格式") from exc

        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise InferenceError("云端缺少 OpenCV 或 NumPy，请执行 pip install -r requirements.txt") from exc
        try:
            from .obb_inference import decode_predictions, draw_detections, draw_severity_hud
        except ImportError:
            from obb_inference import decode_predictions, draw_detections, draw_severity_hud

        frame = cv2.imdecode(np.frombuffer(contents, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("图片解码失败，请更换图片格式")
        if max(frame.shape[:2]) > 1600:
            scale = 1600 / max(frame.shape[:2])
            frame = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)))
        height, width = frame.shape[:2]

        with self.lock:  # A single shared ONNX session; avoid concurrent CPU spikes.
            self._init_session()
            started = time.perf_counter()
            scale = min(self.input_w / width, self.input_h / height)
            nw, nh = round(width * scale), round(height * scale)
            resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
            dw, dh = (self.input_w - nw) / 2, (self.input_h - nh) / 2
            left, right = round(dw - .1), round(dw + .1)
            top, bottom = round(dh - .1), round(dh + .1)
            padded = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                        cv2.BORDER_CONSTANT, value=(114, 114, 114))
            rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
            blob = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
            prediction = self.session.run(None, {self.input_name: blob})[0]
            detections = decode_predictions(prediction, self.class_count, self.rotated,
                                            scale, (left, top), (height, width), .3, .5)
            ratio = min(1.0, sum(box["area"] for box in detections) / max(1, width * height))
            level_num = (0 if not detections else 1 if ratio <= .02 else 2 if ratio <= .10 else 3)
            annotated = draw_detections(frame, detections, self.classes)
            annotated = draw_severity_hud(annotated, ratio, level_num)
            ok1, original = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            ok2, result = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            if not ok1 or not ok2:
                raise InferenceError("检测结果图片编码失败")
            return {
                "original_jpeg": original.tobytes(),
                "result_jpeg": result.tobytes(),
                "level": ("无损伤", "轻微", "中度", "严重")[level_num],
                "damage_ratio": round(ratio, 5),
                "inference_ms": round((time.perf_counter() - started) * 1000),
                "width": width,
                "height": height,
                "detections": [{
                    "class_name": self.classes[det["class_id"]],
                    "confidence": round(det["confidence"], 4),
                    "polygon": det["corners"].tolist(),
                } for det in detections],
            }
