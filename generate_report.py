import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import os


# Define output folder
OUTPUT_FOLDER = "charts"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Load and preprocess data
def load_and_preprocess_data(file_path):
    df = pd.read_csv(file_path, header=None)
    df['date'] = pd.to_datetime(df[0].str.slice(0, 10))
    df['day_of_week'] = df['date'].dt.day_name()
    df['hour'] = df[0].str.slice(11, 13).astype(int).apply(
        lambda x: f"{x % 12 if x % 12 != 0 else 12}{' AM' if x < 12 else ' PM'}"
    )
    df['month'] = df['date'].dt.month_name()
    df['online_players'] = (
        df[0].str.extract(r':\s*(\d+)$', expand=False)
        .fillna(0)
        .astype(int)
    )
    df['server_name'] = (
        df.query('online_players > 0')[0]
        .str.extract(r'- INFO - ([^:]+) :')
        .fillna(0)
    )
    return df.iloc[:, 1:]  # Return processed DataFrame


# Generate heatmap
def generate_heatmap(df):
    heatmap_data = df.pivot_table(
        index='hour',
        columns='day_of_week',
        values='online_players',
        aggfunc='max',
        fill_value=0
    ).iloc[::-1]  # Reverse the hour index

    plt.figure(figsize=(10, 6))
    sns.heatmap(
        heatmap_data,
        cmap="YlGnBu",
        annot=True,
        fmt="d",
        cbar_kws={'label': 'Online Count'},
        mask=heatmap_data == 1
    )

    # Remove 0 annotations
    for text in plt.gca().texts:
        if text.get_text() == "0":
            text.set_text("")

    plt.title('Max Balloon Race Online Players by Day of Week and Hour')
    plt.ylabel('Hour of Day')
    plt.xlabel('Day of Week')
    plt.tight_layout()
    plt.savefig(f"{OUTPUT_FOLDER}/heatmap.png", dpi=300)


# Generate bar plot for top servers
def generate_top_servers_plot(df):
    top_servers = (
        df.query('online_players > 0')
        .groupby('server_name')['online_players']
        .max()
        .sort_values(ascending=False)
        .reset_index()
        .head(10)
        .iloc[::-1]
    )

    top_servers.plot(
        x='server_name',
        y='online_players',
        kind='barh',
        figsize=(10, 6)
    )

    plt.ylabel('People Online')
    plt.xlabel('Server Name')
    plt.title('Max Balloon Race Online Players by Server')
    plt.tight_layout()
    plt.savefig(f"{OUTPUT_FOLDER}/top_servers.png", dpi=300, bbox_inches='tight')


# Generate bar plot for monthly averages
def generate_monthly_avg_plot(df):
    monthly_avg = (
        df.groupby('month')['online_players']
        .max()
        .reset_index(name='online_players')
    )

    monthly_avg.plot(
        x='month',
        y='online_players',
        kind='bar',
        figsize=(10, 6)
    )

    plt.xticks(rotation=45)
    plt.ylabel('People Online')
    plt.xlabel('Month')
    plt.title('Max People On Balloon Race by Month')
    plt.tight_layout()
    plt.savefig(f"{OUTPUT_FOLDER}/monthly_avg.png", dpi=300, bbox_inches='tight')


# Push updates to GitHub
def push_to_github():
    os.system("git add .")
    os.system("git commit -m 'update'")
    os.system("git push origin main")


# Main workflow
if __name__ == "__main__":
    graph_df = load_and_preprocess_data('tf2_balloon_log.txt')
    generate_heatmap(graph_df)
    generate_top_servers_plot(graph_df)
    generate_monthly_avg_plot(graph_df)
    push_to_github()


# cron job runs this "generate_reprort.py" script everyday which 
# 1. pushes to github the latest data at the end of the day with the updated README.md



