from plotly.subplots import make_subplots
import datetime as dt
import holidays
import plotly.graph_objects as go
import pandas as pd
import re
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, "charts")
LOG_PATH = os.path.join(SCRIPT_DIR, 'tf2_balloon_log.txt')
README_PATH = os.path.join(SCRIPT_DIR, 'README.md')

# Log timestamps are written in the server's clock (UTC); charts are shown in this zone
LOG_TIMEZONE = 'UTC'
DISPLAY_TIMEZONE = 'America/Denver'
TZ_LABEL = 'MT'

# Scorecard needs at least this many days of checks before it names a winner
MIN_DAYS_FOR_SCORECARD = 7
# Scorecard looks at this many recent days so it follows changing habits
SCORECARD_RECENT_DAYS = 28
# A lobby with at least this many human players counts as "busy"
BUSY_THRESHOLD = 10
TOP_SERVER_COUNT = 5
CHECK_MINUTES = 5
# A day needs at least this many checks to count in the holiday comparison
MIN_CHECKS_PER_DAY = 144

DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

# Colors (validated dataviz reference palette)
SURFACE = '#fcfcfb'
TEXT_PRIMARY = '#0b0b0b'
TEXT_SECONDARY = '#52514e'
GRID = '#e6e5e0'
BAR = '#2a78d6'
SERIES = ['#2a78d6', '#eb6834']
SEQUENTIAL = ['#f3f2ef', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']
COLORSCALE = [[i / (len(SEQUENTIAL) - 1), c] for i, c in enumerate(SEQUENTIAL)]
FONT = 'Inter, "Helvetica Neue", Helvetica, Arial, sans-serif'

LINE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - INFO - (.*)$')
SERVER_RE = re.compile(r'^SERVER (\S+) \| (.*) : (\d+)$')
PLAYERS_RE = re.compile(r'^(.*) : (\d+)$')

README_START = '<!-- TOP_SERVERS_START -->'
README_END = '<!-- TOP_SERVERS_END -->'


def hour_label(h):
    return f"{h % 12 or 12} {'AM' if h % 24 < 12 else 'PM'}"


def window_label(hour_of_week, short=False):
    day = DAYS[hour_of_week // 24]
    start = hour_of_week % 24
    return f"{day[:3] if short else day} · {hour_label(start)} – {hour_label(start + 2)}"


def add_time_columns(df):
    df['ts'] = (
        pd.to_datetime(df['ts'])
        .dt.tz_localize(LOG_TIMEZONE)
        .dt.tz_convert(DISPLAY_TIMEZONE)
    )
    df['day_of_week'] = pd.Categorical(df['ts'].dt.day_name(), categories=DAYS, ordered=True)
    df['hour'] = df['ts'].dt.hour
    df['hour_of_week'] = df['ts'].dt.dayofweek * 24 + df['hour']
    df['month'] = df['ts'].dt.strftime('%Y-%m')
    return df


# Load and preprocess data: one row per 5-minute check, plus one row per active server per check
def load_and_preprocess_data(file_path):
    checks, servers = [], []
    with open(file_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = LINE_RE.match(line.rstrip('\n'))
            if not m:
                continue
            ts, msg = m.groups()
            if msg.startswith('No one playing') or msg.startswith('Found people playing'):
                checks.append({'ts': ts, 'server_name': None, 'online_players': 0})
            elif not checks or checks[-1]['ts'] != ts:
                continue
            elif s := SERVER_RE.match(msg):
                servers.append({'ts': ts, 'addr': s.group(1), 'name': s.group(2).strip(),
                                'players': int(s.group(3))})
            elif p := PLAYERS_RE.match(msg):
                checks[-1]['server_name'] = p.group(1).strip()
                checks[-1]['online_players'] = int(p.group(2))

    df = add_time_columns(pd.DataFrame(checks, columns=['ts', 'server_name', 'online_players']))
    server_df = add_time_columns(pd.DataFrame(servers, columns=['ts', 'addr', 'name', 'players']))
    return df, server_df


def base_layout(title, subtitle=None, **kwargs):
    text = f"<b>{title}</b>"
    if subtitle:
        text += f"<br><span style='font-size:14px;color:{TEXT_SECONDARY}'>{subtitle}</span>"
    layout = dict(
        title=dict(text=text, x=0.02, xanchor='left', y=0.95, font=dict(size=22, color=TEXT_PRIMARY)),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=13, color=TEXT_SECONDARY),
        margin=dict(l=24, r=32, t=96, b=48),
        width=1000,
        height=600,
        showlegend=False,
    )
    layout.update(kwargs)
    return layout


def empty_state(fig, message='Not enough data yet'):
    fig.add_annotation(x=0.5, y=0.5, xref='paper', yref='paper', showarrow=False,
                       text=message, font=dict(size=20, color=TEXT_SECONDARY))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)


def save(fig, name):
    fig.write_image(os.path.join(OUTPUT_FOLDER, name), scale=2)


def days_of_data(df):
    return (df['ts'].max() - df['ts'].min()).days if len(df) else 0


# % of checks with someone on, for every 2-hour window of the week (wrapping Sunday night into Monday)
def window_rates(df, active):
    rate = (
        df.assign(active=active)
        .groupby('hour_of_week')['active']
        .agg(['sum', 'count'])
        .reindex(range(168), fill_value=0)
    )
    nxt = rate.shift(-1)
    nxt.iloc[-1] = rate.iloc[0]
    window = rate + nxt
    window['pct'] = (window['sum'] / window['count'].where(window['count'] > 0)).fillna(0)
    return window


# Best non-overlapping 2-hour windows, best first
def top_windows(window, n):
    picked = []
    for start in window['pct'].sort_values(ascending=False).index:
        if len(picked) == n or window.loc[start, 'pct'] == 0:
            break
        if all(min((start - p) % 168, (p - start) % 168) >= 2 for p in picked):
            picked.append(int(start))
    return picked


# Big scorecard: the single 2-hour window of the week most likely to have players
def generate_scorecard(df):
    total_days = days_of_data(df)
    recent = df[df['ts'] >= df['ts'].max() - pd.Timedelta(days=SCORECARD_RECENT_DAYS)] if len(df) else df
    window = window_rates(recent, recent['online_players'] > 0)
    all_time = window_rates(df, df['online_players'] > 0)

    fig = go.Figure()
    fig.update_layout(base_layout('', height=360), xaxis=dict(visible=False), yaxis=dict(visible=False))
    fig.update_layout(title=None, margin=dict(l=0, r=0, t=0, b=0))

    def text(y, s, size, color, bold=False):
        fig.add_annotation(
            x=0.5, y=y, xref='paper', yref='paper', showarrow=False,
            text=f"<b>{s}</b>" if bold else s, font=dict(size=size, color=color, family=FONT),
        )

    text(0.88, 'BEST TIME TO FIND PEOPLE ON BALLOON RACE', 16, TEXT_SECONDARY, bold=True)
    if total_days < MIN_DAYS_FOR_SCORECARD or window['sum'].max() == 0:
        text(0.55, 'Not enough data yet', 56, TEXT_PRIMARY, bold=True)
        text(0.28, f'{total_days} of {MIN_DAYS_FOR_SCORECARD} days of checks collected', 18, TEXT_SECONDARY)
    else:
        best = int(window['pct'].idxmax())
        all_time_best = int(all_time['pct'].idxmax())
        text(0.62, f"{window_label(best)} {TZ_LABEL}", 60, TEXT_PRIMARY, bold=True)
        text(0.38, f"People were online in <b>{window.loc[best, 'pct']:.0%}</b> of checks in this window "
                   f"over the last {min(SCORECARD_RECENT_DAYS, total_days)} days", 20, TEXT_SECONDARY)
        text(0.2, f"All-time best: {window_label(all_time_best)} ({all_time.loc[all_time_best, 'pct']:.0%}) "
                  f"· {total_days} days of data", 14, TEXT_SECONDARY)
    save(fig, 'best_time.png')


# Ranked bars: the 5 best non-overlapping 2-hour windows
def generate_top_windows_plot(df):
    window = window_rates(df, df['online_players'] > 0)
    picked = top_windows(window, 5) if days_of_data(df) >= MIN_DAYS_FOR_SCORECARD else []
    picked = picked[::-1]
    pcts = [window.loc[p, 'pct'] * 100 for p in picked]

    fig = go.Figure(go.Bar(
        x=pcts, y=[f"{window_label(p, short=True)} {TZ_LABEL}" for p in picked], orientation='h',
        marker_color=BAR, text=[f"{v:.0f}%" for v in pcts], textposition='outside', cliponaxis=False,
        textfont=dict(color=TEXT_PRIMARY, size=15),
    ))
    fig.update_layout(base_layout(
        'Top 5 times to play',
        'Best 2-hour windows (no overlaps) · % of checks with at least one player',
        height=480, barcornerradius=4, bargap=0.35,
    ))
    if not picked:
        empty_state(fig)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=False, ticksuffix='%', rangemode='tozero')
    fig.update_yaxes(showgrid=False, ticks='', tickfont=dict(size=15, color=TEXT_PRIMARY))
    save(fig, 'top_windows.png')


