"""
GAT (Graph Attention Network) for IoT Botnet Detection

Flow-as-node graph paradigm:
  - Each network flow is a NODE, carrying its feature vector (N-BaIoT 115-dim).
  - Edges connect "similar" flows (K-nearest-neighbor in feature space).
  - The GAT aggregates each flow's own features with those of its neighbours,
    which catches coordinated / bursty attack behaviour that a per-flow model
    (CNN/LSTM) cannot see — e.g. one device suddenly scanning the whole subnet.

Why plain PyTorch (no torch_geometric):
  - All graph ops are dense matrix ops (matmul + softmax + masking), so the
    model exports cleanly to ONNX and runs on ONNX Runtime (Raspberry Pi CPU).

Inputs (training & inference):
    node_features : (N, F)  — F = feature dim (N-BaIoT ~115, minus constants)
    adjacency     : (N, N)  — 0/1 symmetric adjacency (KNN graph, built outside)
Output:
    logits        : (N, C)  — C = 3 [Normal, Mirai, Gafgyt]
"""
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

FEATURE_DIM = 115
NUM_CLASSES = 3
CLASS_NAMES = ['Normal', 'Mirai', 'Gafgyt']

_NEG_INF = -1e9  # mask for non-neighbour attention (avoids -inf in ONNX)


# --------------------------------------------------------------------------- #
#  Graph construction (pure numpy — runs on Windows / backend / Raspberry Pi)
# --------------------------------------------------------------------------- #
def build_knn_graph(features: np.ndarray, k: int = 5, self_loop: bool = True) -> np.ndarray:
    """
    Build a symmetric KNN adjacency matrix from node features.

    Args:
        features: (N, F) float32 array
        k: number of neighbours per node
        self_loop: keep self-connections (recommended for GAT)

    Returns:
        adj: (N, N) float32 0/1 symmetric adjacency matrix
    """
    features = np.asarray(features, dtype=np.float32)
    n = features.shape[0]
    if n < 2:
        return np.ones((n, n), dtype=np.float32)

    # Pairwise squared euclidean distances: ||a-b||^2 = |a|^2 + |b|^2 - 2 a·b
    sq = np.sum(features ** 2, axis=1, keepdims=True)          # (N, 1)
    dist = sq + sq.T - 2.0 * (features @ features.T)           # (N, N)
    dist = np.clip(dist, 0.0, None)                            # guard vs tiny negatives

    k_others = max(1, min(k - 1, n - 1)) if self_loop else max(1, min(k, n - 1))

    idx = np.argpartition(dist, k_others, axis=1)[:, :k_others]  # (N, k_others)

    adj = np.zeros((n, n), dtype=np.float32)
    rows = np.arange(n)[:, None]
    adj[rows, idx] = 1.0
    if self_loop:
        adj[np.arange(n), np.arange(n)] = 1.0
    # Symmetrise (undirected graph) — edge (i,j) implies (j,i)
    adj = np.maximum(adj, adj.T)
    return adj


# --------------------------------------------------------------------------- #
#  GAT building blocks
# --------------------------------------------------------------------------- #
class GATLayer(nn.Module):
    """Single-head graph attention layer (GAT, Veličković et al.)."""

    def __init__(self, in_dim: int, out_dim: int, dropout: float = 0.1):
        super().__init__()
        self.W = nn.Linear(in_dim, out_dim, bias=False)
        self.a1 = nn.Parameter(torch.empty(out_dim))
        self.a2 = nn.Parameter(torch.empty(out_dim))
        self.leaky = nn.LeakyReLU(0.2)
        self.dropout = nn.Dropout(dropout)
        nn.init.xavier_uniform_(self.W.weight)
        nn.init.xavier_uniform_(self.a1.view(1, -1))
        nn.init.xavier_uniform_(self.a2.view(1, -1))

    def forward(self, h: torch.Tensor, adj: torch.Tensor) -> torch.Tensor:
        z = self.W(h)                                   # (N, out_dim)
        score1 = z @ self.a1                            # (N,)
        score2 = z @ self.a2                            # (N,)
        e = score1.unsqueeze(1) + score2.unsqueeze(0)   # (N, N) broadcast
        e = self.leaky(e)

        mask = (adj > 0.5).to(e.dtype)                  # 1 where edge exists
        e = e * mask + (1.0 - mask) * _NEG_INF          # mask non-neighbours
        alpha = F.softmax(e, dim=1)                     # row-normalised attention
        alpha = self.dropout(alpha)
        out = alpha @ z                                 # (N, out_dim)
        return out


