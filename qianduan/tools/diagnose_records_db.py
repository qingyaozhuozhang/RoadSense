#!/usr/bin/env python3
"""Safe read-only diagnostics for the RoadSense loss record SQLite database."""
from pathlib import Path
import os, sqlite3, json
root=Path(__file__).resolve().parent.parent
cfg=root/'.env'
if cfg.exists():
    for line in cfg.read_text(encoding='utf-8').splitlines():
        if line.startswith('ROADSENSE_DATA_DIR='):
            os.environ.setdefault('ROADSENSE_DATA_DIR',line.partition('=')[2].strip().strip('"').strip("'"))
base=Path(os.environ.get('ROADSENSE_DATA_DIR',str(root/'data')))
print('数据目录:',base)
path=base/'records.sqlite3'
if not path.exists():
    print('未找到 records.sqlite3；检查服务是否启动、旧数据目录是否同步正确。')
else:
    try:
        conn=sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)
        try:
            for table in ('records','audit_events'):
                try:print(table,'记录数:',conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0])
                except sqlite3.Error as error:print(table,'读取失败:',error)
            try:
                print('最近损伤记录 (只显示编号/时间/设备/等级):')
                for row in conn.execute('SELECT id,time,device_id,level FROM records ORDER BY time DESC LIMIT 8'):
                    print(' - ',row)
            except sqlite3.Error as error:print('记录预览失败:',error)
        finally:conn.close()
    except sqlite3.Error as error:print('数据库打开失败:',error)
print('旧版 JSON 文件是否存在:',(base/'records.json').exists())
print('JSON 已迁移标记:',(base/'.records_json_imported').exists())
