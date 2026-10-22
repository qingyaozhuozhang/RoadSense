# RoadSense 边缘采集与推理程序

负责读取摄像头、执行 YOLOv8-OBB 道路损伤识别、解析 GPS 模块数据，并将视频和记录送至网页服务。手机备用定位节点也可由本程序提供。

## 目录

```text
houduan/
├── core.py                设备入口、状态控制、位置选择与上传
├── best.onnx              巡检模型（YOLOv8-OBB）
├── obb_inference.py       旋转框后处理与损伤面积评估
├── video_pipeline.py      最新帧缓冲与 MJPEG 预览
├── location_state.py      GPS/手机位置优先级
├── coordinate_utils.py    坐标转换
├── rpi/gps_rmc_reader.py  NMEA RMC 解析器
├── phone_ui/              独立手机 HTTPS 位置采集页
├── tools/create_lan_cert.py 证书工具
└── requirements_runtime.txt
```

训练示例、历史基准测试和备用导出模型未包含在精简版本中。运行时使用 `best.onnx`，无需依赖本地 Ultralytics 源码目录。

## 环境和启动

建议 Linux / Ubuntu、Python 3.10～3.12，配备 OpenCV 可访问的 UVC 摄像头；GPS 模块使用串口 NMEA 输出。

```bash
cd houduan
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_runtime.txt
test -f .env || cp .env.example .env
```

编辑 `.env`，使用适合当前网络的地址：

```ini
CLOUD_BASE_URL=http://127.0.0.1:5000
CAMERA_INDEX=2
ORT_PROVIDER=cpu
VIDEO_RENDER_FPS=25
VIDEO_UPLOAD_FPS=20
VIDEO_JPEG_QUALITY=50
GPS_PORT=/dev/ttyUSB0
GPS_BAUD=9600
```

`CLOUD_BASE_URL` 为 Flask 所在设备的地址：同机使用 `127.0.0.1`；两台机器分别运行时写电脑的局域网地址。最后执行：

```bash
python core.py
```

项目先尝试 `CAMERA_INDEX=2`；失败时检查 `0、1、2`。摄像头无法读取时，网页显示设备占位图，不把空画面当作实时视频。默认采用 CPU 执行 ONNX Runtime，只有具备匹配 CUDA/cuDNN 环境的设备才需配置 GPU。

## 算法与数据流程

1. **图像采集**：OpenCV 异步获取最新帧，避免大量旧帧排队。
2. **旋转目标识别**：`best.onnx` 执行 OBB 推理；解析位置、宽高、类别分数和旋转角度，通过 `NMSBoxesRotated` 抑制重复框并使用四顶点绘制。
3. **面积评估**：计算损伤框覆盖面积与画面面积的比例，按配置阈值确定轻微、中度和严重等级。该比例是图像中估计面积占比，不代表实际路面测量面积。
4. **视频传输**：视频编码、预览与任务心跳分离，本地 MJPEG 默认在 `8091` 端口提供预览；网页在允许的情况下直连，否则通过 Flask 的 `/video_feed` 获取流。
5. **空间记录**：GPS 输出经纬度与损伤记录关联，上传 `/upload`；手机节点只在 GPS 模块位置过期后充当备用。

摄像头 25 FPS 是预览目标值，不是设备保证值；实际 FPS 受硬件解码、模型推理和网络条件影响。右上角检测信息显示损伤占比与严重程度，不显示模型置信度。

## 定位与手机节点

GPS 数据通过 `rpi/gps_rmc_reader.py` 解析 RMC 状态及经纬度。GPS 有效时优先使用 GPS 模块；GPS 位置超过 5 秒未更新后，若手机在最近 30 秒持续上报有效坐标则接管，GPS 恢复后自动切回。

手机端可访问 `https://边缘设备局域网IP:8080/phone`，并允许定位后开始上报。需要为设备配置受手机信任的 HTTPS 证书；可参考 `tools/create_lan_cert.py` 生成用于本地开发的证书。手机与采集设备必须网络可达；同一热点并不保证端口相互可访问。电脑端 `/location` 只接收与展示位置。

## 注意事项

- 设备的 `.env` 和证书私钥必须保密，仓库仅保留 `.env.example`。
- 若 `best.onnx` 与导出时类别数、旋转框维度不一致，推理后处理必须相应调整。
- 摄像头编号因 USB 连接变化而变化；遇到黑屏可先检查 `/dev/video*` 与占用程序。
- 网络可能导致云端视频低于本地预览 FPS。优先核对 8091 本地直连画面，再检查 Flask 转发。
- 地图显示不是 GPS 坐标采集的前提。地图异常不应阻断损伤记录上报。
