# RoadSense 高德地图 JavaScript API 2.0：完整接入及本地测试

> 此项目在「流畅视频 + YOLOv8-OBB」版本上 **只更换地图渲染组件**。不修改检测模型、25 FPS 本地视频通道、设备 GPS 优先逻辑、手机定位上报接口、损伤记录存储规则。
>
> **本版本不包含真实的高德 Key 或安全密钥。** 你之前在聊天里公开过一组凭证，请到高德控制台**重置**后再使用。安全密钥只保存在本机 `.env` 中，不要提交到 GitHub。

## 1. 要选哪种高德 Key？

本项目使用 **高德地图 JS API 2.0（交互式）**，不是旧版 `/v3/staticmap` 静态图片。进入 [高德控制台](https://console.amap.com/)，在「应用管理 → 我的应用」中新建或选中应用，点击「添加 Key」，**服务平台选择「Web端（JS API）」**。

成功后得到两项：

- `Key`：填到 `AMAP_JS_KEY`。这项允许出现在前端加载请求中，务必在控制台设置合理的来源限制。
- `安全密钥 / securityJsCode`：填到 `AMAP_JS_SECRET`。**只能保存在 Flask 和树莓派的私有 `.env`**，绝不能直接写到 HTML/JavaScript。

如果你现在持有的 `map` Key「服务平台」是「Web 服务」，需新建一个「Web端（JS API）」Key；Web 服务 Key 不能替代 JS API Key。新 Key 创建日期在 2021-12-02 之后的，官方要求同时使用安全密钥。

官方文档：
- https://developer.amap.com/api/javascript-api-v2/guide/abc/load
- https://developer.amap.com/api/javascript-api-v2/guide/abc/jscode
- https://developer.amap.com/api/javascript-api-v2/guide/transform/convertfrom

## 2. 在电脑本地填 Key（Ubuntu）

在项目 `qianduan` 根目录编辑 `.env`。本版本提供空白的 `.env` 和 `.env.example`；请将控制台**重置后的**新值填入：

```env
AMAP_JS_KEY=你的Web端JSAPI_Key
AMAP_JS_SECRET=你的securityJsCode安全密钥
HOST=0.0.0.0
PORT=5000
```

文件必须在 `qianduan/.env`，**不是** `qianduan/server/.env`。不要加引号、不要在等号前后加空格。修改 `.env` 后要 **完全停止并重新启动 Flask 进程**。

如果在高德控制台开启了来源域名/白名单，请按控制台界面要求加入本地实际使用的来源，例如 `localhost`、`127.0.0.1` 和你进行局域网联调时的主机名；部署后应改成你自己真正的 HTTPS 域名。控制台允许的具体格式、白名单策略以当时的页面为准。

## 3. 本地运行（不需要树莓派，也不需要 GPS）

```bash
cd qianduan
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server/app.py
```

电脑浏览器依次打开：

1. `http://127.0.0.1:5000/api/amap_check`：检查配置；`js_key_present`、`js_security_code_present` **都应为 true**。**仅表示填了凭证，不表示凭证有效**。
2. **`http://127.0.0.1:5000/map-demo`**：独立地图演示，使用固定的**北京示例坐标**，应可以看到高清街道地图、地图拖动/缩放、道路/明亮/夜间切换。点击「测试损伤标记」会生成**只存在于浏览器页面**的 3 个示例标记，**不会写进巡检档案**。
3. `http://127.0.0.1:5000/gps`：只有硬件 GPS 或手机提供了有效坐标后才显示真实位置。这里没有坐标时出现「等待定位」是**正常现象**，不要误判为地图故障。
4. `http://127.0.0.1:5000/location`：电脑端等待手机位置（无浏览器定位权限请求）。
5. `http://127.0.0.1:5000/detection`：实况视频 + 高德地图。
6. `http://127.0.0.1:5000/severe`：如果没有真实记录，显示原有航拍风格占位图；有记录后才显示高德真实坐标点。

检查时打开 Chrome/Firefox 开发者工具 F12 → Console / Network。地图必须能够访问 `webapi.amap.com`、`restapi.amap.com` 的上游服务；Flask 服务器本身也需要能联网。出现 API 错误要记录错误码和信息，**不要贴出 Key/安全密钥**。

## 4. 为什么不把安全密钥直接写到网页？

项目实现了 Flask 安全代理：

- 网页从 `/api/map_config` 获取 **Web端 Key（公开）**及同源代理路径 `/_AMapService`，**不返回安全密钥**。
- 网页设置 `window._AMapSecurityConfig.serviceHost`，再通过高德官方 `loader.js` 加载 API 2.0。
- 高德 JS API 对相关服务的请求通过 Flask 的 `/_AMapService/...` 发起；Flask 从本地环境变量读取 `AMAP_JS_SECRET`，附加 `jscode`，转发到高德固定官方域名。
- 按高德官方安全代理规则，`/_AMapService/v4/map/styles` 使用 `webapi.amap.com`，其他使用 `restapi.amap.com`。不会将任意 URL 作为代理目标。

**注意**：Key 本身仍是浏览器可见的公开标识；正式公网部署前应在高德控制台设置允许的域名，给 `/_AMapService/` 配置 Nginx 限流和 HTTPS，并检查 API 用量。安全代理能够隐藏 securityJsCode，但**不能单靠它防止接口滥用**。

## 5. 地图与 GPS 坐标为什么不会偏？

硬件 GPS/手机提供的是 **WGS84**，原始经纬度继续存储、上传、显示在数据面板中，**绝对不修改数据库里的数值**。

高德地图在国内采用 **GCJ-02**，只有浏览器地图上绘制标记和移动中心点时做 **WGS84 → GCJ-02** 近似转换。这能避免之前在国内高德底图上直接画 WGS84 带来的数百米偏移。对于车道级精准定位，仅靠手机或普通 GPS 不能保证，需要 RTK 等高精度方案。

## 6. 连接真实树莓派 + 手机节点

电脑先启动 Flask 服务。树莓派在 `houduan/.env` 中填写（将 `.env.example` 复制为 `.env`）：

```env
CLOUD_BASE_URL=http://电脑局域网IP:5000
PI_LAN_HOST=树莓派局域网IP
AMAP_JS_KEY=你的Web端JSAPI_Key
AMAP_JS_SECRET=你的securityJsCode安全密钥
```

**手机端如果不需要显示地图**，可以不在树莓派填写两项高德 Key；定位上传本身与地图服务独立。若希望树莓派 `https://树莓派IP:8080/phone` 手机页面同时显示地图，则必须设置它们。

然后在 `houduan` 运行（按你的设备环境安装依赖）：

```bash
pip install -r requirements_runtime.txt
export ORT_PROVIDER=cpu
export CAMERA_INDEX=2
python core.py
```

手机必须能够访问树莓派真实的 HTTPS 地址，并且信任该 TLS 证书，否则手机浏览器不能访问 GPS。电脑的 `127.0.0.1` **不能**直接作为手机跨设备地址；不需要 Android USB 转发。手机节点上报坐标后电脑 `/location` 与 `/gps` 会显示；硬件 GPS 正常时始终优先。

## 7. 阿里云上线时的步骤

1. 先在本地执行 `/map-demo` 测试、手机坐标测试、摄像头测试，确认均正常。
2. 备份服务器旧版数据目录 `data/`，尤其是 `records.json`、图片，以及旧版配置。
3. 上传 `qianduan` 至服务器（**不包括**你电脑上的 `.env` 或证书私钥）。
4. 在服务器私下新建 `qianduan/.env`，填新的 `AMAP_JS_KEY` 和 `AMAP_JS_SECRET`；使用平台控制台允许的生产域名限制。
5. 通过 Gunicorn 等 WSGI 服务运行 Flask，通过 Nginx 提供 HTTPS 以及限流。保证 `/_AMapService/` 和 `/api/map_config` 能被路由到 Flask，而不是静态前端。
6. 为手机 `/phone` 使用受信任的 HTTPS 域名和证书，手机位置授权才能工作。
7. 重启服务，再打开 `/api/amap_check` 与 `/map-demo`，确认正式域名加载地图后，再连接树莓派进行实际巡检。

不要直接把 `python server/app.py` 的开发服务器用作公网长期服务。

## 8. 常见问题速查

| 现象 | 检查方法 |
|---|---|
| 地图提示未配置 Key | `qianduan/.env` 中 `AMAP_JS_KEY`、`AMAP_JS_SECRET` 是否填写？重启 Flask 后看 `/api/amap_check`。 |
| `map-demo` 仍空白 | F12 Network 是否能下载 `https://webapi.amap.com/loader.js`？高德控制台是否选择 **Web端（JS API）**？ |
| Key 无效 / 权限拒绝 | 是否把 Web服务 Key 误填给 JS API？是否使用了已重置的旧 Key？是否有域名白名单限制？ |
| 浏览器出现 `/_AMapService/...` 502 | 电脑或 Flask 无法访问高德上游；确认电脑网络、DNS、代理、证书。 |
| 真实地图没有定位标记 | 先打开 `/map-demo`。如果演示正常，查看 `/api/location_status` 是否有有效 GPS；与 API Key 无关。 |
| 位置偏到隔壁街道 | 检查纬度/经度顺序；后台保存 WGS84，地图端统一转换成 GCJ-02；普通 GPS 精度不足也可能造成偏差。 |
| 手机点开始定位却失败 | 必须是**手机能实际访问**的受信任 HTTPS 地址，并授权手机定位；单纯同 Wi-Fi 或 `http://` 不够。 |
| 档案中还显示插画 | 说明没有经过筛选的有效经纬度记录；导入/模拟后会自动换成真实高德地图。 |
| 高德地图是否会拖慢 YOLO？ | 地图在浏览器渲染，与树莓派 OBB 模型推理、视频线程分离；网络差可能拖慢地图加载，但不应堵塞本地摄像头。 |

**最后检查：** `/map-demo` 页面是最先要测试的。它展示的是固定示例点，不需要硬件设备；高德地图能显示以后，再检查 GPS 与手机的真坐标。
