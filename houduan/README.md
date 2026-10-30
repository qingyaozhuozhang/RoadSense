# houduan · RoadSense 边缘道路巡检

`houduan` 运行于树莓派 / Ubuntu，负责摄像头采集、YOLOv8-OBB 推理、GPS/手机位置选择、设备控制轮询、视频帧上传与道路损伤归档。

## 1. 环境与接线

- Ubuntu 或 Raspberry Pi 上支持的 Linux，Python 3.10+；有可用 USB/CSI 摄像头。
- `best.onnx` 为边缘检测模型，请保持文件存在。
- 使用 GPIO UART 接 u-blox NEO-F10N 时：模块 **TX→树莓派 RX**，模块 **RX→树莓派 TX**，共地，电平须兼容 3.3 V。启用 UART，并关闭串口 Linux 控制台占用。
- 示例 `GPS_PORT=/dev/serial0`、`GPS_BAUD=38400`；如果串口或波特率不同，以实际硬件为准。`/dev/serial0` 是否存在可通过 `ls -l /dev/serial*` 查看。

## 2. Python 安装

```bash
cd houduan
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements_runtime.txt
# 已带可用 .env；需要添加其他树莓派时从管理员后台下载新设备 .env
```

如果 Raspberry Pi 上 `opencv-python`、`onnxruntime` 安装失败，请检查 CPU 架构、Python 版本和可用 wheel，必要时改用系统的 OpenCV 包。不要随意换训练环境的重依赖。

## 3. 配置 `.env`

本包的 `.env` 已完整填写，下面仅列出关键配置项**示意**；请以实际文件值为准，不要把占位值复制回去：

```dotenv
CLOUD_BASE_URL=http://YOUR_SERVER_IP:5000
DEVICE_ID=pi-main
DEVICE_TOKEN=YOUR_UNIQUE_DEVICE_TOKEN
CAMERA_INDEX=2
YOLO_MODEL=best.onnx
ORT_PROVIDER=auto
VIDEO_RENDER_FPS=25
VIDEO_UPLOAD_FPS=20
VIDEO_UPLOAD_CONCURRENCY=4
GPS_PORT=/dev/serial0
GPS_BAUD=38400
```

**本项目已附首台设备现用 `DEVICE_ID` 与 `DEVICE_TOKEN`，必须与云端登记信息对应；**新增设备**应由管理员后台下载该设备的完整 `.env`，覆盖该台设备自身的 `.env`，而不是复用别台设备的令牌。

`VIDEO_*_FPS` 是目标值，不是保证的实际帧率；`ORT_PROVIDER=auto` 会尝试可用的加速执行提供者，最终以日志中实际启用的 provider 为准。

## 4. 运行

```bash
python core.py
```

云端需要已启动。浏览器在网页控制台选择本设备，点击“开始巡检”才会触发 `RUNNING` 自动检测归档。定位缺失时只显示 YOLO 检测框，不会保存坐标未知的损伤记录。

运行期间重点检查：

- 摄像头状态与实际采集 FPS。
- `[AI耗时]` 中模型前处理、推理和 OBB 后处理耗时。
- 硬件 GPS 串口状态，以及手机备用位置是否在有效期内。
- `[归档]` 消息与云端 `/upload` 返回码。

停止本地进程：在对应终端 `Ctrl+C`；若改为 systemd 服务，请使用你自己配置的 `systemctl stop`。

## 5. 核心归档逻辑

仅在 `RUNNING` 状态使用 **最新 YOLO 检测结果 + 当时的有效位置**生成归档事件；双定位来源优先硬件 GPS（5 秒有效），否则采用当前设备所属手机的坐标（30 秒有效）。没有有效坐标不归档，后来的定位不能补存旧帧。

空间去重使用 Haversine 距离。在同一巡检任务中，距已有记录小于等于 **20 m** 且损伤等级不高于已保存值的事件跳过；等级提升可再次保存，且只有云端确认成功后才把该点标记为已归档。

图像统一由云端生成 `primary_账号_年月日_时分秒_唯一标识` 文件名。细节见 [核心算法说明](../docs/ROAD_DAMAGE_ALGORITHM.md)。

## 6. 关于 Android 备用定位

手机节点由 Android App 上传，不再要求用户扫描二维码。手机登录账号必须有当前设备的访问权限；设备端经 `/api/phone_location` 只读取与自身匹配的手机位置，不会使用其他设备的坐标。

旧二维码手机页面和本地 HTTPS:8080 路由已经移除；Android 直接与云端通信。实际设备 GPS 不依赖 Wi-Fi 获取卫星定位，但**设备向阿里云上传画面和记录需要网络**。

### 当前环境配置

`houduan/.env` 已包含云端连接、高德设置、YOLO、视频上传、GPS 和设备令牌（25 项）。追加其他设备时不能把主设备 `.env` 复制到新设备，而应在管理员网页生成设备专属密钥，下载完整 `.env` 覆盖新设备的配置。

## 当前配置说明

本包**保留** `houduan/.env` 中的历史服务器地址、`pi-main` 设备令牌、摄像头编号、YOLO 参数、GPS UART 参数；新增设备需在管理员页面下载各自的完整 `.env`，不可共用主设备令牌。公开 GitHub 会公开这份配置。
