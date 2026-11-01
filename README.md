# RoadSense — 道路智能巡检平台

基于 **YOLOv8-OBB + 硬件 NEO-F10N / Android 双定位 + Flask + SQLite** 的多设备道路损伤巡检平台。云端集中管理账号、设备、实时画面和归档；树莓派识别道路损伤；Android 负责原生位置、拍照识别和现场归档。

## 项目目录

```text
RoadSense/
├── README.md                         GitHub 首页和全链路运行说明
├── qianduan/
│   ├── README.md                     云端配置、数据库与运维说明
│   ├── .env                          现有配置，部署到新机器时按说明更新
│   ├── server/                       Flask API、认证、设备归属、SQLite
│   ├── frontend/                     HTML/CSS/JS 管理工作台
│   ├── deploy/houduan.env.template   新设备完整环境配置导出模板
│   └── models/best.onnx              手机照片识别模型
├── houduan/
│   ├── README.md                     树莓派接线和配置说明
│   ├── .env                          现有设备配置，含 0.5 秒上报
│   ├── core.py                       摄像头 / YOLO / GPS / 视频 / 归档
│   ├── rpi/neo_f10n_simple.py        新 NEO-F10N 解析驱动（原始上传版）
│   ├── location_state.py             位置选择及过期判断
│   └── best.onnx                     边缘识别模型
├── Android_Studio/
│   ├── README.md                     Android Studio 编译和操作说明
│   ├── app/                          Kotlin 手机程序
│   └── gradle/                       Gradle 配置
└── docs/                             算法、数据备份和清理说明
```

## 0. 先确认运行环境

1. **阿里云**：Ubuntu 24.04，部署路径可用 `/home/admin/RoadSense/qianduan`，运行 HTTP `5000`；需要 Python 与 ONNX Runtime 相关依赖。
2. **边缘设备**：树莓派 Ubuntu / Raspberry Pi OS；USB 摄像头；NEO-F10N UART GPS（默认 `/dev/serial0`、38400）。
3. **手机**：Android 8.0+，安装 RoadSense，允许需要的定位权限。
4. **网络**：树莓派和手机都能访问云端 HTTP 服务。GNSS 卫星定位本身不需要网络。

## 1. 云端配置（先配置、先启动）

```bash
cd /home/admin/RoadSense/qianduan
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

保留 `qianduan/.env` 现有值；部署到自己的机器时逐项核对：

| 配置字段 | 怎么填 |
|---|---|
| `HOST`、`PORT` | 服务监听地址与端口，当前 `0.0.0.0`、`5000` |
| `ROADSENSE_DATA_DIR` | **实际** SQLite 与图片保存的绝对路径，如 `/home/admin/RoadSense/qianduan/data` |
| `PHONE_YOLO_MODEL` | 云端 ONNX 模型的实际绝对路径 |
| `AMAP_JS_KEY`、`AMAP_JS_SECRET` | 本项目当前 Web 端高德密钥；换应用时填自己的 |
| `ROADSENSE_SECRET_KEY` | Flask Session 签名密钥，已提供现有值，更换服务器建议生成新值 |
| `DEVICE_TOKEN`、`BOOTSTRAP_DEVICE_ID` | 初始边缘设备的身份；新增设备由管理员后台单独签发 |
| `SESSION_COOKIE_SECURE` | 暂时 HTTP 调试填 `0`；启用可信 HTTPS 后改 `1` |
| `DEVICE_CLOUD_BASE_URL` | 管理员下载设备 `.env` 时写入的云端公网 URL |

启动测试：

```bash
python server/app.py
```

访问 `http://你的服务器IP:5000`；确认能登录和打开管理员设备管理页面。生产环境建议 HTTPS。**更新已有阿里云服务时先备份 `qianduan/data/`，不要把空目录覆盖旧数据库。**

## 2. 树莓派配置和 NEO-F10N GPS 接线

UART 接线：`GPS TX → Pi RX`，`GPS RX → Pi TX`，`GND → Pi GND`；供电和电平按模块标注，GPIO UART 必须为兼容 3.3V 的串口信号。启用串口并禁用 Linux 串口控制台。

编辑 `houduan/.env`，**不要清空已提供的其他参数**：

```dotenv
CLOUD_BASE_URL=http://39.102.84.218:5000
DEVICE_ID=pi-main
DEVICE_TOKEN=由管理员签发的本机专属令牌
GPS_PORT=/dev/serial0
GPS_BAUD=38400
GPS_UPLOAD_INTERVAL=0.5
PHONE_POLL_INTERVAL=0.5
CAMERA_INDEX=2
YOLO_MODEL=best.onnx
```

上述令牌一行是**填写说明**；交付 `.env` 已保留原来的设备令牌。如果管理员轮换过密钥，必须使用最新下载值。启动前可以先测试驱动：

