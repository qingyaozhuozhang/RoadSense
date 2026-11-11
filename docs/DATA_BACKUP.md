# RoadSense 数据备份与恢复规范

## 1. 备份范围

云端管理平台的业务数据由 SQLite 数据库和图像文件组成。备份需同时覆盖用户与设备关系、授权信息、道路损伤记录及相应图片，以保证恢复后数据库引用与文件内容一致。

| 数据内容 | 默认位置 | 说明 |
|---|---|---|
| 用户与授权信息 | `qianduan/data/accounts.sqlite3` | 账号、角色、密码哈希、移动定位授权 |
| 设备信息 | `qianduan/data/devices.sqlite3` | 设备登记、归属及令牌哈希 |
| 巡检档案 | `qianduan/data/records.sqlite3` | 识别记录、位置、来源与审计信息 |
| 归档图片 | `qianduan/data/images/` | 原图及识别结果图 |

实际数据目录以云端配置项 `ROADSENSE_DATA_DIR` 为准。项目源代码和模型文件不能替代正在运行的业务数据库与历史归档图片。

## 2. 在线备份

SQLite 在 WAL 模式下可能存在尚未合并至主库文件的事务，因此不宜在服务运行期间仅复制单个 `.sqlite3` 文件。推荐使用 SQLite Backup API 创建数据库一致性副本，并同步备份归档图片。

以下示例适用于数据目录为 `/home/admin/RoadSense/qianduan/data` 的部署环境；其他部署位置应调整相应路径。

```bash
cd /home/admin/RoadSense/qianduan
mkdir -p /home/admin/roadsense-data-backup
python3 - <<'PYBACKUP'
from pathlib import Path
import sqlite3
import shutil

source_dir = Path('data')
backup_dir = Path('/home/admin/roadsense-data-backup')
backup_dir.mkdir(parents=True, exist_ok=True)

for db_file in source_dir.glob('*.sqlite3'):
    src = sqlite3.connect(str(db_file))
    dst = sqlite3.connect(str(backup_dir / db_file.name))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

images_dir = source_dir / 'images'
if images_dir.is_dir():
    shutil.copytree(images_dir, backup_dir / 'images', dirs_exist_ok=True)

legacy_file = source_dir / 'records.json'
if legacy_file.is_file():
    shutil.copy2(legacy_file, backup_dir / legacy_file.name)

print('RoadSense 业务数据备份完成：', backup_dir)
PYBACKUP
```

备份期间应控制账号权限、设备归属及巡检记录的写入频率，避免数据库快照与图片复制操作跨越大量业务变更。对于要求强一致性的完整恢复点，可安排维护窗口停止写入后进行备份。

## 3. 归档与保存

```bash
cd /home/admin
tar -czf roadsense-data-backup.tar.gz roadsense-data-backup
```

备份文件可能包含用户账号、位置轨迹、设备关系和道路现场照片，应采用受限存储位置与适当的访问控制。备份文件的保存周期、异地留存和删除方式应符合实际数据管理要求。

## 4. 数据恢复

1. 确认目标云端服务已停止，避免恢复过程与 SQLite 写入操作并行。
2. 备份目标环境当前的数据库和图片目录。
3. 将所需备份中的数据库文件与 `images/` 恢复至 `ROADSENSE_DATA_DIR`。
4. 核对进程用户对数据目录的读写权限及模型、配置文件路径。
5. 启动云端服务，检查账号登录、设备查询、档案列表和照片访问情况。

恢复操作应以完整数据目录为单位进行核验，避免数据库和图像文件版本不一致。
