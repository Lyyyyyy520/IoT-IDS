"""
异构图注意力网络（HTGAT）—— 设备级四级风险分类。

相比普通 GAT，增加了三层异构信息：
  1. 节点类型 embedding：gateway（网关）vs device（设备）
     → 解决"网关广播 ACK 被误判为僵尸扫描"的问题
  2. 边类型 bias：mqtt/http/dns/ntp/coap/other/时序
     → 让模型理解不同协议的通信语义
  3. 边权重：通信频率（log1p 压缩）
     → 让模型看到通信强弱，不再是二值 0/1

全部用稠密矩阵运算实现，可导出 ONNX 跑在树莓派上。

输入：
    features   : (N, F)  节点特征
    adjacency  : (N, N)  邻接矩阵（0/1）
    node_type  : (N,)    节点类型（0=gateway, 1=device）
    edge_type  : (N, N)  边类型（-1=无边, 0~6）
    edge_weight: (N, N)  边权重（通信频率 / 时序=1）
输出：
    logits     : (N, C)  四级风险
"""
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint

_NEG_INF = -1e4  # mask 非邻居的注意力（-1e4 在 fp16 下也安全；-1e9 会溢出成 -inf，导致 0*-inf=NaN）

NUM_NODE_TYPES = 2  # gateway, device
NUM_EDGE_TYPES = 7  # mqtt, http, dns, ntp, coap, other, temporal


class HeteroGATLayer(nn.Module):
    """单头异构图注意力层：边类型 bias + 边权重。"""

    def __init__(self, in_dim: int, out_dim: int, dropout: float = 0.1):
        super().__init__()
        self.W = nn.Linear(in_dim, out_dim, bias=False)
        self.a1 = nn.Parameter(torch.empty(out_dim))
        self.a2 = nn.Parameter(torch.empty(out_dim))
        # 每类边一个标量 bias（0~6）
        self.edge_bias = nn.Parameter(torch.zeros(NUM_EDGE_TYPES))
        self.leaky = nn.LeakyReLU(0.2)
        self.dropout = nn.Dropout(dropout)
        nn.init.xavier_uniform_(self.W.weight)
        nn.init.xavier_uniform_(self.a1.view(1, -1))
        nn.init.xavier_uniform_(self.a2.view(1, -1))

    def forward(self, h: torch.Tensor, adj: torch.Tensor,
                edge_type: torch.Tensor, edge_weight: torch.Tensor) -> torch.Tensor:
        if self.training:
            # 梯度检查点：训练时不存注意力中间矩阵（e/alpha），反向时重算，省 O(N²) 内存
            return checkpoint(self._forward_impl, h, adj, edge_type, edge_weight, use_reentrant=False)
        return self._forward_impl(h, adj, edge_type, edge_weight)

    def _forward_impl(self, h, adj, edge_type, edge_weight):
        z = self.W(h)                                   # (N, out_dim)
        score1 = z @ self.a1                            # (N,)
        score2 = z @ self.a2                            # (N,)
        e = score1.unsqueeze(1) + score2.unsqueeze(0)   # (N, N) 广播
        e = self.leaky(e)

        # 边类型 bias（edge_type=-1 表示无边，clamp 到 0，反正会被 mask 掉）
        # .int() 转 int32 用于索引（edge_type 保持 int8 省内存，这里临时转，int32 比 int64 再省一半）
        et = edge_type.clamp(min=0).int()
        e = e + self.edge_bias[et]
        # 边权重（log1p 压缩，加到注意力 logit 上）
        e = e + torch.log1p(edge_weight)

        # mask 非邻居
        mask = (adj > 0.5).to(e.dtype)
        e = e * mask + (1.0 - mask) * _NEG_INF

        alpha = F.softmax(e, dim=1)                     # 行归一化注意力
        alpha = self.dropout(alpha)
        out = alpha @ z                                 # (N, out_dim)
        return out


class MultiHeadHeteroGATLayer(nn.Module):
    """多头异构 GAT 层（拼接多个头）。"""

    def __init__(self, in_dim: int, out_dim: int, num_heads: int, dropout: float = 0.1):
        super().__init__()
        self.heads = nn.ModuleList([
            HeteroGATLayer(in_dim, out_dim, dropout) for _ in range(num_heads)
        ])

    def forward(self, h, adj, edge_type, edge_weight):
        return torch.cat([head(h, adj, edge_type, edge_weight) for head in self.heads], dim=-1)


class HeteroGATModel(nn.Module):
    """异构图注意力网络（HTGAT）—— 设备级四级风险分类。"""

    def __init__(
        self,
        input_features: int = 13,
        num_classes: int = 4,
        hidden: int = 64,
        num_heads: int = 4,
        num_layers: int = 2,
        dropout: float = 0.3,
    ):
        super().__init__()
        self.input_features = input_features
        self.num_classes = num_classes

        # 节点类型 embedding（gateway/device 各学一个向量）
        self.node_type_embed = nn.Embedding(NUM_NODE_TYPES, hidden)

        # 节点特征编码
        self.encoder = nn.Sequential(
            nn.Linear(input_features, hidden),
            nn.ReLU(inplace=True),
            nn.Linear(hidden, hidden),
            nn.ReLU(inplace=True),
        )

        # 异构 GAT 层
        self.gat_layers = nn.ModuleList()
        self.res_proj = nn.ModuleList()
        in_dim = hidden
        for _ in range(num_layers):
            self.gat_layers.append(MultiHeadHeteroGATLayer(in_dim, hidden, num_heads, dropout))
            out_dim = hidden * num_heads
            if in_dim != out_dim:
                self.res_proj.append(nn.Linear(in_dim, out_dim))
            else:
                self.res_proj.append(nn.Identity())
            in_dim = out_dim

        # 分类头
        self.classifier = nn.Sequential(
            nn.Linear(in_dim, 64),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(64, num_classes),
        )

    def forward(self, x, adj, node_type, edge_type, edge_weight):
        h = self.encoder(x)                              # (N, hidden)
        h = h + self.node_type_embed(node_type)          # 加节点类型 embedding
        for layer, proj in zip(self.gat_layers, self.res_proj):
            h = layer(h, adj, edge_type, edge_weight) + proj(h)
            h = F.relu(h)
        return self.classifier(h)                        # (N, num_classes)


def create_hetero_gat_model(
    input_features: int = 13,
    num_classes: int = 4,
    hidden: int = 64,
    num_heads: int = 4,
    num_layers: int = 2,
) -> HeteroGATModel:
    return HeteroGATModel(input_features, num_classes, hidden, num_heads, num_layers)


if __name__ == '__main__':
    # 冒烟测试
    torch.manual_seed(0)
    np.random.seed(0)

    model = create_hetero_gat_model()
    total = sum(p.numel() for p in model.parameters())
    print(f'HTGAT 参数: {total:,} ({total/1e6:.3f}M)')

    N = 8
    x = torch.randn(N, 13)
    adj = torch.randint(0, 2, (N, N)).float()
    adj = torch.maximum(adj, adj.T)
    adj.fill_diagonal_(0.0)
    node_type = torch.randint(0, 2, (N,))
    edge_type = torch.randint(-1, 7, (N, N)) * (adj > 0.5).long()
    edge_weight = torch.rand(N, N) * adj

    logits = model(x, adj, node_type, edge_type, edge_weight)
    print(f'输入: {x.shape}, 输出: {logits.shape}')
    print(f'预测: {logits.argmax(dim=1).tolist()}')

    loss = F.cross_entropy(logits, torch.randint(0, 4, (N,)))
    loss.backward()
    print(f'Backward OK, loss={loss.item():.4f}')
