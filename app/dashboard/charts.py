"""
Phase 8 — Altair chart builders for the Market Intelligence dashboard.

Pure functions: DataFrame in, alt.Chart out. No Streamlit, no HTTP, no API
shape knowledge. This is the layer Step 5 unit-tests in isolation.

Written against Altair 6.x. Altair 6 validates schema at construction time,
so TitleParams must be given `text=`. We avoid TitleParams entirely and
apply title styling via .configure_title() at the chart level instead.
"""

from __future__ import annotations

import altair as alt
import pandas as pd


# ---- design tokens --------------------------------------------------------

COLORS = {
    "up":      "#00d4aa",   # teal  — positive returns
    "down":    "#ff4d6d",   # red   — negative returns
    "accent":  "#58a6ff",   # blue  — ma7 line, focus color
    "neutral": "#8b949e",   # grey  — ma30 line, secondary series
    "grid":    "#21262d",   # gridlines
    "text":    "#e6edf3",   # axis labels, titles
    "muted":   "#7d8590",   # subtitles, annotations
}

_FONT = "sans-serif"


def _axis(**overrides) -> alt.Axis:
    """Fresh Axis per call — Axis objects are mutable in Altair, sharing
    one instance across encodings is a known footgun."""
    base = dict(
        labelColor=COLORS["text"],
        titleColor=COLORS["text"],
        gridColor=COLORS["grid"],
        domainColor=COLORS["grid"],
    )
    base.update(overrides)
    return alt.Axis(**base)


def _finish(chart: alt.Chart, height: int, title: str) -> alt.Chart:
    """Apply shared height, title, and global config to a finished chart."""
    return (
        chart.properties(height=height, title=title)
        .configure_title(
            fontSize=14,
            fontWeight="bold",
            color=COLORS["text"],
            anchor="start",
            font=_FONT,
        )
        .configure_view(strokeOpacity=0)
        .configure_axis(grid=True, gridColor=COLORS["grid"], domainColor=COLORS["grid"])
    )


def _crosshair(chart: alt.Chart, x_field: str = "trade_date:T") -> alt.Chart:
    """Overlay a vertical hairline that follows the mouse, plus a point marker.

    `chart` MUST be a plain alt.Chart (not a LayerChart) — we read
    chart.data and chart.encoding.y.shorthand, neither of which exists on a
    LayerChart. The caller is responsible for passing an un-layered chart.

    Returns a layered chart: original marks + hairline rule + invisible
    hover points that drive the selection.
    """
    hover = alt.selection_point(
        fields=[x_field.split(":")[0]],
        nearest=True,
        on="mouseover",
        empty=False,
    )
    # Invisible rule marks that become visible on hover — the "hairline".
    rules = (
        alt.Chart(chart.data)
        .mark_rule(color=COLORS["muted"], strokeWidth=1, strokeDash=[3, 3])
        .encode(x=x_field)
        .transform_filter(hover)
    )
    # Invisible points that drive the selection (Vega-Lite pattern).
    selectors = (
        alt.Chart(chart.data)
        .mark_point(opacity=0, size=120)
        .encode(x=x_field, y=alt.Y(chart.encoding.y.shorthand))
        .add_params(hover)
    )
    return chart + rules + selectors


# ---- market breadth (header mini-chart) -----------------------------------

