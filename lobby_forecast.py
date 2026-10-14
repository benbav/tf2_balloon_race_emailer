import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# "Will a lobby form?" model: chance some server hits `threshold`+ players within the next hour,
# from the busiest server right now (who and how many), the time of week and whether it's a holiday
# (in the zone df's timestamps are in).
# The site gets the model's answer for every holiday flag x server x hour of the week x player count,
# so any model can go here.
FORECAST_MINUTES = 60
FORECAST_MAX_PLAYERS = 20  # counts above this are looked up as this
# Servers that were the busiest most often get their own feature; the rest share one "other server" bucket
FORECAST_SERVERS = 6
FORECAST_MIN_SERVER_CHECKS = 20
# A feature that only a day or two of the log can teach just memorizes those days, so day of week and
# holidays are left out until there's enough of each: until then it's weekend vs weekday, and no holiday flag
FORECAST_MIN_DATES_PER_WEEKDAY = 2
FORECAST_MIN_HOLIDAY_DATES = 3


def forecast_features(players, hour, day, server, holiday, threshold, servers, use_days, use_holidays):
    angle = 2 * np.pi * np.asarray(hour, dtype=float) / 24
    players = np.asarray(players, dtype=float)
    day, server = np.asarray(day), np.asarray(server, dtype=object)
    # Nobody on means no busiest server, so it falls in the "other" bucket (all zeros)
    on = players > 0
    days = [(day == d).astype(float) for d in range(7)] if use_days else [(day >= 5).astype(float)]
    holidays = [np.asarray(holiday, dtype=float)] if use_holidays else []
    return np.column_stack([np.log1p(players), (players >= threshold).astype(float),
                            np.sin(angle), np.cos(angle), np.sin(2 * angle), np.cos(2 * angle),
                            *days, *[((server == s) & on).astype(float) for s in servers], *holidays])


# Address of the busiest server at each check. Older checks were logged before per-server lines
# existed, so their address is guessed from the server's name.
def busiest_addrs(df, server_df):
    top = server_df.sort_values('players').groupby('ts').last()['addr']
    by_name = server_df.groupby('name')['addr'].agg(lambda a: a.mode().iloc[0])
    return df['ts'].map(top).fillna(df['server_name'].map(by_name))


def pick_servers(addrs, players):
    counts = addrs[players > 0].value_counts()
    return counts[counts >= FORECAST_MIN_SERVER_CHECKS].index[:FORECAST_SERVERS].tolist()


def forecast_examples(df, servers, holiday_dates, threshold, check_minutes, use_days, use_holidays):
    d = df.sort_values('ts').reset_index(drop=True)
    ts, busy = d['ts'].values, (d['online_players'] >= threshold).values
    horizon = np.timedelta64(FORECAST_MINUTES, 'm')
    labels, keep = [], []
    for i in range(len(d)):
        end = np.searchsorted(ts, ts[i] + horizon, side='right')
        # Skip checks whose next hour is mostly missing (end of the log, or the tracker was down)
        if end - i - 1 < FORECAST_MINUTES // check_minutes * 0.75:
            continue
        labels.append(busy[i + 1:end].any())
        keep.append(i)
    d = d.loc[keep]
    hour = d['ts'].dt.hour + d['ts'].dt.minute / 60
    holiday = d['ts'].dt.date.isin(holiday_dates)
    X = forecast_features(d['online_players'], hour, d['ts'].dt.dayofweek, d['addr'], holiday, threshold, servers,
                          use_days, use_holidays)
    return X, np.array(labels, dtype=int)


def new_forecast_model():
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.5))


def train_lobby_forecast(df, server_df, holiday_dates, threshold, check_minutes, timezone):
    df = df.assign(addr=busiest_addrs(df, server_df))
    servers = pick_servers(df['addr'], df['online_players'])
    dates = df['ts'].dt.date.drop_duplicates()
    use_days = len(dates) and pd.to_datetime(dates).dt.dayofweek.value_counts().reindex(range(7), fill_value=0).min() \
        >= FORECAST_MIN_DATES_PER_WEEKDAY
    use_holidays = dates.isin(holiday_dates).sum() >= FORECAST_MIN_HOLIDAY_DATES
    X, y = forecast_examples(df, servers, holiday_dates, threshold, check_minutes, use_days, use_holidays)
    days = (df['ts'].max() - df['ts'].min()).days if len(df) else 0
    info = {'model': 'Logistic regression', 'horizon_minutes': FORECAST_MINUTES, 'threshold': threshold, 'timezone': timezone,
            'days': days, 'examples': int(len(y)), 'positives': int(y.sum()),
            'uses_day_of_week': bool(use_days), 'uses_holidays': bool(use_holidays)}
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
            auc = float(roc_auc_score(test, probs))
            info.update(test_auc=round(auc, 3), test_gini=round(2 * auc - 1, 3))
        print(f'Lobby forecast holdout log loss: {score:.3f} (base rate guess: {baseline:.3f})')
    model = new_forecast_model().fit(X, y)
    # grid[holiday][server][hour_of_week][players]: holiday 0/1, server is an index into `servers`
    # (len(servers) = any other server, or nobody on), hour_of_week 0 = Monday 12 AM scored at the
    # middle of each hour
    shape = (2, len(servers) + 1, 168, FORECAST_MAX_PLAYERS + 1)
    holiday, server, hours, players = (a.ravel() for a in np.indices(shape))
    server = np.array(servers + [None], dtype=object)[server]
    X = forecast_features(players, hours % 24 + 0.5, hours // 24, server, holiday, threshold, servers,
                          use_days, use_holidays)
    grid = model.predict_proba(X)[:, 1].reshape(shape).round(3).tolist()
    return dict(info, ready=True, max_players=FORECAST_MAX_PLAYERS, servers=servers,
                holidays=sorted(d.isoformat() for d in holiday_dates), grid=grid)
