# qianduan · RoadSense 云端服务

本目录包含统一 Web 控制台、Flask API、SQLite 持久化、多设备管理、照片 YOLO 推理及设备定位服务。

## 1. 运行要求

- Ubuntu 22.04/24.04 或兼容 Linux；Python 3.10+。
- `models/best.onnx` 存在；云端执行手机照片的 ONNX 推理。
- 至少一个可访问的 TCP 端口（示例 5000），服务器须有写入 `data/` 的权限。

## 2. 安装与配置

```bash
cd qianduan
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# 已包含 .env；迁移新服务器时可按 .env.example 调整
```

`qianduan/.env` 已包含之前使用的部署值。第一次运行前核对：

- `HOST=0.0.0.0`、`PORT=5000`：绑定地址及端口。
- `AMAP_JS_KEY`、`AMAP_JS_SECRET`：已使用历史项目中的高德 Web JS API Key 与安全密钥。
- `DEVICE_TOKEN`：**首台设备**令牌，和本包 `houduan/.env` 中的主设备令牌相同。
- `BOOTSTRAP_DEVICE_ID=pi-main`：首台设备编号；更多设备在管理员后台新增，每台获取独立令牌。
- `DEVICE_CLOUD_BASE_URL=http://YOUR_SERVER_IP:5000`：管理员下载设备 `.env` 时使用的云端可达地址（若文件中没有此键，使用默认部署值）。
- `SESSION_COOKIE_SECURE=0` 仅供本地 HTTP 测试；接入 HTTPS 后设置为 `1`。
- `ROADSENSE_DATA_DIR` / `PHONE_YOLO_MODEL`：留空或不设置时分别使用 `data/` 与 `models/best.onnx` 的默认位置。

**交付包按使用者要求包含已填好的 `.env`；公开仓库会暴露设备令牌和高德密钥。请在生产部署前轮换这些值。**

## 3. 首次创建管理员

现有代码自带两个预置管理员账号的**加盐密码哈希**（`server/admin_seeds.py`），第一次创建空数据库时自动写入。已有阿里云 `data/accounts.sqlite3` 的话，应当保留原数据，避免账号状态丢失。

可使用 `python tools/check_admin_login.py` 仅在服务器本机检查账号；如需创建额外管理员，也可运行 `python tools/create_admin.py --username <登录名>`（输入密码时不回显）。

**注意：历史压缩包不包含 ECS 上的真实数据库；新环境启动只创建新数据库，原损伤记录不会自动恢复，必须另外复制 ECS 上 `data/`。**

## 4. 启动与检查

调试环境：

```bash
python server/app.py
```

在本机检查：

```bash
curl -i http://127.0.0.1:5000/api/health
```

浏览器进入 `http://YOUR_SERVER_IP:5000/login`，登录后使用 `/admin` 管理设备和账号。注册、验证码、设备可见范围和管理员权限在服务器端校验。

## 5. 关闭终端后保持运行（systemd）

部署示例在 `deploy/roadsense.service`。当前 service 已按历史部署路径 `/home/admin/RoadSense/qianduan` 和 `/home/admin/Intelligent-Road-Inspection-System/venv/bin/python` 设置；如实际环境不同再修改，然后：

```bash
sudo cp deploy/roadsense.service /etc/systemd/system/roadsense.service
sudo systemctl daemon-reload
sudo systemctl disable roadsense        # 不随系统开机自动启动
sudo systemctl start roadsense          # 后台运行；可以关闭 SSH 窗口
sudo systemctl is-active roadsense      # active 说明运行中
```

真正停止：

```bash
sudo systemctl stop roadsense
```

更新代码后：`sudo systemctl restart roadsense`。日志：`sudo journalctl -u roadsense -n 100 --no-pager`。

> 现有项目的 Flask 开发服务器仅用于测试；生产建议单 worker + 多线程 Gunicorn、受信任反向代理与 HTTPS，避免开发服务器直接暴露公网。

## 6. 数据库、设备和照片

运行时在 `data/` 自动建立：

