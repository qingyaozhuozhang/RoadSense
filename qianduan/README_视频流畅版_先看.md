# RoadSense 本地视频流畅版 · 2026-10-22

本版基于上一版 `RoadSense_街道精细地图_OBB旋转框_视频流优化_完整本地联调.zip` 修改，保持原有 `qianduan/`、`houduan/`、GPS/手机节点和 OBB 识别功能。**只优化视频显示链路，不替换模型。**

## 为什么 runs.zip/example.py 比旧版网页流畅？

`runs.zip` 的 `example.py` 用 `cv2.VideoCapture(2)` 采集，使用 `cv2.imshow()` 本地显示，完全绕开每帧 HTTP POST → Flask → 浏览器的视频传输，且两边模型任务不同（example：YOLO11 Detect；本项目：YOLOv8 OBB）。旧版前端视频虽然是 MJPEG，但发送端限制 `VIDEO_UPLOAD_FPS=8`，并且每个 POST 都同步等待服务器响应，网络一次延迟便造成直接跳帧。

## 新视频链路

```text
                         ┌→ 独立 YOLOv8-OBB 推理（只更新最近斜框）
摄像头持续采集(0/1/2) ────┤
                         └→ 25 FPS 独立编码 → 最新帧缓存（丢弃旧帧）
                                             ├→ 本地直连 :8091/live.mjpg → 浏览器（优先）
                                             └→ 网络上传 :5000/update_stream → Flask /video_feed（备用）
```

- 摄像头采集不等待模型推理；MJPEG 编码不等待 HTTP 上传；HTTP 上传也不等待新推理结束。
- 浏览器在本地 HTTP 页面优先选低延时直连地址，直连失败自动回退 Flask 中继。
- Flask 通过帧到达事件即时唤醒浏览器流，移除了原来每 55 ms 轮询一次的固定间隔。
- 原有 `/update_stream`、`/video_feed`、`/api/video_status` 接口兼容保留。
- 视频优先显示最新帧，而不是排队播放几秒前积压的帧；低帧率 AI 检测框按最近有效结果叠加。
- 默认约 25 FPS 本地预览、20 FPS 云端上传目标，**这是目标值，不保证所有硬件都能达到**。

## 一、同一台 Ubuntu 电脑联调（推荐先执行）

打开第一个终端：

```bash
cd qianduan
python -m pip install -r requirements.txt
python server/app.py
```

再开第二个终端：

```bash
cd houduan
python -m pip install -r requirements_runtime.txt
export CLOUD_BASE_URL=http://127.0.0.1:5000
export CAMERA_INDEX=2
export ORT_PROVIDER=cpu
export VIDEO_RENDER_FPS=25
export VIDEO_UPLOAD_FPS=20
export VIDEO_OUTPUT_WIDTH=640
export VIDEO_JPEG_QUALITY=50
export DIRECT_VIDEO_ENABLED=1
python core.py
```

**先不启动 YOLO 巡检也能检查摄像头流：**

- http://127.0.0.1:8091/live.mjpg —— 摄像头低延时直连视频，正常时应能直接看到连贯画面。
- http://127.0.0.1:8091/health —— 显示 `render_fps`（视频编码速率）；`/api/video_status` 还报告 `camera_capture_fps`（真实摄像头采集速率）、`preview_render_fps`（编码速率）和 `video_fps`（云端收到的速率）。
- http://127.0.0.1:5000/video_feed —— 云端中继视频，理论上比直连更容易受网络影响。
- http://127.0.0.1:5000/api/video_status —— 云端接收的 `video_fps`。
- http://127.0.0.1:5000/detection —— 正式仪表盘；此页需要有效的 GPS/手机定位才能进入巡检控制。

真正开始 AI 识别前，建议先比较 **8091 直连** 和 **5000 云端中继**：若 8091 流畅、5000 卡，瓶颈在 HTTP 上传/服务器/网络；两边都卡，重点检查摄像头采集帧率、分辨率和 CPU 占用。

## 二、树莓派摄像头 + 电脑 Flask，同一个局域网

电脑启动 `qianduan/server/app.py`。树莓派上（将示例 IP 换成真实地址）：

