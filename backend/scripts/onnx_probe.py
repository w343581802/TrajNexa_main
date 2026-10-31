"""打印本地 ONNX 模型的输入/输出契约，用于核对 D-FINE 权重是否兼容。"""
import sys

import onnxruntime as ort

path = sys.argv[1] if len(sys.argv) > 1 else '/models/dfine.onnx'
session = ort.InferenceSession(path, providers=['CPUExecutionProvider'])
print('inputs:')
for item in session.get_inputs():
    print(' ', item.name, item.shape, item.type)
print('outputs:')
for item in session.get_outputs():
    print(' ', item.name, item.shape, item.type)
