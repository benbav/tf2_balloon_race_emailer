from plotly.subplots import make_subplots
import datetime as dt
import holidays
import requests
import plotly.graph_objects as go
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from server_types import clean_name, is_dedicated
import subprocess
import json
import re
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, "charts")
LOG_PATH = os.path.join(SCRIPT_DIR, 'tf2_balloon_log.txt')
README_PATH = os.path.join(SCRIPT_DIR, 'README.md')
# BLS v1 has a tight daily request limit, so the series is cached and refetched at most once a day
BLS_URL = 'https://api.bls.gov/publicAPI/v1/timeseries/data/LNS14000000'
BLS_CACHE_PATH = os.path.join(SCRIPT_DIR, 'bls_unemployment.json')

# Log timestamps are written in the server's clock (UTC); charts are shown in these zones.
# The first zone is the default (README, all charts); the rest only redraw the charts that depend on the hour.
LOG_TIMEZONE = 'UTC'
ZONES = [
    # (timezone, label, file suffix)
    ('America/Denver', 'MT', ''),
    ('America/New_York', 'ET', '_et'),
    ('America/Chicago', 'CT', '_ct'),
    ('America/Los_Angeles', 'PT', '_pt'),
]
DISPLAY_TIMEZONE, TZ_LABEL, ZONE_SUFFIX = ZONES[0]

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

# Every chart is drawn for all servers, then again for each server type: (file suffix, label, color)
ALL_SERVERS = ('', None, None)
DEDICATED = ('_dedicated', 'Dedicated 24/7 servers', SERIES[0])
ROTATING = ('_rotating', 'Rotating servers', SERIES[1])

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
    df['ts'] = pd.to_datetime(df['ts']).dt.tz_localize(LOG_TIMEZONE)
    return in_zone(df)


# Recompute the clock columns in the current DISPLAY_TIMEZONE
def in_zone(df):
    df = df.copy()
    df['ts'] = df['ts'].dt.tz_convert(DISPLAY_TIMEZONE)
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
                servers.append({'ts': ts, 'addr': s.group(1), 'name': clean_name(s.group(2)),
                                'players': int(s.group(3))})
            elif p := PLAYERS_RE.match(msg):
                checks[-1]['server_name'] = clean_name(p.group(1))
                checks[-1]['online_players'] = int(p.group(2))

    df = add_time_columns(pd.DataFrame(checks, columns=['ts', 'server_name', 'online_players']))
    server_df = add_time_columns(pd.DataFrame(servers, columns=['ts', 'addr', 'name', 'players']))
    server_df['dedicated'] = [is_dedicated(n, a) for n, a in zip(server_df['name'], server_df['addr'])]
    return df, server_df


# The checks again, but only counting one type of server: each check keeps its busiest server of that type
def split_checks(df, server_df, dedicated):
    top = server_df[server_df['dedicated'] == dedicated].sort_values('players').groupby('ts').last()
    # Older checks were logged before per-server lines existed, so only the busiest server is known
    legacy = ~df['ts'].isin(server_df['ts']) & (df['online_players'] > 0)
    legacy &= df['server_name'].fillna('').map(is_dedicated) == dedicated
    out = df.copy()
    out['online_players'] = df['ts'].map(top['players']).where(~legacy, df['online_players']).fillna(0).astype(int)
    out['server_name'] = df['ts'].map(top['name']).where(~legacy, df['server_name'])
    return out


def base_layout(title, subtitle=None, variant=ALL_SERVERS, **kwargs):
    text = f"<b>{title}</b>"
    if variant[1]:
        text += f" <span style='color:{variant[2]}'><b>· {variant[1]}</b></span>"
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


