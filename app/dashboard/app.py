"""
Phase 8 — Market Intelligence Streamlit dashboard.

Single-page, top-to-bottom scroll. Consumes only app.dashboard.api_client
and app.dashboard.charts. Handles API-down / ticker-no-data / slow-API
degraded states without ever surfacing a Python traceback.

Phase 9 Step 5 addition:
    Sidebar "Ask" panel calling POST /ask, plus a main-area history block.
    Additive only — no Phase 8 code was restructured.

Run from repo root:
    streamlit run app/dashboard/app.py
"""

from __future__ import annotations

# --- sys.path bootstrap ----------------------------------------------------
# `streamlit run` executes this file as a script, so sys.path[0] becomes
# app/dashboard/, not the repo root. We add the repo root explicitly so
# `from app.dashboard...` imports resolve. Smallest fix; no packaging needed.
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# ---------------------------------------------------------------------------

import datetime as dt

import pandas as pd
import requests
import streamlit as st

from app.dashboard.api_client import APIError, get_health, get_metrics, get_ticker
from app.dashboard.charts import (
    COLORS,
    drawdown_chart,
    market_breadth_chart,
    price_chart,
    volatility_chart,
)

# ---- page config (must be first Streamlit call) ---------------------------

st.set_page_config(
    layout="wide",
    page_title="Market Intelligence",
    page_icon="📈",
)

API_DOCS_URL = "http://127.0.0.1:8000/docs"
API_BASE = "http://127.0.0.1:8000"
DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "JPM", "JNJ", "PG", "FB", "V"]


# ---- global CSS: hero band, pills, tabular numbers ------------------------

