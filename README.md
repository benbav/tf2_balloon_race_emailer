### When Are People On Balloon Race?
#### No more randomly checking and hoping people are on - here is the cold hard data

##### Checks every 5 minutes, charts update every day (times in Mountain Time)

**Website with a live "who's on now" list and server filters: https://benbav.github.io/tf2_balloon_race_emailer/**

![Best Time](charts/best_time.png)

![Top 5 Times](charts/top_windows.png)

![Heatmap](charts/heatmap.png)

![Busy Lobby Heatmap](charts/busy_heatmap.png)

![Weekdays vs Weekends](charts/weekday_vs_weekend.png)

![Session Length](charts/session_length.png)

![Weekly Trend](charts/weekly_trend.png)

![Holidays](charts/holidays.png)

![Monthly Max](charts/monthly_avg.png)

![Monthly Avg Daily Peak](charts/monthly_daily_avg.png)

![Top Servers](charts/top_servers.png)

### Top 5 Balloon Race servers

Ranked by how often they have people on. Favorite these so they're one click away.

<!-- TOP_SERVERS_START -->
| # | Server | Connect (paste in TF2 console) | Best time | Best day | Has players | Avg players when on | Peak |
|---|---|---|---|---|---|---|---|
| 1 | Balloon Race 24/7 | `connect 45.88.229.19:27024` | Mon · 11 AM – 1 PM MT (100%) | Sunday | 55% of checks | 1.2 | 6 |
| 2 | [TR]  Türk Ninjalar sunucusu | `connect 88.233.138.82:27017` | Sun · 5 AM – 7 AM MT (100%) | Sunday | 3% of checks | 14.7 | 17 |
| 3 | Aar's Server \| Map Testing \| Virginia | `connect 169.254.112.56:12184` | Sat · 1 PM – 3 PM MT (100%) | Saturday | 1% of checks | 24.7 | 26 |
| 4 | shounic trenches (USA Chicago) | `connect 193.243.190.25:27015` | Fri · 5 PM – 7 PM MT (100%) | Friday | 1% of checks | 26.9 | 48 |
| 5 | [24/7 Funbox] Dυɱρʂƚҽɾ ɾαTʂ + ϝɾҽҽ Bυɱρz | `connect 91.230.199.49:27015` | Sat · 10 AM – 12 PM MT (44%) | Saturday | 1% of checks | 1.5 | 2 |

To favorite one: Steam → View → Game Servers → Favorites → **+** and paste the address.
<!-- TOP_SERVERS_END -->

![Server Heatmaps](charts/server_heatmaps.png)

### Dedicated 24/7 servers vs rotating servers

Dedicated servers run Balloon Race all day (e.g. "Balloon Race 24/7"). Rotating servers only land on it now and then as part of a map cycle, but that's often when the big lobbies happen.

<table>
<tr><th width="50%">Dedicated 24/7 servers</th><th width="50%">Rotating servers</th></tr>
<tr><td><img src="charts/best_time_dedicated.png" alt="Best Time - dedicated"></td><td><img src="charts/best_time_rotating.png" alt="Best Time - rotating"></td></tr>
<tr><td><img src="charts/top_windows_dedicated.png" alt="Top 5 Times - dedicated"></td><td><img src="charts/top_windows_rotating.png" alt="Top 5 Times - rotating"></td></tr>
<tr><td><img src="charts/heatmap_dedicated.png" alt="Heatmap - dedicated"></td><td><img src="charts/heatmap_rotating.png" alt="Heatmap - rotating"></td></tr>
<tr><td><img src="charts/busy_heatmap_dedicated.png" alt="Busy Lobby Heatmap - dedicated"></td><td><img src="charts/busy_heatmap_rotating.png" alt="Busy Lobby Heatmap - rotating"></td></tr>
<tr><td><img src="charts/weekday_vs_weekend_dedicated.png" alt="Weekdays vs Weekends - dedicated"></td><td><img src="charts/weekday_vs_weekend_rotating.png" alt="Weekdays vs Weekends - rotating"></td></tr>
<tr><td><img src="charts/session_length_dedicated.png" alt="Session Length - dedicated"></td><td><img src="charts/session_length_rotating.png" alt="Session Length - rotating"></td></tr>
<tr><td><img src="charts/weekly_trend_dedicated.png" alt="Weekly Trend - dedicated"></td><td><img src="charts/weekly_trend_rotating.png" alt="Weekly Trend - rotating"></td></tr>
<tr><td><img src="charts/holidays_dedicated.png" alt="Holidays - dedicated"></td><td><img src="charts/holidays_rotating.png" alt="Holidays - rotating"></td></tr>
<tr><td><img src="charts/monthly_avg_dedicated.png" alt="Monthly Max - dedicated"></td><td><img src="charts/monthly_avg_rotating.png" alt="Monthly Max - rotating"></td></tr>
<tr><td><img src="charts/monthly_daily_avg_dedicated.png" alt="Monthly Avg Daily Peak - dedicated"></td><td><img src="charts/monthly_daily_avg_rotating.png" alt="Monthly Avg Daily Peak - rotating"></td></tr>
<tr><td><img src="charts/top_servers_dedicated.png" alt="Top Servers - dedicated"></td><td><img src="charts/top_servers_rotating.png" alt="Top Servers - rotating"></td></tr>
<tr><td><img src="charts/server_heatmaps_dedicated.png" alt="Server Heatmaps - dedicated"></td><td><img src="charts/server_heatmaps_rotating.png" alt="Server Heatmaps - rotating"></td></tr>
</table>