def save(fig, name, variant=ALL_SERVERS):
    fig.write_image(os.path.join(OUTPUT_FOLDER, name.replace('.png', f'{variant[0]}{ZONE_SUFFIX}.png')), scale=2)


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
def generate_scorecard(df, variant=ALL_SERVERS):
    total_days = days_of_data(df)
    recent = df[df['ts'] >= df['ts'].max() - pd.Timedelta(days=SCORECARD_RECENT_DAYS)] if len(df) else df
    window = window_rates(recent, recent['online_players'] > 0)
    all_time = window_rates(df, df['online_players'] > 0)

    fig = go.Figure()
    fig.update_layout(base_layout('', height=480), xaxis=dict(visible=False), yaxis=dict(visible=False))
    fig.update_layout(title=None, margin=dict(l=0, r=0, t=0, b=0))

    def text(y, s, size, color, bold=False):
        fig.add_annotation(
            x=0.5, y=y, xref='paper', yref='paper', showarrow=False,
            text=f"<b>{s}</b>" if bold else s, font=dict(size=size, color=color, family=FONT),
        )

    text(0.88, 'BEST TIME TO FIND PEOPLE ON BALLOON RACE', 16, TEXT_SECONDARY, bold=True)
    if variant[1]:
        text(0.77, variant[1].upper(), 14, variant[2], bold=True)
    if total_days < MIN_DAYS_FOR_SCORECARD or window['sum'].max() == 0:
        text(0.55, 'Not enough data yet', 56, TEXT_PRIMARY, bold=True)
        text(0.28, f'{total_days} of {MIN_DAYS_FOR_SCORECARD} days of checks collected', 18, TEXT_SECONDARY)
    else:
        best = int(window['pct'].idxmax())
        all_time_best = int(all_time['pct'].idxmax())
        text(0.62, f"{window_label(best)} {TZ_LABEL}", 60, TEXT_PRIMARY, bold=True)
        text(0.4, f"<b>{window.loc[best, 'pct']:.0%}</b> chance people are on", 30, TEXT_PRIMARY)
        text(0.24, f"Based on checks in this window over the last {min(SCORECARD_RECENT_DAYS, total_days)} days",
             16, TEXT_SECONDARY)
        text(0.1, f"All-time best: {window_label(all_time_best)} ({all_time.loc[all_time_best, 'pct']:.0%}) "
                  f"· {total_days} days of data", 14, TEXT_SECONDARY)
    save(fig, 'best_time.png', variant)


# Ranked bars: the 5 best non-overlapping 2-hour windows
def generate_top_windows_plot(df, variant=ALL_SERVERS):
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
        variant=variant,
    ))
    if not picked:
        empty_state(fig)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=False, ticksuffix='%', rangemode='tozero')
    fig.update_yaxes(showgrid=False, ticks='', tickfont=dict(size=15, color=TEXT_PRIMARY))
    save(fig, 'top_windows.png', variant)


# Day x hour heatmap of how often a condition held
def generate_heatmap(df, active, title, subtitle, filename, variant=ALL_SERVERS):
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

    fig.update_layout(base_layout(title, subtitle, variant=variant, height=820))
    fig.update_yaxes(autorange='reversed', showgrid=False, ticks='')
    fig.update_xaxes(side='top', showgrid=False, ticks='')
    fig.update_layout(margin=dict(l=24, r=32, t=130, b=24))
    save(fig, filename, variant)


# How long people stick around once they show up
def generate_session_length_plot(df, variant=ALL_SERVERS):
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
                                  barcornerradius=4, bargap=0.4, variant=variant))
    if not sessions:
        empty_state(fig, 'No sessions seen yet')
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, title='Sessions', rangemode='tozero')
    fig.update_xaxes(showgrid=False, ticks='')
    save(fig, 'session_length.png', variant)


# Weekly trend: is the scene growing or dying?
def generate_weekly_trend_plot(df, variant=ALL_SERVERS):
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
        variant=variant,
    ))
    if len(weekly) < 2:
        empty_state(fig, 'Needs at least 2 weeks of data')
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, ticksuffix='%', rangemode='tozero')
    fig.update_xaxes(showgrid=False, ticks='', tickformat='%b %d')
    if len(weekly):
        fig.update_xaxes(range=[weekly.index[0] - pd.Timedelta(days=3), weekly.index[-1] + pd.Timedelta(days=3)])
    save(fig, 'weekly_trend.png', variant)


