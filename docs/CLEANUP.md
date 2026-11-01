# GitHub 文件清理与保留说明

已清理旧版重复 README、树莓派旧扫码页面、预览图副本、个人签名证书指引、已停用扫码网页、缓存、构建目录、虚拟环境、临时 ZIP、历史压缩包本身未提供的 SQLite 运行数据库及历史照片（不会伪造或用空库替代）。

已保留：完整云端前端/服务端、边缘 YOLOv8-OBB 及 GPS、Android Kotlin 源码、两套 ONNX 模型、单元测试、部署服务文件和 4 份 README。

根据使用者明确要求，**完整包保留实际 `qianduan/.env`、`houduan/.env`、高德 Android Key 和云端默认地址**。本包并不是安全脱敏的公开仓库模板；如果上传到 GitHub，密钥可被任何人获取和复用。

不包含：阿里云实际 SQLite 用户/设备/道路损伤记录，历史照片，Android 发布证书、Keystore 密码、Android 本地 SDK 文件与 Gradle 缓存。

公开 GitHub 之前，可使用 `git status --short` 审核待提交文件；如果选择继续发布，请自行承担凭据泄露风险，并在生产环境轮换凭据。
