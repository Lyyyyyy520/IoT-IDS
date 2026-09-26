"""
设备图 GNN 推理模块（设备级四级风险分类）。

输入：一段时间窗口内的设备流量（DataFrame）
输出：每台设备的风险等级（0-3）+ 置信度

流程：
  流量 → 聚合 13 维设备特征（与训练一致）→ log1p + z-score 归一化
       → 建设备图（通信边）→ ONNX 推理 → softmax → 风险等级

用法:
    from backend.models.device_gnn_inference import DeviceGNNInference
    detector = DeviceGNNInference(onnx_path, norm_path, community_subnet='192.168.4.')
    result = detector.predict(flows_df)
"""
import os
import numpy as np
import pandas as pd
import onnxruntime as ort

LEVEL_NAMES = {0: '正常(绿)', 1: '侦察(黄)', 2: '拒绝服务(橙)', 3: '僵尸网络(红)'}

# 13 个特征（顺序必须与训练时 device_gnn_norm.npz 的 feature_names 一致）
FEATURE_COLUMNS = [
    'flow_count', 'total_packets', 'total_bytes', 'avg_packets', 'avg_bytes',
    'max_packets', 'unique_dst_ports', 'unique_dst_ips', 'unique_src_ports',
    'unique_protocols', 'tcp_flags_mean', 'avg_flow_duration', 'internal_ratio',
]


def port_to_edge_type(port) -> int:
    """把目的端口映射成边类型（协议），与训练一致。"""
    port = int(port)
    if port in (1883, 8883):
        return 0  # mqtt
    if port in (80, 443):
        return 1  # http
    if port == 53:
        return 2  # dns
    if port == 123:
        return 3  # ntp
    if port in (5683, 5684):
        return 4  # coap
    return 5  # other


class DeviceGNNInference:
    def __init__(self, onnx_path: str, norm_path: str, community_subnet: str = '192.168.4.'):
        self.community_subnet = community_subnet
        self.session = ort.InferenceSession(onnx_path, providers=['CPUExecutionProvider'])

        norm = np.load(norm_path, allow_pickle=True)
        self.feature_mean = norm['feature_mean'].astype(np.float32)
        self.feature_std = norm['feature_std'].astype(np.float32)
        # 归一化特征顺序
        if 'feature_names' in norm:
            self.feature_names = list(norm['feature_names'])
        else:
            self.feature_names = FEATURE_COLUMNS

    def _is_community(self, ip) -> bool:
        return str(ip).startswith(self.community_subnet)

    def _build_features(self, flows: pd.DataFrame) -> pd.DataFrame:
        """从出站流量聚合 13 维设备特征（与训练一致）。"""
        g = flows.copy()
        g['flow_duration'] = g['ts_end'] - g['ts_start']
        g['is_internal_dst'] = g['dst_ip'].apply(self._is_community).astype(float)

        feats = g.groupby('src_ip').agg(
            flow_count=('packets', 'count'),
            total_packets=('packets', 'sum'),
            total_bytes=('bytes', 'sum'),
            avg_packets=('packets', 'mean'),
            avg_bytes=('bytes', 'mean'),
            max_packets=('packets', 'max'),
            unique_dst_ports=('dst_port', 'nunique'),
            unique_dst_ips=('dst_ip', 'nunique'),
            unique_src_ports=('src_port', 'nunique'),
            unique_protocols=('protocol', 'nunique'),
            tcp_flags_mean=('tcp_flags', 'mean'),
            avg_flow_duration=('flow_duration', 'mean'),
            internal_ratio=('is_internal_dst', 'mean'),
        )
        return feats

    def _build_graph(self, devices: list, flows: pd.DataFrame):
        """构建异构图：adjacency + node_type + edge_type + edge_weight。"""
        idx = {ip: i for i, ip in enumerate(devices)}
        n = len(devices)
        adj = np.zeros((n, n), dtype=np.float32)
        edge_type = np.full((n, n), -1, dtype=np.int64)
        edge_weight = np.zeros((n, n), dtype=np.float32)

        # 节点类型：gateway=0, device=1（网关 = 社区网段第一个 IP）
        gateway_ip = self.community_subnet + '1'
        node_type = np.where(np.array(devices) == gateway_ip, 0, 1).astype(np.int64)

        # 通信边（带协议类型 + 频率权重）
        d2d = flows[flows['src_ip'].apply(self._is_community)
                    & flows['dst_ip'].apply(self._is_community)]
        for (sip, dip), grp in d2d.groupby(['src_ip', 'dst_ip']):
            i = idx.get(sip)
            j = idx.get(dip)
            if i is not None and j is not None and i != j:
                adj[i, j] = 1.0
                adj[j, i] = 1.0
                et = port_to_edge_type(grp['dst_port'].mode()[0])
                freq = len(grp)
                edge_type[i, j] = et
                edge_type[j, i] = et
                edge_weight[i, j] = freq
                edge_weight[j, i] = freq

        return adj, node_type, edge_type, edge_weight

    def predict(self, flows: pd.DataFrame) -> dict:
        """
        对一段时间窗口的流量做设备级风险分类。

        Args:
            flows: DataFrame，需含列 [src_ip, dst_ip, src_port, dst_port,
                   protocol, packets, bytes, tcp_flags, ts_start, ts_end]

        Returns:
            dict: {device_ip: {'level': int, 'name': str, 'probs': np.ndarray}}
        """
        if flows.empty:
            return {}

        # 社区设备 = 内网 IP（作为源或目的）
        src_devs = set(flows[flows['src_ip'].apply(self._is_community)]['src_ip'])
        dst_devs = set(flows[flows['dst_ip'].apply(self._is_community)]['dst_ip'])
        devices = sorted(src_devs | dst_devs)

        if not devices:
            return {}

        # 特征（只统计出站，未出站的设备填 0）
        feats = self._build_features(flows[flows['src_ip'].apply(self._is_community)])
        feats = feats.reindex(devices, fill_value=0.0)
        X = feats[self.feature_names].values.astype(np.float32)

        # 归一化（log1p + z-score，与训练一致）
        X = np.log1p(X)
        X = (X - self.feature_mean) / self.feature_std

        # 异构图（邻接 + 节点类型 + 边类型 + 边权重）
        adj, node_type, edge_type, edge_weight = self._build_graph(devices, flows)

        # ONNX 推理
        logits = self.session.run(None, {
            'features': X, 'adjacency': adj, 'node_type': node_type,
            'edge_type': edge_type, 'edge_weight': edge_weight,
        })[0]
        probs = self._softmax(logits)
        preds = probs.argmax(axis=1)

        result = {}
        for i, ip in enumerate(devices):
            result[ip] = {
                'level': int(preds[i]),
                'name': LEVEL_NAMES[int(preds[i])],
                'probs': probs[i].astype(np.float64).tolist(),
            }
        return result

    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        e = np.exp(x - x.max(axis=1, keepdims=True))
        return e / e.sum(axis=1, keepdims=True)