# Generate bar plot for top servers
def generate_top_servers_plot(df, variant=ALL_SERVERS):
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
        variant=variant,
    ))
    if top_servers.empty:
        empty_state(fig, 'No players seen yet')
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=False, title='Players')
    fig.update_yaxes(showgrid=False, ticks='')
    save(fig, 'top_servers.png', variant)


# Generate bar plot for monthly max
def generate_monthly_avg_plot(df, variant=ALL_SERVERS):
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
        variant=variant,
    ))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, title='Players', rangemode='tozero')
    fig.update_xaxes(showgrid=False, ticks='')
    save(fig, 'monthly_avg.png', variant)


# Generate bar plot for average daily peak by month
def generate_monthly_daily_avg_plot(df, variant=ALL_SERVERS):
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
        variant=variant,
    ))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, title='Players', rangemode='tozero')
    fig.update_xaxes(showgrid=False, ticks='')
    save(fig, 'monthly_daily_avg.png', variant)


# Monthly US unemployment rate (seasonally adjusted) from BLS, as a Series indexed by month start
def load_unemployment():
    cached = None
    if os.path.exists(BLS_CACHE_PATH):
        with open(BLS_CACHE_PATH, encoding='utf-8') as f:
            cached = json.load(f)
    fresh = cached and dt.datetime.now().timestamp() - os.path.getmtime(BLS_CACHE_PATH) < 86400
    if not fresh:
        try:
            r = requests.get(BLS_URL, timeout=30)
            r.raise_for_status()
            body = r.json()
            if body.get('status') != 'REQUEST_SUCCEEDED':
                raise ValueError(body.get('message'))
            cached = body['Results']['series'][0]['data']
            with open(BLS_CACHE_PATH, 'w', encoding='utf-8') as f:
                json.dump(cached, f)
        except Exception as e:
            print(f'Error fetching BLS unemployment data: {e}')
    rows = [(pd.Timestamp(f"{d['year']}-{d['period'][1:]}-01"), float(d['value']))
            for d in cached or []
            if d['period'].startswith('M') and d['period'] != 'M13' and d['value'] != '-']  # '-' = not published
    return pd.Series(dict(rows), dtype=float).sort_index()


# Two lines on two axes: the US unemployment rate vs Balloon Race's daily peak players
def generate_unemployment_plot(df, unemployment, variant=ALL_SERVERS):
    daily_peak = df.groupby(df['ts'].dt.date)['online_players'].max()
    daily_peak.index = pd.to_datetime(daily_peak.index)
    end = daily_peak.index.max() if len(daily_peak) else pd.Timestamp.now()
    rate = unemployment[unemployment.index >= end - pd.DateOffset(months=12)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=rate.index, y=rate.values, name='US unemployment rate', mode='lines+markers',
        line=dict(color=SERIES[0], width=2, shape='spline'), marker=dict(size=8),
    ))
    fig.add_trace(go.Scatter(
        x=daily_peak.index, y=daily_peak.values, name='Balloon Race daily peak players', yaxis='y2',
        mode='lines+markers', line=dict(color=SERIES[1], width=2, shape='spline'), marker=dict(size=8),
    ))
    # Direct labels at the end of each line, in text ink
    for series, axis, label in [(rate, 'y', f"Unemployment {rate.iloc[-1]:.1f}%" if len(rate) else None),
                                (daily_peak, 'y2', f"Players {daily_peak.iloc[-1]}" if len(daily_peak) else None)]:
        if label:
            fig.add_annotation(x=series.index[-1], y=series.iloc[-1], yref=axis, text=f"<b>{label}</b>",
                               showarrow=False, xanchor='left', xshift=10, font=dict(color=TEXT_PRIMARY))
    fig.update_layout(base_layout(
        'Unemployment vs Balloon Race players',
        'US unemployment rate (BLS, monthly, seasonally adjusted) · most Balloon Race players seen at once each day',
        variant=variant, showlegend=True,
        legend=dict(orientation='h', x=0, y=1.02, yanchor='bottom'),
        margin=dict(l=24, r=24, t=130, b=48),
        yaxis=dict(title=dict(text='Unemployment rate', font=dict(color=TEXT_PRIMARY)), ticksuffix='%',
                   tickformat='.1f', dtick=0.1, showgrid=True, gridcolor=GRID, zeroline=False),
        yaxis2=dict(title=dict(text='Daily peak players', font=dict(color=TEXT_PRIMARY)), overlaying='y',
                    side='right', showgrid=False, zeroline=False, tickformat='d', tickmode='auto',
                    range=[0, max(float(daily_peak.max()) if len(daily_peak) else 0, 1) * 1.1]),
        xaxis=dict(showgrid=False, ticks='', tickformat='%b %Y'),
    ))
    if not len(rate) and not len(daily_peak):
        empty_state(fig)
    save(fig, 'unemployment.png', variant)


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
def generate_holiday_table(df, variant=ALL_SERVERS):
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
        height=max(600, 200 + 38 * len(rows)),  # at least as tall as the chart beside it on the site
        margin=dict(l=24, r=24, t=130, b=16),
        variant=variant,
    ))
    fig.update_layout(title=dict(y=1, yanchor='top', pad=dict(t=24)))
    if not rows:
        empty_state(fig, 'No holidays in the data yet')
    save(fig, 'holidays.png', variant)


