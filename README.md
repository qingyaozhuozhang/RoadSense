# RoadSense · 道路损伤智能巡检平台

RoadSense 将道路图像采集、YOLOv8-OBB 路面损伤识别、GPS/手机定位与云端记录管理整合到统一的网页工作台。网页可查看实时画面、位置、任务状态与历史损伤档案。

## 功能

- **实时巡检**：采集设备上传画面，支持开始、暂停和结束巡检，并绘制旋转检测框。
- **道路损伤评估**：根据检测区域面积占比划分轻微、中度、严重等级。
- **多源定位**：GPS 模块优先；模块失去有效数据时自动采用手机节点上报的备用坐标。
- **空间档案**：按巡检批次、损伤等级筛选历史记录，在地图中定位并导出 CSV。
- **地图**：高德地图 JavaScript API 2.0；位置数据存储为 WGS84，地图展示转换为 GCJ-02。

## 项目结构

```text
RoadSense/
├── qianduan/             网页和 Flask API
│   ├── frontend/         HTML 模板、CSS、JavaScript 和视觉资源
│   ├── server/           HTTP 接口、定位管理和记录存储
│   ├── data/             本地巡检记录（运行时写入）
│   └── README.md
├── houduan/              边缘采集与推理程序
│   ├── core.py           采集、巡检状态、定位及数据传输
│   ├── obb_inference.py  YOLOv8-OBB 输出后处理
│   ├── video_pipeline.py 低延时 MJPEG 输出
│   ├── rpi/              GPS RMC 数据解析
│   ├── phone_ui/         手机定位采集界面
│   ├── best.onnx         当前使用的 OBB 检测模型
│   └── README.md
└── README.md
```

这里的 `qianduan` 包含网页代码和云端 Flask 服务；`houduan` 是摄像头、GPS 与模型推理所在的边缘设备程序。两者通过 HTTP API 通信。

## 本地启动（Ubuntu）

建议使用 Python 3.10～3.12。先启动网页服务，再启动采集端。

**终端 1：**

```bash
cd qianduan
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
test -f .env || cp .env.example .env
# 本地交付版已配置高德凭证，不要覆盖已有 .env
python server/app.py
```

访问 `http://127.0.0.1:5000`。

**终端 2：**

```bash
cd houduan
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_runtime.txt
test -f .env || cp .env.example .env
# 同机联调：在 .env 中设置 CLOUD_BASE_URL=http://127.0.0.1:5000
python core.py
```

摄像头默认先尝试设备号 `2`，失败时会依次检查 `0、1、2`。在网页打开「实时检测监控」，确认已收到有效位置后开始巡检。若前端和采集端不在同一台机器，将 `CLOUD_BASE_URL` 改为网页服务器实际的可访问地址。

## 关键技术

| 模块 | 方案 |
|---|---|
| 图像检测 | YOLOv8-OBB ONNX，ONNX Runtime |
| 旋转框 | 旋转坐标解码、`NMSBoxesRotated`、四顶点绘制 |
| 视频 | OpenCV 采集、独立推理/编码、MJPEG 低延时视频流 |
| 后端 | Python Flask HTTP API |
| 网页 | Jinja2、原生 JavaScript 和 CSS |
| 定位 | NMEA RMC 串口 GPS、手机浏览器 Geolocation（仅手机采集页） |
| 地图 | 高德 JS API 2.0，WGS84 → GCJ-02 显示转换 |
| 数据 | JSON 检测记录与本地图片文件；支持 CSV 导出 |

## 文档

- [网页、Flask 和高德地图配置](qianduan/README.md)
- [边缘设备、OBB 推理和 GPS 联调](houduan/README.md)

## 发布说明

项目默认面向本地联调。正式部署时请启用 HTTPS、反向代理、设备鉴权，并使用适合并发访问的记录存储服务。高德安全密钥、设备令牌和 HTTPS 私钥保存在 `.env`/私有证书文件中，均不应提交至仓库；已经公开的旧凭证应重新签发。真实定位、视频帧率及地图授权须在实际设备与网络中验证。
