"""Bucketing and the rolling transition matrix: shape, causality, and recovery of a planted edge."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.markov import (
    BucketScheme,
    bucket_states,
    estimate,
    final_matrix,
    read_rows,
    scaled_returns,
    state_table,
)


def _scheme(width: float = 1.0, n_side: int = 3, scale: str = "percent") -> BucketScheme:
    return BucketScheme(width=width, n_side=n_side, scale=scale)


def _planted(n_symbols: int = 6, n_bars: int = 900, seed: int = 0) -> np.ndarray:
    """Returns where a fall past -1% is followed by a strongly positive bar."""
    rng = np.random.default_rng(seed)
    out = np.zeros((n_symbols, n_bars))
    for i in range(n_symbols):
        previous = 0.0
        for t in range(n_bars):
            drift = 0.015 if previous < -0.01 else 0.0
            previous = drift + rng.normal(0, 0.012)
            out[i, t] = previous
    return out


def test_bucket_edges_put_zero_in_the_flat_bucket_and_clip_the_tails():
    scheme = _scheme(width=1.0, n_side=3)
    moves = np.array([[0.0, 0.4, -0.4, 1.2, -1.2, 9.0, -9.0]])
    states = bucket_states(moves, scheme)
    assert list(states[0]) == [3, 3, 3, 4, 2, 6, 0]
    assert scheme.n_states == 7
    assert scheme.centres[3] == pytest.approx(0.0)
    assert scheme.centres[6] == pytest.approx(0.03)


def test_unknown_moves_become_minus_one_and_are_never_counted():
    scheme = _scheme()
    moves = np.array([[np.nan, 1.0, np.inf, -1.0]])
    states = bucket_states(moves, scheme)
    assert states[0][0] == -1
    assert states[0][2] == -1
    assert (states[0][[1, 3]] >= 0).all()


def test_sigma_scale_makes_two_symbols_of_different_volatility_comparable():
    index = pd.bdate_range("2020-01-01", periods=400)
    rng = np.random.default_rng(5)
    returns = pd.DataFrame(
        {"CALM": rng.normal(0, 0.004, 400), "WILD": rng.normal(0, 0.040, 400)},
        index=index,
    )
    scheme = _scheme(width=0.5, n_side=4, scale="sigma")
    scaled = scaled_returns(returns, scheme)
    spread = scaled.std()
    assert spread["CALM"] == pytest.approx(spread["WILD"], rel=0.25)

    percent = scaled_returns(returns, _scheme(scale="percent")).std()
    assert percent["WILD"] > 5 * percent["CALM"]


def test_the_matrix_recovers_a_planted_transition():
    moves = _planted()
    scheme = _scheme(width=0.75, n_side=5)
    states = bucket_states(moves * 100, scheme)
    table = state_table(states, moves, scheme)

    deep_down = table[(table["mean_next_return"] > 0.01) & (table["observations"] >= 100)]
    assert not deep_down.empty
    assert (deep_down["p_up_given_move"] > 0.8).all()
    assert (deep_down["p_up"] > 0.7).all()
    assert (deep_down["lift_vs_base"] > 0.2).all()

    thin = table[table["observations"] < 40]
    assert (thin["p_up"] < deep_down["p_up"].min()).all(), "smoothing must pull a thin row toward the middle"

    flat = table.iloc[scheme.n_side]
    assert flat["p_up"] < 0.5
    assert abs(flat["mean_next_return"]) < 0.002


def test_every_row_of_the_matrix_is_a_probability_distribution():
    moves = _planted(n_symbols=3, n_bars=400, seed=2)
    scheme = _scheme(width=0.75, n_side=4)
    matrix = final_matrix(bucket_states(moves * 100, scheme), moves, scheme)
    assert matrix.shape == (scheme.n_states, scheme.n_states)
    assert np.allclose(matrix.sum(axis=1), 1.0)
    assert (matrix.to_numpy() > 0).all()


def test_smoothing_keeps_an_unseen_transition_away_from_certainty():
    scheme = _scheme(width=1.0, n_side=1)
    moves = np.array([[np.nan, 0.02, 0.02, 0.02, 0.02]])
    states = bucket_states(moves * 100, scheme)
    confident = final_matrix(states, moves, scheme, alpha=0.001)
    cautious = final_matrix(states, moves, scheme, alpha=5.0)
    up = scheme.n_states - 1
    assert confident.to_numpy()[up, up] > 0.99
    assert cautious.to_numpy()[up, up] < 0.6


def test_confidence_renormalises_against_the_down_side_not_the_whole_row():
    """The flat bucket holds real mass, so a raw P(up) cannot reach a sensible threshold. The
    directional reading divides the up share by the up-plus-down share instead."""
    moves = _planted()
    scheme = _scheme(width=0.75, n_side=5)
    states = bucket_states(moves * 100, scheme)
    stats = estimate(states, moves, scheme)

    raw = stats.p_up[-1]
    directional = stats.directional[-1]
    assert np.nanmax(raw) < np.nanmax(directional)
    assert np.allclose(directional, stats.p_up[-1] / (stats.p_up[-1] + stats.p_down[-1]), equal_nan=True)
    assert 0.0 <= np.nanmin(directional) and np.nanmax(directional) <= 1.0
    assert 0.3 < stats.base_directional[-1] < 0.7


def test_the_estimate_on_a_bar_never_uses_a_later_bar():
    """Deleting the tail of the sample must leave every earlier reading untouched."""
    rng = np.random.default_rng(11)
    moves = rng.normal(0.0005, 0.015, (5, 600))
    scheme = _scheme(width=0.75, n_side=4)
    states = bucket_states(moves * 100, scheme)
    cut = 400

    full = estimate(states, moves, scheme, window=250)
    truncated = estimate(states[:, :cut], moves[:, :cut], scheme, window=250)

    for attribute in ("p_up", "p_down", "expected", "samples", "directional"):
        a = read_rows(states, getattr(full, attribute))[:, :cut]
        b = read_rows(states[:, :cut], getattr(truncated, attribute))
        assert np.allclose(np.nan_to_num(a), np.nan_to_num(b))
    assert np.allclose(np.nan_to_num(full.base_up[:cut]), np.nan_to_num(truncated.base_up))
    assert np.allclose(np.nan_to_num(full.base_directional[:cut]), np.nan_to_num(truncated.base_directional))


def test_a_trailing_window_forgets_and_an_expanding_one_does_not():
    scheme = _scheme(width=1.0, n_side=1)
    n_bars = 300
    moves = np.full((1, n_bars), 0.02)
    moves[0, :100] = -0.02
    states = bucket_states(moves * 100, scheme)

    rolling = estimate(states, moves, scheme, window=50)
    expanding = estimate(states, moves, scheme, window=0)
    assert rolling.samples[-1].sum() == pytest.approx(50)
    assert expanding.samples[-1].sum() == pytest.approx(n_bars - 1)


def test_samples_accumulate_across_the_basket_when_pooled():
    moves = _planted(n_symbols=8, n_bars=300, seed=4)
    scheme = _scheme(width=0.75, n_side=3)
    states = bucket_states(moves * 100, scheme)
    pooled = estimate(states, moves, scheme)
    single = estimate(states[:1], moves[:1], scheme)
    assert pooled.samples[-1].sum() > 6 * single.samples[-1].sum()


def test_read_rows_picks_each_symbol_its_own_state_and_blanks_the_unknown():
    values = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    states = np.array([[0, 2], [1, -1]])
    picked = read_rows(states, values)
    assert picked[0].tolist() == [0.1, 0.6]
    assert picked[1][0] == pytest.approx(0.2)
    assert np.isnan(picked[1][1])


def test_the_scheme_rejects_nonsense():
    with pytest.raises(ValueError, match="width"):
        BucketScheme(width=0.0, n_side=3)
    with pytest.raises(ValueError, match="n_side"):
        BucketScheme(width=1.0, n_side=99)
    with pytest.raises(ValueError, match="scale"):
        BucketScheme(width=1.0, n_side=3, scale="log")


def test_labels_name_every_bucket_and_mark_the_clipped_tails():
    labels = _scheme(width=1.0, n_side=2).labels()
    assert len(labels) == 5
    assert labels[2].startswith("flat")
    assert labels[0].startswith("≤")
    assert labels[-1].startswith("≥")
