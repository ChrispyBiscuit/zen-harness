"""The MTP head switch: sized from the model file's own head tensors, nothing when the file has no head, off by default."""
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llama_local
from agent_config import DEFAULT_AGENT

MIB = 1048576


def _gguf(path, blocks, nextn, tensors):
    """A minimal GGUF v3: two uint32 metadata keys and tensor infos whose data sizes come from their offsets."""
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    kv = [("qwen35.block_count", blocks)] + ([("qwen35.nextn_predict_layers", nextn)] if nextn else [])
    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", len(tensors), len(kv))
    for k, v in kv:
        out += s(k) + struct.pack("<I", 4) + struct.pack("<I", v)
    off = 0
    for name, size in tensors:
        out += s(name) + struct.pack("<I", 1) + struct.pack("<Q", 1) + struct.pack("<IQ", 0, off)
        off += size
    out += b"\0" * ((32 - len(out) % 32) % 32)
    with open(path, "wb") as f:
        f.write(out)
        f.truncate(len(out) + off)
    return path


def test_head_is_the_last_block_plus_overhead():
    d = tempfile.mkdtemp()
    p = _gguf(os.path.join(d, "m.gguf"), 3, 1, [("blk.0.w", 4 * MIB), ("blk.1.w", 4 * MIB), ("blk.2.w", 3 * MIB)])
    assert llama_local.mtp_head_mib(p) == 3 + llama_local.MTP_OVERHEAD_MIB


def test_no_head_means_no_reserve():
    d = tempfile.mkdtemp()
    p = _gguf(os.path.join(d, "m.gguf"), 3, 0, [("blk.0.w", 4 * MIB), ("blk.1.w", 4 * MIB), ("blk.2.w", 4 * MIB)])
    assert llama_local.mtp_head_mib(p) == 0


def test_switch_is_off_by_default():
    assert DEFAULT_AGENT["mtp_head"] is False
