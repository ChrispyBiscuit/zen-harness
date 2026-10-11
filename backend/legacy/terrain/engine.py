"""
Terrain — end-to-end steering slice on Gemma-2-2B + Gemma Scope SAEs.

This is the load-bearing loop the whole product grows from: load the model and one
learned feature dictionary (a Gemma Scope residual SAE), then steer a single feature by
adding a scaled copy of its decoder direction to the residual stream at that layer, and
generate steered vs. unsteered output.

Substrate notes:
  * Gemma Scope SAEs are trained on the BASE model (`google/gemma-2-2b`, the "pt" repo),
    so we use the base model and plain-text completion prompts — NOT the -it chat model.
  * "res" SAEs sit on the residual stream at the OUTPUT of a decoder block. We hook that
    block's forward output for steering, and read hidden_states[layer+1] for encoding.

Prereqs (one-time, done by the user — gated repos):
    huggingface-cli login          # token with Gemma + Gemma Scope licenses accepted
Requires a free GPU (don't run while a fine-tune is training).
"""
import numpy as np
import torch
from huggingface_hub import hf_hub_download, list_repo_files
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL = "google/gemma-2-2b"
SAE_REPO = "google/gemma-scope-2b-pt-res"
WIDTH = "16k"


def _find_sae_file(layer: int, width: str = WIDTH) -> str:
    """Gemma Scope stores each SAE at layer_{L}/width_{W}/average_l0_{K}/params.npz;
    the L0 value varies per layer, so discover it instead of hardcoding."""
    prefix = f"layer_{layer}/width_{width}/"
    files = [f for f in list_repo_files(SAE_REPO)
             if f.startswith(prefix) and f.endswith("params.npz")]
    if not files:
        raise FileNotFoundError(f"no SAE under {prefix} in {SAE_REPO}")
    # prefer the canonical mid-range L0 if several exist; else first
    files.sort(key=lambda f: int(f.split("average_l0_")[1].split("/")[0]))
    return files[len(files) // 2]


class TerrainEngine:
    def __init__(self, layer: int = 12, model: str = MODEL,
                 device: str = "cuda", dtype=torch.float16):
        self.layer = layer
        self.dtype = dtype
        self.tok = AutoTokenizer.from_pretrained(model)
        self.model = AutoModelForCausalLM.from_pretrained(
            model, dtype=dtype, device_map={"": 0} if device == "cuda" else device)
        self.model.eval()
        self.device = next(self.model.parameters()).device

        sae_path = hf_hub_download(SAE_REPO, _find_sae_file(layer))
        p = np.load(sae_path)
        t = lambda k: torch.tensor(p[k], dtype=dtype, device=self.device)
        self.W_enc = t("W_enc")          # [d_model, n_features]
        self.W_dec = t("W_dec")          # [n_features, d_model]
        self.b_enc = t("b_enc")          # [n_features]
        self.b_dec = t("b_dec")          # [d_model]
        self.threshold = t("threshold")  # [n_features]  (JumpReLU gate)
        self.n_features, self.d_model = self.W_dec.shape

        self._steer_vec = None           # [d_model] added to the residual, or None
        self._decoder_block().register_forward_hook(self._hook)

    def _decoder_block(self):
        return self.model.model.layers[self.layer]

    def _hook(self, module, inputs, output):
        if self._steer_vec is None:
            return output
        hidden = output[0] if isinstance(output, tuple) else output
        hidden = hidden + self._steer_vec.to(hidden.dtype)
        if isinstance(output, tuple):
            return (hidden,) + tuple(output[1:])
        return hidden

    # ---- steering control ----------------------------------------------------
    def set_steer(self, feature: int | None, weight: float = 0.0):
        """Steer `feature` by `weight` (positive = amplify, negative = suppress).
        Weight is in units of the feature's decoder direction added per token."""
        if feature is None or weight == 0.0:
            self._steer_vec = None
        else:
            self._steer_vec = weight * self.W_dec[feature]

    def clear_steer(self):
        self._steer_vec = None

    # ---- feature discovery (SAE encode of the residual) ----------------------
    @torch.no_grad()
    def top_features(self, text: str, k: int = 12):
        """Return [(feature_idx, activation), ...] most active at the last token."""
        self.clear_steer()
        ids = self.tok(text, return_tensors="pt").to(self.device)
        out = self.model(**ids, output_hidden_states=True)
        resid = out.hidden_states[self.layer + 1][0, -1]          # [d_model]
        pre = resid @ self.W_enc + self.b_enc                     # [n_features]
        acts = torch.where(pre > self.threshold, pre, torch.zeros_like(pre))  # JumpReLU
        vals, idx = torch.topk(acts, k)
        return [(int(i), float(v)) for i, v in zip(idx, vals) if v > 0]

    # ---- generation ----------------------------------------------------------
    @torch.no_grad()
    def generate(self, prompt: str, max_new_tokens: int = 60, temperature: float = 0.0):
        ids = self.tok(prompt, return_tensors="pt").to(self.device)
        kw = dict(max_new_tokens=max_new_tokens, repetition_penalty=1.3,
                  pad_token_id=self.tok.eos_token_id)
        if temperature and temperature > 0:
            kw.update(do_sample=True, temperature=temperature)
        else:
            kw.update(do_sample=False)
        out = self.model.generate(**ids, **kw)
        return self.tok.decode(out[0, ids["input_ids"].shape[1]:], skip_special_tokens=True)

    def compare(self, prompt: str, feature: int, weight: float,
                max_new_tokens: int = 60):
        """(baseline, steered) for one feature at one weight."""
        self.clear_steer()
        base = self.generate(prompt, max_new_tokens)
        self.set_steer(feature, weight)
        steered = self.generate(prompt, max_new_tokens)
        self.clear_steer()
        return base, steered
