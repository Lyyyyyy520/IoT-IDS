"""
模型效果评估：准确率、精确率、召回率、F1值、模型体积、推理时间、内存占用
"""
import os, sys, time
import numpy as np
import torch
import pickle
import pandas as pd
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, classification_report
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backend.models.cnn_lstm import create_model

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'processed')
MODEL_PATH = os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'best_model.onnx')
PT_PATH = os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'best_model.pt')

LABEL_NAMES = ['Normal', 'Mirai', 'Gafgyt', 'Other']  # override with pickle
# Try to load actual label names
try:
    with open(os.path.join(DATA_DIR, 'label_names.pkl'), 'rb') as f:
        LABEL_NAMES = pickle.load(f)
except:
    pass


def load_test_data():
    test_csv = os.path.join(DATA_DIR, 'test.csv')
    df = pd.read_csv(test_csv)
    X = df.iloc[:, :-1].values.astype(np.float32)
    y = df.iloc[:, -1].values.astype(np.int64)
    return X, y


def evaluate_model():
    print('=' * 60)
    print('IoT IDS 模型效果评估')
    print('=' * 60)

    X_test, y_test = load_test_data()
    print(f'\n测试集: {len(X_test)} 条, 特征: {X_test.shape[1]} 维')
    print(f'类别分布: {dict(zip(*np.unique(y_test, return_counts=True)))}')

    # ----- 1. 模型体积 -----
    print('\n--- 模型体积 ---')
    pt_size = os.path.getsize(PT_PATH) / 1024
    onnx_size = os.path.getsize(MODEL_PATH) / 1024 if os.path.exists(MODEL_PATH) else 0
    print(f'  PyTorch: {pt_size:.1f} KB ({pt_size/1024:.2f} MB)')
    print(f'  ONNX:    {onnx_size:.1f} KB ({onnx_size/1024:.2f} MB)')

    # ----- 2. 加载模型 + 内存占用 -----
    print('\n--- 内存占用 ---')
    import psutil
    proc = psutil.Process()
    mem_before = proc.memory_info().rss / 1024 / 1024

    model = create_model(input_features=X_test.shape[1], num_classes=len(LABEL_NAMES))
    model.load_state_dict(torch.load(PT_PATH, map_location='cpu'))
    model.eval()

    mem_after = proc.memory_info().rss / 1024 / 1024
    print(f'  加载前: {mem_before:.1f} MB')
    print(f'  加载后: {mem_after:.1f} MB')
    print(f'  模型占用: {mem_after - mem_before:.1f} MB')

    # ----- 3. 推理 + 指标 -----
    print('\n--- 分类指标 ---')
    X_tensor = torch.from_numpy(X_test)

    # 批量推理
    with torch.no_grad():
        start = time.time()
        logits = model(X_tensor)
        batch_time = time.time() - start
        preds = logits.argmax(1).numpy()

    acc = accuracy_score(y_test, preds)
    prec = precision_score(y_test, preds, average='weighted', zero_division=0)
    rec = recall_score(y_test, preds, average='weighted', zero_division=0)
    f1 = f1_score(y_test, preds, average='weighted', zero_division=0)

    print(f'  准确率 Accuracy : {acc:.4f} ({acc*100:.2f}%)')
    print(f'  精确率 Precision: {prec:.4f} ({prec*100:.2f}%)')
    print(f'  召回率 Recall   : {rec:.4f} ({rec*100:.2f}%)')
    print(f'  F1值   F1-Score : {f1:.4f} ({f1*100:.2f}%)')

    print(f'\n  批量推理 {len(X_test):,} 条: {batch_time:.3f} 秒')
    print(f'  批量吞吐: {len(X_test)/batch_time:.0f} 条/秒')

    # ----- 4. 单条推理时间 -----
    print('\n--- 单条推理时间 (1000次取平均) ---')
    single = X_tensor[0:1]
    # warmup
    for _ in range(10):
        _ = model(single)
    times = []
    for _ in range(1000):
        t0 = time.perf_counter()
        _ = model(single)
        times.append(time.perf_counter() - t0)
    avg_ms = np.mean(times) * 1000
    print(f'  平均: {avg_ms:.4f} ms')
    print(f'  最快: {np.min(times)*1000:.4f} ms')
    print(f'  最慢: {np.max(times)*1000:.4f} ms')

    # ----- 5. 各类别详细指标 -----
    print('\n--- 各类别指标 ---')
    report = classification_report(y_test, preds, target_names=LABEL_NAMES, zero_division=0, digits=4)
    print(report)

    # ----- 6. 总结 -----
    print('=' * 60)
    print(f'模型体积:  {pt_size:.0f} KB')
    print(f'内存占用:  {mem_after - mem_before:.1f} MB')
    print(f'准确率:    {acc*100:.2f}%')
    print(f'F1 值:     {f1*100:.2f}%')
    print(f'单条推理:  {avg_ms:.4f} ms')
    print(f'批量吞吐:  {len(X_test)/batch_time:.0f} 条/秒')
    print('=' * 60)


if __name__ == '__main__':
    evaluate_model()