def _inject_css() -> None:
    st.markdown(
        f"""
        <style>
          /* Tabular numbers everywhere — columns of figures align like a terminal */
          html, body, [class*="css"] {{
            font-feature-settings: "tnum" 1, "cv11" 1;
          }}

          /* Hero band behind the header */
          .mi-hero {{
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 1.5rem 1.75rem;
            margin-bottom: 1rem;
          }}

          /* KPI card shell */
          .mi-kpi {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 1rem 1.1rem .9rem 1.1rem;
            height: 100%;
          }}
          .mi-kpi-label {{
            color: {COLORS['muted']};
            font-size: .75rem;
            letter-spacing: .06em;
            text-transform: uppercase;
            margin-bottom: .35rem;
          }}
          .mi-kpi-value {{
            color: {COLORS['text']};
            font-size: 1.65rem;
            font-weight: 600;
            line-height: 1.1;
            margin-bottom: .15rem;
          }}
          .mi-kpi-sub {{
            color: {COLORS['muted']};
            font-size: .75rem;
          }}
          .mi-kpi-delta-up   {{ color: {COLORS['up']};   font-weight: 600; }}
          .mi-kpi-delta-down {{ color: {COLORS['down']}; font-weight: 600; }}

          /* Movers HTML table (rendered via Styler.to_html) */
          .mi-movers-wrap table {{
            width: 100%;
            border-collapse: collapse;
            font-size: .92rem;
          }}
          .mi-movers-wrap th {{
            text-align: left;
            color: {COLORS['muted']};
            font-weight: 500;
            padding: .55rem .75rem;
            border-bottom: 1px solid #30363d;
            font-size: .8rem;
            text-transform: uppercase;
            letter-spacing: .05em;
          }}
          .mi-movers-wrap td {{
            padding: .55rem .75rem;
            border-bottom: 1px solid #21262d;
            color: {COLORS['text']};
          }}
          .mi-movers-wrap tr:last-child td {{ border-bottom: none; }}

          /* Horizontal pill radio (ticker selector) */
          div[role="radiogroup"] {{
            gap: .35rem;
            flex-wrap: wrap;
            display: flex;
          }}
          div[role="radiogroup"] > label {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 999px;
            padding: .3rem .85rem;
            cursor: pointer;
            transition: border-color .12s ease, background .12s ease;
          }}
          div[role="radiogroup"] > label:hover {{
            border-color: {COLORS['accent']};
          }}
          /* Hide the default radio circle; keep only the label text */
          div[role="radiogroup"] > label > div:first-child {{
            display: none;
          }}
          div[role="radiogroup"] > label p {{
            color: {COLORS['text']};
            font-size: .85rem;
            margin: 0;
          }}
          div[role="radiogroup"] > label[data-checked="true"] {{
            background: {COLORS['accent']}22;
            border-color: {COLORS['accent']};
          }}

          /* Tighten Streamlit's default block spacing */
          .block-container {{ padding-top: 1.5rem; padding-bottom: 2rem; }}

          /* Ask panel — sidebar card styling */
          .mi-ask-explanation {{
            background: #161b22;
            border: 1px solid #30363d;
            border-left: 3px solid {COLORS['accent']};
            border-radius: 8px;
            padding: .8rem .9rem;
            margin: .4rem 0 .6rem 0;
            color: {COLORS['text']};
            font-size: .88rem;
            line-height: 1.45;
          }}
          .mi-ask-q {{
            color: {COLORS['muted']};
            font-size: .75rem;
            text-transform: uppercase;
            letter-spacing: .05em;
            margin-bottom: .25rem;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---- cached API wrappers --------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def _cached_health() -> dict:
    return get_health()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_metrics(top_n: int = 5) -> dict:
    return get_metrics(top_n=top_n)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_ticker(symbol: str, limit: int = 500) -> dict:
    return get_ticker(symbol, limit=limit)


# ---- formatting helpers ---------------------------------------------------

def _fmt_pct(value: float | None) -> str:
    """0.3265625 -> '+32.7%' ; -0.0234 -> '−2.3%' (true minus, not hyphen)."""
    if value is None or pd.isna(value):
        return "—"
    sign = "+" if value >= 0 else "−"
    return f"{sign}{abs(value) * 100:.1f}%"


def _fmt_pct_arrow(value: float | None) -> str:
    """Same as _fmt_pct but with a ▲/▼ prefix for table cells."""
    if value is None or pd.isna(value):
        return "—"
    arrow = "▲" if value >= 0 else "▼"
    sign = "+" if value >= 0 else "−"
    return f"{arrow} {sign}{abs(value) * 100:.1f}%"


def _fmt_price(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"${value:,.2f}"


def _sparkline_svg(values: list[float], color: str, width: int = 120, height: int = 28) -> str:
    """Inline SVG polyline from a series of numbers. No external deps."""
    vals = [v for v in values if v is not None and not pd.isna(v)]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = width / (len(vals) - 1)
    pts = " ".join(
        f"{i * step:.2f},{(height - ((v - lo) / span) * height):.2f}"
        for i, v in enumerate(vals)
    )
    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        f"preserveAspectRatio='none' style='display:block; margin-top:.35rem;'>"
        f"<polyline points='{pts}' fill='none' stroke='{color}' "
        f"stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/>"
        f"</svg>"
    )


# ---- failure mode 1: API down ---------------------------------------------

def _render_api_unavailable(err: APIError) -> None:
    st.markdown(
        f"""
        <div style="
            max-width:640px; margin:12vh auto; padding:2.5rem;
            background:{COLORS['grid']};
            border:1px solid #30363d; border-radius:12px;
            text-align:center; font-family:sans-serif;">
          <div style="font-size:2.5rem; margin-bottom:.5rem;">🔌</div>
          <h2 style="color:{COLORS['text']}; margin:0 0 .5rem 0;">API unavailable</h2>
          <p style="color:{COLORS['muted']}; margin:0 0 1.25rem 0;">{err.message}</p>
          <code style="
              display:block; background:#0d1117; color:{COLORS['up']};
              padding:.75rem; border-radius:6px; font-size:.85rem;
              text-align:left; overflow-x:auto;">
            uvicorn app.api.main:app --host 127.0.0.1 --port 8000
          </code>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Retry", type="primary"):
        st.cache_data.clear()
        st.rerun()


# ---- row 1: header (hero band + breadth bar + status pill) ----------------

def _render_header(health: dict, as_of_date: str, metrics: dict) -> None:
    gainers = metrics.get("top_gainers") or []
    losers = metrics.get("top_losers") or []
    ok = health.get("status") == "ok" and health.get("db") == "ok"
    dot = COLORS["up"] if ok else COLORS["down"]
    label = "API healthy" if ok else "API offline"

    st.markdown(
        f"""
        <div class="mi-hero">
          <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:1rem;">
            <div>
              <h1 style="margin:0; color:{COLORS['text']}; font-size:2rem; line-height:1.1;">
                Market Intelligence
              </h1>
              <p style="margin:.35rem 0 0 0; color:{COLORS['muted']}; font-size:.95rem;">
                S&amp;P 500 analytics · 2013–2015 · 10 tickers · 947 daily observations
              </p>
              <p style="margin:.5rem 0 0 0; color:{COLORS['muted']}; font-size:.8rem;">
                Data as of {as_of_date}
              </p>
            </div>
            <div style="text-align:right;">
              <span style="display:inline-block; padding:.35rem .85rem;
                background:#0d1117; border:1px solid #30363d; border-radius:999px;
                color:{COLORS['text']}; font-size:.85rem;">
                <span style="display:inline-block; width:8px; height:8px;
                  border-radius:50%; background:{dot};
                  margin-right:.5rem; vertical-align:middle;"></span>{label}
              </span>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Breadth bar sits flush under the hero band, right-aligned caption.
    bcol1, bcol2 = st.columns([3, 1])
    with bcol1:
        st.altair_chart(
            market_breadth_chart(len(gainers), len(losers)),
            use_container_width=True,
        )
    with bcol2:
        st.markdown(
            f"<div style='text-align:right; color:{COLORS['muted']}; font-size:.8rem;'>"
            f"<span style='color:{COLORS['up']};'>▲ {len(gainers)} gainers</span> · "
            f"<span style='color:{COLORS['down']};'>▼ {len(losers)} losers</span>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ---- row 2: KPI tiles with sparklines -------------------------------------

def _kpi_card(label: str, value: str, sub: str, spark_svg: str = "") -> str:
    return (
        f"<div class='mi-kpi'>"
        f"<div class='mi-kpi-label'>{label}</div>"
        f"<div class='mi-kpi-value'>{value}</div>"
        f"<div class='mi-kpi-sub'>{sub}</div>"
        f"{spark_svg}"
        f"</div>"
    )


def _render_kpis(metrics: dict) -> None:
    gainers = metrics.get("top_gainers") or []
    losers = metrics.get("top_losers") or []
    best = gainers[0] if gainers else None
    worst = losers[0] if losers else None

    # Sparkline data: last 30 closes per relevant ticker. Wrapped in try/except
    # because a single missing ticker shouldn't blank the whole tile row.
    def _spark(symbol: str, color: str) -> str:
        try:
            payload = _cached_ticker(symbol, limit=30)
            closes = [p["close_price"] for p in (payload.get("points") or [])]
            return _sparkline_svg(closes, color)
        except APIError:
            return ""

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(
            _kpi_card(
                "Tickers tracked",
                f"{metrics.get('tickers_count', '—')}",
                "across the S&P 500 sample",
            ),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            _kpi_card(
                "Daily observations",
                f"{metrics.get('rows_count', 0):,}",
                "2013-01-02 → 2015-09-27",
            ),
            unsafe_allow_html=True,
        )
    with c3:
        if best:
            st.markdown(
                _kpi_card(
                    "Best 1-day move",
                    f"<span class='mi-kpi-delta-up'>{_fmt_pct(best['return_1d'])}</span>",
                    f"{best['ticker']} @ {_fmt_price(best['close_price'])}",
                    _spark(best["ticker"], COLORS["up"]),
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_kpi_card("Best 1-day move", "—", ""), unsafe_allow_html=True)
    with c4:
        if worst:
            st.markdown(
                _kpi_card(
                    "Worst 1-day move",
                    f"<span class='mi-kpi-delta-down'>{_fmt_pct(worst['return_1d'])}</span>",
                    f"{worst['ticker']} @ {_fmt_price(worst['close_price'])}",
                    _spark(worst["ticker"], COLORS["down"]),
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_kpi_card("Worst 1-day move", "—", ""), unsafe_allow_html=True)


# ---- row 3: movers, rendered as styled HTML -------------------------------

def _movers_df(items: list[dict]) -> pd.DataFrame:
    """Build a movers DataFrame. Index is the default RangeIndex — the
    Styler requires unique labels, and we hide the index at render time
    via Styler.hide(), so no duplicate-label KeyError."""
    if not items:
        return pd.DataFrame(columns=["Ticker", "Close", "1-Day Return"])
    return pd.DataFrame(
        [
            {
                "Ticker": it["ticker"],
                "Close": it["close_price"],
                "1-Day Return": it["return_1d"],
            }
            for it in items
        ]
    )


def _render_movers(metrics: dict) -> None:
    left, right = st.columns(2)

    def _table(items: list[dict]) -> None:
        df = _movers_df(items)

        if df.empty:
            st.info("No rows on the as-of date.")
            return

        styled = (
            df.style
            .format({
                "Close": "${:,.2f}",
                "1-Day Return": _fmt_pct_arrow
            })
            .apply(
                lambda s: [
                    f"color: {'#00d4aa' if v >= 0 else '#ff4d4f'};"
                    f"font-weight: 600;"
                    for v in s.values
                ],
                axis=0,
                subset=["1-Day Return"],
            )
            .hide(axis="index")
            .set_table_styles(
                [
                    {
                        "selector": "table",
                        "props": [
                            ("width", "100%"),
                        ],
                    },
                    {
                        "selector": "th",
                        "props": [
                            ("color", "#8b949e"),
                            ("font-weight", "500"),
                        ],
                    },
                    {
                        "selector": "td",
                        "props": [
                            ("padding", ".55rem .75rem"),
                        ],
                    },
                ]
            )
        )

        st.markdown(
            f"<div class='mi-movers-wrap'>{styled.to_html()}</div>",
            unsafe_allow_html=True,
        )

    with left:
        st.subheader("Top gainers")
        _table(metrics.get("top_gainers") or [])

    with right:
        st.subheader("Top losers")
        _table(metrics.get("top_losers") or [])

# ---- row 4: ticker deep-dive ---------------------------------------------

def _render_ticker_panel(symbol: str) -> None:
    st.subheader("Ticker deep-dive")

    try:
        payload = _cached_ticker(symbol, limit=500)
    except APIError as e:
        st.warning(f"Could not load {symbol}: {e.message}")
        return

    points = payload.get("points") or []
    if not points:
        st.info(f"No data for {symbol} in the selected range.")
        return

    df = pd.DataFrame(points)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    wide, narrow = st.columns([3, 1])
    with wide:
        st.altair_chart(
            price_chart(df, title=f"{symbol} — close price with MA7 / MA30", height=320),
            use_container_width=True,
        )
    with narrow:
        latest = df.iloc[-1]
        st.markdown(
            f"<div style='padding:.5rem 0 .25rem 0; color:{COLORS['muted']}; "
            f"font-size:.85rem; text-transform:uppercase; letter-spacing:.05em;'>Latest</div>"
            f"<div style='font-size:1.6rem; font-weight:600; color:{COLORS['text']};'>"
            f"{_fmt_price(latest['close_price'])}</div>"
            f"<div style='color:{COLORS['muted']}; font-size:.8rem; margin-bottom:1rem;'>"
            f"{latest['trade_date'].date().isoformat()}</div>",
            unsafe_allow_html=True,
        )
        st.metric(
            "30-day volatility",
            _fmt_pct(latest["volatility_30d"]) if pd.notna(latest["volatility_30d"]) else "—",
        )
        st.metric(
            "Current drawdown",
            _fmt_pct(latest["drawdown"]) if pd.notna(latest["drawdown"]) else "—",
        )
        st.metric("Rows in view", f"{len(df):,}")


# ---- row 5: risk panel ----------------------------------------------------

def _render_risk_panel(symbol: str) -> None:
    try:
        payload = _cached_ticker(symbol, limit=500)
    except APIError:
        return

    points = payload.get("points") or []
    if not points:
        return

    df = pd.DataFrame(points)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    left, right = st.columns(2)
    with left:
        st.altair_chart(
            drawdown_chart(df, title=f"{symbol} — drawdown from peak", height=200),
            use_container_width=True,
        )
    with right:
        st.altair_chart(
            volatility_chart(df, title=f"{symbol} — 30-day rolling volatility", height=200),
            use_container_width=True,
        )


# ---- row 6: footer --------------------------------------------------------

def _render_footer() -> None:
    st.divider()
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.markdown(
        f"<div style='color:{COLORS['muted']}; font-size:.8rem; text-align:center;'>"
        f"Data pipeline: Bronze → Silver → dbt → Postgres → FastAPI → Streamlit"
        f"<br/>Last refreshed: {now} · "
        f"<a href='{API_DOCS_URL}' target='_blank' style='color:{COLORS['accent']};'>"
        f"OpenAPI docs</a>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---- Phase 9 Step 5: Ask panel (sidebar) ----------------------------------
# Additive only — the Phase 8 dashboard flow above is unchanged. This block
# renders in the sidebar so it never shifts or restructures the main layout.

def _render_ask_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Ask the data")
        st.caption(
            "Natural-language questions are translated to SQL, validated, and run "
            "against the two dbt marts. The model explains the result in plain English."
        )

        if "ask_history" not in st.session_state:
            st.session_state.ask_history = []

        with st.form("ask_form", clear_on_submit=False):
            q = st.text_input(
                "Your question",
                placeholder="Top 5 gainers on 2015-09-27?",
                key="ask_input",
            )
            submitted = st.form_submit_button("Ask")

        if submitted and q.strip():
            with st.spinner("Thinking..."):
                try:
                    r = requests.post(
                        f"{API_BASE}/ask",
                        json={"question": q.strip()},
                        timeout=180,
                    )
                except requests.RequestException as e:
                    st.error(f"Request failed: {e}")
                else:
                    if r.status_code == 200:
                        st.session_state.ask_history.insert(0, r.json())
                    else:
                        try:
                            detail = r.json().get("detail", r.text)
                        except Exception:
                            detail = r.text
                        st.error(f"HTTP {r.status_code}: {detail}")

        # Latest answer preview in the sidebar (compact).
        if st.session_state.ask_history:
            latest = st.session_state.ask_history[0]
            st.markdown(
                f"<div class='mi-ask-q'>Latest question</div>"
                f"<div style='color:{COLORS['text']}; font-size:.88rem; "
                f"margin-bottom:.4rem;'>{latest['question']}</div>"
                f"<div class='mi-ask-explanation'>{latest['explanation']}</div>",
                unsafe_allow_html=True,
            )


def _render_ask_history() -> None:
    """Full Ask history in the main area, below the dashboard. Renders
    nothing until at least one question has been asked."""
    history = st.session_state.get("ask_history") or []
    if not history:
        return

    st.divider()
    st.subheader("Ask history")

    for item in history:
        st.markdown(f"**Q:** {item['question']}")
        st.markdown(item["explanation"])

        c1, c2, c3 = st.columns(3)
        c1.metric("Rows", item["row_count"])
        c2.metric(
            "Total latency",
            f"{item['latency_ms']['total_ms']:.0f} ms",
        )
        c3.metric(
            "SQL gen",
            f"{item['latency_ms']['sql_gen_ms']:.0f} ms",
        )

        if item["rows"]:
            st.dataframe(item["rows"], use_container_width=True)

        with st.expander("Generated SQL"):
            st.code(item["sql"], language="sql")

        st.divider()


# ---- main -----------------------------------------------------------------

def main() -> None:
    _inject_css()

    try:
        health = _cached_health()
    except APIError as e:
        _render_api_unavailable(e)
        st.stop()

    with st.spinner("Loading market data..."):
        try:
            metrics = _cached_metrics(top_n=5)
        except APIError as e:
            _render_api_unavailable(e)
            st.stop()

    as_of_date = metrics.get("as_of_date", "—")

    _render_header(health, as_of_date, metrics)
    st.divider()

    _render_kpis(metrics)
    st.divider()

    _render_movers(metrics)
    st.divider()

    symbol = st.radio(
        "Ticker",
        options=DEFAULT_TICKERS,
        index=0,
        horizontal=True,
        label_visibility="collapsed",
    )

    _render_ticker_panel(symbol)
    _render_risk_panel(symbol)

    # Phase 9 Step 5: Ask panel. Sidebar renders immediately; the main-area
    # history block renders nothing until the first question is asked.
    _render_ask_sidebar()
    _render_ask_history()

    _render_footer()


if __name__ == "__main__":
    main()