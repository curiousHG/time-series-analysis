import pytest

from strategies.parameters import BoolParameter, CategoricalParameter, DecimalParameter, IntParameter


class _Holder:
    window = IntParameter(14, 2, 50, step=2, help="RSI window")
    threshold = DecimalParameter(0.55, 0.5, 0.75, step=0.05)
    model = CategoricalParameter("ridge", choices=("ridge", "logistic"), optimize=False)
    trailing = BoolParameter(False)


def test_class_access_returns_the_parameter_and_instance_access_the_value():
    assert isinstance(_Holder.window, IntParameter)
    assert _Holder.window.name == "window"
    holder = _Holder()
    assert holder.window == 14
    holder.__dict__["window"] = 20
    assert holder.window == 20


def test_kinds_and_grids():
    assert _Holder.window.kind == "int"
    assert _Holder.window.grid() == list(range(2, 51, 2))
    assert _Holder.threshold.kind == "decimal"
    assert _Holder.threshold.grid() == pytest.approx([0.5, 0.55, 0.6, 0.65, 0.7, 0.75])
    assert _Holder.model.kind == "categorical"
    assert _Holder.model.grid() == ["ridge", "logistic"]
    assert _Holder.trailing.kind == "bool"
    assert _Holder.trailing.grid() == [False, True]


def test_defaults_must_lie_inside_the_declared_space():
    with pytest.raises(ValueError):
        IntParameter(100, 2, 50)
    with pytest.raises(ValueError):
        DecimalParameter(0.1, 0.5, 0.75)
    with pytest.raises(ValueError):
        CategoricalParameter("svm", choices=("ridge",))


def test_optimize_flag_and_help_are_kept():
    assert _Holder.model.optimize is False
    assert _Holder.window.optimize is True
    assert _Holder.window.help == "RSI window"
