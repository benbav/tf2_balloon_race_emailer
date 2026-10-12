from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from server_types import clean_name, is_dedicated
import subprocess
import requests
import logging
import smtplib
import dotenv
import json
import os

# Email send limit per day
EMAIL_SEND_LIMIT = 4
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EMAIL_COUNT_FILE = os.path.join(SCRIPT_DIR, 'email_count.txt')
# Branch the website reads "who's on now" from; force-pushed every check so it stays one commit
LIVE_BRANCH = 'live'
MIN_PLAYER_THRESHOLD = 10

def get_server_list():
    api_key = os.getenv('steam_api_key')
    url = f'https://api.steampowered.com/IGameServersService/GetServerList/v1/?key={api_key}&limit=50&filter=\\appid\\440\\map\\balloon_race_v2b'
    r = requests.get(url)
    servers = r.json()['response'].get('servers', [])
    # Human players only - some idle servers fill up with bots
    return [
        {'addr': s['addr'], 'name': s['name'], 'players': s['players'] - s.get('bots', 0),
         'max_players': s.get('max_players')}
        for s in servers
    ]

def send_email(message):
    to_emails = os.getenv("to_email").split(",")
    try:
        # Create an SMTP connection
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()

        # Login using App Password or secure credentials
        email_username = os.getenv("from_email")
        email_password = os.getenv("email_app_pass")  # Replace with your App Password
        server.login(email_username, email_password)

        msg = MIMEMultipart()
        msg["Subject"] = message
        for to_email in to_emails:
            server.sendmail(email_username, to_email, msg.as_string())

        # # Close the SMTP connection
        server.quit()

        print("email sent")
    except Exception as e:
        logging.error(f'Error sending email: {e}')

def publish_status(servers):
    """Push the current server list to the live branch without touching the working tree."""
    status = {
        'checked': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'servers': [dict(s, name=clean_name(s['name']), dedicated=is_dedicated(s['name'], s['addr'])) for s in servers],
    }

    def git(*args, stdin=None):
        return subprocess.run(['git', *args], cwd=SCRIPT_DIR, input=stdin, capture_output=True,
                              text=True, check=True, timeout=60).stdout.strip()

    try:
        blob = git('hash-object', '-w', '--stdin', stdin=json.dumps(status, ensure_ascii=False))
        tree = git('mktree', stdin=f'100644 blob {blob}\tstatus.json\n')
        commit = git('commit-tree', tree, '-m', 'live status')
        git('push', '-fq', 'origin', f'{commit}:refs/heads/{LIVE_BRANCH}')
    except (subprocess.SubprocessError, OSError) as e:
        # Printed to cron.log rather than logged, so failures don't clutter the activity log
        print(f'Error publishing live status: {getattr(e, "stderr", None) or e}')

def get_email_count():
    """Retrieve the email count and the date from the file."""
    try:
        with open(EMAIL_COUNT_FILE, 'r') as f:
            count, last_date = f.read().splitlines()
            return int(count), last_date
    except FileNotFoundError:
        return 0, ""

def update_email_count(count):
    """Update the email count in the file."""
    with open(EMAIL_COUNT_FILE, 'w') as f:
        f.write(f"{count}\n{datetime.now().strftime('%Y-%m-%d')}")

def reset_email_count_if_new_day():
    """Reset the email count if a new day starts."""
    current_date = datetime.now().strftime('%Y-%m-%d')
    count, last_date = get_email_count()

    if current_date != last_date:
        update_email_count(0)  # Reset the count if the date is different

def main():
    # Load environment variables
    dotenv.load_dotenv()

    # Set up logging
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_file_path = os.path.join(script_dir, 'tf2_balloon_log.txt')
    logging.basicConfig(
        filename=log_file_path,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Reset email count if a new day has started
    reset_email_count_if_new_day()

    # Retrieve the current email send count
    count, _ = get_email_count()

    # Get the server list and check for players
    servers = get_server_list()
    publish_status(servers)
    output_dict = {s['name']: s['players'] for s in servers}
    if any(value > 0 for value in output_dict.values()):
        # Log server activity
        max_server = max(output_dict, key=output_dict.get)
        logging.info(f'Found people playing Balloon Race')
        logging.info(f'{max_server} : {output_dict[max_server]}')
        # Log every active server so the report can rank them
        for s in servers:
            if s['players'] > 0:
                logging.info(f"SERVER {s['addr']} | {s['name']} : {s['players']}")

        # Check if we can send more emails
        if count < EMAIL_SEND_LIMIT and output_dict[max_server] > MIN_PLAYER_THRESHOLD:
            logging.info('Sending email')
            message = f"PEOPLE ON BALLOON RACE: {max_server[:30]}... has {output_dict[max_server]} players"
            send_email(message)
            update_email_count(count + 1)
        elif count >= EMAIL_SEND_LIMIT:
            logging.info(f"Email limit of {EMAIL_SEND_LIMIT} reached for today. No email will be sent.")
    else:
        logging.info('No one playing balloon race :(')

if __name__ == "__main__":
    main()


# cron to run every 5 minutes - can also integrate with fishstick bot?
# 