# Rank servers by how often they have people on, with each one's best times
def top_server_stats(df, server_df):
    stats = []
    for addr, rows in server_df.groupby('addr'):
        # Only count checks since the server was first seen, so new servers aren't penalized
        checks = df[df['ts'] >= rows['ts'].min()]
        active_ts = set(rows.loc[rows['players'] > 0, 'ts'])
        busy_ts = set(rows.loc[rows['players'] >= BUSY_THRESHOLD, 'ts'])
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
            'busy': checks['ts'].isin(busy_ts),
        })
    stats.sort(key=lambda s: s['pct_active'], reverse=True)
    return stats


# Small multiples: one day x hour heatmap per top server, on a shared scale
def generate_server_heatmaps(stats, variant=ALL_SERVERS):
    # Pick the servers that most often have a busy lobby, not just someone on
    stats = sorted((s for s in stats if s['busy'].any()), key=lambda s: s['busy'].mean(), reverse=True)
    stats = stats[:TOP_SERVER_COUNT]
    if not stats:
        fig = go.Figure()
        fig.update_layout(base_layout('When the top servers are busy', height=300, variant=variant))
        empty_state(fig, 'No busy lobbies seen yet')
        save(fig, 'server_heatmaps.png', variant)
        return

    grids = []
    for s in stats:
        grids.append(
            (s['busy'] * 100).groupby([s['checks']['day_of_week'], s['checks']['hour']], observed=False)
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
        f'% of checks each server had {BUSY_THRESHOLD}+ human players · times in {TZ_LABEL}',
        height=170 + 260 * len(stats),
        variant=variant,
    ))
    fig.update_annotations(font=dict(size=14, color=TEXT_PRIMARY), xanchor='left', x=0)
    fig.update_xaxes(showgrid=False, ticks='', tickfont=dict(size=10), tickangle=-45)
    fig.update_yaxes(showgrid=False, ticks='')
    fig.update_layout(margin=dict(l=24, r=32, t=130, b=40))
    save(fig, 'server_heatmaps.png', variant)


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


