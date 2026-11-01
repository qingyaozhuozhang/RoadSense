# houduan — RoadSense 树莓派/Ubuntu 边缘端

负责摄像头采集、YOLOv8-OBB 道路损伤检测、NEO-F10N 硬件定位、Android 手机备用定位、视频及定位上传、有效定位下的自动归档。

## 1. 环境与接线

- Raspberry Pi / Ubuntu Linux，Python 3.10+；可连接摄像头，具备网络访问云端的条件。
- u-blox **NEO-F10N** GPIO UART 连接：GPS TX → 树莓派 RX，GPS RX → 树莓派 TX，GND → GND。必须确认 UART 电平兼容 **3.3V**，供电按模块标注连接；不要把 5V 信号直接连接到树莓派 GPIO。
- 在树莓派 `sudo raspi-config` → Interface Options → Serial Port，禁用串口登录控制台、启用串口硬件。重启后 `ls -l /dev/serial0` 检查设备。
- 默认 `/dev/serial0`、38400、8N1。实际串口或波特率不同，修改 `.env`。

## 2. 运行环境

```bash
cd RoadSense/houduan
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements_runtime.txt
```

安装 `pyserial` 已包含在 `requirements_runtime.txt`。若 ONNX Runtime 在 ARM 环境无匹配 wheel，请按树莓派架构安装兼容版本。

## 3. 用户如何填写 `houduan/.env`

压缩包保留了原先的运行配置，**无需先删除整个 `.env`**。更换设备/服务器时在现有文件里修改相应行：

```dotenv
# 云端地址：换成你的服务器地址，不能填树莓派本机 127.0.0.1
CLOUD_BASE_URL=http://39.102.84.218:5000
# 设备编号与密钥：从云端管理员“设备与数据管理”下载该设备专属 .env
DEVICE_ID=pi-main
DEVICE_TOKEN=请使用管理员页面为本机签发的令牌

# 本机硬件
CAMERA_INDEX=2
YOLO_MODEL=best.onnx
GPS_PORT=/dev/serial0
GPS_BAUD=38400
GPS_UPLOAD_INTERVAL=0.5
PHONE_POLL_INTERVAL=0.5

# 目标视频帧率，与 GPS 的 0.5 秒上报间隔无关
VIDEO_RENDER_FPS=25
VIDEO_UPLOAD_FPS=20
```

**已有设备不要照抄上面的 `DEVICE_TOKEN` 文字**：文件内原有令牌已保留，只有重新签发或换服务器时才改。其他 `.env` 字段（ORT、视频 JPEG、直连配置、高德 Key 等）也保留。

- `GPS_UPLOAD_INTERVAL=0.5`：每 0.5 秒通过 `/update_stream` 发一次当前**未过期**定位状态。不代表 NEO-F10N 实际每 0.5 秒输出一条新的 RMC。设备若默认 1 Hz，会重复上报未过期坐标；过期以后上报空位置。
- `PHONE_POLL_INTERVAL=0.5`：每 0.5 秒查询云端对应设备的 Android 备用位置。只接受有权限且归属于本设备的手机位置。
- GPS **5 秒有效期**，手机备用位置 **30 秒有效期**，硬件定位有效时始终优先硬件；两者无效则不会归档损伤。
- 改动 `.env` 后重新启动 `python core.py` 才会生效。

## 4. 先测试 NEO-F10N 驱动

**停止正在运行的 `python core.py` 后**，单独运行测试程序，避免两个程序抢占同一个串口：

```bash
python -m rpi.neo_f10n_simple --port /dev/serial0 --baud 38400
```

收到符合校验和且状态为 `A` 的 `$GNRMC`/`$GPRMC`，才打印 WGS84 经度纬度；如果一直显示等待定位，请在室外无遮挡环境检查天线、串口配置和接线。停止使用 `Ctrl+C`。

## 5. 正式运行与操作流程

```bash
cd RoadSense/houduan
source .venv/bin/activate
python core.py
```

在浏览器打开云端网页，管理员为该设备分配用户；在实时监控页面选择设备并点击“开始巡检”。本地采集线程保持最新帧，YOLO 检测损伤并分级；只有在巡检运行、检测结果有效且硬件/手机定位有效时才允许按同一任务 20 米空间去重规则归档。

- 管理员的云端“设备与数据管理”可添加设备、授权账号、重新分配设备；每台设备使用独立 `DEVICE_ID` + `DEVICE_TOKEN`。
- Android 手机使用自己的账号登录云端并开始定位；定位与所选设备/账号关联，不再需要扫描树莓派二维码。
- 屏幕显示损伤检测框 ≠ 必然写入数据库；没有有效位置、巡检未运行或同位置已去重时不保存。
- 结束按 `Ctrl+C`。需要让边缘端关窗口继续运行时另行配置 systemd（与云端独立）。

## 6. 需要排查时

`ModuleNotFoundError: serial`：在当前 `.venv` 中 `python -m pip install pyserial`。

`/dev/serial0 permission denied`：确认用户属于 `dialout` 组：`sudo usermod -aG dialout $USER`，重新登录终端后验证。

`GPS 无定位`：先用驱动单独测试 RMC 内容与 checksum；网络和 Wi-Fi **不影响 GNSS 卫星定位本身**。

`云端 HTTP 401`：确认本机 `DEVICE_ID`、`DEVICE_TOKEN` 与管理员页面登记值一致。

`位置更新间隔变慢`：0.5 秒为计划周期，HTTP 网络超时、GNSS 模块实际输出频率及后台线程调度会影响实测周期。