---

### How it works

- `main.py` runs every 5 minutes. It asks the Steam API which TF2 servers are on `balloon_race_v2b`, logs every server with human players (bots excluded) to `tf2_balloon_log.txt`, and emails you when a server has more than `MIN_PLAYER_THRESHOLD` players (max `EMAIL_SEND_LIMIT` emails a day).
- `index.html` is the website, served free by GitHub Pages straight from `main`. It shows the charts in `charts/`, the top servers from `charts/site_data.json`, and the live server list.
- `generate_report.py` runs once a day. It turns the log into the charts above, rewrites the top servers table, and pushes the charts, README and log to GitHub. The log lives in the repo so the history is never lost.

### First-time setup

**1. Install**

```bash
git clone git@github.com:benbav/tf2_balloon_race_emailer.git
cd tf2_balloon_race_emailer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/plotly_get_chrome   # headless Chrome that plotly uses to save the chart PNGs
```

**2. Create a `.env` file** in the project folder (it's gitignored, never commit it):

```bash
# Steam Web API key
steam_api_key=

# Gmail account the alerts are sent FROM
from_email=

# Gmail App Password for from_email (not your normal password)
email_app_pass=

# Comma-separated recipients, no spaces: a@x.com,b@y.com
to_email=
```

Where to get each value:

| Key | Where to get it |
|---|---|
| `steam_api_key` | Sign in at https://steamcommunity.com/dev/apikey, enter any domain name (e.g. `localhost`), and copy the key. |
| `from_email` | Any Gmail address you want the alerts to come from. |
| `email_app_pass` | Turn on 2-Step Verification for that Google account, then create an app password at https://myaccount.google.com/apppasswords. Paste the 16 characters (spaces optional). |
| `to_email` | Whoever should get the alerts. Can be the same as `from_email`. |

**3. Test it**

```bash
.venv/bin/python main.py && tail tf2_balloon_log.txt
.venv/bin/python generate_report.py --no-push   # draws charts without pushing
```

**4. Schedule it** with `crontab -e` (cron runs in the server's clock, UTC here):

```cron
*/5 * * * * cd /path/to/tf2_balloon_race_emailer && .venv/bin/python main.py >> cron.log 2>&1
0 6 * * * cd /path/to/tf2_balloon_race_emailer && .venv/bin/python generate_report.py >> cron.log 2>&1
```

The daily report pushes to GitHub, so the repo remote needs to use SSH (`git remote set-url origin git@github.com:benbav/tf2_balloon_race_emailer.git`) with a key added to your GitHub account.

**5. Turn on the website**: on GitHub, go to **Settings → Pages → Build and deployment → Deploy from a branch**, pick `main` and `/ (root)`, and save. Free Pages needs a public repo. The site is live at `https://<user>.github.io/tf2_balloon_race_emailer/` a minute later.

### Settings

- `main.py`: `EMAIL_SEND_LIMIT`, `MIN_PLAYER_THRESHOLD`
- `server_types.py`: `DEDICATED_NAME_RE` / `DEDICATED_ADDRS` (which servers count as dedicated 24/7)
- `generate_report.py`: `DISPLAY_TIMEZONE` / `TZ_LABEL` (chart timezone), `LOG_TIMEZONE` (clock the log was written in), `MIN_DAYS_FOR_SCORECARD`, `SCORECARD_RECENT_DAYS`, `BUSY_THRESHOLD`, `TOP_SERVER_COUNT`
