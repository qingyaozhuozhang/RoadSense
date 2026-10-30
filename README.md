# RoadSense · 道路智能巡检平台

RoadSense 是一个**云端管理 + 多台边缘巡检设备 + Android 手机节点**的道路损伤检测与归档系统。边缘端负责摄像头、YOLOv8-OBB 与双来源定位；云端提供实时监控、账户权限、设备管理、损伤档案和 SQLite 持久化；手机端提供原生定位、拍照识别及确认归档。

> **部署说明**：本交付包按使用者要求包含现有运行配置（两份 `.env`、高德 Key 和管理员账号的密码哈希）。公开到 GitHub 后这些配置也会公开，请自行承担密钥被盗用和管理会话泄露的风险；部署前推荐轮换密钥。SQLite 真实业务数据、历史照片和 JKS 私钥仍不包含。

## 目录

```text
RoadSense/
├── qianduan/              # 云端 Flask API、页面、SQLite、设备管理、ONNX 推理
│   ├── server/            # API、账户认证、设备隔离、记录存储
│   ├── frontend/          # HTML / CSS / JS / 静态图片
│   ├── models/best.onnx   # 云端手机图片识别模型
│   ├── deploy/            # systemd 示例和设备环境模板
│   ├── tests/             # 云端测试
│   └── README.md
├── houduan/               # 树莓派或 Ubuntu 边缘端
│   ├── core.py            # 主程序
│   ├── obb_inference.py   # YOLO-OBB 解码和绘制
│   ├── location_state.py  # 硬件 GPS 优先、手机定位备用
│   ├── best.onnx          # 边缘推理模型
│   └── README.md
├── Android_Studio/        # Kotlin Android 工程
│   ├── app/
│   └── README.md
├── docs/
│   └── ROAD_DAMAGE_ALGORITHM.md
└── .gitignore
```

## 核心能力

- **多设备隔离**：每台边缘设备通过独立 `DEVICE_ID` + `DEVICE_TOKEN` 上报；云端按设备隔离视频、控制指令和损伤记录。
- **账号与权限**：管理员管理设备、成员与损伤数据；普通用户访问所属设备。手机定位的无设备用户需由管理员单独授权。
- **视频与检测**：摄像头持续采集，使用 ONNX Runtime 执行 YOLOv8-OBB，按设备上传实时画面与状态。
- **双定位来源**：有效硬件 GPS 优先；GPS 无效时，可使用与该设备关联的已授权手机定位；两者均无效，不自动归档。
- **道路损伤归档**：使用检测面积占比确定等级，并对同一巡检任务内空间距离 20 米以内、等级未上升的事件去重；云端保存 SQLite 记录及图片文件。
- **手机拍照归档**：先上传预览，取得有效定位后再由用户“确认归档”；照片按 `minor_账号_时间_标识` 命名，摄像头记录按 `primary_账号_时间_标识` 命名。

## 快速开始

推荐先部署云端，再运行边缘端，最后安装 Android。三个子项目均有独立使用说明：

1. **云端**：阅读 [qianduan/README.md](qianduan/README.md)。安装依赖、配置 `qianduan/.env`、初始化管理员，再启动 `python server/app.py` 或配置后台服务。
2. **边缘**：阅读 [houduan/README.md](houduan/README.md)。检查已提供的 `houduan/.env` 中设备身份和实际串口，安装依赖并运行 `python core.py`。
3. **Android**：阅读 [Android_Studio/README.md](Android_Studio/README.md)。配置 Android SDK/JDK；云端 URL 和高德 Android Key 已包含在 app 模块，然后用 Android Studio 编译。

当前云端已包含首次启动管理员初始化逻辑；其他部署可运行 `python tools/create_admin.py --username <账号>` 创建自己的管理员。**不要重复使用同一 `DEVICE_ID` 和 `DEVICE_TOKEN`。**

## 记录算法

完整说明：[docs/ROAD_DAMAGE_ALGORITHM.md](docs/ROAD_DAMAGE_ALGORITHM.md)。主要流程为：

```text
摄像头帧 → Letterbox → YOLO-OBB → NMS → 识别面积占比 → 轻微/中度/严重
                                          ↓
                        RUNNING 状态 + GPS/手机有效定位
                                          ↓
                         20 m 空间去重 + 等级提升判断
                                          ↓
                        POST /upload → SQLite + JPEG 文件
```

> 网页视频帧率**不等于 YOLO 推理帧率**。CPU 上每帧推理可能明显慢于摄像头采集；请看边缘端真实 `[AI耗时]` 日志。

## 数据、安全及运行限制

- SQLite 账号、设备、道路损伤等数据放在阿里云实际运行的 `qianduan/data/`；照片保存在磁盘中。历史 ZIP **不含你实际 ECS 上的数据库或照片**，因此新部署会自动初始化空数据库；要保留历史记录，请从原 ECS 服务器安全备份该目录再恢复。按你的要求，如果你把备份后的真实数据放到 `qianduan/data/`，本仓库的 `.gitignore` 不会阻止提交它们。
- `*.onnx` 为模型二进制，已保留在交付包；向公开仓库发布前请确认你有权分发所使用的权重和图片资源。本仓库**未自行指定开源许可证**。
- 原系统使用 HTTP `:5000` 进行内网/测试联调；**公网账号和设备令牌应在可信 HTTPS 下使用**。部署生产环境前还应限制安全组端口并审计权限。
- 云端实时帧缓存和未确认照片预览使用进程内存；若采用 Gunicorn，暂时保持单 worker、多线程，不要盲目增加进程数。
- 此 GitHub 整理版由源码合并及静态检查得到，尚未在真实服务器、树莓派和 Android 真机上完成全链路回归测试。

## 实际数据库备份

历史代码压缩包没有包含你现有 ECS 的 `accounts.sqlite3`、`devices.sqlite3`、`records.sqlite3` 和历史图片。如何从 ECS 安全导出见 [docs/DATA_BACKUP.md](docs/DATA_BACKUP.md)。

## GitHub 上传

在本目录执行：

```bash
git init
git add .
git status       # 本包按要求含 .env；检查没有 SQLite / APK / JKS / local.properties
git commit -m "Initial RoadSense project"
git branch -M main
git remote add origin https://github.com/<your-account>/<your-repository>.git
git push -u origin main
```

先在 GitHub 创建**空仓库**，并替换示例仓库地址。本版按你的选择包含真实 `.env`，即使之后在 `.gitignore` 排除它们，旧提交里的凭据仍然可被读取，需清理历史并轮换密钥。不要将 README 中的示例地址当成真实服务地址。

## 现有运行配置与公开上传

本包**已经包含** `qianduan/.env` 和 `houduan/.env`，预置的首台设备配置与现有云端对应；Android 默认连接现有云端地址，同时保留 `local.properties` 覆盖方式。因为你明确要求一份包含配置的完整工程，这两份 `.env` **不会被 `.gitignore` 忽略**，执行 `git add .` 就会一并发布。请注意：设备密钥、会话密钥和高德 Web 安全密钥都会被所有人看到；公开后建议立即使用新的密钥，尤其不要继续使用聊天里出现过的管理员密码作为正式密码。
