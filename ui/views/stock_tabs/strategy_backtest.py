"""Strategy Backtest tab — run trading strategies on a selected stock and view performance."""

import pandas as pd
import plotly.graph_objects as go
import quantstats as qs
import streamlit as st

from services.backtest_service import RISK_FREE, run_backtest
from strategies import STRATEGY_REGISTRY


def render(sdf: pd.DataFrame, symbol: str):
    if not STRATEGY_REGISTRY:
        st.warning("No strategies registered.")
        return

    strategy_name, params, init_cash, fees, use_sl, sl_pct, use_trail = _render_sidebar()

    price = sdf.set_index("Date")["Close"].dropna()
    if len(price) < 30:
        st.info("Need at least 30 trading days for backtesting.")
        return

    strategy_cls = STRATEGY_REGISTRY[strategy_name]
    result = run_backtest(price, strategy_cls, params, init_cash, fees, use_sl, sl_pct, use_trail)

    if result.returns.empty or len(result.returns) < 2:
        st.warning("Strategy produced no data in this period.")
        return

    _render_summary(result.portfolio, init_cash)
    st.divider()

    total_trades = result.portfolio.trades.count()
    if total_trades == 0:
        st.info(
            "No trades were executed with the current parameters. Try adjusting the strategy settings or date range."
        )
        return

    _render_metrics(result.metrics)
    st.divider()
    _render_charts(result.portfolio, price, result.returns, result.entries, result.exits, symbol)
    st.divider()
    _render_trade_log(result.portfolio)


def _render_sidebar():
    with st.sidebar:
        st.markdown("### Strategy")
        strategy_name = st.selectbox(
            "Select Strategy",
            options=list(STRATEGY_REGISTRY.keys()),
            key="_strategy_select",
        )
        strategy_cls = STRATEGY_REGISTRY[strategy_name]

        # Dynamic params from strategy definition
        params = {}
        if strategy_cls.params:
            for pname, pconfig in strategy_cls.params.items():
                default = pconfig["default"]
                if isinstance(default, float):
                    params[pname] = st.number_input(
                        pname.replace("_", " ").title(),
                        value=default,
                        min_value=float(pconfig.get("min", 0)),
                        max_value=float(pconfig.get("max", 1000)),
                        step=float(pconfig.get("step", 0.1)),
                        help=pconfig.get("help", ""),
                        key=f"_strat_param_{pname}",
                    )
                else:
                    params[pname] = st.number_input(
                        pname.replace("_", " ").title(),
                        value=int(default),
                        min_value=int(pconfig.get("min", 1)),
                        max_value=int(pconfig.get("max", 1000)),
                        step=int(pconfig.get("step", 1)),
                        help=pconfig.get("help", ""),
                        key=f"_strat_param_{pname}",
                    )

        st.markdown("### Capital & Fees")
        init_cash = st.number_input(
            "Starting Capital (INR)",
            value=100_000,
            min_value=1_000,
            max_value=100_000_000,
            step=10_000,
            key="_backtest_capital",
        )
        fees = st.number_input(
            "Fees per trade (%)",
            value=0.1,
            min_value=0.0,
            max_value=5.0,
            step=0.05,
            help="Brokerage fee applied on each entry/exit. 0.1% is typical for Indian brokers.",
            key="_backtest_fees",
        )

        st.markdown("### Risk Management")
        use_sl = st.checkbox(
            "Enable Stoploss",
            value=False,
            key="_backtest_use_sl",
            help=f"Default: {strategy_cls.stoploss * 100:.0f}% from strategy",
        )
        sl_pct = 0.0
        use_trail = False
        if use_sl:
            sl_pct = st.number_input(
                "Stoploss (%)",
                value=abs(strategy_cls.stoploss) * 100,
                min_value=1.0,
                max_value=50.0,
                step=1.0,
                help="Max loss % below entry price before forced exit.",
                key="_backtest_sl_pct",
            )
            use_trail = st.checkbox(
                "Trailing Stoploss",
                value=strategy_cls.trailing_stop,
                key="_backtest_trail",
                help="Stoploss trails upward as price rises, locking in gains.",
            )

    return strategy_name, params, init_cash, fees / 100, use_sl, sl_pct / 100, use_trail


