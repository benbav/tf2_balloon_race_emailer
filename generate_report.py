import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np
import os

output_folder = "charts"
os.makedirs(output_folder, exist_ok=True)

# base data setup
df = pd.read_csv('tf2_balloon_log.txt', header=None)

df['date'] = df[0].str.slice(0, 10)
df['date'] = pd.to_datetime(df['date'])
df['day_of_week'] = df['date'].dt.day_name()
df['hour'] = df[0].str.slice(11, 13).astype(int)
df['month'] = df['date'].dt.month_name()
df['hour'] = df['hour'].apply(
    lambda x: f"{x % 12 if x % 12 != 0 else 12}{' AM' if x < 12 else ' PM'}"
)

df['online_players'] = (df[0]
    .str.extract(r':\s*(\d+)$', expand=False)
    .fillna(0)  # Replace NaN with 0 (or another default value)
    .astype(int)  # Convert to integer
)

df['server_name'] = df.query('online_players > 0')[0].str.extract(r'- INFO - ([^:]+) :').fillna(0)

graph_df = df.iloc[:, 1:]

# generate heatmap
# Pivot the data to have 'hour' as columns and 'day_of_week' as rows
heatmap_data = graph_df.pivot_table(
    index='hour',
    columns='day_of_week',
    values='online_players', 
    aggfunc='max',  # You can use sum, mean, or another aggregation function
    fill_value=0  # Fill missing values with 0 (or another value)
)

# Reverse the hour index
heatmap_data = heatmap_data.iloc[::-1]

# Create a mask where values are 0 (to make those cells white)
mask = heatmap_data == 1

# Plot the heatmap
plt.figure(figsize=(10, 6))
sns.heatmap(heatmap_data, cmap="YlGnBu", annot=True, fmt="d", cbar_kws={'label': 'Online Count'}, mask=mask)

# Replace 0 with empty strings in the annotations
for text in plt.gca().texts:
    if text.get_text() == "0":
        text.set_text("")  # Set empty string for 0 values

# Add labels and title
plt.title('Max Balloon Race Online Players by Day of Week and Hour')
plt.ylabel('Hour of Day')
plt.xlabel('Day of Week')
plt.tight_layout()

# save
plt.savefig(f"{output_folder}/heatmap.png")

# list top servers as well and what time and day people play on them

# bar graph of top server names by player count

plot = (
    graph_df.query('online_players > 0')
    .groupby(['server_name'])['online_players']
    .max()
    .sort_values(ascending=False)  # Sort values in descending order (largest at the top)
    .reset_index()
    .head(10)
    .iloc[::-1]  # Reverse the order to have the largest bar on top
    .plot(
        x='server_name', y='online_players', kind='barh'
    )
)
plt.xticks(rotation=90)
plt.ylabel('People Online') 
plt.xlabel('Server Name')

plt.title('Max Balloon Race Online Players by Server')
plt.tight_layout()

plt.savefig(f"{output_folder}/top_servers.png")




# save the image of the charts and tables we want



def push_to_github():
    os.system("git add .")
    os.system("git commit -m 'update'")
    os.system("git push origin main")


push_to_github()

# cron job runs this "main" script everyday which 
# 1. pushes to github the latest data at the end of the day with the updated README.md



