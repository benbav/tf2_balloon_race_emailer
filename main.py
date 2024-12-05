from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import requests
import logging
import dotenv
import bs4
import json
import os

# scrape website to find when people are playing tf2 balloon race
# https://www.battlemetrics.com/servers/tf2?q=balloon&sort=score 

def scrape_battlemetrics():

    url = 'https://www.battlemetrics.com/servers/tf2?q=balloon&sort=players'

    r = requests.get(url)
    r.status_code

    soup = bs4.BeautifulSoup(r.text, 'html.parser')

    # Extract the JSON data
    script_tag = soup.find('script', id='storeBootstrap')
    json_data = json.loads(script_tag.string)

    # Navigate to the servers information
    servers = json_data['state']['servers']['servers']

    output_dict = {}

    for server_id, server_info in servers.items():
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
        pass

def main():

    dotenv.load_dotenv()
    r = scrape_battlemetrics()

    # if any values are greater than 0, send email
    if any(value > 0 for value in r.values()):
        # server with max players
        max_server = max(r, key=r.get)

        print("yes")
        print(max_server)
        print(r[max_server])

        # send email
        message = f"{max_server} has {r[max_server]} players"
        send_email(message)
        
    else:
        print("no")

if __name__ == "__main__":
    main()