def market_breadth_chart(gainers_count: int, losers_count: int, height: int = 14) -> alt.Chart:
    """Single horizontal stacked bar: green segment for gainers, red for losers.

    Designed to sit inline with the header text. Tiny, no axes, no legend.
    """
    df = pd.DataFrame(
        {
            "side": ["Gainers", "Losers"],
            "count": [gainers_count, losers_count],
        }
    )
    return (
        alt.Chart(df)
        .mark_bar(height=height, cornerRadius=3)
        .encode(
            x=alt.X("count:Q", stack="normalize", axis=None, title=None),
            color=alt.Color(
                "side:N",
                scale=alt.Scale(
                    domain=["Gainers", "Losers"],
                    range=[COLORS["up"], COLORS["down"]],
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("side:N", title="Side"),
                alt.Tooltip("count:Q", title="Count"),
                alt.Tooltip("count:Q", title="Share", format=".0%"),
            ],
        )
        .properties(height=height, width="container")
        .configure_view(strokeOpacity=0)
    )


# ---- price chart ----------------------------------------------------------

def price_chart(df: pd.DataFrame, title: str = "Price", height: int = 320) -> alt.Chart:
    """Close price with ma7 and ma30 overlaid, plus a hover crosshair.

    df columns required: trade_date (date), close_price, ma7, ma30.
    ma7/ma30 may be null for the first few rows — Altair skips nulls.
    """
    if df.empty:
        return _finish(
            alt.Chart(pd.DataFrame({"trade_date": [], "close_price": []}))
            .mark_line()
            .encode(x="trade_date:T", y="close_price:Q"),
            height, title,
        )

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # Long-form for the two MA lines so we get one legend, not two.
    ma_long = df.melt(
        id_vars=["trade_date"],
        value_vars=["ma7", "ma30"],
        var_name="ma",
        value_name="ma_value",
    ).dropna(subset=["ma_value"])

    close_line = (
        alt.Chart(df)
        .mark_line(strokeWidth=2, color=COLORS["up"])
        .encode(
            x=alt.X(
                "trade_date:T",
                title="Date",
                axis=_axis(format="%Y-%m"),
            ),
            y=alt.Y("close_price:Q", title="Close price ($)", axis=_axis()),
            tooltip=[
                alt.Tooltip("trade_date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("close_price:Q", title="Close", format="$.2f"),
                alt.Tooltip("ma7:Q", title="MA7", format="$.2f"),
                alt.Tooltip("ma30:Q", title="MA30", format="$.2f"),
            ],
        )
    )

    ma_lines = (
        alt.Chart(ma_long)
        .mark_line(strokeWidth=1.2, opacity=0.85)
        .encode(
            x="trade_date:T",
            y="ma_value:Q",
            color=alt.Color(
                "ma:N",
                scale=alt.Scale(
                    domain=["ma7", "ma30"],
                    range=[COLORS["accent"], COLORS["neutral"]],
                ),
                legend=alt.Legend(
                    title=None,
                    orient="top-right",
                    labelColor=COLORS["text"],
                ),
            ),
        )
    )

    # Crosshair goes on close_line (a plain Chart) BEFORE layering, because
    # _crosshair reads .data and .encoding.y.shorthand — neither exists on
    # a LayerChart. Then we layer MA lines on top of the crosshaired chart.
    close_with_hairline = _crosshair(close_line)
    return _finish(close_with_hairline + ma_lines, height, title)


# ---- risk charts ----------------------------------------------------------

def drawdown_chart(df: pd.DataFrame, title: str = "Drawdown", height: int = 200) -> alt.Chart:
    """Filled area of drawdown over time. Values are <= 0 by construction.

    df columns required: trade_date, drawdown.
    """
    if df.empty:
        return _finish(
            alt.Chart(pd.DataFrame({"trade_date": [], "drawdown": []}))
            .mark_area()
            .encode(x="trade_date:T", y="drawdown:Q"),
            height, title,
        )

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.dropna(subset=["drawdown"])

    chart = (
        alt.Chart(df)
        .mark_area(
            line={"color": COLORS["down"], "strokeWidth": 1.5},
            color=alt.Gradient(
                gradient="linear",
                stops=[
                    alt.GradientStop(color="#ff4d6d00", offset=0),
                    alt.GradientStop(color="#ff4d6d55", offset=1),
                ],
                x1=1, x2=1, y1=1, y2=0,
            ),
            interpolate="monotone",
        )
        .encode(
            x=alt.X("trade_date:T", title="Date", axis=_axis(format="%Y-%m")),
            y=alt.Y("drawdown:Q", title="Drawdown", axis=_axis(format=".0%")),
            tooltip=[
                alt.Tooltip("trade_date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("drawdown:Q", title="Drawdown", format=".2%"),
            ],
        )
    )
    return _finish(_crosshair(chart), height, title)


def volatility_chart(df: pd.DataFrame, title: str = "30-day volatility", height: int = 200) -> alt.Chart:
    """Line of volatility_30d. First two points are null per Phase 5's ratchet;
    Altair skips nulls, so the line naturally starts at the first valid value.
    """
    if df.empty:
        return _finish(
            alt.Chart(pd.DataFrame({"trade_date": [], "volatility_30d": []}))
            .mark_line()
            .encode(x="trade_date:T", y="volatility_30d:Q"),
            height, title,
        )

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.dropna(subset=["volatility_30d"])

    chart = (
        alt.Chart(df)
        .mark_line(strokeWidth=2, color=COLORS["accent"], interpolate="monotone")
        .encode(
            x=alt.X("trade_date:T", title="Date", axis=_axis(format="%Y-%m")),
            y=alt.Y(
                "volatility_30d:Q",
                title="Volatility (annualized)",
                axis=_axis(format=".0%"),
            ),
            tooltip=[
                alt.Tooltip("trade_date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("volatility_30d:Q", title="Volatility", format=".2%"),
            ],
        )
    )
    return _finish(_crosshair(chart), height, title)