```bash
cd houduan
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements_runtime.txt
python -m rpi.neo_f10n_simple --port /dev/serial0 --baud 38400
```

成功取得定位后按 `Ctrl+C` 退出单独测试，再执行 `python core.py`。同一 UART 串口不能同时被测试程序和主程序占用。

`GPS_UPLOAD_INTERVAL=0.5` 表示**每 0.5 秒发送一次最新有效位置状态**，但新坐标的产生速度仍取决于 GPS 模块实际导航更新率和网络状况；不要误认为设备在软件配置后一定输出 2Hz 新坐标。

## 3. Android 配置与安装

在 Android Studio 打开 `Android_Studio`，Gradle JDK 使用 21；保持现有 `build.gradle.kts` 中的云端 URL 和高德 Android Key，或者在 Android Studio 自动生成的 `local.properties` 追加：

```properties
roadsense.cloudBaseUrl=http://39.102.84.218:5000
roadsense.amapAndroidKey=在高德开放平台申请的Android类型Key
```

下载 Gradle 依赖后运行 `gradlew.bat :app:assembleDebug`。发布签名 APK 需使用你自己原来的 `.jks`，它不随源码提供。Gradle wrapper JAR 若缺失，可运行 `Setup-Windows.ps1`，或复制已验证可用的版本。详见 [Android 使用说明](Android_Studio/README.md)。

## 4. 普通用户的使用流程

在网页注册账号 → 登录 Android App → 管理员分配所属设备（如果没有设备，管理员可单独授权手机定位）→ 点击 **开始定位** → 手机按权限上传自己的坐标 → 在网页查看对应设备的实时监控、执行开始/结束巡检 → 拍照预览道路损伤 → 有效坐标下点击 **确认归档** → 在自己的损伤记录档案中查阅。

**普通用户无权查看其他账号设备。** 无设备且未获定位权限的普通用户点击“开始定位”时会提示联系管理员，不会新增灰色禁用按钮。

## 5. 管理员的使用流程

登录网页管理员账号 → 进入“设备与数据管理” → 添加设备或分配归属账号 → 为无设备普通用户授予/撤销手机定位权限 → 在“在线设备画面”查看各设备 → 选择设备开始巡检 → 在“损伤记录档案”查询地理位置和图片 → 在管理员管理页编辑/删除记录、重置普通账号密码或删除符合条件的账号。

新设备通过管理员页面下载完整 `houduan/.env`，放入对应的树莓派目录；**设备编号和密钥要与云端登记的一致**。

## 6. 自动归档规则

`摄像头 → YOLOv8-OBB → 损伤分级 → 巡检 RUNNING 检查 → 硬件 GPS 优先/手机备用 → 定位有效期判断 → 20 米空间去重 → POST /upload → SQLite 及图片`。

没有任何有效定位时只显示损伤检测框，**绝不保存无坐标损伤记录**。手机确认归档也必须有新鲜且有效的手机坐标；拍照预览不等于已归档。

## 7. 关闭窗口后继续运行（阿里云）

如果已安装 `roadsense.service`，使用以下命令；**不需要设置开机自启**：

```bash
sudo systemctl disable roadsense
sudo systemctl start roadsense
sudo systemctl status roadsense --no-pager
# 关闭 Workbench / SSH 窗口，程序继续运行
sudo systemctl stop roadsense
```

若尚未安装服务，请根据 `qianduan/deploy/roadsense.service` 内容核对**实际虚拟环境路径**后，再放到 `/etc/systemd/system/roadsense.service`，执行 `sudo systemctl daemon-reload`。不要同时手动运行 `python server/app.py` 占用同一端口。

## 8. 配置、安全与数据

- 按你的要求，完整包保留了 `qianduan/.env`、`houduan/.env`、当前设备凭据和 ONNX 模型。公开 GitHub 后，已提交的密钥将对所有人可见；建议未来轮换设备令牌及 Session 密钥。
- 历史 ZIP **无法代替线上真正运行的账号 SQLite、道路损伤记录和照片**，需要从阿里云 `qianduan/data/` 另行备份（见 `docs/DATA_BACKUP.md`）。
- 当前 HTTP `5000` 是调试配置，传输的登录密码、身份凭据没有 TLS 加密。
- 视频编码帧率不等于 YOLO 推理帧率；GPS 上报间隔也不等于 GNSS 模块实际定位间隔。
- 源码已做静态检查，真实 UART/网络/Android 实机运行需要在设备上验证。

## 子项目 README

- [qianduan 使用与配置](qianduan/README.md)
- [houduan 使用与配置](houduan/README.md)
- [Android 使用与配置](Android_Studio/README.md)
