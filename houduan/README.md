#  路面损伤检测系统 - 边缘端 

**本项目是基于树莓派的端云协同道路损伤识别系统。**
设备部署于巡检车辆端，负责采集路面视频流、执行轻量化 YOLO 实时推理、融合多源 GPS 数据，并与云端服务器进行状态同步与数据归档。

## ONNX 推理与 CUDA 依赖

`core.py` 使用 `best.onnx`，优先尝试 CUDA，无法启用时由 ONNX Runtime 回退到 CPU。
模型初始化时会打印实际执行后端；只有其中包含 `CUDAExecutionProvider` 才表示已启用 CUDA。

如果出现 `libcublasLt.so.12: cannot open shared object file`，运行库可能已安装在当前
Python 环境的 `site-packages/nvidia/` 下，但没有被系统动态链接器找到。
程序现在会在创建 CUDA 会话前调用 ONNX Runtime 1.21 及以上版本提供的
`preload_dlls()`，加载该环境中的 CUDA/cuDNN 库。CPU 环境和旧版 ONNX Runtime 不调用此接口。
参见 [ONNX Runtime 官方预加载说明](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#preload-dlls)。

树莓派/CPU 环境使用 `requirements_runtime.txt` 中的 `onnxruntime`；NVIDIA GPU 环境使用
`onnxruntime-gpu` 及其匹配的 CUDA/cuDNN 运行库。两种 ONNX Runtime 包共用同一导入目录，
应只保留所需的一种，避免相互覆盖。`requirements_runtime.txt` 是 CPU 依赖清单。

在项目目录、实际运行程序的 Python 环境中，可以单独检查模型加载（不启动摄像头或网络服务）：

```bash
python -c 'from core import MODEL_PATH, YOLO_ONNX_Engine; YOLO_ONNX_Engine(MODEL_PATH)'
```