# Day x hour heatmap of how often a condition held
def generate_heatmap(df, active, title, subtitle, filename):
    heatmap_data = (
        df.assign(active=active * 100)
        .pivot_table(index='hour', columns='day_of_week', values='active',
                     aggfunc='mean', observed=False)
        .reindex(index=range(24), columns=DAYS)
        .fillna(0)
    )
    z = heatmap_data.values
    zmax = max(float(z.max()), 1)

    fig = go.Figure(go.Heatmap(
        z=z,
        x=DAYS,
        y=[hour_label(h) for h in range(24)],
        colorscale=COLORSCALE,
        zmin=0, zmax=zmax,
        xgap=2, ygap=2,
        hoverinfo='skip',
        colorbar=dict(title=dict(text='% of checks', side='top'), ticksuffix='%',
                      thickness=12, outlinewidth=0, len=0.8),
    ))
    # Label non-zero cells, with ink that stays readable on light and dark steps
    for yi, h in enumerate(range(24)):
        for xi, d in enumerate(DAYS):
            v = z[yi, xi]
            if round(v) > 0:
                fig.add_annotation(x=d, y=hour_label(h), text=f"{v:.0f}%", showarrow=False,
                                   font=dict(size=11, color='#ffffff' if v / zmax > 0.45 else TEXT_PRIMARY))

    fig.update_layout(base_layout(title, subtitle, height=820))
    fig.update_yaxes(autorange='reversed', showgrid=False, ticks='')
    fig.update_xaxes(side='top', showgrid=False, ticks='')
    fig.update_layout(margin=dict(l=24, r=32, t=130, b=24))
    save(fig, filename)