def _render_summary(portfolio, init_cash):
    final_value = portfolio.value().iloc[-1]
    total_return = (final_value - init_cash) / init_cash * 100
    total_trades = portfolio.trades.count()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Starting Capital", f"INR {init_cash:,.0f}")
    c2.metric("Final Value", f"INR {final_value:,.0f}")
    c3.metric("Total Return", f"{total_return:+.2f}%")
    c4.metric("Total Trades", f"{total_trades}")


def _render_metrics(metrics: dict):
    # Row 1 — return & risk
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CAGR", f"{metrics['cagr']:.2f}%", help="Compound annual growth rate.")
    c2.metric(
        "Cumulative Return", f"{metrics['cumulative_return']:.2f}%", help="Total return over the backtest period."
    )
    c3.metric("Max Drawdown", f"{metrics['max_drawdown']:.2f}%", help="Largest peak-to-trough decline.")
    c4.metric(
        "Annual Volatility", f"{metrics['annual_volatility']:.2f}%", help="Annualized standard deviation of returns."
    )

    # Row 2 — risk-adjusted
    c5, c6, c7, c8 = st.columns(4)
    c5.metric(
        "Sharpe Ratio",
        f"{metrics['sharpe']:.2f}",
        help=f"Risk-adjusted return vs {RISK_FREE * 100:.1f}% risk-free rate.",
    )
    c6.metric("Sortino Ratio", f"{metrics['sortino']:.2f}", help="Like Sharpe but only penalizes downside volatility.")
    c7.metric("Calmar Ratio", f"{metrics['calmar']:.2f}", help="CAGR / Max Drawdown.")
    pf = metrics["profit_factor"]
    pf_display = f"{pf:.2f}" if pf != float("inf") else "INF"
    c8.metric("Profit Factor", pf_display, help="Gross wins / gross losses. >1 is profitable.")

    # Row 3 — trade stats
    c9, c10, c11, c12 = st.columns(4)
    c9.metric("Trade Win Rate", f"{metrics['win_rate']:.1f}%", help="Percentage of profitable trades.")
    c10.metric("Avg Winning Trade", f"{metrics['avg_win']:.2f}%", help="Average return on winning trades.")
    c11.metric("Avg Losing Trade", f"{metrics['avg_loss']:.2f}%", help="Average return on losing trades.")
    c12.metric(
        "Payoff Ratio",
        f"{metrics['payoff']:.2f}",
        help="Avg win / avg loss magnitude. >1 means wins larger than losses.",
    )

    # Row 4 — advanced
    c13, c14, c15, c16 = st.columns(4)
    c13.metric("Best Trade", f"{metrics['best_trade']:+.2f}%", help="Largest single trade return.")
    c14.metric("Worst Trade", f"{metrics['worst_trade']:+.2f}%", help="Worst single trade return.")
    c15.metric("Expectancy", f"INR {metrics['expectancy']:,.2f}", help="Average P&L per trade.")
    c16.metric("SQN", f"{metrics['sqn']:.2f}", help="System Quality Number. >2 good, >3 excellent, >5 superb.")

    # Row 5 — tail risk & position sizing
    c17, c18, c19, c20 = st.columns(4)
    c17.metric("VaR (95%)", f"{metrics['var_95']:.2f}%", help="95% confidence daily loss bound.")
    c18.metric("CVaR (95%)", f"{metrics['cvar_95']:.2f}%", help="Expected loss in the worst 5% of days.")
    c19.metric("Kelly Criterion", f"{metrics['kelly']:.1f}%", help="Optimal allocation %. Use half-Kelly for safety.")
    c20.metric("Avg Trade Duration", metrics["avg_duration"], help="Average holding period per trade.")


