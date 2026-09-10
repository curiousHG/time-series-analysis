# User strategies

Drop a `.py` file here and it shows up in the Backtest Lab strategy picker, the way freqtrade
discovers strategies in `user_data/strategies`. No registration decorator and no restart: the
directory is re-scanned whenever a file's timestamp changes.

A file may define any number of `BasketStrategy` subclasses. Each one needs a `name` (the label the
UI shows; the class name is used when it is missing) and a `mode` of either `signal` or `rank`.
A file that fails to import is listed as a load error on the Strategies view instead of breaking
the picker.

Start from the template: the Strategies view has a **New strategy** action, or run

```bash
uv run python -c "from strategies.resolver import new_strategy_file; print(new_strategy_file('MyStrategy'))"
```

Set `BT_STRATEGY_DIR` to read strategies from somewhere else.