# Two lines: share of checks with players by hour, weekdays vs weekends
def generate_weekday_weekend_plot(df):
    hours = list(range(24))
    labels = [hour_label(h) for h in hours]
    groups = [
        ('Weekdays (Mon–Fri)', df['ts'].dt.dayofweek < 5),
        ('Weekends (Sat–Sun)', df['ts'].dt.dayofweek >= 5),
    ]
    fig = go.Figure()
    ends = []
    for (name, mask), color in zip(groups, SERIES):
        sub = df[mask]
        pct = (sub['online_players'] > 0).groupby(sub['hour']).mean().reindex(hours) * 100
        fig.add_trace(go.Scatter(
            x=labels, y=pct.values, name=name, mode='lines+markers',
            line=dict(color=color, width=2), marker=dict(size=8, color=color, line=dict(color=SURFACE, width=2)),
            connectgaps=False,
        ))
        last = pct.last_valid_index()
        if last is not None:
            ends.append((name.split(' ')[0], last, pct[last]))
    # Direct labels at the line ends, pushed apart when the lines finish close together
    if len(ends) == 2 and abs(ends[0][2] - ends[1][2]) < 5:
        ends.sort(key=lambda e: e[2])
        shifts = [-9, 9]
    else:
        shifts = [0] * len(ends)
    for (label, last, y), shift in zip(ends, shifts):
        fig.add_annotation(x=hour_label(last), y=y, text=f"<b>{label}</b>", yshift=shift,
                           xanchor='left', xshift=10, showarrow=False, font=dict(color=TEXT_PRIMARY))

    fig.update_layout(base_layout(
        'Weekdays vs weekends',
        f'% of checks with at least one player, by hour ({TZ_LABEL})',
        showlegend=True,
        legend=dict(orientation='h', x=0.02, y=1.02, xanchor='left', yanchor='bottom'),
        margin=dict(l=24, r=110, t=130, b=48),
    ))
    if df.empty:
        empty_state(fig)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, ticksuffix='%', rangemode='tozero')
    fig.update_xaxes(showgrid=False, ticks='', tickangle=-45)
    save(fig, 'weekday_vs_weekend.png')


