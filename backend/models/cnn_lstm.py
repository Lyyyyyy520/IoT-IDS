"""Lightweight CNN+LSTM model for N-BaIoT family classification.

The real N-BaIoT CSV files contain time-ordered statistical flow records.  The
model therefore consumes a short sequence instead of treating one row as a
fake time series.

Input:  (batch, sequence_length, feature_count)
Output: (batch, num_classes)
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class DepthwiseSeparableConv1d(nn.Module):
    """Temporal depthwise convolution followed by pointwise channel mixing."""

    def __init__(self, in_channels: int, out_channels: int, kernel_size: int = 3):
        super().__init__()
        padding = kernel_size // 2
        self.block = nn.Sequential(
            nn.Conv1d(
                in_channels,
                in_channels,
                kernel_size=kernel_size,
                padding=padding,
                groups=in_channels,
                bias=False,
            ),
            nn.Conv1d(in_channels, out_channels, kernel_size=1, bias=False),
            nn.BatchNorm1d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


class SEBlock(nn.Module):
    """Small squeeze-and-excitation channel attention block."""

    def __init__(self, channels: int, reduction: int = 4):
        super().__init__()
        hidden = max(4, channels // reduction)
        self.fc = nn.Sequential(
            nn.Linear(channels, hidden),
            nn.ReLU(inplace=True),
            nn.Linear(hidden, channels),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        weights = self.fc(x.mean(dim=2)).unsqueeze(2)
        return x * weights


class TemporalAttention(nn.Module):
    """Learn a normalized importance score for every LSTM time step."""

    def __init__(self, hidden_dim: int):
        super().__init__()
        self.score = nn.Linear(hidden_dim, 1)

    def forward(self, sequence: torch.Tensor) -> torch.Tensor:
        weights = F.softmax(self.score(sequence).squeeze(-1), dim=1).unsqueeze(-1)
        return torch.sum(sequence * weights, dim=1)


class CNNLSTMModel(nn.Module):
    """Parallel temporal-CNN and LSTM classifier."""

    def __init__(
        self,
        input_features: int = 21,
        num_classes: int = 3,
        lstm_hidden: int = 64,
        dropout: float = 0.25,
    ) -> None:
        super().__init__()
        self.input_features = input_features
        self.num_classes = num_classes

        # A learned gate is applied to each selected feature.
        self.feature_gate = nn.Parameter(torch.zeros(input_features))

        self.cnn = nn.Sequential(
            DepthwiseSeparableConv1d(input_features, 32),
            nn.Dropout(dropout / 2),
            DepthwiseSeparableConv1d(32, 64),
            SEBlock(64),
        )
        self.cnn_pool = nn.AdaptiveAvgPool1d(1)

        self.lstm = nn.LSTM(
            input_size=input_features,
            hidden_size=lstm_hidden,
            num_layers=1,
            batch_first=True,
            bidirectional=False,
        )
        self.temporal_attention = TemporalAttention(lstm_hidden)

        self.classifier = nn.Sequential(
            nn.Linear(64 + lstm_hidden, 64),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(64, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.ndim == 2:
            # Compatibility path for callers that provide one feature row.
            x = x.unsqueeze(1)
        if x.ndim != 3:
            raise ValueError(f"Expected input (B,T,F), got {tuple(x.shape)}")
        if x.shape[-1] != self.input_features:
            raise ValueError(
                f"Expected {self.input_features} features, got {x.shape[-1]}"
            )

        gate = torch.sigmoid(self.feature_gate).view(1, 1, -1) * 2.0
        weighted = x * gate

        cnn_features = self.cnn(weighted.transpose(1, 2))
        cnn_features = self.cnn_pool(cnn_features).squeeze(-1)

        lstm_output, _ = self.lstm(weighted)
        lstm_features = self.temporal_attention(lstm_output)

        return self.classifier(torch.cat([cnn_features, lstm_features], dim=1))


def create_model(input_features: int = 21, num_classes: int = 3) -> CNNLSTMModel:
    return CNNLSTMModel(
        input_features=input_features,
        num_classes=num_classes,
        lstm_hidden=64,
        dropout=0.25,
    )


def count_parameters(model: nn.Module) -> tuple[int, float]:
    total = sum(parameter.numel() for parameter in model.parameters())
    return total, total / 1_000_000


if __name__ == "__main__":
    model = create_model()
    sample = torch.randn(4, 16, 21)
    output = model(sample)
    total, millions = count_parameters(model)
    print(f"Parameters: {total:,} ({millions:.3f}M)")
    print(f"Input: {tuple(sample.shape)} -> Output: {tuple(output.shape)}")
