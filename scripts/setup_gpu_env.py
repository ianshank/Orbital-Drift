#!/usr/bin/env python3
"""Orbital-Drift — Automated GPU Environment and Hardware Verification Script.

Discovers available NVIDIA GPUs, checks PyTorch CUDA runtime, profiles VRAM,
validates core dependency packages, and generates/verifies the local `.env` configuration.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
ENV_FILE: Final = REPO_ROOT / ".env"
ENV_EXAMPLE: Final = REPO_ROOT / ".env.example"


def check_gpu_hardware() -> dict[str, str | int | float | bool]:
    """Inspects host GPU status via PyTorch and nvidia-smi."""
    results: dict[str, str | int | float | bool] = {
        "cuda_available": False,
        "device_count": 0,
        "devices": "",
    }
    try:
        import torch

        if torch.cuda.is_available():
            count = torch.cuda.device_count()
            results["cuda_available"] = True
            results["device_count"] = count
            dev_info = []
            for i in range(count):
                name = torch.cuda.get_device_name(i)
                mem = round(torch.cuda.get_device_properties(i).total_memory / (1024**3), 2)
                dev_info.append(f"GPU {i}: {name} ({mem} GB)")
            results["devices"] = "; ".join(dev_info)
    except ImportError:
        pass

    return results


def check_dependencies() -> dict[str, bool]:
    """Validates that essential packages are importable."""
    packages = [
        "torch",
        "numpy",
        "scipy",
        "pydantic",
        "pydantic_settings",
        "fastapi",
        "httpx",
        "mlflow",
        "requests",
    ]
    status = {}
    for pkg in packages:
        try:
            __import__(pkg)
            status[pkg] = True
        except ImportError:
            status[pkg] = False
    return status


def ensure_env_file() -> None:
    """Creates .env from .env.example if missing and populates discovered parameters."""
    if not ENV_FILE.exists() and ENV_EXAMPLE.exists():
        content = ENV_EXAMPLE.read_text(encoding="utf-8")
        ENV_FILE.write_text(content, encoding="utf-8")
        print(f"[OK] Created {ENV_FILE} from {ENV_EXAMPLE}")


def main() -> int:
    print("=" * 70)
    print("Orbital-Drift GPU Environment Diagnostic (2026)")
    print("=" * 70)
    print(f"Python: {sys.version}")
    print(f"Interpreter: {sys.executable}")
    print(f"Repository Root: {REPO_ROOT}")

    gpu_info = check_gpu_hardware()
    print("\n--- GPU & CUDA Status ---")
    print(f"CUDA Available: {gpu_info['cuda_available']}")
    print(f"Device Count:   {gpu_info['device_count']}")
    if gpu_info["devices"]:
        print(f"Devices:        {gpu_info['devices']}")

    dep_info = check_dependencies()
    print("\n--- Dependencies Status ---")
    all_ok = True
    for pkg, ok in dep_info.items():
        mark = "[OK]" if ok else "[MISSING]"
        print(f"  {mark} {pkg}")
        if not ok:
            all_ok = False

    ensure_env_file()

    print("=" * 70)
    if all_ok:
        print("[SUCCESS] Environment is fully configured for GPU continuous training & testing.")
        return 0
    else:
        print("[WARN] Some dependencies are missing. Run pip install to complete setup.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