```bash
cd houduan
export CLOUD_BASE_URL=http://<电脑IP>:5000
export DIRECT_VIDEO_BIND=0.0.0.0
export DIRECT_VIDEO_PUBLIC_HOST=<树莓派IP>
export CAMERA_INDEX=2
export ORT_PROVIDER=cpu
export VIDEO_RENDER_FPS=25
export VIDEO_UPLOAD_FPS=20
python core.py
```

浏览器打开电脑上的 `http://<电脑IP>:5000/detection`。页面从后端获得树莓派所公布的 `http://<树莓派IP>:8091/live.mjpg`，自动优先直连。手机热点或某些路由器有客户端隔离时，浏览器可能无法直连树莓派，网页会切回云端视频。**8091 是无登录凭证的本地视频预览端口，只能在可信局域网开放，千万不要映射到公网**。

若使用 HTTPS 网站，则浏览器可能阻止 HTTP 直连（混合内容），网页自动使用同源 Flask 视频流；公网生产部署应使用 TLS 反向代理处理直连，而不是把 8091 裸露到互联网。

## 三、实际测速（避免只看感觉）

需要先启动 `core.py`，然后执行：

```bash
cd houduan
python tools/video_fps_check.py --url http://127.0.0.1:8091/live.mjpg --seconds 10
python tools/video_fps_check.py --url http://127.0.0.1:5000/video_feed --seconds 10
```

如果运行在树莓派，测速 URL 的主机地址应改为相应的树莓派/电脑 IP。

测试本次「视频直连管道」本身能否持续传送画面（**合成帧，不代表真实摄像头 FPS**）：

```bash
python tools/benchmark_preview_transport.py
```

在本次构建环境的合成 JPEG 测试中，目标 25 FPS，传送约 25.2 FPS；实际摄像头、YOLO 与网络仍需在你的机器上测试。

## 四、如果还是卡

1. 先看 `/api/video_status` 的 `camera_capture_fps`（摄像头采集速率）。如果摄像头采集不足 15 FPS，即使编码显示 25 FPS，也可能重复旧图像而不是真正流畅。再查看 `http://127.0.0.1:8091/health` 的 `render_fps`。若低于 15，先测试摄像头是否真的能采集 25/30 FPS；执行 `v4l2-ctl --list-formats-ext -d /dev/video2` 检查 USB 模式与分辨率；默认会尝试 MJPG、640×480、30 FPS。
2. `render_fps` 高，但 Flask `/api/video_status` 的 `video_fps` 低，说明瓶颈在图片上传延迟/带宽，尽量优先用 8091 直连；也可降低 `VIDEO_OUTPUT_WIDTH=480` 和 `VIDEO_JPEG_QUALITY=40`。
3. 如果视频连贯但 OBB 旋转框变化慢，说明 AI **推理 FPS** 低，不是显示 FPS 低。可以在 `core.py` 日志查看 `AI Nms`，运行 `python tools/benchmark_obb.py --model best.onnx --loops 20`。CPU 上速度受模型和设备制约；独立视频线程能避免画面随推理停顿，但不能凭空提高推理吞吐量。
4. 如果浏览器一直用“云端 MJPEG”，检查浏览器网页是否为 HTTP、本地 8091 是否可打开、`DIRECT_VIDEO_BIND` 与 `DIRECT_VIDEO_PUBLIC_HOST` 是否正确。
5. 同一台电脑不要同时运行 `runs.zip/example.py` 与 `core.py` 打开同一个摄像头，否则设备占用和驱动抢占可能造成黑屏/卡顿。

## 五、此次关键修改文件

- `houduan/core.py`：新增独立视频帧生产、直连/上传线程，取消 8 FPS 上限，摄像头 MJPG/30 FPS 采集尝试。
- `houduan/video_pipeline.py`：只保留最新一帧的同步对象 + 8091 直连 MJPEG 服务。
- `houduan/tools/video_fps_check.py`：直接测 MJPEG 接收帧率。
- `houduan/tools/benchmark_preview_transport.py`：合成视频链路吞吐能力测试。
- `qianduan/server/app.py`：视频帧 `Condition` 通知与直连信息接口，同时保留原 API。
- `qianduan/frontend/static/js/detection.js`：网页优先选择直连，异常时自动退回 Flask 视频。

注意：本环境无 Flask 和 onnxruntime，未能进行完整设备联调。本次已完成核心代码语法、OBB 单元测试和直连 MJPEG 流功能测试。**不能保证未知机器无卡顿**，但解除的 8 FPS 硬上限和同步网络等待是确定的问题。