class MultiHeadGATLayer(nn.Module):
    """Concatenate several GAT heads."""

    def __init__(self, in_dim: int, out_dim: int, num_heads: int, dropout: float = 0.1):
        super().__init__()
        self.heads = nn.ModuleList([
            GATLayer(in_dim, out_dim, dropout) for _ in range(num_heads)
        ])

    def forward(self, h: torch.Tensor, adj: torch.Tensor) -> torch.Tensor:
        return torch.cat([head(h, adj) for head in self.heads], dim=-1)


# --------------------------------------------------------------------------- #
#  Full model
# --------------------------------------------------------------------------- #
class GATModel(nn.Module):
    """
    Flow-as-node GAT for IoT botnet detection (3-class).

    Node encoder (MLP) = "look at a single flow"
    GAT layers           = "look at relationships between flows"
    """

    def __init__(
        self,
        input_features: int = FEATURE_DIM,
        num_classes: int = NUM_CLASSES,
        hidden: int = 64,
        num_heads: int = 4,
        num_layers: int = 2,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.input_features = input_features
        self.num_classes = num_classes

        self.encoder = nn.Sequential(
            nn.Linear(input_features, hidden),
            nn.ReLU(inplace=True),
            nn.Linear(hidden, hidden),
            nn.ReLU(inplace=True),
        )

        self.gat_layers = nn.ModuleList()
        self.res_proj = nn.ModuleList()
        in_dim = hidden
        for _ in range(num_layers):
            self.gat_layers.append(MultiHeadGATLayer(in_dim, hidden, num_heads, dropout))
            out_dim = hidden * num_heads
            if in_dim != out_dim:
                self.res_proj.append(nn.Linear(in_dim, out_dim))
            else:
                self.res_proj.append(nn.Identity())
            in_dim = out_dim

        self.classifier = nn.Sequential(
            nn.Linear(in_dim, 64),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(64, num_classes),
        )

    def forward(self, x: torch.Tensor, adj: torch.Tensor) -> torch.Tensor:
        h = self.encoder(x)                    # (N, hidden)
        for layer, proj in zip(self.gat_layers, self.res_proj):
            h = layer(h, adj) + proj(h)        # residual (with dim projection)
            h = F.relu(h)
        return self.classifier(h)              # (N, num_classes)


def create_gat_model(
    input_features: int = FEATURE_DIM,
    num_classes: int = NUM_CLASSES,
    hidden: int = 64,
    num_heads: int = 4,
    num_layers: int = 2,
) -> GATModel:
    return GATModel(input_features, num_classes, hidden, num_heads, num_layers)


def count_parameters(model: nn.Module) -> tuple[int, float]:
    total = sum(p.numel() for p in model.parameters())
    return total, total / 1_000_000


if __name__ == '__main__':
    # Smoke test
    torch.manual_seed(0)
    np.random.seed(0)

    model = create_gat_model()
    total, m = count_parameters(model)
    print(f'GAT Model: {total:,} params ({m:.3f}M)')

    N = 8
    feats = np.random.randn(N, FEATURE_DIM).astype(np.float32)
    adj = build_knn_graph(feats, k=4)
    print(f'Adjacency (N={N}): {adj.sum():.0f} edges, symmetric={np.allclose(adj, adj.T)}')

    x = torch.from_numpy(feats)
    a = torch.from_numpy(adj)
    logits = model(x, a)
    print(f'Input:  {x.shape}')
    print(f'Output: {logits.shape}')
    print(f'Predicted: {logits.argmax(dim=1).tolist()}')

    loss = F.cross_entropy(logits, torch.randint(0, NUM_CLASSES, (N,)))
    loss.backward()
    print(f'Backward OK, loss={loss.item():.4f}')