# How long people stick around once they show up
def generate_session_length_plot(df):
    active = (df.sort_values('ts')['online_players'] > 0).values
    ts = df.sort_values('ts')['ts'].values
    sessions, run = [], 0
    for i, a in enumerate(active):
        contiguous = i > 0 and (ts[i] - ts[i - 1]) <= pd.Timedelta(minutes=CHECK_MINUTES + 2)
        if a and run and contiguous:
            run += 1
        else:
            if run:
                sessions.append(run * CHECK_MINUTES)
            run = 1 if a else 0
    if run:
        sessions.append(run * CHECK_MINUTES)

    buckets = [('Under 15 min', 0, 15), ('15–30 min', 15, 30), ('30–60 min', 30, 60),
               ('1–2 hours', 60, 120), ('2+ hours', 120, float('inf'))]
    counts = [sum(lo <= s < hi for s in sessions) for _, lo, hi in buckets]
    median = pd.Series(sessions).median() if sessions else None

    fig = go.Figure(go.Bar(
        x=[b[0] for b in buckets], y=counts, marker_color=BAR,
        text=counts, textposition='outside', cliponaxis=False, textfont=dict(color=TEXT_PRIMARY),
    ))
    subtitle = 'A session = back-to-back 5-minute checks with at least one player on'
    if median is not None:
        subtitle += f' · median session: {median:.0f} min'
    fig.update_layout(base_layout('How long sessions last', subtitle,
                                  barcornerradius=4, bargap=0.4))
    if not sessions:
        empty_state(fig, 'No sessions seen yet')
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, title='Sessions', rangemode='tozero')
    fig.update_xaxes(showgrid=False, ticks='')
    save(fig, 'session_length.png')


