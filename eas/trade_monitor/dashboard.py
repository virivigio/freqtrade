from html import escape
import sqlite3

from trade_monitor.core import (
    format_compact_time_from_epoch,
    format_price,
    format_timestamp_for_header,
    parse_timestamp,
    format_timestamp_for_table,
    render_hero_info,
    rounded_ten_bounds,
)
from trade_monitor.store import TradeStore


def marker_style(event_type: str, side: str) -> tuple[str, str]:
    color = "#111111"
    shape = "triangle-up" if event_type == "OPEN" and side == "BUY" else "triangle-down" if event_type == "OPEN" else "circle"
    return color, shape


def render_marker_svg(x: float, y: float, color: str, shape: str) -> str:
    if shape == "triangle-up":
        points = f"{x:.2f},{y - 8:.2f} {x - 7:.2f},{y + 6:.2f} {x + 7:.2f},{y + 6:.2f}"
        return f"<polygon points='{points}' fill='{color}' stroke='#fffaf1' stroke-width='1.5' />"
    if shape == "triangle-down":
        points = f"{x:.2f},{y + 8:.2f} {x - 7:.2f},{y - 6:.2f} {x + 7:.2f},{y - 6:.2f}"
        return f"<polygon points='{points}' fill='{color}' stroke='#fffaf1' stroke-width='1.5' />"
    return f"<circle cx='{x:.2f}' cy='{y:.2f}' r='5.5' fill='{color}' stroke='#fffaf1' stroke-width='1.5' />"


