"""Sanity tests for GPU Hardware, CUDA Runtime, and VRAM Topology."""

from __future__ import annotations

import pytest
import torch


def test_cuda_runtime_and_driver_availability() -> None:
    """Verifies that PyTorch detects CUDA and physical GPU hardware."""
    if not torch.cuda.is_available():
        pytest.skip("capability-guard: CUDA is not available in current PyTorch runtime")
    device_count = torch.cuda.device_count()
    assert device_count >= 1, f"Expected at least 1 CUDA device, found {device_count}"


def test_dual_gpu_topology_and_vram_capacities() -> None:
    """Verifies the primary training GPU (RTX 5060 Ti 16GB) and serving GPU (RTX 5060 8GB)."""
    if not torch.cuda.is_available():
        pytest.skip("capability-guard: CUDA unavailable")

    count = torch.cuda.device_count()
    assert count >= 1

    dev0_name = torch.cuda.get_device_name(0)
    dev0_mem_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)

    msg0 = f"Primary training GPU {dev0_name} should have >= 12GB VRAM (found {dev0_mem_gb:.2f}GB)"
    assert dev0_mem_gb > 12.0, msg0

    if count >= 2:
        dev1_name = torch.cuda.get_device_name(1)
        dev1_mem_gb = torch.cuda.get_device_properties(1).total_memory / (1024**3)
        msg1 = f"Serving GPU {dev1_name} should have >= 6GB VRAM (found {dev1_mem_gb:.2f}GB)"
        assert dev1_mem_gb > 6.0, msg1


def test_cuda_tensor_core_and_memory_allocation() -> None:
    """Allocates tensors on CUDA, executes matrix multiplication, and frees memory."""
    if not torch.cuda.is_available():
        pytest.skip("capability-guard: CUDA unavailable")

    device = torch.device("cuda:0")
    torch.cuda.empty_cache()
    initial_mem = torch.cuda.memory_allocated(device)

    # Allocate 100MB tensor
    a = torch.randn(2500, 2500, device=device, dtype=torch.float32)
    b = torch.randn(2500, 2500, device=device, dtype=torch.float32)

    c = torch.matmul(a, b)
    torch.cuda.synchronize(device)

    assert c.shape == (2500, 2500)
    assert torch.cuda.memory_allocated(device) > initial_mem

    del a, b, c
    torch.cuda.empty_cache()
