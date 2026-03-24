# send_mail.py
# Handles sending emails with attachments and scheduling

import os
import time
import smtplib
import threading
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

GMAIL_USER = "awhproject2024@gmail.com"
GMAIL_PASS = "yrxzscumxhjecqeo"   # App Password


def send_email_with_attachments(recipients, filenames, sent_files=None,
                                subject="Data from AquaPars#2 @ CHP-PowerPlant",
                                body="Attached file is data from AquaPars @ CHP-PowerPlant",
                                max_retries=5, delay=5):
    """
    Sends an email with file attachments via Gmail.
    - recipients: list of email addresses
    - filenames: list of file paths
    - sent_files: optional set() to track which files already sent
    """
    if sent_files is not None:
        unsent_files = [f for f in filenames if f not in sent_files]
        if not unsent_files:
            print("No new files to send.")
            return False
    else:
        unsent_files = filenames

    print("Sending email...")

    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = ", ".join(recipients)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    for filename in unsent_files:
        with open(filename, "rb") as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition',
                            f'attachment; filename= {os.path.basename(filename)}')
            msg.attach(part)

    attempts = 0
    while attempts < max_retries:
        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, recipients, msg.as_string())
            server.quit()
            print("Email sent successfully")

            if sent_files is not None:
                sent_files.update(unsent_files)
            return True
        except smtplib.SMTPException as e:
            print(f"Failed to send email: {e}")
            attempts += 1
            if attempts < max_retries:
                print(f"Retrying in {delay} seconds... (Attempt {attempts}/{max_retries})")
                time.sleep(delay)
            else:
                print("Exceeded maximum retries. Email not sent.")
                return False


def schedule_email(recipients, filenames, time_str, sent_files=None):
    """
    Schedules email sending daily at a specific HH:MM.
    Runs in a background thread.
    """
    def _task():
        print(f"Scheduling email at {time_str} daily")
        schedule_time = datetime.strptime(time_str, "%H:%M").time()
        while True:
            now = datetime.now()
            current_time = now.time()
            if current_time >= schedule_time and (now - timedelta(minutes=1)).time() < schedule_time:
                print(f"Time to send scheduled email: {now.strftime('%H:%M')}")
                send_email_with_attachments(recipients, filenames, sent_files)
                # Wait until next day
                while datetime.now().time() >= schedule_time:
                    time.sleep(60)
            time.sleep(10)

    thread = threading.Thread(target=_task, daemon=True)
    thread.start()
    return thread
