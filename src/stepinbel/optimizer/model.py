"""Sparse continuous PHS LP construction. Solver-neutral."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from stepinbel.config import SimulationConfig
from stepinbel.markets.base import MarketDispatchInputs
from stepinbel.optimizer.types import DT_H, CapacityCommitment, ModelError

_INF = 1.0e30


@dataclass(frozen=True)
class VariableLayout:
    n: int
    pv_enabled: bool
    use_r_pump: bool
    use_r_turb: bool
    n_commitments: int
    idx_pump: int
    idx_turb: int
    idx_e: int
    idx_pump_grid: int | None
    idx_pv_export: int | None
    idx_pv_to_pump: int | None
    idx_pv_curtail: int | None
    idx_r_pump: int | None
    idx_r_turb: int | None
    idx_capacity: int | None
    num_col: int


@dataclass(frozen=True)
class PreparedPhysical:
    n: int
    dt_h: float
    e_max_mwh: float
    e0_mwh: float
    e_terminal_mwh: float | None
    eta_pump: float
    eta_turbine: float
    epsilon_pump: float
    epsilon_turbine: float
    pump_ramp_up_mw: float
    pump_ramp_down_mw: float
    turbine_ramp_up_mw: float
    turbine_ramp_down_mw: float
    pump_bound_mw: np.ndarray
    turbine_bound_mw: np.ndarray
    sell_price: np.ndarray
    buy_price: np.ndarray
    grid_import_mw: float
    grid_export_mw: float
    pv_enabled: bool
    pv_available_mw: np.ndarray
    pv_export_price: np.ndarray
    commitments: tuple[CapacityCommitment, ...]


@dataclass(frozen=True)
class SparseLp:
    layout: VariableLayout
    prepared: PreparedPhysical
    col_cost: np.ndarray
    col_lower: np.ndarray
    col_upper: np.ndarray
    row_lower: np.ndarray
    row_upper: np.ndarray
    a_start: np.ndarray
    a_index: np.ndarray
    a_value: np.ndarray
    num_col: int
    num_row: int
    num_nz: int
    n_pump_ramp_up_vars: int
    n_turbine_ramp_up_vars: int
    has_downward_pump_capacity_rows: bool


def prepare_physical(
    config: SimulationConfig,
    market: MarketDispatchInputs,
    pv_load_factor: np.ndarray | None,
) -> PreparedPhysical:
    n = market.interval_count
    asset = config.asset
    e_max = float(asset.e_max_mwh())
    e0 = float(asset.soc_initial_frac) * e_max
    e_terminal = (
        float(asset.soc_terminal_frac) * e_max if asset.enforce_terminal_soc else None
    )
    grid_import = float(config.effective_grid_import_mw())
    grid_export = float(config.effective_grid_export_mw())
    pv_enabled = config.pv_enabled()
    if pv_enabled:
        if pv_load_factor is None:
            raise ModelError("PV is enabled but no load-factor series was provided")
        factor = np.array(pv_load_factor, dtype=np.float64, copy=True)
        if factor.shape != (n,):
            raise ModelError("PV load_factor length must match the market horizon")
        if not np.all(np.isfinite(factor)):
            raise ModelError("PV load_factor contains NaN or infinite values")
        if np.any(factor < -1e-9) or np.any(factor > 1.0 + 1e-9):
            raise ModelError("PV load_factor must be in [0, 1]")
        factor = np.clip(factor, 0.0, 1.0)
        avail = factor * (float(config.site.pv_ac_kw) / 1000.0)
        if config.site.pv_revenue_mode == "fixed":
            assert config.site.pv_fixed_price_eur_mwh is not None
            price = np.full(n, float(config.site.pv_fixed_price_eur_mwh), dtype=np.float64)
        else:
            price = np.array(market.day_ahead_price_eur_mwh, dtype=np.float64, copy=True)
        pump_bound = np.minimum(market.buy_upper_mw, float(asset.power_pump_mw))
        turbine_bound = np.minimum(market.sell_upper_mw, float(asset.power_turbine_mw))
    else:
        if pv_load_factor is not None:
            raise ModelError("PV load_factor was supplied while PV is disabled")
        avail = np.zeros(n, dtype=np.float64)
        price = np.full(n, np.nan, dtype=np.float64)
        pump_bound = np.minimum(market.buy_upper_mw, grid_import)
        turbine_bound = np.minimum(market.sell_upper_mw, grid_export)

    pump_bound = np.array(pump_bound, dtype=np.float64, copy=True)
    turbine_bound = np.array(turbine_bound, dtype=np.float64, copy=True)
    if np.any(pump_bound < 0.0) or np.any(turbine_bound < 0.0):
        raise ModelError("prepared machine bounds must be >= 0")
    for spec in market.capacity_commitments:
        if not (0 <= spec.start_index < spec.end_index <= n):
            raise ModelError(
                f"capacity commitment {spec.identifier!r} span "
                f"[{spec.start_index}, {spec.end_index}) is outside [0, {n})"
            )
    pump_bound.setflags(write=False)
    turbine_bound.setflags(write=False)
    avail.setflags(write=False)
    price.setflags(write=False)
    return PreparedPhysical(
        n=n,
        dt_h=DT_H,
        e_max_mwh=e_max,
        e0_mwh=e0,
        e_terminal_mwh=e_terminal,
        eta_pump=float(asset.eta_pump),
        eta_turbine=float(asset.eta_turbine),
        epsilon_pump=float(asset.epsilon_pump_mwh_per_mw()),
        epsilon_turbine=float(asset.epsilon_turbine_mwh_per_mw()),
        pump_ramp_up_mw=float(asset.pump_ramp_up_mw_per_h()) * DT_H,
        pump_ramp_down_mw=float(asset.pump_ramp_down_mw_per_h()) * DT_H,
        turbine_ramp_up_mw=float(asset.turbine_ramp_up_mw_per_h()) * DT_H,
        turbine_ramp_down_mw=float(asset.turbine_ramp_down_mw_per_h()) * DT_H,
        pump_bound_mw=pump_bound,
        turbine_bound_mw=turbine_bound,
        sell_price=market.sell_price_eur_mwh,
        buy_price=market.buy_price_eur_mwh,
        grid_import_mw=grid_import,
        grid_export_mw=grid_export,
        pv_enabled=pv_enabled,
        pv_available_mw=avail,
        pv_export_price=price,
        commitments=market.capacity_commitments,
    )


def _layout(prepared: PreparedPhysical) -> VariableLayout:
    n = prepared.n
    use_r_pump = prepared.epsilon_pump > 0.0
    use_r_turb = prepared.epsilon_turbine > 0.0
    n_c = len(prepared.commitments)
    idx = 0
    idx_pump = idx
    idx += n
    idx_turb = idx
    idx += n
    idx_e = idx
    idx += n + 1
    idx_pump_grid = idx_pv_export = idx_pv_to_pump = idx_pv_curtail = None
    if prepared.pv_enabled:
        idx_pump_grid = idx
        idx += n
        idx_pv_export = idx
        idx += n
        idx_pv_to_pump = idx
        idx += n
        idx_pv_curtail = idx
        idx += n
    idx_r_pump = None
    if use_r_pump:
        idx_r_pump = idx
        idx += n
    idx_r_turb = None
    if use_r_turb:
        idx_r_turb = idx
        idx += n
    idx_capacity = None
    if n_c:
        idx_capacity = idx
        idx += n_c
    return VariableLayout(
        n=n,
        pv_enabled=prepared.pv_enabled,
        use_r_pump=use_r_pump,
        use_r_turb=use_r_turb,
        n_commitments=n_c,
        idx_pump=idx_pump,
        idx_turb=idx_turb,
        idx_e=idx_e,
        idx_pump_grid=idx_pump_grid,
        idx_pv_export=idx_pv_export,
        idx_pv_to_pump=idx_pv_to_pump,
        idx_pv_curtail=idx_pv_curtail,
        idx_r_pump=idx_r_pump,
        idx_r_turb=idx_r_turb,
        idx_capacity=idx_capacity,
        num_col=idx,
    )


class _Matrix:
    def __init__(self, num_col: int) -> None:
        self.entries: list[list[tuple[int, float]]] = [[] for _ in range(num_col)]

    def put(self, col: int, row: int, value: float) -> None:
        if value == 0.0:
            return
        self.entries[col].append((row, float(value)))

    def freeze(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
        num_col = len(self.entries)
        starts = np.zeros(num_col + 1, dtype=np.int32)
        for col in range(num_col):
            starts[col + 1] = starts[col] + len(self.entries[col])
        num_nz = int(starts[-1])
        indices = np.empty(num_nz, dtype=np.int32)
        values = np.empty(num_nz, dtype=np.float64)
        for col in range(num_col):
            start = int(starts[col])
            for offset, (row, value) in enumerate(self.entries[col]):
                indices[start + offset] = row
                values[start + offset] = value
        return starts, indices, values, num_nz


def build_sparse_lp(prepared: PreparedPhysical) -> SparseLp:
    layout = _layout(prepared)
    n = prepared.n
    dt = prepared.dt_h
    matrix = _Matrix(layout.num_col)
    col_cost = np.zeros(layout.num_col, dtype=np.float64)
    col_lower = np.zeros(layout.num_col, dtype=np.float64)
    col_upper = np.empty(layout.num_col, dtype=np.float64)
    col_upper[layout.idx_pump : layout.idx_pump + n] = prepared.pump_bound_mw
    col_upper[layout.idx_turb : layout.idx_turb + n] = prepared.turbine_bound_mw
    col_upper[layout.idx_e : layout.idx_e + n + 1] = prepared.e_max_mwh

    grid_col = layout.idx_pump if not prepared.pv_enabled else layout.idx_pump_grid
    assert grid_col is not None
    col_cost[layout.idx_turb : layout.idx_turb + n] = prepared.sell_price * dt
    col_cost[grid_col : grid_col + n] = -prepared.buy_price * dt

    row = 0
    row += 1
    row += n
    if prepared.e_terminal_mwh is not None:
        row += 1

    matrix.put(layout.idx_e, 0, 1.0)
    for t in range(n):
        r = 1 + t
        matrix.put(layout.idx_e + t + 1, r, 1.0)
        matrix.put(layout.idx_e + t, r, -1.0)
        matrix.put(layout.idx_pump + t, r, -(prepared.eta_pump * dt))
        matrix.put(layout.idx_turb + t, r, dt / prepared.eta_turbine)
        if layout.idx_r_pump is not None:
            matrix.put(
                layout.idx_r_pump + t, r, prepared.eta_pump * prepared.epsilon_pump
            )
        if layout.idx_r_turb is not None:
            matrix.put(
                layout.idx_r_turb + t, r, prepared.epsilon_turbine / prepared.eta_turbine
            )
    if prepared.e_terminal_mwh is not None:
        matrix.put(layout.idx_e + n, 1 + n, 1.0)

    row = 1 + n + (1 if prepared.e_terminal_mwh is not None else 0)
    if layout.idx_r_pump is not None:
        col_upper[layout.idx_r_pump : layout.idx_r_pump + n] = _INF
        matrix.put(layout.idx_r_pump, row, 1.0)
        matrix.put(layout.idx_pump, row, -1.0)
        row += 1
        for t in range(1, n):
            matrix.put(layout.idx_r_pump + t, row, 1.0)
            matrix.put(layout.idx_pump + t, row, -1.0)
            matrix.put(layout.idx_pump + t - 1, row, 1.0)
            row += 1
    if layout.idx_r_turb is not None:
        col_upper[layout.idx_r_turb : layout.idx_r_turb + n] = _INF
        matrix.put(layout.idx_r_turb, row, 1.0)
        matrix.put(layout.idx_turb, row, -1.0)
        row += 1
        for t in range(1, n):
            matrix.put(layout.idx_r_turb + t, row, 1.0)
            matrix.put(layout.idx_turb + t, row, -1.0)
            matrix.put(layout.idx_turb + t - 1, row, 1.0)
            row += 1

    matrix.put(layout.idx_pump, row, 1.0)
    row += 1
    matrix.put(layout.idx_turb, row, 1.0)
    row += 1
    for t in range(1, n):
        matrix.put(layout.idx_pump + t, row, 1.0)
        matrix.put(layout.idx_pump + t - 1, row, -1.0)
        row += 1
        matrix.put(layout.idx_pump + t - 1, row, 1.0)
        matrix.put(layout.idx_pump + t, row, -1.0)
        row += 1
        matrix.put(layout.idx_turb + t, row, 1.0)
        matrix.put(layout.idx_turb + t - 1, row, -1.0)
        row += 1
        matrix.put(layout.idx_turb + t - 1, row, 1.0)
        matrix.put(layout.idx_turb + t, row, -1.0)
        row += 1

    if prepared.pv_enabled:
        assert layout.idx_pump_grid is not None
        assert layout.idx_pv_export is not None
        assert layout.idx_pv_to_pump is not None
        assert layout.idx_pv_curtail is not None
        for t in range(n):
            col_upper[layout.idx_pump_grid + t] = min(
                float(prepared.pump_bound_mw[t]), prepared.grid_import_mw
            )
        col_upper[layout.idx_pv_export : layout.idx_pv_export + n] = _INF
        col_upper[layout.idx_pv_to_pump : layout.idx_pv_to_pump + n] = _INF
        col_upper[layout.idx_pv_curtail : layout.idx_pv_curtail + n] = _INF
        col_cost[layout.idx_pv_export : layout.idx_pv_export + n] = (
            prepared.pv_export_price * dt
        )
        for t in range(n):
            matrix.put(layout.idx_pv_export + t, row, 1.0)
            matrix.put(layout.idx_pv_to_pump + t, row, 1.0)
            matrix.put(layout.idx_pv_curtail + t, row, 1.0)
            row += 1
            matrix.put(layout.idx_pump + t, row, 1.0)
            matrix.put(layout.idx_pump_grid + t, row, -1.0)
            matrix.put(layout.idx_pv_to_pump + t, row, -1.0)
            row += 1
            matrix.put(layout.idx_turb + t, row, 1.0)
            matrix.put(layout.idx_pv_export + t, row, 1.0)
            row += 1

    if layout.idx_capacity is not None:
        for i, spec in enumerate(prepared.commitments):
            col_upper[layout.idx_capacity + i] = spec.cap_max_mw
            col_cost[layout.idx_capacity + i] = spec.price_eur_mw_h * spec.block_hours
            if spec.direction == "up":
                for t in range(spec.start_index, spec.end_index):
                    matrix.put(layout.idx_turb + t, row, 1.0)
                    matrix.put(layout.idx_capacity + i, row, -1.0)
                    row += 1
                matrix.put(layout.idx_e + spec.start_index, row, 1.0)
                matrix.put(
                    layout.idx_capacity + i,
                    row,
                    -(spec.coverage_hours / prepared.eta_turbine),
                )
                row += 1
            else:
                matrix.put(layout.idx_e + spec.start_index, row, 1.0)
                matrix.put(
                    layout.idx_capacity + i,
                    row,
                    prepared.eta_pump * spec.coverage_hours,
                )
                row += 1

    num_row = row
    row_lower = np.empty(num_row, dtype=np.float64)
    row_upper = np.empty(num_row, dtype=np.float64)
    row = 0
    row_lower[row] = prepared.e0_mwh
    row_upper[row] = prepared.e0_mwh
    row += 1
    row_lower[row : row + n] = 0.0
    row_upper[row : row + n] = 0.0
    row += n
    if prepared.e_terminal_mwh is not None:
        row_lower[row] = prepared.e_terminal_mwh
        row_upper[row] = prepared.e_terminal_mwh
        row += 1
    if layout.idx_r_pump is not None:
        row_lower[row : row + n] = 0.0
        row_upper[row : row + n] = _INF
        row += n
    if layout.idx_r_turb is not None:
        row_lower[row : row + n] = 0.0
        row_upper[row : row + n] = _INF
        row += n
    row_lower[row] = -_INF
    row_upper[row] = prepared.pump_ramp_up_mw
    row += 1
    row_lower[row] = -_INF
    row_upper[row] = prepared.turbine_ramp_up_mw
    row += 1
    for _t in range(1, n):
        row_lower[row] = -_INF
        row_upper[row] = prepared.pump_ramp_up_mw
        row += 1
        row_lower[row] = -_INF
        row_upper[row] = prepared.pump_ramp_down_mw
        row += 1
        row_lower[row] = -_INF
        row_upper[row] = prepared.turbine_ramp_up_mw
        row += 1
        row_lower[row] = -_INF
        row_upper[row] = prepared.turbine_ramp_down_mw
        row += 1
    if prepared.pv_enabled:
        for t in range(n):
            row_lower[row] = prepared.pv_available_mw[t]
            row_upper[row] = prepared.pv_available_mw[t]
            row += 1
            row_lower[row] = 0.0
            row_upper[row] = 0.0
            row += 1
            row_lower[row] = -_INF
            row_upper[row] = prepared.grid_export_mw
            row += 1
    if layout.idx_capacity is not None:
        for spec in prepared.commitments:
            if spec.direction == "up":
                span = spec.end_index - spec.start_index
                row_lower[row : row + span] = -_INF
                row_upper[row : row + span] = 0.0
                row += span
                row_lower[row] = 0.0
                row_upper[row] = _INF
                row += 1
            else:
                row_lower[row] = -_INF
                row_upper[row] = prepared.e_max_mwh
                row += 1

    if row != num_row:
        raise ModelError("sparse LP row bounds do not match constructed rows")

    starts, indices, values, num_nz = matrix.freeze()
    return SparseLp(
        layout=layout,
        prepared=prepared,
        col_cost=col_cost,
        col_lower=col_lower,
        col_upper=col_upper,
        row_lower=row_lower,
        row_upper=row_upper,
        a_start=starts,
        a_index=indices,
        a_value=values,
        num_col=layout.num_col,
        num_row=num_row,
        num_nz=num_nz,
        n_pump_ramp_up_vars=n if layout.use_r_pump else 0,
        n_turbine_ramp_up_vars=n if layout.use_r_turb else 0,
        has_downward_pump_capacity_rows=False,
    )