# Weekly trend: is the scene growing or dying?
def generate_weekly_trend_plot(df):
    week = df['ts'].dt.tz_localize(None).dt.to_period('W-SUN').dt.start_time
    grouped = (df['online_players'] > 0).groupby(week)
    # Skip weeks with under 3 days of checks (e.g. the week logging started) so they don't skew the line
    min_checks = 3 * 24 * 60 // CHECK_MINUTES
    keep = grouped.size() >= min_checks
    if len(keep):
        keep.iloc[-1] = True  # always show the current week
    weekly = (grouped.mean() * 100)[keep]

    fig = go.Figure(go.Scatter(
        x=weekly.index, y=weekly.values, mode='lines+markers',
        line=dict(color=BAR, width=2), marker=dict(size=8, color=BAR, line=dict(color=SURFACE, width=2)),
    ))
    if len(weekly):
        fig.add_annotation(x=weekly.index[-1], y=weekly.values[-1], text=f"<b>{weekly.values[-1]:.0f}%</b>",
                           yshift=16, showarrow=False, font=dict(color=TEXT_PRIMARY))
    fig.update_layout(base_layout(
        'Weekly trend',
        '% of checks each week with at least one player · latest week may be partial',
    ))
    if len(weekly) < 2:
        empty_state(fig, 'Needs at least 2 weeks of data')
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, ticksuffix='%', rangemode='tozero')
    fig.update_xaxes(showgrid=False, ticks='', tickformat='%b %d')
    if len(weekly):
        fig.update_xaxes(range=[weekly.index[0] - pd.Timedelta(days=3), weekly.index[-1] + pd.Timedelta(days=3)])
    save(fig, 'weekly_trend.png')


# Generate bar plot for top servers
def generate_top_servers_plot(df):
    top_servers = (
        df.query('online_players > 0')
        .groupby('server_name')['online_players']
        .max()
        .sort_values(ascending=True)
        .tail(10)
    )
    names = [n if len(n) <= 45 else n[:44] + '…' for n in top_servers.index]

    fig = go.Figure(go.Bar(
        x=top_servers.values, y=names, orientation='h', marker_color=BAR,
        text=top_servers.values, textposition='outside', cliponaxis=False,
        textfont=dict(color=TEXT_PRIMARY),
    ))
    fig.update_layout(base_layout(
        'Busiest Balloon Race servers',
        'Most players seen at once, top 10 servers',
        barcornerradius=4, bargap=0.35,
    ))
    if top_servers.empty:
        empty_state(fig, 'No players seen yet')
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=False, title='Players')
    fig.update_yaxes(showgrid=False, ticks='')
    save(fig, 'top_servers.png')


# Generate bar plot for monthly max
def generate_monthly_avg_plot(df):
    monthly = df.groupby('month')['online_players'].max().sort_index()
    labels = [pd.Timestamp(m + '-01').strftime('%b %Y') for m in monthly.index]

    fig = go.Figure(go.Bar(
        x=labels, y=monthly.values, marker_color=BAR,
        text=monthly.values, textposition='outside', cliponaxis=False,
        textfont=dict(color=TEXT_PRIMARY),
    ))
    fig.update_layout(base_layout(
        'Max Balloon Race players by month',
        'Most players seen at once on any server',
        barcornerradius=4, bargap=0.45,
    ))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, title='Players', rangemode='tozero')
    fig.update_xaxes(showgrid=False, ticks='')
    save(fig, 'monthly_avg.png')


# Generate bar plot for average daily peak by month
def generate_monthly_daily_avg_plot(df):
    daily_peak = df.groupby(df['ts'].dt.date)['online_players'].max()
    months = pd.to_datetime(daily_peak.index).strftime('%Y-%m')
    monthly = daily_peak.groupby(months).mean().sort_index()
    labels = [pd.Timestamp(m + '-01').strftime('%b %Y') for m in monthly.index]

    fig = go.Figure(go.Bar(
        x=labels, y=monthly.values, marker_color=BAR,
        text=[f"{v:.1f}" for v in monthly.values], textposition='outside', cliponaxis=False,
        textfont=dict(color=TEXT_PRIMARY),
    ))
    fig.update_layout(base_layout(
        'Average daily peak players by month',
        "Each day's most players seen at once, averaged over the month (days nobody played count as 0)",
        barcornerradius=4, bargap=0.45,
    ))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, title='Players', rangemode='tozero')
    fig.update_xaxes(showgrid=False, ticks='')
    save(fig, 'monthly_daily_avg.png')


