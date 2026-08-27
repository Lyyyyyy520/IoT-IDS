"""
端到端验证设备图 GNN 推理模块。

用 flows_sample.csv 的一个时间窗口模拟"实时流量"，输入推理模块，
对比预测风险等级与实际标签，验证推理链路正确性。

用法:
    python training/verify_device_gnn_inference.py
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backend.models.device_gnn_inference import DeviceGNNInference

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'training', 'data', 'ciciot2023', 'flows_sample.csv')
ONNX = os.path.join(ROOT, 'backend', 'data', 'device_gnn.onnx')
NORM = os.path.join(ROOT, 'backend', 'data', 'device_gnn_norm.npz')

LABEL_TO_LEVEL = {
    'Benign_Final': 0, 'Recon-PortScan': 1, 'DoS-TCP_Flood': 2,
    'DDoS-SYN_Flood': 2, 'Mirai-greip_flood': 3, 'Mirai-greeth_flood': 3,
    'Mirai-udpplain': 3,
}
LEVEL_NAMES = {0: '正常(绿)', 1: '侦察(黄)', 2: '拒绝服务(橙)', 3: '僵尸网络(红)'}

WINDOW_SIZE = 3600  # 与训练一致


def main():
    df = pd.read_csv(DATA)
    df['level'] = df['label_class'].map(LABEL_TO_LEVEL)
    t0 = df['ts_start'].min()
    df['window'] = ((df['ts_start'] - t0) / WINDOW_SIZE).astype(int)

    detector = DeviceGNNInference(ONNX, NORM, community_subnet='192.168.137.')
    print('推理模块加载完成')

    # 选几个窗口验证
    windows = sorted(df['window'].unique())
    np.random.seed(42)
    test_windows = np.random.choice(windows, size=8, replace=False)

    total_correct = 0
    total_devices = 0
    per_class_correct = {i: 0 for i in range(4)}
    per_class_total = {i: 0 for i in range(4)}

    print(f'\n验证 {len(test_windows)} 个时间窗口:\n')
    for w in test_windows:
        window_df = df[df['window'] == w]
        if window_df.empty:
            continue

        # 实际标签（每设备最大严重度）
        actual = window_df[window_df['src_ip'].str.startswith('192.168.137.', na=False)] \
            .groupby('src_ip')['level'].max()

        # 推理预测
        result = detector.predict(window_df)

        correct = 0
        for ip, true_level in actual.items():
            if ip in result:
                pred_level = result[ip]['level']
                per_class_total[true_level] += 1
                if pred_level == true_level:
                    correct += 1
                    per_class_correct[true_level] += 1

        n = len(actual)
        total_correct += correct
        total_devices += n
        print(f'  窗口 {w}: {correct}/{n} 正确 ({correct/max(n,1)*100:.0f}%)')

    print(f'\n=== 端到端验证结果 ===')
    print(f'总体准确率: {total_correct}/{total_devices} = {total_correct/max(total_devices,1)*100:.1f}%')
    print(f'\n分级别准确率:')
    for lvl in range(4):
        n = per_class_total[lvl]
        c = per_class_correct[lvl]
        print(f'  {LEVEL_NAMES[lvl]:<12} {c}/{n} = {c/max(n,1)*100:.1f}%')


if __name__ == '__main__':
    main()
