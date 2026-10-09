import plotly.graph_objects as go
import pandas as pd
import re
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, "charts")
LOG_PATH = os.path.join(SCRIPT_DIR, 'tf2_balloon_log.txt')

# Log timestamps are written in the server's clock (UTC); charts are shown in this zone
LOG_TIMEZONE = 'UTC'
DISPLAY_TIMEZONE = 'America/Denver'
TZ_LABEL = 'MT'

# Scorecard needs at least this many days of checks before it names a winner
MIN_DAYS_FOR_SCORECARD = 7

DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

# Colors (validated dataviz reference palette)
SURFACE = '#fcfcfb'
TEXT_PRIMARY = '#0b0b0b'
TEXT_SECONDARY = '#52514e'
GRID = '#e6e5e0'
BAR = '#2a78d6'
SEQUENTIAL = ['#f3f2ef', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']
FONT = 'Inter, "Helvetica Neue", Helvetica, Arial, sans-serif'

LINE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - INFO - (.*)$')
PLAYERS_RE = re.compile(r'^(.*) : (\d+)$')


def hour_label(h):
    return f"{h % 12 or 12} {'AM' if h % 24 < 12 else 'PM'}"


# Load and preprocess data: one row per 5-minute check
def load_and_preprocess_data(file_path):
    rows = []
    with open(file_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = LINE_RE.match(line.rstrip('\n'))
            if not m:
                continue
            ts, msg = m.groups()
            if msg.startswith('No one playing'):
                rows.append({'ts': ts, 'server_name': None, 'online_players': 0})
            elif msg.startswith('Found people playing'):
                rows.append({'ts': ts, 'server_name': None, 'online_players': 0})
            elif rows and rows[-1]['ts'] == ts and (p := PLAYERS_RE.match(msg)):
                rows[-1]['server_name'] = p.group(1).strip()
                rows[-1]['online_players'] = int(p.group(2))

    df = pd.DataFrame(rows, columns=['ts', 'server_name', 'online_players'])
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
    )
    layout.update(kwargs)
    return layout


def save(fig, name):
    fig.write_image(os.path.join(OUTPUT_FOLDER, name), scale=2)


# Big scorecard: the single 2-hour window of the week most likely to have players
def generate_scorecard(df):
    days_of_data = (df['ts'].max() - df['ts'].min()).days if len(df) else 0
    rate = (
        df.assign(active=df['online_players'] > 0)
        .groupby('hour_of_week')['active']
        .agg(['sum', 'count'])
        .reindex(range(168), fill_value=0)
    )
    # 2-hour windows starting on every hour, wrapping Sunday night into Monday
    nxt = rate.shift(-1)
    nxt.iloc[-1] = rate.iloc[0]
    window = (rate + nxt)
    window['pct'] = window['sum'] / window['count'].where(window['count'] > 0)

    fig = go.Figure()
    fig.update_layout(base_layout('', height=360), xaxis=dict(visible=False), yaxis=dict(visible=False))
    fig.update_layout(title=None, margin=dict(l=0, r=0, t=0, b=0))

    def text(y, s, size, color, bold=False):
        fig.add_annotation(
            x=0.5, y=y, xref='paper', yref='paper', showarrow=False,
            text=f"<b>{s}</b>" if bold else s, font=dict(size=size, color=color, family=FONT),
        )

    text(0.86, 'BEST TIME TO FIND PEOPLE ON BALLOON RACE', 16, TEXT_SECONDARY, bold=True)
    if days_of_data < MIN_DAYS_FOR_SCORECARD or window['sum'].max() == 0:
        text(0.55, 'Not enough data yet', 56, TEXT_PRIMARY, bold=True)
        text(0.28, f'{days_of_data} of {MIN_DAYS_FOR_SCORECARD} days of checks collected', 18, TEXT_SECONDARY)
    else:
        best = int(window['pct'].idxmax())
        day, start = DAYS[best // 24], best % 24
        pct = window.loc[best, 'pct']
        text(0.58, f"{day} · {hour_label(start)} – {hour_label(start + 2)} {TZ_LABEL}", 60, TEXT_PRIMARY, bold=True)
        text(0.32, f"People were online in <b>{pct:.0%}</b> of checks during this window", 20, TEXT_SECONDARY)
        text(0.18, f"Based on {int(window.loc[best, 'count'])} checks over {days_of_data} days", 14, TEXT_SECONDARY)
    save(fig, 'best_time.png')


# Generate heatmap: how often someone was on, by day and hour
def generate_heatmap(df):
    heatmap_data = (
        df.assign(active=(df['online_players'] > 0) * 100)
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
        colorscale=[[i / (len(SEQUENTIAL) - 1), c] for i, c in enumerate(SEQUENTIAL)],
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

    fig.update_layout(base_layout(
        'How often people are on Balloon Race',
        f'% of 5-minute checks with at least one player · times in {TZ_LABEL} · blank = never seen',
        height=820,
    ))
    fig.update_yaxes(autorange='reversed', showgrid=False, ticks='')
    fig.update_xaxes(side='top', showgrid=False, ticks='')
    fig.update_layout(margin=dict(l=24, r=32, t=130, b=24))
    save(fig, 'heatmap.png')


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
        fig.add_annotation(x=0.5, y=0.5, xref='paper', yref='paper', showarrow=False,
                           text='No players seen yet', font=dict(size=20, color=TEXT_SECONDARY))
        fig.update_yaxes(visible=False)
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


# Push updates to GitHub
def push_to_github():
    os.chdir(SCRIPT_DIR)
    os.system("git add charts")
    os.system("git commit -m 'update charts'")
    os.system("git push origin main")


# Main workflow
if __name__ == "__main__":
    # Optional: generate_report.py [log_path [output_folder]] [--no-push]
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    log_path = args[0] if args else LOG_PATH
    if len(args) > 1:
        OUTPUT_FOLDER = args[1]
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    graph_df = load_and_preprocess_data(log_path)
    generate_scorecard(graph_df)
    generate_heatmap(graph_df)
    generate_top_servers_plot(graph_df)
    generate_monthly_avg_plot(graph_df)
    if '--no-push' not in sys.argv:
        push_to_github()