# Top servers for each timezone and filter on the website
def write_site_data(stats_by_zone, forecast):
    data = {'updated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'top_servers': {},
            'forecast': forecast}
    for (zone_key, key), (stats, label) in stats_by_zone.items():
        data['top_servers'].setdefault(zone_key, {})[key] = [{
            'name': s['name'],
            'addr': s['addr'],
            'best_time': f"{window_label(s['best_window'], short=True)} {label}",
            'best_time_pct': round(float(s['best_window_pct']), 3),
            'best_day': s['best_day'],
            'pct_active': round(float(s['pct_active']), 3),
            'avg_players': round(float(s['avg_players']), 1),
            'peak': int(s['peak']),
        } for s in stats]
    with open(os.path.join(OUTPUT_FOLDER, 'site_data.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


# "Will a lobby form?" model: chance some server hits BUSY_THRESHOLD+ players within the next hour,
# from the busiest server's count right now and the time of week (in the default zone).
# The site gets the model's answer for every hour of the week x player count, so any model can go here.
FORECAST_MINUTES = 60
FORECAST_MAX_PLAYERS = 20  # counts above this are looked up as this


def forecast_features(players, hour, weekend):
    angle = 2 * np.pi * np.asarray(hour, dtype=float) / 24
    players = np.asarray(players, dtype=float)
    return np.column_stack([np.log1p(players), (players >= BUSY_THRESHOLD).astype(float),
                            np.sin(angle), np.cos(angle), np.sin(2 * angle), np.cos(2 * angle),
                            np.asarray(weekend, dtype=float)])


def forecast_examples(df):
    d = df.sort_values('ts').reset_index(drop=True)
    ts, busy = d['ts'].values, (d['online_players'] >= BUSY_THRESHOLD).values
    horizon = np.timedelta64(FORECAST_MINUTES, 'm')
    labels, keep = [], []
    for i in range(len(d)):
        end = np.searchsorted(ts, ts[i] + horizon, side='right')
        # Skip checks whose next hour is mostly missing (end of the log, or the tracker was down)
        if end - i - 1 < FORECAST_MINUTES // CHECK_MINUTES * 0.75:
            continue
        labels.append(busy[i + 1:end].any())
        keep.append(i)
    d = d.loc[keep]
    hour = d['ts'].dt.hour + d['ts'].dt.minute / 60
    return forecast_features(d['online_players'], hour, d['ts'].dt.dayofweek >= 5), np.array(labels, dtype=int)


def new_forecast_model():
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.5))


