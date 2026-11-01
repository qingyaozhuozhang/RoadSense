# 实际阿里云数据库与损伤照片备份

这份完整源码包没有连接你正在运行的 ECS，因此**不能假装带上了服务器上的 SQLite 和损伤照片**。以下步骤从你自己的 ECS 提取真实数据；请单独保存，不要误覆盖云端在线库。

## 1. 在线安全快照

在阿里云 `qianduan` 根目录运行：

```bash
cd /home/admin/RoadSense/qianduan
mkdir -p /home/admin/roadsense-data-backup
python3 - <<'PY'
from pathlib import Path
import sqlite3, shutil
src=Path('data'); dst=Path('/home/admin/roadsense-data-backup')
for db in src.glob('*.sqlite3'):
    source=sqlite3.connect(str(db)); target=sqlite3.connect(str(dst/db.name))
    try: source.backup(target)
    finally: target.close(); source.close()
for item in ('images','records.json'):
    p=src/item
    if p.is_dir(): shutil.copytree(p,dst/item,dirs_exist_ok=True)
    elif p.is_file(): shutil.copy2(p,dst/item)
print('备份已保存到',dst)
PY
```

`sqlite3.Connection.backup()` 对正在使用 WAL 的库比直接复制主 `.sqlite3` 文件更可靠。备份期间请避免修改损伤记录和设备分配，以确保图片与记录保持一致。

## 2. 导出与恢复

```bash
cd /home/admin
tar -czf roadsense-data-backup.tar.gz roadsense-data-backup
```

从 Workbench 下载这个归档后，请妥善保存（里面可能有真实用户及位置数据）。若要在新环境恢复，必须**停止新环境的服务**并备份现有 `qianduan/data/` 后，才能将备份文件复制回 `qianduan/data/`，检查权限后重启；不要覆盖仍在运行的生产数据。
