import os
from twilio.rest import Client

account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token  = os.environ["TWILIO_AUTH_TOKEN"]
from_number = os.environ["TWILIO_PHONE_NUMBER"]

recipients = [
    "+12282641052",
]

client = Client(account_sid, auth_token)

try:
    with open("daily_brief.txt", "r") as f:
        brief = f.read()
    # Keep SMS concise - just the key stats
    lines = brief.split("\n")
    sms_lines = [l for l in lines if any(x in l for x in ["RPI Rank", "Record", "Host Outlook", "Improving", "Slipping", "Flat"])]
    sms_body = "\n".join(sms_lines[:6])
except Exception:
    sms_body = "Southern Miss RPI update complete."

message = f"""Southern Miss RPI Update
{sms_body}

Dashboard: https://jeffbank2.github.io/usm-rpi/"""

for number in recipients:
    client.messages.create(
        body=message,
        from_=from_number,
        to=number
    )
    print(f"SMS sent to {number}")
```

---

## File 2: Update `requirements.txt`

Edit your existing `requirements.txt` to add twilio:
```
requests
beautifulsoup4
openai
twilio
