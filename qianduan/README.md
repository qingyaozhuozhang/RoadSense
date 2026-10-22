# RoadSense 网页与 Flask 服务

此目录同时包含用户网页和 Flask API，适合先在电脑上运行，再部署到服务器。没有额外的 Node.js 构建步骤。

## 目录

```text
qianduan/
├── frontend/
│   ├── templates/      首页、巡检、档案、GPS 和手机节点页面
│   └── static/         dashboard.css（基础组件）、theme.css（主题）、JavaScript 和图片
├── server/
│   ├── app.py          Flask 应用与边缘设备通信接口
│   ├── location_state.py 位置时效与优先级管理
│   ├── coordinate_utils.py 经纬度转换
│   ├── storage.py      损伤记录与图片存储
│   └── amap_proxy.py   高德安全代理
├── scripts/create_lan_cert.py  局域网 HTTPS 测试证书工具
├── data/              运行时数据
├── .env.example       配置模板
└── requirements.txt
```

## 启动

```bash
cd qianduan
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
test -f .env || cp .env.example .env
python server/app.py
```

服务地址：`http://127.0.0.1:5000`。使用脚本 `./start_local.sh` 也可在 Ubuntu 上启动；Windows 环境可运行 `start_local.bat`。

### 高德地图接入

1. 登录 [高德开放平台](https://console.amap.com/)，在应用中创建 **Web端（JS API）** 类型的 Key，并获取安全密钥。
2. 本地交付包中的 `.env` 已包含所需配置；重新创建环境或更换凭证时，在本目录 `.env` 中填写：

   ```ini
   AMAP_JS_KEY=你的JSAPI_Key
   AMAP_JS_SECRET=你的安全密钥
   HOST=0.0.0.0
   PORT=5000
   ```

3. 重启 Flask，在浏览器查看 `/gps`、`/location` 和 `/severe`。地图只在存在相应有效坐标或损伤记录时显示定位标记。
4. 本地交付包包含私有配置 `.env`，上传 GitHub 前确保它仍被 `.gitignore` 排除，切勿将含密钥的 ZIP 公开发布。安全密钥由 Flask 服务端代理使用，不能写入公开的 HTML、JavaScript 或提交 GitHub。**不要混用 Web 服务 Key 和 Web端 JS API Key。**

根据项目实现，高德地图只显示转换后的 GCJ-02 坐标；后端和存档保留 WGS84 原始值。

### 手机位置上报

电脑端 `/location` 显示手机节点状态、可访问网址和二维码，**不读取电脑浏览器的地理位置**。手机通过 HTTPS 访问 `/phone` 并启动位置上报，电脑收到有效坐标后再显示经纬度与地图。运行在边缘设备上的独立手机 HTTPS 页面详见 [边缘端说明](../houduan/README.md)。

浏览器 Geolocation 要求安全上下文；仅连接同一 Wi-Fi 不能替代可信 HTTPS。`scripts/create_lan_cert.py` 可辅助制作本地证书，但手机仍须正确信任该证书。

## 主要接口

| 路径 | 说明 |
|---|---|
| `/` | 巡检概览 |
| `/detection` | 实时视频与任务控制 |
| `/severe` | 损伤档案与空间分布 |
| `/gps` | GPS 模块位置 |
| `/location` | 电脑端手机位置接收 |
| `/phone` | 手机端位置采集 |
| `/api/status`、`/api/control`、`/api/command` | 设备与任务状态 |
| `/update_stream`、`/video_feed`、`/api/video_status` | 实时画面与状态 |
| `/upload`、`/api/records` | 损伤记录上传与查询 |
| `/api/receive_location` | 手机节点位置上报 |

## 功能实现

Flask 负责接收边缘设备的心跳、视频帧及识别记录，Jinja2 渲染页面，JavaScript 轮询运行状态并使用 MJPEG 展示画面。历史数据写入 `data/records.json` 和 `data/images/`，暂不依赖数据库服务器。位置管理器会检查位置合法性、数据来源和最新上报时间，硬件 GPS 优先，手机备用；过期位置不会继续标记为当前有效位置。

## 注意事项

- 本目录的 `.env`、局域网证书私钥和设备令牌禁止提交至 GitHub。
- 使用真实设备时，需要保证采集端能访问网页服务器的 IP/端口；不能把采集端的 `127.0.0.1` 指向另一台电脑。
- 公开部署应使用 HTTPS、设备认证及生产级 WSGI 服务；Flask 内置开发服务器仅用于本地调试。
- JSON 文件适合本地演示和单进程运行，多设备并发及长期运行建议迁移到数据库。
- 地图初始化需要互联网和可用的高德 Key；没有定位时地图保持等待状态是正常的。
