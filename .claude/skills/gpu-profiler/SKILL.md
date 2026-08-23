---
name: gpu-profiler
description: Use when profiling dual-GPU memory footprint, verifying PyTorch AMP execution, or assessing CUDA compute health.
---

# GPU Hardware Profiler & VRAM Verification Skill

Use this skill to assess dual-GPU health, test CUDA memory isolation, and diagnose memory leaks.

## Hardware Topography
- **GPU 0 (`cuda:0`)**: NVIDIA GeForce RTX 5060 Ti (16GB VRAM) — Dedicated U-Net Trainer with AMP fp16 autocast and gradient accumulation.
- **GPU 1 (`cuda:1`)**: NVIDIA GeForce RTX 5060 (8GB VRAM) — Dedicated FastAPI Serving container (4GB memory ceiling).

## Step 1: Query CUDA Runtime Properties
```python
import torch

print(f"CUDA Available: {torch.cuda.is_available()}")
print(f"Device Count: {torch.cuda.device_count()}")
for idx in range(torch.cuda.device_count()):
    name = torch.cuda.get_device_name(idx)
    mem_gb = torch.cuda.get_device_properties(idx).total_memory / (1024**3)
    print(f"Device {idx}: {name} ({mem_gb:.2f} GB VRAM)")
```

## Step 2: Profile AMP Training Execution
```python
import torch
from torch.utils.data import DataLoader
from orbital_drift.data.dataset import Sentinel2PatchDataset
from orbital_drift.train.baseline import SimpleUNet, train_baseline_epoch
import numpy as np

# Sample synthetic 4-band cube
data = np.random.randint(0, 10000, size=(4, 512, 512), dtype=np.int16)
labels = np.random.randint(0, 10, size=(512, 512), dtype=np.int64)
dataset = Sentinel2PatchDataset(data, labels=labels, patch_size=256, stride=256)
loader = DataLoader(dataset, batch_size=2)

model = SimpleUNet(in_channels=4, num_classes=10)
opt = torch.optim.Adam(model.parameters(), lr=1e-3)
crit = torch.nn.CrossEntropyLoss()

loss, train_time = train_baseline_epoch(
    model, loader, opt, crit, device="cuda:0", use_amp=True, grad_accum_steps=2
)
print(f"AMP Training Epoch Loss: {loss:.4f} in {train_time:.2f}s")
```

## Step 3: VRAM Reclamation Check
```python
torch.cuda.empty_cache()
for idx in range(torch.cuda.device_count()):
    allocated = torch.cuda.memory_allocated(idx) / (1024**2)
    print(f"GPU {idx} Post-Cleanup Allocated: {allocated:.2f} MB")
```
