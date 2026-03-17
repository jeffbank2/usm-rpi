import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

gmail_user = os.environ["GMAIL_ADDRESS"]
gmail_pass = os.environ["GMAIL_APP_PASSWORD"]

try:
    with open("daily_brief.txt", "r") as f:
        brief = f.read()
except Exception:
    brief = "Daily RPI update complete. Check the dashboard."

msg = MIMEMultipart("alternative")
msg["Subject"] = "Southern Miss RPI Update"
msg["From"] = gmail_user
msg["To"] = "bankston.jeff@gmail.com"

body = f"""Southern Miss RPI Daily Update

{brief}

View full dashboard: https://jeffbank2.github.io/usm-rpi/

--
Southern Miss RPI Bot
"""

msg.attach(MIMEText(body, "plain"))

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(gmail_user, gmail_pass)
    server.sendmail(gmail_user, "bankston.jeff@gmail.com", msg.as_string())
    print("Email sent successfully")
