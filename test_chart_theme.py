import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import chart_theme as ct


def test_apply_theme_single_axes():
    fig, ax = plt.subplots()
    ct.apply_theme(fig, ax)
    assert fig.get_facecolor() == matplotlib.colors.to_rgba(ct.BG)
    assert ax.get_facecolor() == matplotlib.colors.to_rgba(ct.PANEL)


def test_apply_theme_list_of_axes():
    fig, axes = plt.subplots(2, 1)
    ct.apply_theme(fig, [axes[0], axes[1]])
    for ax in axes:
        assert ax.get_facecolor() == matplotlib.colors.to_rgba(ct.PANEL)


def test_apply_theme_numpy_flatiter():
    # the exact bug found migrating option_telegram_report.py: plt.subplots'
    # own 2D axes array's .flat is a numpy.flatiter, not a list/tuple --
    # isinstance(axes, (list, tuple)) is False for it, so a naive check
    # would wrap the flatiter itself as "one axes" instead of iterating it.
    fig, axes = plt.subplots(2, 2)
    ct.apply_theme(fig, axes.flat)
    for ax in axes.flat:
        assert ax.get_facecolor() == matplotlib.colors.to_rgba(ct.PANEL)


def test_apply_theme_numpy_ndarray_2d():
    fig, axes = plt.subplots(2, 2)
    ct.apply_theme(fig, axes)  # the raw 2D ndarray itself, not .flat
    for ax in np.asarray(axes).flat:
        assert ax.get_facecolor() == matplotlib.colors.to_rgba(ct.PANEL)


def test_apply_theme_defaults_to_all_figure_axes_when_none():
    fig, axes = plt.subplots(1, 2)
    ct.apply_theme(fig)
    for ax in axes:
        assert ax.get_facecolor() == matplotlib.colors.to_rgba(ct.PANEL)


def test_categorical_returns_fixed_order_and_cycles_when_exceeded():
    assert ct.categorical(3) == ct.CATEGORICAL[:3]
    assert len(ct.categorical(2 * len(ct.CATEGORICAL) + 1)) == 2 * len(ct.CATEGORICAL) + 1
