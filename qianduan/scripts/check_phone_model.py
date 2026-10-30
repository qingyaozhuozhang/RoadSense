"""CLI smoke test for cloud YOLO: python scripts/check_phone_model.py path/to/photo.jpg"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.phone_inference import PhoneInference


def main():
    if len(sys.argv) != 2:
        raise SystemExit('用法: python scripts/check_phone_model.py tests/fixtures/legacy_damage.jpg')
    photo = Path(sys.argv[1])
    if not photo.is_file():
        raise SystemExit(f'测试图片不存在：{photo}')
    result = PhoneInference().analyze(photo.read_bytes())
    print('云端 best.onnx 真实推理成功')
    print('等级：', result['level'])
    print('目标数：', len(result['detections']))
    print('面积占比：', f'{result["damage_ratio"]:.2%}')
    print('推理耗时：', f'{result["inference_ms"]} ms')
    print('标注图字节数：', len(result['result_jpeg']))


if __name__ == '__main__':
    main()
