"""Merge legacy records.json and severe_cracks/ into new cloud_web/data.
Do not run this unless you have backed up production data.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description='从旧版云端导入 records.json 和严重损伤图片')
    p.add_argument('--records',required=True,help='旧服务器下载的 records.json 路径')
    p.add_argument('--images',required=True,help='旧服务器 severe_cracks 图片目录')
    p.add_argument('--data',default=str(ROOT/'data'),help='新版数据目录')
    p.add_argument('--apply',action='store_true',help='实际写入；不加为 dry-run')
    a=p.parse_args()
    src=Path(a.records).resolve(); img_dir=Path(a.images).resolve(); data=Path(a.data).resolve()
    if not src.is_file() or not img_dir.is_dir():p.error('旧 records.json 或 severe_cracks 目录不存在')
    old=json.loads(src.read_text('utf-8'))
    if not isinstance(old,list):p.error('旧记录必须是 JSON 数组')
    dst=data/'records.json'
    current=json.loads(dst.read_text('utf-8')) if dst.exists() else []
    if not isinstance(current,list):p.error('目标记录文件内容异常，已停止')
    seen={json.dumps({k:r.get(k) for k in ('time','lon','lat','level','image','session_id')},ensure_ascii=False,sort_keys=True) for r in current}
    new=[]
    for r in old:
        if not isinstance(r,dict):continue
        signature=json.dumps({k:r.get(k) for k in ('time','lon','lat','level','image','session_id')},ensure_ascii=False,sort_keys=True)
        if signature not in seen:
            seen.add(signature);new.append(r)
    images=[]
    for r in new:
        name=r.get('image')
        if isinstance(name,str) and Path(name).name==name and (img_dir/name).is_file():
            images.append(name)
    print(f'旧记录 {len(old)}，目标已有 {len(current)}，新增 {len(new)}，可复制图片 {len(set(images))}')
    if not a.apply:
        print('仅预览，未修改数据。检查输出后加 --apply 执行。')
        return
    data.mkdir(parents=True,exist_ok=True);(data/'images').mkdir(parents=True,exist_ok=True)
    if dst.exists():
        backup=data/'records.before_import.json'
        if backup.exists():p.error('备份文件已存在，先手动移走，避免覆盖')
        shutil.copy2(dst,backup)
    for name in set(images):
        target=data/'images'/name
        if not target.exists():shutil.copy2(img_dir/name,target)
    tmp=data/'records.json.tmp';tmp.write_text(json.dumps(current+new,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(dst)
    print('导入完成。原始记录与照片均未删除。')

if __name__=='__main__':main()