def trade_markers(events: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return [row for row in events if row["event_type"] in ("OPEN", "CLOSE")]


def build_trade_segments(events: list[sqlite3.Row]) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
    opens = {}
    segments = []
    for event in reversed(events):
        ticket = event["ticket"]
        if event["event_type"] == "OPEN":
            opens[ticket] = event
        elif event["event_type"] == "CLOSE" and ticket in opens:
            segments.append((opens[ticket], event))
    return segments


def rounded_five_bounds(values: list[float]) -> tuple[float, float]:
    minimum = min(values)
    maximum = max(values)
    lower = int(minimum // 5.0) * 5.0
    upper = int(maximum // 5.0) * 5.0
    if upper < maximum:
        upper += 5.0
    if lower == upper:
        upper += 5.0
    return lower, upper


def polyline_price_chart(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "<p class='meta'>Nessuno stato intra-minuto disponibile.</p>"

    width = 1080
    height = 280
    padding_left = 54
    padding_right = 18
    padding_top = 18
    padding_bottom = 34
    values = [float(row["close"]) for row in rows]
    min_value, max_value = rounded_five_bounds(values)
    usable_width = width - padding_left - padding_right
    usable_height = height - padding_top - padding_bottom
    step_x = usable_width / max(len(rows) - 1, 1)

    def x_pos(index: int) -> float:
        return padding_left + step_x * index

    def y_pos(value: float) -> float:
        return padding_top + (max_value - value) / (max_value - min_value) * usable_height

    points = " ".join(f"{x_pos(i):.2f},{y_pos(value):.2f}" for i, value in enumerate(values))
    last_price = values[-1]
    last_y = y_pos(last_price)
    label_indexes = sorted({0, len(rows) // 2, len(rows) - 1})
    x_labels = "".join(
        f"<text x='{x_pos(i):.2f}' y='{height - 10}' text-anchor='middle'>{escape(format_timestamp_for_table(rows[i]['captured_at']).split(' ')[1])}</text>"
        for i in label_indexes
    )
    y_labels = []
    tick_count = int((max_value - min_value) / 5.0) + 1
    for tick in range(tick_count):
        value = max_value - tick * 5.0
        ratio = (max_value - value) / (max_value - min_value)
        y = padding_top + usable_height * ratio
        y_labels.append(
            f"<text x='8' y='{y + 4:.2f}'>{format_price(value)}</text>"
            f"<line x1='{padding_left}' y1='{y:.2f}' x2='{width - padding_right}' y2='{y:.2f}' class='chart-grid' />"
        )

    return (
        f"<div class='chart-meta'>Ultimi punti: <strong>{len(rows)}</strong> | Ultimo prezzo: <strong>{format_price(last_price)}</strong></div>"
        f"<svg viewBox='0 0 {width} {height}' class='chart-svg' role='img' aria-label='Grafico ultimo prezzo intra-minuto'>"
        f"<line x1='{padding_left}' y1='{last_y:.2f}' x2='{width - padding_right}' y2='{last_y:.2f}' class='chart-last-line' />"
        f"{''.join(y_labels)}"
        f"<polyline points='{points}' fill='none' stroke='#9c6b1a' stroke-width='3' stroke-linecap='round' stroke-linejoin='round' />"
        f"<circle cx='{x_pos(len(rows) - 1):.2f}' cy='{last_y:.2f}' r='4.5' fill='#9c6b1a' />"
        f"{x_labels}"
        "</svg>"
    )


def polyline_price_chart_with_markers(rows: list[sqlite3.Row], events: list[sqlite3.Row]) -> str:
    if not rows:
        return "<p class='meta'>Nessuno stato intra-minuto disponibile.</p>"

    width = 1080
    height = 280
    padding_left = 54
    padding_right = 18
    padding_top = 18
    padding_bottom = 34
    values = [float(row["close"]) for row in rows]
    min_value, max_value = rounded_ten_bounds(values)
    usable_width = width - padding_left - padding_right
    usable_height = height - padding_top - padding_bottom
    step_x = usable_width / max(len(rows) - 1, 1)

    def x_pos(index: int) -> float:
        return padding_left + step_x * index

    def y_pos(value: float) -> float:
        return padding_top + (max_value - value) / (max_value - min_value) * usable_height

    points = " ".join(f"{x_pos(i):.2f},{y_pos(value):.2f}" for i, value in enumerate(values))
    last_price = values[-1]
    last_y = y_pos(last_price)
    label_indexes = sorted({0, len(rows) // 2, len(rows) - 1})
    x_labels = "".join(
        f"<text x='{x_pos(i):.2f}' y='{height - 10}' text-anchor='middle'>{escape(format_timestamp_for_table(rows[i]['captured_at']).split(' ')[1])}</text>"
        for i in label_indexes
    )
    y_labels = []
    tick_count = int((max_value - min_value) / 10.0) + 1
    for tick in range(tick_count):
        value = max_value - tick * 10.0
        ratio = (max_value - value) / (max_value - min_value)
        y = padding_top + usable_height * ratio
        y_labels.append(
            f"<text x='8' y='{y + 4:.2f}'>{format_price(value)}</text>"
            f"<line x1='{padding_left}' y1='{y:.2f}' x2='{width - padding_right}' y2='{y:.2f}' class='chart-grid' />"
        )

    marker_items = []
    marker_positions = {}
    state_times = [parse_timestamp(row["captured_at"]) for row in rows]
    for event in trade_markers(events):
        event_time = parse_timestamp(event["event_time"])
        if event_time < state_times[0] or event_time > state_times[-1]:
            continue
        nearest_index = min(range(len(state_times)), key=lambda idx: abs((state_times[idx] - event_time).total_seconds()))
        color, shape = marker_style(event["event_type"], event["side"])
        marker_x = x_pos(nearest_index)
        marker_y = y_pos(float(rows[nearest_index]["close"]))
        marker_positions[(event["ticket"], event["event_type"])] = (marker_x, marker_y)
        marker_items.append(
            "<g>"
            f"<title>{escape(event['event_type'])} {escape(event['side'])} {escape(format_timestamp_for_header(event['event_time']))}</title>"
            f"{render_marker_svg(marker_x, marker_y, color, shape)}"
            "</g>"
        )
    segment_items = []
    for open_event, close_event in build_trade_segments(events):
        open_pos = marker_positions.get((open_event["ticket"], "OPEN"))
        close_pos = marker_positions.get((close_event["ticket"], "CLOSE"))
        if open_pos is None or close_pos is None:
            continue
        segment_items.append(
            f"<line x1='{open_pos[0]:.2f}' y1='{open_pos[1]:.2f}' x2='{close_pos[0]:.2f}' y2='{close_pos[1]:.2f}' stroke='#111111' stroke-width='1.2' />"
        )

    return (
        f"<div class='chart-meta'>Ultimi punti: <strong>{len(rows)}</strong> | Ultimo prezzo: <strong>{format_price(last_price)}</strong></div>"
        f"<svg viewBox='0 0 {width} {height}' class='chart-svg' role='img' aria-label='Grafico ultimo prezzo intra-minuto'>"
        f"<line x1='{padding_left}' y1='{last_y:.2f}' x2='{width - padding_right}' y2='{last_y:.2f}' class='chart-last-line' />"
        f"{''.join(y_labels)}"
        f"<polyline points='{points}' fill='none' stroke='#9c6b1a' stroke-width='3' stroke-linecap='round' stroke-linejoin='round' />"
        f"<circle cx='{x_pos(len(rows) - 1):.2f}' cy='{last_y:.2f}' r='4.5' fill='#9c6b1a' />"
        f"{''.join(segment_items)}"
        f"{''.join(marker_items)}"
        f"{x_labels}"
        "</svg>"
    )


def candlestick_chart(rows: list[sqlite3.Row], events: list[sqlite3.Row]) -> str:
    if not rows:
        return "<p class='meta'>Nessuna candela chiusa disponibile.</p>"

    latest_open_time = max(int(row["open_time"]) for row in rows)
    slot_times = [latest_open_time - 60 * offset for offset in range(59, -1, -1)]
    rows_by_open_time = {int(row["open_time"]): row for row in rows}
    slotted_rows = [rows_by_open_time.get(open_time) for open_time in slot_times]

    width = 1080
    height = 320
    padding_left = 54
    padding_right = 18
    padding_top = 18
    padding_bottom = 34
    values = []
    for row in slotted_rows:
        if row is not None:
            values.extend([float(row["high"]), float(row["low"])])
    min_value, max_value = rounded_ten_bounds(values)
    usable_width = width - padding_left - padding_right
    usable_height = height - padding_top - padding_bottom
    candle_slot = usable_width / 60
    candle_width = max(min(candle_slot * 0.58, 10), 3)

    def x_center(index: int) -> float:
        return padding_left + candle_slot * index + candle_slot / 2

    def y_pos(value: float) -> float:
        return padding_top + (max_value - value) / (max_value - min_value) * usable_height

    candle_shapes = []
    candle_centers = {}
    candle_rows = {}
    for index, row in enumerate(slotted_rows):
        if row is None:
            continue
        center_x = x_center(index)
        candle_centers[int(row["open_time"])] = center_x
        candle_rows[int(row["open_time"])] = row
        open_price = float(row["open"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        close_price = float(row["close"])
        top = y_pos(max(open_price, close_price))
        bottom = y_pos(min(open_price, close_price))
        wick_top = y_pos(high_price)
        wick_bottom = y_pos(low_price)
        color = "#176b3a" if close_price >= open_price else "#9e2f21"
        body_height = max(bottom - top, 1.5)
        candle_shapes.append(
            f"<line x1='{center_x:.2f}' y1='{wick_top:.2f}' x2='{center_x:.2f}' y2='{wick_bottom:.2f}' stroke='{color}' stroke-width='1.5' />"
            f"<rect x='{center_x - candle_width / 2:.2f}' y='{top:.2f}' width='{candle_width:.2f}' height='{body_height:.2f}' fill='{color}' rx='1.5' />"
        )

    label_indexes = list(range(0, 60, 10))
    x_labels = "".join(
        f"<text x='{x_center(i):.2f}' y='{height - 10}' text-anchor='middle'>{escape(format_compact_time_from_epoch(slot_times[i])[:5])}</text>"
        for i in label_indexes
    )
    x_grids = "".join(
        f"<line x1='{x_center(i):.2f}' y1='{padding_top}' x2='{x_center(i):.2f}' y2='{height - padding_bottom}' class='chart-grid chart-grid-vertical' />"
        for i in label_indexes
    )
    y_labels = []
    tick_count = int((max_value - min_value) / 10.0) + 1
    for tick in range(tick_count):
        value = max_value - tick * 10.0
        ratio = (max_value - value) / (max_value - min_value)
        y = padding_top + usable_height * ratio
        y_labels.append(
            f"<text x='8' y='{y + 4:.2f}'>{format_price(value)}</text>"
            f"<line x1='{padding_left}' y1='{y:.2f}' x2='{width - padding_right}' y2='{y:.2f}' class='chart-grid' />"
        )

    latest = rows_by_open_time[latest_open_time]
    shown_count = sum(1 for row in slotted_rows if row is not None)
    marker_items = []
    marker_positions = {}
    available_open_times = sorted(candle_centers.keys())
    for event in trade_markers(events):
        if not available_open_times:
            continue
        event_dt = parse_timestamp(event["event_time"])
        bucket_open_time = int(event_dt.timestamp() // 60) * 60
        nearest_open_time = min(available_open_times, key=lambda open_time: abs(open_time - bucket_open_time))
        center_x = candle_centers[nearest_open_time]
        candle_row = candle_rows[nearest_open_time]
        marker_price = min(max_value, float(candle_row["high"]) + 1.0)
        y = y_pos(marker_price)
        color, shape = marker_style(event["event_type"], event["side"])
        marker_positions[(event["ticket"], event["event_type"])] = (center_x, y)
        marker_items.append(
            "<g>"
            f"<title>{escape(event['event_type'])} {escape(event['side'])} {escape(format_timestamp_for_header(event['event_time']))}</title>"
            f"{render_marker_svg(center_x, y, color, shape)}"
            "</g>"
        )
    segment_items = []
    for open_event, close_event in build_trade_segments(events):
        open_pos = marker_positions.get((open_event["ticket"], "OPEN"))
        close_pos = marker_positions.get((close_event["ticket"], "CLOSE"))
        if open_pos is None or close_pos is None:
            continue
        segment_items.append(
            f"<line x1='{open_pos[0]:.2f}' y1='{open_pos[1]:.2f}' x2='{close_pos[0]:.2f}' y2='{close_pos[1]:.2f}' stroke='#111111' stroke-width='1.2' />"
        )
    return (
        f"<div class='chart-meta'>Finestra: <strong>60 minuti</strong> | Candele presenti: <strong>{shown_count}</strong> | Ultima chiusa: <strong>{format_price(float(latest['close']))}</strong></div>"
        f"<svg viewBox='0 0 {width} {height}' class='chart-svg' role='img' aria-label='Grafico candele chiuse'>"
        f"{''.join(y_labels)}"
        f"{x_grids}"
        f"{''.join(candle_shapes)}"
        f"{''.join(segment_items)}"
        f"{''.join(marker_items)}"
        f"{x_labels}"
        "</svg>"
    )


def render_trade_table(current_trades: list[sqlite3.Row]) -> str:
    trade_rows = []
    for row in current_trades:
        trade_rows.append(
            "<tr>"
            f"<td>{row['ticket']}</td>"
            f"<td>{escape(row['side'])}</td>"
            f"<td>{format_price(row['open_price'])}</td>"
            f"<td>{format_price(row['stop_loss'])}</td>"
            f"<td>{format_price(row['take_profit'])}</td>"
            f"<td>{format_price(row['profit'])}</td>"
            f"<td>{format_price(row['bid'])}</td>"
            f"<td>{format_price(row['ask'])}</td>"
            "</tr>"
        )

    return (
        "<table><thead><tr><th>Ticket</th><th>Side</th><th>Apertura</th><th>SL</th><th>TP</th>"
        "<th>Profit</th><th>Bid</th><th>Ask</th></tr></thead><tbody>"
        + ("".join(trade_rows) if trade_rows else "<tr><td colspan='8'>Nessun trade aperto.</td></tr>")
        + "</tbody></table>"
    )


def render_recent_trades_table(recent_events: list[sqlite3.Row]) -> str:
    event_rows = []
    for row in recent_events:
        if row["event_type"] != "CLOSE":
            continue
        event_rows.append(
            "<tr>"
            f"<td>{escape(format_timestamp_for_table(row['event_time']))}</td>"
            f"<td>{row['ticket']}</td>"
            f"<td>{escape(row['side'])}</td>"
            f"<td>{format_price(row['open_price'])}</td>"
            f"<td>{format_price(row['stop_loss'])}</td>"
            f"<td>{format_price(row['take_profit'])}</td>"
            f"<td>{format_price(row['profit'])}</td>"
            "</tr>"
        )

    return (
        "<table><thead><tr><th>Ora</th><th>Ticket</th><th>Side</th><th>Apertura</th>"
        "<th>SL</th><th>TP</th><th>Profit</th></tr></thead><tbody>"
        + ("".join(event_rows) if event_rows else "<tr><td colspan='7'>Nessun trade chiuso registrato.</td></tr>")
        + "</tbody></table>"
    )


def render_dashboard_fragments(store: TradeStore) -> dict[str, str]:
    current_trades = store.fetch_current_trades()
    recent_events = store.fetch_recent_events()
    last_api_call = store.fetch_last_api_call()
    latest_errors = store.fetch_recent_errors(limit=1)
    commands_enabled = store.get_commands_enabled()
    current_candle_states = store.fetch_recent_current_candle_states()
    recent_closed_candles = store.fetch_recent_closed_candles()
    return {
        "hero_info_html": render_hero_info(
            last_api_call,
            latest_errors[0] if latest_errors else None,
            commands_enabled,
        ),
        "trade_table_html": render_trade_table(current_trades),
        "recent_trades_html": render_recent_trades_table(recent_events),
        "price_chart_html": polyline_price_chart_with_markers(current_candle_states, recent_events),
        "candle_chart_html": candlestick_chart(recent_closed_candles, recent_events),
    }


def render_homepage(store: TradeStore) -> str:
    dashboard_fragments = render_dashboard_fragments(store)
    hero_info_html = dashboard_fragments["hero_info_html"]
    trade_table_html = dashboard_fragments["trade_table_html"]
    recent_trades_html = dashboard_fragments["recent_trades_html"]
    price_chart_html = dashboard_fragments["price_chart_html"]
    candle_chart_html = dashboard_fragments["candle_chart_html"]

    return f"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MT4 Trade Monitor</title>
  <style>
    :root {{
      --bg: #f6f1e8;
      --panel: #fffaf1;
      --ink: #1d1b19;
      --accent: #9c6b1a;
      --line: #d7c7ad;
      --good: #176b3a;
      --bad: #9e2f21;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(156,107,26,0.12), transparent 30%),
        linear-gradient(180deg, #fbf7f0 0%, var(--bg) 100%);
    }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 32px 20px 48px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(320px, 0.42fr) minmax(420px, 0.58fr);
      gap: 24px;
      margin-bottom: 24px;
      align-items: start;
    }}
    .hero-card,
    .hero-side {{
      padding: 24px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 18px;
      box-shadow: 0 18px 40px rgba(70, 49, 16, 0.08);
    }}
    .stack {{ display: grid; gap: 24px; }}
    section {{
      border: 1px solid var(--line);
      background: rgba(255,250,241,0.92);
      border-radius: 18px;
      padding: 20px;
      overflow-x: auto;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ color: var(--accent); }}
    .meta {{ color: #5a5247; }}
    .error-inline {{ color: var(--bad); }}
    .control-row {{
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      margin: 10px 0 0;
    }}
    .control-btn {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 14px;
      font: inherit;
      cursor: pointer;
      background: #f4eadb;
      color: var(--ink);
    }}
    .control-btn.is-on {{
      background: #e7f4ea;
      color: var(--good);
      border-color: rgba(23, 107, 58, 0.25);
    }}
    .control-btn.is-off {{
      background: #fff1ef;
      color: var(--bad);
      border-color: rgba(158, 47, 33, 0.25);
    }}
    .chart-meta {{
      margin-bottom: 10px;
      color: #5a5247;
      font-size: 14px;
    }}
    .chart-svg {{
      display: block;
      width: 100%;
      min-width: 880px;
      height: auto;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.9), rgba(244,233,216,0.7));
      border: 1px solid var(--line);
      border-radius: 16px;
    }}
    .chart-grid {{
      stroke: rgba(156, 107, 26, 0.16);
      stroke-width: 1;
    }}
    .chart-last-line {{
      stroke: rgba(156, 107, 26, 0.4);
      stroke-width: 1;
      stroke-dasharray: 5 4;
    }}
    svg text {{
      fill: #6b604f;
      font-size: 11px;
      font-family: Georgia, "Times New Roman", serif;
    }}
    .chart-shell {{
      min-height: 140px;
    }}
    @media (max-width: 980px) {{
      .hero {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="hero">
      <div class="hero-card">
        <h1>MT4 Trade Monitor</h1>
        <div id="hero-info-panel">
          {hero_info_html}
        </div>
      </div>
      <section class="hero-side">
        <h2>Trade in Corso</h2>
        <div id="trade-table-panel">
          {trade_table_html}
        </div>
      </section>
    </div>
    <div class="stack">
      <section>
        <h2>Ultimi Trade</h2>
        <div id="recent-trades-panel">
          {recent_trades_html}
        </div>
      </section>
      <section>
        <h2>Ultimo Prezzo Intra-Minuto</h2>
        <div id="price-chart-panel" class="chart-shell">
          {price_chart_html}
        </div>
      </section>
      <section>
        <h2>Candele Chiuse</h2>
        <div id="candle-chart-panel" class="chart-shell">
          {candle_chart_html}
        </div>
      </section>
    </div>
  </main>
  <script>
    const heroInfoPanel = document.getElementById("hero-info-panel");
    const tradeTablePanel = document.getElementById("trade-table-panel");
    const recentTradesPanel = document.getElementById("recent-trades-panel");
    const priceChartPanel = document.getElementById("price-chart-panel");
    const candleChartPanel = document.getElementById("candle-chart-panel");

    async function refreshDashboard() {{
      try {{
        const response = await fetch("/api/dashboard", {{
          headers: {{ "Accept": "application/json" }},
          cache: "no-store"
        }});
        if (!response.ok) {{
          return;
        }}
        const payload = await response.json();
        if (typeof payload.hero_info_html === "string") {{
          heroInfoPanel.innerHTML = payload.hero_info_html;
        }}
        if (typeof payload.trade_table_html === "string") {{
          tradeTablePanel.innerHTML = payload.trade_table_html;
        }}
        if (typeof payload.recent_trades_html === "string") {{
          recentTradesPanel.innerHTML = payload.recent_trades_html;
        }}
        if (typeof payload.price_chart_html === "string") {{
          priceChartPanel.innerHTML = payload.price_chart_html;
        }}
        if (typeof payload.candle_chart_html === "string") {{
          candleChartPanel.innerHTML = payload.candle_chart_html;
        }}
      }} catch (error) {{
        console.debug("Dashboard refresh failed", error);
      }}
    }}

    async function toggleTradingCommands() {{
      try {{
        const response = await fetch("/api/commands/toggle", {{
          method: "POST",
          headers: {{ "Accept": "application/json" }},
          cache: "no-store"
        }});
        if (!response.ok) {{
          return;
        }}
        await refreshDashboard();
      }} catch (error) {{
        console.debug("Command toggle failed", error);
      }}
    }}

    window.setInterval(refreshDashboard, 1000);
  </script>
</body>
</html>"""
