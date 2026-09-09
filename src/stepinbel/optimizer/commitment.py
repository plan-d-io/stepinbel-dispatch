"""Resolve optional machine-commitment assumptions into model flags."""

from __future__ import annotations

from dataclasses import dataclass

from stepinbel.config import AssetConfig, MachineCommitmentConfig, SimulationConfig
from stepinbel.optimizer.types import ModelError


@dataclass(frozen=True)
class ResolvedCommitment:
    """Validated physical commitment flags used while building the sparse model."""

    fixed_speed_pump: bool
    rated_pump_mw: float
    turbine_minimum_mw: float
    rated_turbine_mw: float
    forbid_simultaneous: bool
    use_pump_commitment: bool
    use_turbine_commitment: bool

    def binary_count(self, n: int) -> int:
        count = 0
        if self.use_pump_commitment:
            count += n
        if self.use_turbine_commitment:
            count += n
        return count


def resolve_machine_commitment(config: SimulationConfig) -> ResolvedCommitment | None:
    """Return resolved MILP flags, or ``None`` to keep the continuous LP."""
    if not isinstance(config, SimulationConfig):
        raise ModelError("commitment resolution requires a SimulationConfig")
    options = config.machine_commitment
    if not isinstance(options, MachineCommitmentConfig):
        raise ModelError("machine_commitment must be a MachineCommitmentConfig")
    asset = config.asset
    if not isinstance(asset, AssetConfig):
        raise ModelError("commitment resolution requires an AssetConfig")
    if not options.physically_active():
        return None
    rated_pump = float(asset.power_pump_mw)
    rated_turbine = float(asset.power_turbine_mw)
    if options.fixed_speed_pump and rated_pump <= 0.0:
        raise ModelError("fixed-speed pump rated power must be > 0")
    fraction = float(options.turbine_minimum_output_fraction)
    turbine_minimum_mw = fraction * rated_turbine if options.turbine_minimum_active() else 0.0
    if options.turbine_minimum_active() and turbine_minimum_mw > rated_turbine:
        raise ModelError(
            "turbine_minimum_output_fraction resolves above rated turbine power "
            f"({rated_turbine:g} MW)"
        )
    return ResolvedCommitment(
        fixed_speed_pump=options.fixed_speed_pump,
        rated_pump_mw=rated_pump,
        turbine_minimum_mw=turbine_minimum_mw,
        rated_turbine_mw=rated_turbine,
        forbid_simultaneous=options.forbid_simultaneous_operation,
        use_pump_commitment=options.needs_pump_commitment(),
        use_turbine_commitment=options.needs_turbine_commitment(),
    )