def train_lobby_forecast(df):
    X, y = forecast_examples(df)
    info = {'model': 'Logistic regression', 'horizon_minutes': FORECAST_MINUTES, 'threshold': BUSY_THRESHOLD, 'timezone': ZONES[0][0],
            'days': days_of_data(df), 'examples': int(len(y)), 'positives': int(y.sum())}
    if len(y) < 100 or y.sum() < 3 or y.sum() == len(y):
        print('Lobby forecast: not enough data to train')
        return dict(info, ready=False)
    # Score on the most recent quarter, trained on the rest, against just guessing the base rate
    cut = int(len(y) * 0.75)
    if len(set(y[:cut])) == 2:
        model = new_forecast_model().fit(X[:cut], y[:cut])
        test, probs, guess = y[cut:], model.predict_proba(X[cut:])[:, 1], np.full(len(y) - cut, y[:cut].mean())
        score = log_loss(test, probs, labels=[0, 1])
        baseline = log_loss(test, guess, labels=[0, 1])
        info.update(test_share=0.25, test_examples=int(len(test)),
                    test_log_loss=round(score, 4), baseline_log_loss=round(baseline, 4),
                    test_brier=round(float(brier_score_loss(test, probs)), 4),
                    baseline_brier=round(float(brier_score_loss(test, guess)), 4))
        # AUC needs both outcomes in the test set
        if len(set(test)) == 2:
            info['test_auc'] = round(float(roc_auc_score(test, probs)), 3)
        print(f'Lobby forecast holdout log loss: {score:.3f} (base rate guess: {baseline:.3f})')
    model = new_forecast_model().fit(X, y)
    # grid[hour_of_week][players], hour_of_week 0 = Monday 12 AM, scored at the middle of each hour
    hours = np.repeat(np.arange(168), FORECAST_MAX_PLAYERS + 1)
    players = np.tile(np.arange(FORECAST_MAX_PLAYERS + 1), 168)
    probs = model.predict_proba(forecast_features(players, hours % 24 + 0.5, hours // 24 >= 5))[:, 1]
    grid = probs.reshape(168, FORECAST_MAX_PLAYERS + 1).round(3).tolist()
    return dict(info, ready=True, max_players=FORECAST_MAX_PLAYERS, grid=grid)


# Push the log and README to main, and the website (page + charts) to its own branch
def push_to_github():
    os.chdir(SCRIPT_DIR)
    os.system("git add README.md tf2_balloon_log.txt")
    os.system("git commit -m 'update log'")
    os.system("git push origin main")
    publish_site()


# GitHub Pages serves SITE_BRANCH, which is replaced by a single fresh commit each day so the
# ~16MB of charts never pile up in the repo history
SITE_BRANCH = 'site'
SITE_FILES = ['index.html', '.nojekyll', 'assets', 'charts']


def publish_site():
    # A throwaway index, so the main branch's staging area is never touched
    env = dict(os.environ, GIT_INDEX_FILE=os.path.join(SCRIPT_DIR, '.git', 'site-index'))

    def git(*args):
        return subprocess.run(['git', *args], cwd=SCRIPT_DIR, env=env, capture_output=True,
                              text=True, check=True).stdout.strip()

    try:
        git('read-tree', '--empty')
        git('add', '-f', *SITE_FILES)
        commit = git('commit-tree', git('write-tree'), '-m', 'update site')
        git('push', '-fq', 'origin', f'{commit}:refs/heads/{SITE_BRANCH}')
    except subprocess.CalledProcessError as e:
        print(f'Error publishing site: {e.stderr}')
    finally:
        if os.path.exists(env['GIT_INDEX_FILE']):
            os.remove(env['GIT_INDEX_FILE'])


# Main workflow
if __name__ == "__main__":
    # Optional: generate_report.py [log_path [output_folder]] [--no-push] [--no-readme]
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    log_path = args[0] if args else LOG_PATH
    if len(args) > 1:
        OUTPUT_FOLDER = args[1]
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    graph_df, server_df = load_and_preprocess_data(log_path)
    unemployment = load_unemployment()
    stats_by_zone = {}
    for DISPLAY_TIMEZONE, TZ_LABEL, ZONE_SUFFIX in ZONES:
        default_zone = ZONE_SUFFIX == ZONES[0][2]
        zone_df, zone_servers = in_zone(graph_df), in_zone(server_df)
        variants = [
            (ALL_SERVERS, zone_df, zone_servers),
            (DEDICATED, split_checks(zone_df, zone_servers, True), zone_servers[zone_servers['dedicated']]),
            (ROTATING, split_checks(zone_df, zone_servers, False), zone_servers[~zone_servers['dedicated']]),
        ]
        for variant, df, servers in variants:
            generate_scorecard(df, variant)
            generate_top_windows_plot(df, variant)
            generate_heatmap(
                df, df['online_players'] >= BUSY_THRESHOLD,
                f'How often there is a busy lobby ({BUSY_THRESHOLD}+ players)',
                f'% of 5-minute checks with a server at {BUSY_THRESHOLD}+ human players · times in {TZ_LABEL}',
                'busy_heatmap.png', variant,
            )
            all_server_stats = top_server_stats(zone_df, servers)
            server_stats = all_server_stats[:TOP_SERVER_COUNT]
            generate_server_heatmaps(all_server_stats, variant)
            stats_by_zone[(TZ_LABEL.lower(), variant[0].lstrip('_') or 'all')] = (server_stats, TZ_LABEL)
            # These don't depend on the time of day, so they're only drawn once
            if default_zone:
                generate_session_length_plot(df, variant)
                generate_weekly_trend_plot(df, variant)
                generate_top_servers_plot(df, variant)
                generate_monthly_avg_plot(df, variant)
                generate_monthly_daily_avg_plot(df, variant)
                generate_holiday_table(df, variant)
                generate_unemployment_plot(df, unemployment, variant)
    DISPLAY_TIMEZONE, TZ_LABEL, ZONE_SUFFIX = ZONES[0]
    stats = stats_by_zone[(TZ_LABEL.lower(), 'all')][0]
    write_site_data(stats_by_zone, train_lobby_forecast(graph_df))
    if '--no-readme' not in sys.argv:
        update_readme(stats)
    if '--no-push' not in sys.argv:
        push_to_github()
