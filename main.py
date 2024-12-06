from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
import logging
import smtplib
import logging
import dotenv
import os

# scrape website to find when people are playing tf2 balloon race
# https://www.battlemetrics.com/servers/tf2?q=balloon&sort=score 

def scrape_battlemetrics():

    api_key = os.getenv('steam_api_key')

    url = f'https://api.steampowered.com/IGameServersService/GetServerList/v1/?key={api_key}&limit=50&filter=\\appid\\440\\map\\balloon_race_v2b'

    r = requests.get(url)

    servers = r.json()['response']['servers']

    output_dict = {}

    # Iterate over each server and extract relevant information
    for server_info in servers:
        name = server_info['name']
        players = server_info['players']
        output_dict[name] = players
        return output_dict


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

        # logging.info("Email sent successfully.")
        print("email sent")
    except Exception as e:
        logging.error('error sending email: ', e)

script_dir = os.path.dirname(os.path.abspath(__file__))
log_file_path = os.path.join(script_dir, 'tf2_balloon_log.txt')

# Configure logging
logging.basicConfig(
    filename=log_file_path,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def main():

    
    dotenv.load_dotenv()
    output_dict = scrape_battlemetrics()

    # if any server has more than 0 players send email
    if any(value > 0 for value in output_dict.values()):
        logging.info('Found people playing Balloon Race - sending email')
        
        # Find the server with the maximum players
        max_server = max(output_dict, key=output_dict.get)
        logging.info(f'{max_server} : {output_dict[max_server]}')
        
        # Send email
        message = f"PEOPLE ON BALOON RACE - {max_server[:30]}... has {output_dict[max_server]} players"
        send_email(message)
        
    else:
        logging.info('no one playing balloon race :(')
        

if __name__ == "__main__":
    main()