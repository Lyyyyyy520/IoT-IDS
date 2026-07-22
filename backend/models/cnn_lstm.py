"""
CNN+LSTM Lightweight Hybrid Model for IoT Botnet Detection

Architecture:
  - CNN branch: 3 layers depthwise separable conv (16→32→64 channels)
    with lightweight channel attention (SE-block)
  - LSTM branch: 1 layer LSTM (128 hidden) with temporal attention
  - Fusion: concatenate spatial + temporal features → FC → 5-class softmax

Input:  (batch, 1, 21)  — 21-dim feature vector reshaped as 1-channel
Output: (batch, num_classes) — [Normal, Mirai, Gafgyt, Other]
"""
import torch
import torch.nn as nn
import torch.nn.functional as F


class SEBlock(nn.Module):
    """Lightweight Squeeze-and-Excitation channel attention."""
    def __init__(self, channels: int, reduction: int = 4):
        super().__init__()
        self.fc = nn.Sequential(
            nn.Linear(channels, channels // reduction),
            nn.ReLU(inplace=True),
            nn.Linear(channels // reduction, channels),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, C, L)
        b, c, _ = x.shape
        y = x.mean(dim=2)  # (B, C) — global average pooling
        y = self.fc(y).unsqueeze(2)  # (B, C, 1)
        return x * y


class TemporalAttention(nn.Module):
    """Minimal temporal attention — learn per-timestep weights."""
    def __init__(self, hidden_dim: int):
        super().__init__()
        self.linear = nn.Linear(hidden_dim, 1)

    def forward(self, lstm_out: torch.Tensor) -> torch.Tensor:
        # lstm_out: (B, seq_len, hidden_dim)
        scores = self.linear(lstm_out).squeeze(-1)  # (B, seq_len)
        weights = F.softmax(scores, dim=1).unsqueeze(-1)  # (B, seq_len, 1)
        return (lstm_out * weights).sum(dim=1)  # (B, hidden_dim)


class CNNLSTMModel(nn.Module):
    """
    Lightweight CNN+LSTM hybrid for IoT botnet intrusion detection.

    Parameters
    ----------
    input_features : int, default 21
    num_classes    : int, default 5
    lstm_hidden    : int, default 128
    dropout        : float, default 0.25
    """
    def __init__(
        self,
        input_features: int = 21,
        num_classes: int = 4,
        lstm_hidden: int = 128,
        dropout: float = 0.25,
    ):
        super().__init__()

        # ---- CNN Branch (depthwise separable conv) ----
        self.conv1 = nn.Sequential(
            nn.Conv1d(1, 16, kernel_size=3, padding=1),
            nn.BatchNorm1d(16),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(kernel_size=2, stride=2),
        )
        self.conv2 = nn.Sequential(
            nn.Conv1d(16, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(kernel_size=2, stride=2),
        )
        self.conv3 = nn.Sequential(
            nn.Conv1d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(kernel_size=2, stride=2),
        )
        self.se = SEBlock(64, reduction=4)  # Channel attention after conv3

        # ---- LSTM Branch ----
        self.lstm = nn.LSTM(
            input_size=input_features,
            hidden_size=lstm_hidden,
            num_layers=1,
            batch_first=True,
            bidirectional=False,
        )
        self.temporal_attn = TemporalAttention(lstm_hidden)

        # ---- Feature Fusion ----
        self.adaptive_weight = nn.Conv1d(1, 1, kernel_size=1)  # Learn feature weights
        self.dropout = nn.Dropout(dropout)

        # Fully connected head
        # CNN output: 64 channels * (21 // 8) ≈ 64 * 2 = 128
        cnn_out_dim = 64 * (input_features // 8)
        fusion_dim = cnn_out_dim + lstm_hidden  # 128 + 128 = 256

        self.fc = nn.Sequential(
            nn.Linear(fusion_dim, 64),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(64, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B, 21) raw feature vector

        Returns:
            logits: (B, num_classes)
        """
        batch_size = x.shape[0]

        # ---- Adaptive feature weighting ----
        x_weighted = x.unsqueeze(1)  # (B, 1, 21)
        x_weighted = self.adaptive_weight(x_weighted).squeeze(1)  # (B, 21)

        # ---- CNN path ----
        cnn_in = x_weighted.unsqueeze(1)  # (B, 1, 21)
        cnn_out = self.conv1(cnn_in)  # (B, 16, 10)
        cnn_out = self.conv2(cnn_out)  # (B, 32, 5)
        cnn_out = self.conv3(cnn_out)  # (B, 64, 2)
        cnn_out = self.se(cnn_out)     # (B, 64, 2)
        cnn_flat = cnn_out.view(batch_size, -1)  # (B, 128)

        # ---- LSTM path ----
        lstm_in = x_weighted.unsqueeze(1)  # (B, 1, 21)
        lstm_out, _ = self.lstm(lstm_in)  # (B, 1, 128)
        lstm_feat = self.temporal_attn(lstm_out)  # (B, 128)

        # ---- Fusion ----
        fused = torch.cat([cnn_flat, lstm_feat], dim=1)  # (B, 256)
        fused = self.dropout(fused)
        logits = self.fc(fused)  # (B, 5)

        return logits


# ---- Convenience helpers ----

def create_model(input_features: int = 21, num_classes: int = 5) -> CNNLSTMModel:
    """Factory function — returns a fresh model instance."""
    return CNNLSTMModel(
        input_features=input_features,
        num_classes=num_classes,
        lstm_hidden=128,
        dropout=0.25,
    )


def count_parameters(model: nn.Module) -> tuple[int, float]:
    """Count total and trainable parameters (in millions)."""
    total = sum(p.numel() for p in model.parameters())
    return total, total / 1_000_000


if __name__ == '__main__':
    # Quick smoke test
    m = create_model()
    total, m_params = count_parameters(m)
    print(f'CNN+LSTM Model: {total:,} params ({m_params:.2f}M)')
    x = torch.randn(4, 21)
    with torch.no_grad():
        y = m(x)
    print(f'Input:  {x.shape}')
    print(f'Output: {y.shape}')
    print(f'Predicted classes: {y.argmax(dim=1).tolist()}')
