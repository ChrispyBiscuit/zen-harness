"""KV cache size per token from GGUF metadata."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llama_local as L


def test_per_layer_heads_count_only_attention_layers():
    # LFM2.5-24B-A2B: 40 layers, KV heads listed per layer (0 on convolution layers), no key_length -> 2048/32 = 64
    heads = [8 if i % 4 == 2 else 0 for i in range(40)]
    meta = {"block_count": 40, "attention.head_count_kv": heads, "attention.head_count": 32, "embedding_length": 2048}
    assert L.kv_bytes_per_token(meta, "f16") == 10 * 8 * (64 + 64) * 2.0


def test_uniform_heads_and_hybrid_interval_unchanged():
    dense = {"block_count": 32, "attention.head_count_kv": 8, "attention.key_length": 128}
    assert L.kv_bytes_per_token(dense, "f16") == 32 * 8 * 256 * 2.0
    hybrid = {"block_count": 32, "attention.head_count_kv": 4, "attention.key_length": 256,
              "full_attention_interval": 4}
    assert L.kv_bytes_per_token(hybrid, "q8_0") == 8 * 4 * 512 * 1.0625
