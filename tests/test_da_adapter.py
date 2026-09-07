from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from stepinbel.config import AFRRCase, DayAheadCase, MFRRCase, SimulationConfig, UtcPeriod
from stepinbel.data import load_market_data, open_published_bundle
from stepinbel.markets import (
    MarketDispatchInputs,
    MarketInputError,
    build_day_ahead_inputs,
    build_mfrr_inputs,
)
from stepinbel.optimizer import ModelError, SolverOptions, solve_case


def test_da_adapter_price_symmetry_and_no_commitments(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = UtcPeriod(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 15, 2, tzinfo=timezone.utc),
    )
    slice_ = load_market_data(
        bundle, SimulationConfig(period=period, market_case=DayAheadCase())
    )
    market = build_day_ahead_inputs(slice_)
    np.testing.assert_array_equal(market.sell_price_eur_mwh, market.buy_price_eur_mwh)
    np.testing.assert_array_equal(market.day_ahead_price_eur_mwh, market.sell_price_eur_mwh)
    assert market.sell_price_eur_mwh.flags.writeable is False
    assert market.buy_price_eur_mwh.flags.writeable is False
    assert market.sell_upper_mw.flags.writeable is False
    assert market.day_ahead_price_eur_mwh.flags.writeable is False
    assert np.allclose(market.sell_upper_mw, 1.0)
    assert np.allclose(market.buy_upper_mw, 1.0)
    assert market.capacity_commitments == ()
    owned = np.array(market.sell_price_eur_mwh)
    owned[0] = 0.0
    assert market.sell_price_eur_mwh[0] != owned[0] or slice_.da_prices.num_rows == 0


def test_da_adapter_rejects_wrong_market(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = UtcPeriod(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 15, 1, tzinfo=timezone.utc),
    )
    slice_ = load_market_data(
        bundle, SimulationConfig(period=period, market_case=MFRRCase())
    )
    with pytest.raises(ModelError, match="DayAheadCase"):
        build_day_ahead_inputs(slice_)


def test_market_inputs_own_independent_read_only_copies() -> None:
    sell = np.array([11.0, 22.0], dtype=np.float64)
    buy = np.array([33.0, 44.0], dtype=np.float64)
    sell_ub = np.array([1.0, 1.5], dtype=np.float64)
    buy_ub = np.array([0.5, 0.75], dtype=np.float64)
    da_price = np.array([55.0, 66.0], dtype=np.float64)
    market = MarketDispatchInputs(
        sell, buy, sell_ub, buy_ub, day_ahead_price_eur_mwh=da_price
    )
    sell[0] = -99.0
    buy[1] = -99.0
    sell_ub[0] = -99.0
    buy_ub[1] = -99.0
    da_price[0] = -99.0
    np.testing.assert_array_equal(market.sell_price_eur_mwh, [11.0, 22.0])
    np.testing.assert_array_equal(market.buy_price_eur_mwh, [33.0, 44.0])
    np.testing.assert_array_equal(market.sell_upper_mw, [1.0, 1.5])
    np.testing.assert_array_equal(market.buy_upper_mw, [0.5, 0.75])
    np.testing.assert_array_equal(market.day_ahead_price_eur_mwh, [55.0, 66.0])
    assert market.sell_price_eur_mwh.flags.writeable is False
    assert market.buy_price_eur_mwh.flags.writeable is False
    assert market.sell_upper_mw.flags.writeable is False
    assert market.buy_upper_mw.flags.writeable is False
    assert market.day_ahead_price_eur_mwh.flags.writeable is False


def test_malformed_market_arrays_fail() -> None:
    with pytest.raises(MarketInputError, match="empty"):
        MarketDispatchInputs([], [], [], [], day_ahead_price_eur_mwh=[])
    with pytest.raises(MarketInputError, match="equal length"):
        MarketDispatchInputs(
            [1.0, 2.0], [1.0], [1.0, 1.0], [1.0, 1.0], day_ahead_price_eur_mwh=[1.0, 2.0]
        )
    with pytest.raises(MarketInputError, match="one-dimensional"):
        MarketDispatchInputs(
            [[1.0, 2.0]], [1.0, 2.0], [1.0, 1.0], [1.0, 1.0], day_ahead_price_eur_mwh=[1.0, 2.0]
        )
    with pytest.raises(MarketInputError, match="NaN"):
        MarketDispatchInputs([np.nan], [1.0], [1.0], [1.0], day_ahead_price_eur_mwh=[1.0])
    with pytest.raises(MarketInputError, match="infinite"):
        MarketDispatchInputs([np.inf], [1.0], [1.0], [1.0], day_ahead_price_eur_mwh=[1.0])
    with pytest.raises(MarketInputError, match=">= 0"):
        MarketDispatchInputs([1.0], [1.0], [-1.0], [1.0], day_ahead_price_eur_mwh=[1.0])
    with pytest.raises(MarketInputError, match="day_ahead_price_eur_mwh"):
        MarketDispatchInputs([1.0], [1.0], [1.0], [1.0], day_ahead_price_eur_mwh=[object()])
    with pytest.raises(MarketInputError, match="equal length"):
        MarketDispatchInputs(
            [1.0, 2.0],
            [1.0, 2.0],
            [1.0, 1.0],
            [1.0, 1.0],
            day_ahead_price_eur_mwh=[1.0],
        )


def test_omitted_day_ahead_price_is_required() -> None:
    with pytest.raises(MarketInputError, match="day_ahead_price_eur_mwh"):
        MarketDispatchInputs([200.0], [10.0], [1.0], [1.0])


def test_unconvertible_market_array_is_domain_error() -> None:
    with pytest.raises(MarketInputError, match="sell_price_eur_mwh") as caught:
        MarketDispatchInputs(
            [object()], [1.0], [1.0], [1.0], day_ahead_price_eur_mwh=[1.0]
        )
    assert isinstance(caught.value.__cause__, TypeError)


def test_ragged_market_array_is_domain_error() -> None:
    with pytest.raises(MarketInputError, match="sell_price_eur_mwh") as caught:
        MarketDispatchInputs(
            [[1.0, 2.0], [3.0]],
            [1.0, 1.0],
            [1.0, 1.0],
            [1.0, 1.0],
            day_ahead_price_eur_mwh=[1.0, 1.0],
        )
    assert isinstance(caught.value.__cause__, (TypeError, ValueError))


def test_null_like_market_array_is_domain_error() -> None:
    with pytest.raises(MarketInputError, match="sell_price_eur_mwh"):
        MarketDispatchInputs(None, [1.0], [1.0], [1.0], day_ahead_price_eur_mwh=[1.0])


def test_mfrr_adapter_uses_da_for_buy_and_reference_not_sell(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = UtcPeriod(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 15, 4, tzinfo=timezone.utc),
    )
    slice_ = load_market_data(
        bundle, SimulationConfig(period=period, market_case=MFRRCase())
    )
    with pytest.raises(MarketInputError, match="complete capacity blocks"):
        build_mfrr_inputs(slice_)


def test_solver_options_reject_non_booleans() -> None:
    with pytest.raises(ModelError, match="boolean"):
        SolverOptions(detailed_output=1)  # type: ignore[arg-type]


def test_da_adapter_rejects_afrr_case(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = UtcPeriod(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 15, 1, tzinfo=timezone.utc),
    )
    afrr = load_market_data(
        bundle, SimulationConfig(period=period, market_case=AFRRCase())
    )
    with pytest.raises(ModelError, match="DayAheadCase"):
        build_day_ahead_inputs(afrr)