| 文件或目录 | 内容 |
|---|---|
| `accounts.sqlite3` | 用户、密码哈希、管理员/普通用户角色、定位授权 |
| `devices.sqlite3` | 设备编号、所属账号、独立设备令牌哈希 |
| `records.sqlite3` | 损伤记录、来源身份、管理员审计日志 |
| `images/` | 原始及识别图像；数据库保存文件名 |

旧版 `records.json` 可在首次初始化时迁移；**请先备份现有 `data/`**；本次代码压缩包不会自动提取云端历史数据库。系统使用 SQLite WAL 处理单实例并发；真正跨多个云工作进程时还需要独立状态服务。

管理员可以新增设备、为设备分配普通用户，下载设备专属完整 `.env`；一个普通用户最多关联一台边缘设备。管理员也可以为未分配设备的普通用户单独开通手机定位权限。普通用户不能读取其他人的设备视频/归档图片。

## 7. 主要接口

- `GET /api/health`：服务健康检查。
- `POST /update_stream`：经过设备令牌认证的实时画面上传。
- `POST /upload`：设备损伤记录入库。
- `GET /api/phone_location`：对应设备读取关联手机的备用坐标。
- `POST /api/receive_location`：经登录会话与定位权限检查的手机坐标上传。
- `POST /api/phone/preview`、`POST /api/phone/archive`：手机先预览、确认后归档。

不建议直接在浏览器控制台手写请求绕过前端；所有操作都需要身份和权限校验。

## 8. 测试与安全

```bash
python -m unittest discover -s tests -v
```

若依赖或摄像头模拟环境不齐，先跑单独的 SQLite/纯 Python 测试再排查。所有密码应当以哈希存储；管理员只能重置普通用户密码，不能读取原始明文。有关路面检测与去重请看仓库 `docs/ROAD_DAMAGE_ALGORITHM.md`。

### 已配置的部署路径

云端 `.env` 默认将模型指向 `/home/admin/RoadSense/qianduan/models/best.onnx`，数据目录为 `/home/admin/RoadSense/qianduan/data`；你的系统若安装在别处，请调整对应项。`qianduan/deploy/roadsense.service` 指向你曾使用的共享 Python 虚拟环境路径，务必在不同服务器上检查 `ExecStart`。使用 `sudo systemctl disable roadsense` 可取消开机启动，但不会停止正在运行的服务。


## NEO-F10N 边缘位置同步（本版）

无需在阿里云安装 GPS 串口驱动。新版 `houduan` 通过 `/update_stream` **每 0.5 秒**发送最新有效位置状态；云端原有接口即可接收。硬件 GPS 优先、Android 手机备用。

管理员在 **设备与数据管理** 添加或轮换设备后，可下载完整的 `houduan/.env`，模板位于 `qianduan/deploy/houduan.env.template`，已包含 `GPS_UPLOAD_INTERVAL=0.5`、`PHONE_POLL_INTERVAL=0.5`。

第一次部署请检查本目录 `.env` 的 `HOST`、`PORT`、`ROADSENSE_DATA_DIR`、`PHONE_YOLO_MODEL`、`ROADSENSE_SECRET_KEY`、`AMAP_JS_KEY`、`AMAP_JS_SECRET`、`BOOTSTRAP_DEVICE_ID` 和 `DEVICE_TOKEN`：

- 本交付包保留了现有配置；切换服务器后 **修改目录路径与公网地址**，不能把旧服务器的绝对路径视为通用路径。
- `DEVICE_TOKEN` 在云端是首台设备初始化令牌；新设备由管理员页面生成独立令牌。
- SQLite 的历史数据与图片只在实际服务器 `qianduan/data/`，源码包中的数据不能代替线上数据库；更新时要先备份，不要覆盖旧数据。

用 Ubuntu 常驻方式运行（**不设置开机自启**）：

```bash
sudo systemctl disable roadsense  # 若曾启用开机自启
sudo systemctl start roadsense    # 手动启动，关闭 Workbench 后持续运行
sudo systemctl status roadsense --no-pager
sudo systemctl stop roadsense     # 手动关闭
```

如果尚未安装服务文件，按 `deploy/roadsense.service` 中实际 Python 虚拟环境路径创建服务；第一次建议先 `python server/app.py` 验证依赖。
