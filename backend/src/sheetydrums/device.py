"""PyTorch device helpers shared across stages.

Three responsibilities:

- `best_device()` picks the highest-performance device available (MPS on
  Apple Silicon, CUDA on Nvidia, else CPU). Stages call this so device
  selection is consistent across the pipeline.
- `empty_cache()` releases cached GPU memory and waits for pending kernels.
  Cheap no-op on CPU; safe to call anywhere.
- `release_to_cpu()` is the one that actually frees a model's GPU memory:
  it moves the model to CPU first, then clears caches. `empty_cache()` alone
  cannot free memory held by an still-live model reference on the device.

We discovered this the hard way on Apple Silicon: with Demucs, ADTOF, and
Beat This! all loaded on MPS at once, the cached state from a 4-minute
Demucs run silently broke downstream inference (every model returned 0
hits / 0 beats / garbage). Moving each model off MPS after its single
forward pass fixes it. Designed for one-shot pipelines — repeated inference
on the same instance would need to re-allocate the model on the GPU first.
"""
from __future__ import annotations

import gc
from typing import Any

import torch


def best_device() -> str:
    """Pick the highest-performance PyTorch device available."""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def downstream_device() -> str:
    """Device for stages running AFTER a heavy separator stage (Demucs).

    On Apple Silicon, returns "cpu" — even after `release_to_cpu(model)` and
    `empty_cache()`, MPS retains state from Demucs that silently breaks
    downstream models (they return zero hits / zero beats on long audio).
    ADTOF and Beat This! are small enough that CPU is competitive with MPS
    anyway (no GPU transfer overhead), so the workaround costs little.

    On CUDA, the state-pollution issue isn't observed, so we return cuda.
    """
    if torch.backends.mps.is_available():
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def empty_cache() -> None:
    """Release cached GPU memory and wait for pending kernels. No-op on CPU."""
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
        torch.mps.synchronize()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


def release_to_cpu(model: Any) -> None:
    """Move `model` to CPU and free its GPU allocations.

    Use after a stage's single forward pass to free memory for downstream
    stages. Stages that may be called again on the same instance need to
    re-move the model back to the GPU first.
    """
    if model is not None and hasattr(model, "to"):
        model.to("cpu")
    empty_cache()
    gc.collect()
    empty_cache()
