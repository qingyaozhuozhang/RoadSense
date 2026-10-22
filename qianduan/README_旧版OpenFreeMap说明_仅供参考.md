# RoadSense · OpenFreeMap 免 Key 地图版

## 改动
- 地图：采用 MapLibre GL JS + OpenFreeMap Liberty / Bright / Dark，**不需要高德 Key，也不需要注册新 Key**。
- 实时监控、GPS 模块定位、手机节点定位（电脑端和手机采集端）、损伤档案均使用同一地图组件。
- 经纬度使用 WGS84，前端不做 GCJ-02 偏移。地图可拖动缩放、切换底图，档案标记可点开查看等级、时间、位置。
- 无损伤记录时仅显示现有城市航拍插画；有有效记录时显示真实在线地图和记录坐标。
- 保留原有摄像头和 YOLO、GPS 模块优先、手机辅助定位、批次记录、视频等功能。

## 本地运行

```bash
cd qianduan
pip install -r requirements.txt
python server/app.py
```

电脑打开 http://127.0.0.1:5000；网页 /gps、/location、/detection、/severe。
地图组件经 cdn.jsdelivr.net 加载，在线地图经 tiles.openfreemap.org 加载，**浏览器需要能访问这两个域名**。如本地网络无法访问其中任一个，页面会给出地图网络提示，定位数据和损伤记录仍可正常使用。

可以用 `python demo/simulate_device.py --scenario both --seconds 90 --records 5 --auto-start` 运行模拟设备（另开终端、保持云端运行）。

真实树莓派：在 houduan 目录设置 `CLOUD_BASE_URL=http://电脑局域网IP:5000`，安装 `requirements_runtime.txt` 并执行 `python core.py`。摄像头优先序号 2，不可用时扫描 0、1、2。真实手机定位仍需可信 HTTPS，直接通过手机页面 URL/二维码访问；不需要 USB。

## 验证
- 浏览器打开 /api/map_provider，预期 `needs_key:false`。
- 浏览器打开 /severe，只有有效记录才显示真实地图；无记录展示一张占位图。
- /gps 模块坐标更新时显示蓝色定位点。
- /location 手机上报后地图显示手机坐标，不从电脑浏览器取 GPS。
- /phone 手机采集权限受 HTTPS 信任约束（不是地图本身的问题）。

## 注意
OpenFreeMap 是第三方公网地图服务，提供免 Key 公共实例但不保证网络覆盖和 SLA。如果中国大陆网络无法访问相关域名，需要改用可访问的合规瓦片服务或自托管；本项目不会静默退回高德。地图版权归属在底图控件中显示。


### 2026-10 地图精细度升级
默认精细道路地图已改为接近街道级放大（18 级），并加入“◎ 跟随定位”按钮，可缩放与跟随 GPS。
中国大陆部分地区的 OSM/ OpenFreeMap 路网数据确实不够完整，**放大无法创造缺失的道路**，GPS 本身也不等于车道级定位。
可选卫星影像：自行免费申请 MapTiler 浏览器 Key，放入 `qianduan/.env` 的 `MAPTILER_KEY=...`，重启 Flask 就会出现“卫星”切换按钮。浏览器 Key 应在服务商后台限制允许访问的域名。不要将私有密钥交付前端。
