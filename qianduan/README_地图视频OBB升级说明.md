# RoadSense：街道地图 / 高帧率视频 / YOLOv8-OBB 旋转检测联调说明

## 本次直接修改的文件

- `houduan/core.py`：识别真实 OBB ONNX 元数据；修改摄像头流处理；心跳与视频上传解耦；支持 `ORT_PROVIDER`/`ORT_THREADS`；自动搜索摄像头 2、0、1。
- `houduan/obb_inference.py`（新文件）：模型原始张量解码、OBB 旋转 NMS、四顶点斜框描画、类别置信度与面积计算；与普通 Detect 兼容。
- `houduan/tools/benchmark_obb.py`（新文件）：独立检测 ONNX 推理延迟。
- `houduan/tests/test_obb.py`（新文件）：4 项 OBB/Detect/坐标变换测试。
- `qianduan/server/app.py`：视频上传速率统计、事件驱动 MJPEG 视频推送、可选地图配置接口。
- `qianduan/frontend/static/js/detection.js`：原先每秒一帧图片刷新改为 `/video_feed` MJPEG 流；展示真实视频 FPS。
- `qianduan/frontend/static/js/live_map.js`：默认地图缩放至 18 级、拖动后停止自动追踪、新增“◎ 跟随定位”、可选卫星影像图层。
- `qianduan/frontend/static/css/dashboard.css`：地图定位操作按钮。
- `qianduan/.env.example`：可选 `MAPTILER_KEY` 使用说明。

## 已核对两个 ONNX 模型真实差异

| 来源 | 元数据 task | 输出张量 | 类别 |
|---|---|---|---|
| `houduan/best.onnx` | `obb` | `[1,7,8400]` | `Crack`、`Hole` |
| 用户 `runs.zip/runs/detect/train/weights/best.onnx` | `detect` | `[1,5,8400]` | `pin` |

`runs.zip/example.py` 调用 Ultralytics `YOLO(..., task='detect')`、`cv2.VideoCapture(2)`、`cv2.imshow` 本地窗口，没有 Flask 上传和浏览器播放。故观感差异首先应区分显示链路和模型耗时；不能根据 `runs` 的 FPS 断言 OBB ONNX 同样快。

旧 OBB 代码按 `cv2.dnn.NMSBoxes` + `cv2.rectangle` 普通矩形处理，且最后角度渠道会被丢弃。修复后把角度（**弧度**）转换成旋转框，以 `NMSBoxesRotated` 筛选并用 `polylines` 画四边形，读取模型内 2 个类别，不再使用错误的五类别硬编码。

## 第 1 步：启动电脑网页 / Flask

在本地 Ubuntu 终端：

```bash
cd qianduan
python -m pip install -r requirements.txt
python server/app.py
```

浏览器：<http://127.0.0.1:5000/detection>。若设备和网页位于不同的主机，网页正常运行不代表边缘端已连接。

## 第 2 步：启动真实摄像头 + OBB 检测

新开终端：

```bash
cd houduan
python -m pip install -r requirements_runtime.txt
export CLOUD_BASE_URL=http://127.0.0.1:5000
export CAMERA_INDEX=2
export ORT_PROVIDER=cpu
export ORT_THREADS=2
export VIDEO_UPLOAD_FPS=8
export VIDEO_JPEG_QUALITY=60
python core.py
```

如果 `core.py` 运行在树莓派上而 Flask 在电脑上，请把 `CLOUD_BASE_URL` 改成电脑的实际局域网 IP，**不是树莓派自己的 127.0.0.1**。当硬件序号 2 不存在时，会自动探测 0、1、2。未连接摄像头会显示原有双目摄像头示意图。

**定位限制**：继续保持“GPS 模块最近 5 秒有效优先、手机最近 30 秒有效备用”。实际点击开始巡检之前，请确保 `/api/location_status` 的 `source` 不为 `none`。不要为了测试视频关闭现有定位校验。

## 第 3 步：验证视频实际帧率

```bash
curl http://127.0.0.1:5000/api/video_status
```

观察 `available`、`frames_received`、`video_fps`、`camera_index`、`camera_signal`；不要把 `device_online=true` 当作已收到画面。视频上报和每秒一次的心跳分别运行，页面 MJPEG 直接播放 `/video_feed`。

**视频与 AI 帧率的区别**：浏览器视频可在推理慢时继续显示最新摄像头原始帧；只有刚更新过的 OBB 检测结果才叠加旋转框，超过时效就暂时只显示视频。因而页面流畅不代表推理也达到同一 FPS。如果 CPU 推理耗时过高，需要调整线程、部署 ONNX Runtime 适配的 GPU 加速或减少模型开销，而不是伪造检测框。

独立测量一次 ONNX 推理：

```bash
cd houduan
python tools/benchmark_obb.py --model best.onnx --loops 20
```

`ORT_PROVIDER=cpu` 是默认项；只有 CUDA/cuDNN 实际兼容时才设置 `ORT_PROVIDER=cuda`。

## 第 4 步：更精细的地图

原先默认缩放 15.2（街区级），现在使用 18.1（街道级）并允许继续放大、拖动及点击“◎ 跟随定位”。仍然无须申请 OpenFreeMap Key。

**重要局限**：OpenFreeMap 与 OpenStreetMap 共用基础路网数据；在缺失道路数据的地区，换配色和放大并不能出现本来就没有的道路，更不保证车道级地图或车道级 GPS 精度。如果需要看到真实街面照片/航拍影像，项目现已支持可选 `MapTiler` 卫星图层：注册浏览器 Key 后添加到 `qianduan/.env`：

```
MAPTILER_KEY=你的浏览器地图Key
```

重启 Flask 后地图会出现“卫星”切换按钮；**没有 Key 时不会显示假的卫星图，更不会偷偷调用第三方地图**。MapTiler 使用前请确认账户免费配额、使用条款，并限制浏览器 Key 的访问来源；影像存在拍摄时间及精度差异，不能用作真实车道归属的唯一依据。

## 运行检查结果（在本次生成环境）

- YOLO ONNX 元数据直接从文件读取：OBB `[1,7,8400]`，`Crack`、`Hole`；对比 Detect `[1,5,8400]`，`pin`。
- `houduan/tests/test_obb.py`：4 项通过（OBB 四角旋转、区分类别与角度、普通 Detect、Letterbox 缩放）。
- `qianduan/tests/`：26 项，其中 16 项通过，10 项因环境缺少 Flask 跳过。
- Python 语法与前端 JS 语法检查通过。
- **未在你的摄像头、ONNX Runtime 或浏览器上完成真实性能及 GPS 联调**；不能保证达到指定 FPS，也不能声称卫星影像在未配置 Key 时可用。