# US holidays plus a few unofficial gaming days and school-break periods
def holiday_calendar(years):
    rename = {"Washington's Birthday": "Presidents' Day",
              'Juneteenth National Independence Day': 'Juneteenth'}
    days = {}
    for d, name in holidays.US(years=years).items():
        name = name.replace(' (observed)', '')
        days.setdefault(rename.get(name, name), []).append(d)
    for y in years:
        days.setdefault('Halloween', []).append(dt.date(y, 10, 31))
        days.setdefault('Christmas Eve', []).append(dt.date(y, 12, 24))
        days.setdefault("New Year's Eve", []).append(dt.date(y, 12, 31))
        thanksgiving = next(d for d, n in holidays.US(years=y).items() if n == 'Thanksgiving Day')
        days.setdefault('Thanksgiving weekend (Thu–Sun)', []).extend(
            thanksgiving + dt.timedelta(days=i) for i in range(4))
        days.setdefault('Winter break (Dec 20 – Jan 3)', []).extend(
            dt.date(y, 12, 20) + dt.timedelta(days=i) for i in range(15))
    return days


# Table: how much busier each holiday is than a normal day of the same weekday
def generate_holiday_table(df):
    daily = (
        df.assign(active=df['online_players'] > 0, date=df['ts'].dt.date)
        .groupby('date')
        .agg(rate=('active', 'mean'), checks=('active', 'size'), peak=('online_players', 'max'))
    )
    daily = daily[daily['checks'] >= MIN_CHECKS_PER_DAY]
    years = sorted({d.year for d in daily.index} | {dt.date.today().year}) if len(daily) else [dt.date.today().year]
    calendar = holiday_calendar(range(years[0] - 1, years[-1] + 2))
    holiday_dates = {d for dates in calendar.values() for d in dates}

    # Baseline: average day of the same weekday that isn't a holiday
    normal = daily[[d not in holiday_dates for d in daily.index]]
    baseline = (normal.groupby([d.weekday() for d in normal.index])['rate'].mean()
                if len(normal) else pd.Series(dtype=float))

    rows = []
    for name, dates in calendar.items():
        seen = [d for d in dates if d in daily.index]
        if not seen or baseline.empty:
            continue
        rate = daily.loc[seen, 'rate'].mean()
        base = pd.Series([baseline.get(d.weekday()) for d in seen]).mean()
        if pd.isna(base):
            continue
        rows.append({
            'name': name,
            'last': max(seen),
            'days': len(seen),
            'rate': rate,
            'base': base,
            'lift': rate / base if base > 0 else float('inf') if rate > 0 else 1.0,
            'peak': int(daily.loc[seen, 'peak'].max()),
        })
    rows.sort(key=lambda r: r['lift'], reverse=True)

    today = dt.date.today()
    upcoming = sorted((min(d for d in dates if d >= today), name)
                      for name, dates in calendar.items() if any(d >= today for d in dates))[:3]
    next_up = ' · '.join(f"{name} ({d.strftime('%b %d')})" for d, name in upcoming)

    fig = go.Figure()
    if rows:
        def lift_text(r):
            if r['lift'] == float('inf'):
                return '<b>New activity</b>'
            lift = round(r['lift'], 1)
            arrow = '▲' if lift >= 1.1 else '▼' if lift <= 0.9 else '–'
            return f"<b>{r['lift']:.1f}×</b> {arrow}"

        def lift_fill(r):
            if r['lift'] >= 2:
                return SEQUENTIAL[2]
            if r['lift'] >= 1.25:
                return SEQUENTIAL[1]
            return SURFACE

        fig.add_trace(go.Table(
            columnwidth=[3.2, 1.6, 1.4, 1.4, 1.3, 0.9],
            header=dict(
                values=['<b>Holiday</b>', '<b>Last seen</b>', '<b>Players on</b>', '<b>Normal day</b>',
                        '<b>vs normal</b>', '<b>Peak</b>'],
                fill_color=SURFACE, line_color=GRID, align=['left'] + ['right'] * 5, height=40,
                font=dict(size=14, color=TEXT_SECONDARY, family=FONT),
            ),
            cells=dict(
                values=[
                    [f"<b>{r['name']}</b>" for r in rows],
                    [r['last'].strftime('%b %d, %Y') for r in rows],
                    [f"{r['rate']:.0%}" for r in rows],
                    [f"{r['base']:.0%}" for r in rows],
                    [lift_text(r) for r in rows],
                    [r['peak'] for r in rows],
                ],
                fill_color=[[SURFACE] * len(rows)] * 4 + [[lift_fill(r) for r in rows], [SURFACE] * len(rows)],
                line_color=GRID, align=['left'] + ['right'] * 5, height=38,
                font=dict(size=14, color=TEXT_PRIMARY, family=FONT),
            ),
        ))
    fig.update_layout(base_layout(
        'Holidays vs normal days',
        '"Players on" = % of checks with at least one player · compared with non-holiday days of the same weekday'
        + f'<br>Next up: {next_up}',
        height=200 + 38 * max(len(rows), 4),
        margin=dict(l=24, r=24, t=130, b=16),
    ))
    fig.update_layout(title=dict(y=1, yanchor='top', pad=dict(t=24)))
    if not rows:
        empty_state(fig, 'No holidays in the data yet')
    save(fig, 'holidays.png')


