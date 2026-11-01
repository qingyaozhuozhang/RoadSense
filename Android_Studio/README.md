# Android_Studio · RoadSense 手机端

Kotlin Android App，桌面名称为 **RoadSense**。提供道路智能巡检工作台、高德原生手机定位、照片识别/确认归档、账号切换和服务器离线跑酷页面。

## 1. 开发环境

- Windows 10/11 或兼容系统，Android Studio；Gradle JDK 推荐 **21**。
- Android SDK Platform 35；项目 `minSdk 26`、`targetSdk 35`。
- Gradle 8.13；使用 Android Studio **File → Sync Project with Gradle Files** 同步。
- 已安装 Android SDK，且 `local.properties` 中具有 Android Studio 写入的 `sdk.dir`。

## 2. 项目当前配置

本包已在 `app/build.gradle.kts` 中保留原有 `CLOUD_BASE_URL`（HTTP 5000）与高德 Android 定位 Key，可与现有阿里云配套，**不用重新填写 Key**。但发布到 GitHub Public 时这些值同样公开。Gradle 的 Android SDK 路径依赖 Android Studio 自动生成的本机 `local.properties`，该文件无法通用，不属于云端配置。

`gradle/wrapper/gradle-wrapper.properties` 默认使用 Gradle 8.13 官方发行 URL，避免把原 Windows 电脑的绝对 ZIP 路径写入 GitHub。网络受限时，可在自己的电脑将它改为本地 ZIP 路径。当前完整包**缺少 `gradle/wrapper/gradle-wrapper.jar`**（历史工程包也没有）；可运行 `Setup-Windows.ps1` 下载，或从你已成功编译的原工程复制相同的 JAR。私人的发布 JKS 证书同样不在源码包里，Release 签名应继续用 Windows 原有证书。

## 3. 编译和安装

Android Studio 打开 `Android_Studio/`，完成同步后，在 Terminal 运行：

```powershell
.\gradlew.bat :app:assembleDebug
```

生成的调试版一般位于 `app/build/outputs/apk/debug/app-debug.apk`。生成已签名正式 APK：**Build → Generate Signed App Bundle / APK → APK → release**，选择你自己保存的 JKS 证书。本包不包含 `.jks` 发布私钥和签名密码。生成 Release APK 时需要自己的 JKS。

## 4. 使用流程

1. 确认云端已启动；启动 App，使用现有账号登录。
2. 查看工作台定位状态，点击“开始定位”开始上报；点击“停止定位”停止。
3. 管理员账号可直接定位；普通用户若已分配设备，可使用对应设备；未分配设备者默认无权限，需要管理员在云端单独授权。**不增加灰色“禁用”按钮**，无权限时点击会收到明确提示。
4. 拍摄或选择道路图片，进行云端 YOLO 预览；检查结果后点击“确认归档”，必须有有效手机定位和允许的账号权限。
5. 手机账号只访问自己的设备；无设备且已获得权限者可以上传个人定位/照片记录，但不能访问其他用户设备。
6. 服务器暂时不可达时显示内置跑酷页面；恢复服务后进入正常页面，定位不会未经用户操作自动重新开启。

## 5. 注意事项

- HTTP 地址只适合封闭/短期调试；实际公网账号登录建议配置 HTTPS。
- Android 后台定位需要按系统提示授予必要权限，厂商省电策略可能限制长期定位，不保证所有机型息屏都能保持固定周期。
- `AndroidManifest.xml` 声明权限及服务；请以手机实际授权和日志为准，不要将静态编译成功等同于后台定位实测通过。
- 登录凭据和账号权限由云端验证，不把树莓派 `DEVICE_TOKEN` 放进 APK。

### 首次打开项目时 Gradle Wrapper 提示缺失

首次运行 `Setup-Windows.ps1` 下载标准的 Gradle Wrapper JAR；如果访问 GitHub 下载失败，可以复制之前已经成功构建过的 Android Studio 项目中相同版本的 `gradle/wrapper/gradle-wrapper.jar`。本交付包没有附带已验证的 Wrapper JAR，不能声称下载后无需任何工具准备就可构建。


## 7. 首次安装：用户填写哪些配置

Android 不读取 `qianduan/.env` 或 `houduan/.env`，不要复制设备令牌进 APK。用户设置 Android 的云端 URL 与高德 Key 时，建议在项目根目录 `local.properties` **追加**（保留由 Android Studio 写入的 `sdk.dir`）：

```properties
roadsense.cloudBaseUrl=http://39.102.84.218:5000
roadsense.amapAndroidKey=填写你在高德开放平台的Android应用Key
```

这两项也可以直接使用 `app/build.gradle.kts` 中保留的现有默认配置；更换项目包名、发布签名 SHA1 或高德应用后，必须重新创建对应 Android 类型 Key。

> 编译前确认 Android Studio 中 Gradle JDK 为 21。第一次导入缺少 Wrapper JAR 时，执行 `Setup-Windows.ps1` 或从已有可编译项目复制 `gradle/wrapper/gradle-wrapper.jar`。正式 APK 需要原有 JKS 签名文件；此包不包含 JKS。

**实际使用流程**：先确保云端运行，安装 RoadSense → 注册/登录 → 在工作台选择本人设备（未分配设备的普通用户须由管理员另外授权定位）→ “开始定位” → 实时位置与地图更新 → 拍照 → 云端 YOLO 预览 → “确认归档”（要求有效实时坐标）→ “停止定位”。

NEO-F10N 是树莓派侧的硬件设备；Android App 不需要加载任何 Python GPS 驱动，其原生定位上传机制不受本次替换影响。
