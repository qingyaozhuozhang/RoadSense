# RoadSense 云端服务部署说明

本目录包含云端服务的 systemd 配置文件 `roadsense.service`，用于在 Linux 主机上管理 Flask 服务进程。

## 1. 服务参数

部署前应核对服务文件中的运行用户、工作目录及 Python 可执行文件路径，确保其与目标服务器上的实际安装位置一致。项目的主程序入口为 `qianduan/server/app.py`。

## 2. systemd 服务管理

将确认后的服务单元安装至系统目录，并重新加载 systemd 配置：

```bash
sudo cp deploy/roadsense.service /etc/systemd/system/roadsense.service
sudo systemctl daemon-reload
sudo systemctl start roadsense
sudo systemctl status roadsense --no-pager
```

常用维护命令：

```bash
sudo systemctl restart roadsense
sudo systemctl stop roadsense
sudo journalctl -u roadsense -n 100 --no-pager
```

运行进程不依赖 SSH 或远程终端窗口保持开启。是否启用开机自启动由部署管理策略决定；执行 `sudo systemctl enable roadsense` 后方可配置开机启动。

## 3. 网络与安全

运行服务需要开放相应的访问端口，并确保云端 `.env` 中的监听地址、端口及会话安全参数与实际环境匹配。生产环境建议在受信任代理后配置 HTTPS，并限制设备凭据、数据库和归档图片目录的访问权限。

完整部署及数据管理要求参见 `../README.md`。