def _render_charts(portfolio, price, returns, entries, exits, symbol):
    # 1. Equity curve — strategy vs buy-and-hold
    equity = portfolio.value()
    bh = price / price.iloc[0] * equity.iloc[0]

    fig_eq = go.Figure()
    fig_eq.add_trace(
        go.Scatter(
            x=equity.index,
            y=equity,
            mode="lines",
            name="Strategy",
            line=dict(color="#6366f1", width=2),
        )
    )
    fig_eq.add_trace(
        go.Scatter(
            x=bh.index,
            y=bh,
            mode="lines",
            name="Buy & Hold",
            line=dict(color="#94a3b8", width=1, dash="dash"),
        )
    )
    fig_eq.update_layout(
        height=400,
        title=f"Equity Curve — {symbol}",
        yaxis_title="Portfolio Value (INR)",
        hovermode="x unified",
    )
    st.plotly_chart(fig_eq, use_container_width=True, key="bt-equity-curve")

    # 2. Two columns: Drawdown + Distribution
    col1, col2 = st.columns(2)

    with col1:
        drawdown = qs.stats.to_drawdown_series(returns) * 100
        fig_dd = go.Figure()
        fig_dd.add_trace(
            go.Scatter(
                x=drawdown.index,
                y=drawdown,
                mode="lines",
                name="Drawdown",
                line=dict(color="#ef4444", width=1.5),
                fill="tozeroy",
                fillcolor="rgba(239, 68, 68, 0.15)",
            )
        )
        fig_dd.update_layout(
            height=350,
            title="Drawdown (Underwater)",
            yaxis_title="Drawdown (%)",
            xaxis_title="Date",
        )
        st.plotly_chart(fig_dd, use_container_width=True, key="bt-drawdown")

    with col2:
        fig_hist = go.Figure()
        fig_hist.add_trace(
            go.Histogram(
                x=returns * 100,
                nbinsx=60,
                marker_color="#6366f1",
                opacity=0.7,
                name="Daily Returns",
            )
        )
        fig_hist.add_vline(x=0, line_color="#94a3b8", line_dash="dash")
        fig_hist.add_vline(
            x=returns.mean() * 100,
            line_color="#f59e0b",
            annotation_text=f"Mean: {returns.mean() * 100:.3f}%",
        )
        fig_hist.update_layout(
            height=350,
            title="Daily Returns Distribution",
            xaxis_title="Return (%)",
            yaxis_title="Count",
        )
        st.plotly_chart(fig_hist, use_container_width=True, key="bt-returns-hist")

    # 3. Trade signals on price
    fig_trades = go.Figure()
    fig_trades.add_trace(
        go.Scatter(
            x=price.index,
            y=price,
            mode="lines",
            name="Price",
            line=dict(color="#94a3b8", width=1),
        )
    )

    entry_mask = entries.fillna(False).astype(bool)
    exit_mask = exits.fillna(False).astype(bool)

    if entry_mask.any():
        fig_trades.add_trace(
            go.Scatter(
                x=price.index[entry_mask],
                y=price[entry_mask],
                mode="markers",
                name="Buy",
                marker=dict(symbol="triangle-up", size=10, color="#10b981"),
            )
        )
    if exit_mask.any():
        fig_trades.add_trace(
            go.Scatter(
                x=price.index[exit_mask],
                y=price[exit_mask],
                mode="markers",
                name="Sell",
                marker=dict(symbol="triangle-down", size=10, color="#ef4444"),
            )
        )

    fig_trades.update_layout(
        height=400,
        title="Trade Signals",
        yaxis_title="Price",
        hovermode="x unified",
    )
    st.plotly_chart(fig_trades, use_container_width=True, key="bt-trade-signals")

    # 4. Monthly returns heatmap
    st.subheader("Monthly Returns (%)")
    monthly = qs.stats.monthly_returns(returns)
    if monthly is not None and not monthly.empty:
        monthly_pct = monthly * 100
        st.dataframe(
            monthly_pct.style.format("{:.1f}").background_gradient(cmap="RdYlGn", axis=None),
            use_container_width=True,
        )


def _render_trade_log(portfolio):
    st.subheader("Trade Log")
    trades = portfolio.trades.records_readable
    if trades.empty:
        st.info("No trades were executed.")
        return

    display = trades[
        [
            "Entry Timestamp",
            "Exit Timestamp",
            "Size",
            "Avg Entry Price",
            "Avg Exit Price",
            "PnL",
            "Return",
            "Direction",
            "Status",
        ]
    ].copy()
    display["Return"] = display["Return"] * 100
    display.columns = [
        "Entry",
        "Exit",
        "Size",
        "Entry Price",
        "Exit Price",
        "P&L (INR)",
        "Return (%)",
        "Direction",
        "Status",
    ]
    st.dataframe(
        display.style.format(
            {
                "Entry Price": "{:.2f}",
                "Exit Price": "{:.2f}",
                "P&L (INR)": "{:,.2f}",
                "Return (%)": "{:+.2f}",
                "Size": "{:.2f}",
            }
        ).background_gradient(subset=["Return (%)"], cmap="RdYlGn"),
        use_container_width=True,
    )