# Rank servers by how often they have people on, with each one's best times
def top_server_stats(df, server_df):
    stats = []
    for addr, rows in server_df.groupby('addr'):
        # Only count checks since the server was first seen, so new servers aren't penalized
        checks = df[df['ts'] >= rows['ts'].min()]
        active_ts = set(rows.loc[rows['players'] > 0, 'ts'])
        active = checks['ts'].isin(active_ts)
        window = window_rates(checks, active)
        best = int(window['pct'].idxmax())
        by_day = active.groupby(checks['day_of_week'], observed=False).mean()
        stats.append({
            'addr': addr,
            'name': rows.sort_values('ts')['name'].iloc[-1],
            'pct_active': active.mean(),
            'best_window': best,
            'best_window_pct': window.loc[best, 'pct'],
            'best_day': by_day.idxmax(),
            'avg_players': rows.loc[rows['players'] > 0, 'players'].mean(),
            'peak': rows['players'].max(),
            'checks': checks,
            'active': active,
        })
    stats.sort(key=lambda s: s['pct_active'], reverse=True)
    return stats[:TOP_SERVER_COUNT]


# Small multiples: one day x hour heatmap per top server, on a shared scale
def generate_server_heatmaps(stats):
    if not stats:
        fig = go.Figure()
        fig.update_layout(base_layout('When the top servers are busy', height=300))
        empty_state(fig, 'No server data yet')
        save(fig, 'server_heatmaps.png')
        return

    grids = []
    for s in stats:
        grids.append(
            (s['active'] * 100).groupby([s['checks']['day_of_week'], s['checks']['hour']], observed=False)
            .mean().unstack().reindex(index=DAYS, columns=range(24)).fillna(0)
        )
    zmax = max(max(float(g.values.max()) for g in grids), 1)

    titles = [f"<b>#{i + 1} {s['name'][:60]}</b>  ·  {s['addr']}" for i, s in enumerate(stats)]
    fig = make_subplots(rows=len(stats), cols=1, subplot_titles=titles, vertical_spacing=0.32 / len(stats))
    for i, g in enumerate(grids):
        fig.add_trace(go.Heatmap(
            z=g.values, x=[hour_label(h) for h in range(24)], y=[d[:3] for d in DAYS],
            colorscale=COLORSCALE, zmin=0, zmax=zmax, xgap=2, ygap=2, hoverinfo='skip',
            showscale=(i == 0),
            colorbar=dict(title=dict(text='% of checks', side='top'), ticksuffix='%',
                          thickness=12, outlinewidth=0, len=0.3, y=1, yanchor='top'),
        ), row=i + 1, col=1)
        fig.update_yaxes(autorange='reversed', row=i + 1, col=1)

    fig.update_layout(base_layout(
        'When the top servers are busy',
        f'% of checks each server had at least one player · times in {TZ_LABEL}',
        height=170 + 260 * len(stats),
    ))
    fig.update_annotations(font=dict(size=14, color=TEXT_PRIMARY), xanchor='left', x=0)
    fig.update_xaxes(showgrid=False, ticks='', tickfont=dict(size=10), tickangle=-45)
    fig.update_yaxes(showgrid=False, ticks='')
    fig.update_layout(margin=dict(l=24, r=32, t=130, b=40))
    save(fig, 'server_heatmaps.png')


