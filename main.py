from email.mime.multipart import MIMEMultipart
from datetime import datetime
import requests
import logging
import smtplib
import dotenv
import os

# Email send limit per day
EMAIL_SEND_LIMIT = 30
EMAIL_COUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'email_count.txt')

def get_server_list():
    api_key = os.getenv('steam_api_key')
    url = f'https://api.steampowered.com/IGameServersService/GetServerList/v1/?key={api_key}&limit=50&filter=\\appid\\440\\map\\balloon_race_v2b'
    r = requests.get(url)
    servers = r.json()['response']['servers']
    return {server['name']: server['players'] for server in servers}

def send_email(message):
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
        server.sendmail(email_username, os.getenv("to_email"), msg.as_string())

        # Close the SMTP connection
        server.quit()

        print("email sent")
    except Exception as e:
        logging.error(f'Error sending email: {e}')

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
    output_dict = get_server_list()
    if any(value > 0 for value in output_dict.values()):
        # Log server activity
        max_server = max(output_dict, key=output_dict.get)
        logging.info(f'Found people playing Balloon Race')
        logging.info(f'{max_server} : {output_dict[max_server]}')

        # Check if we can send more emails
        if count < EMAIL_SEND_LIMIT:
            logging.info('Sending email')
            message = f"PEOPLE ON BALLOON RACE - {max_server[:30]}... has {output_dict[max_server]} players"
            send_email(message)
            update_email_count(count + 1)
        else:
            logging.info(f"Email limit of {EMAIL_SEND_LIMIT} reached for today. No email will be sent.")
    else:
        logging.info('No one playing balloon race :(')

if __name__ == "__main__":
    main()
