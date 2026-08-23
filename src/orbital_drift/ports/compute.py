"""Hardware-topology port that keeps compute libraries out of the domain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class DeviceSpec:
    """A configuration-level description of a selectable compute device."""

    identifier: str
    kind: str
    index: int | None
    total_memory_bytes: int | None


@runtime_checkable
class ComputePort(Protocol):
    """Expose device topology through domain language rather than a hardware SDK."""

    def devices(self) -> tuple[DeviceSpec, ...]:
        """Return available devices."""

    def select(self, role: str) -> DeviceSpec:
        """Select a device configured for a logical role."""

    def memory_budget_bytes(self, device: DeviceSpec) -> int:
        """Return the safely usable memory budget for a device."""


class InMemoryCompute:
    """A CPU-only compute fake with explicit role assignments and memory budgets."""

    def __init__(
        self,
        devices: tuple[DeviceSpec, ...],
        role_assignments: dict[str, str],
        memory_budgets: dict[str, int],
    ) -> None:
        self._devices = devices
        self._role_assignments = dict(role_assignments)
        self._memory_budgets = dict(memory_budgets)

    def devices(self) -> tuple[DeviceSpec, ...]:
        """Return the configured device inventory."""
        return self._devices

    def select(self, role: str) -> DeviceSpec:
        """Return the device assigned to a role."""
        identifier = self._role_assignments.get(role)
        if identifier is None:
            raise KeyError(f"no device configured for role: {role}")
        for device in self._devices:
            if device.identifier == identifier:
                return device
        raise KeyError(f"configured device not found: {identifier}")

    def memory_budget_bytes(self, device: DeviceSpec) -> int:
        """Return the configured memory budget for a known device."""
        try:
            return self._memory_budgets[device.identifier]
        except KeyError as error:
            raise KeyError(
                f"no memory budget configured for device: {device.identifier}"
            ) from error