# Write the top servers table into the README between the marker comments
def update_readme(stats):
    if stats:
        lines = [
            '| # | Server | Connect (paste in TF2 console) | Best time | Best day | Has players | Avg players when on | Peak |',
            '|---|---|---|---|---|---|---|---|',
        ]
        for i, s in enumerate(stats):
            name = s['name'].replace('|', '\\|')
            lines.append(
                f"| {i + 1} | {name} | `connect {s['addr']}` "
                f"| {window_label(s['best_window'], short=True)} {TZ_LABEL} ({s['best_window_pct']:.0%}) "
                f"| {s['best_day']} | {s['pct_active']:.0%} of checks | {s['avg_players']:.1f} | {s['peak']} |"
            )
        lines.append('')
        lines.append('To favorite one: Steam → View → Game Servers → Favorites → **+** and paste the address.')
    else:
        lines = ['_No server data yet - check back after a few days._']
    section = f"{README_START}\n" + '\n'.join(lines) + f"\n{README_END}"

    with open(README_PATH, encoding='utf-8') as f:
        readme = f.read()
    pattern = re.compile(re.escape(README_START) + '.*?' + re.escape(README_END), re.S)
    if not pattern.search(readme):
        print('README markers not found; skipping top servers table')
        return
    with open(README_PATH, 'w', encoding='utf-8') as f:
        f.write(pattern.sub(lambda _: section, readme))


# Push updates to GitHub
def push_to_github():
    os.chdir(SCRIPT_DIR)
    os.system("git add charts README.md tf2_balloon_log.txt")
    os.system("git commit -m 'update charts and log'")
    os.system("git push origin main")


# Main workflow
if __name__ == "__main__":
    # Optional: generate_report.py [log_path [output_folder]] [--no-push] [--no-readme]
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    log_path = args[0] if args else LOG_PATH
    if len(args) > 1:
        OUTPUT_FOLDER = args[1]
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    graph_df, server_df = load_and_preprocess_data(log_path)
    generate_scorecard(graph_df)
    generate_top_windows_plot(graph_df)
    generate_heatmap(
        graph_df, graph_df['online_players'] > 0,
        'How often people are on Balloon Race',
        f'% of 5-minute checks with at least one player · times in {TZ_LABEL} · blank = never seen',
        'heatmap.png',
    )
    generate_heatmap(
        graph_df, graph_df['online_players'] >= BUSY_THRESHOLD,
        f'How often there is a busy lobby ({BUSY_THRESHOLD}+ players)',
        f'% of 5-minute checks with a server at {BUSY_THRESHOLD}+ human players · times in {TZ_LABEL}',
        'busy_heatmap.png',
    )
    generate_weekday_weekend_plot(graph_df)
    generate_session_length_plot(graph_df)
    generate_weekly_trend_plot(graph_df)
    generate_top_servers_plot(graph_df)
    generate_monthly_avg_plot(graph_df)
    generate_monthly_daily_avg_plot(graph_df)
    generate_holiday_table(graph_df)
    stats = top_server_stats(graph_df, server_df)
    generate_server_heatmaps(stats)
    if '--no-readme' not in sys.argv:
        update_readme(stats)
    if '--no-push' not in sys.argv:
        push_to_github()
