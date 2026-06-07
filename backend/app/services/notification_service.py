import smtplib
from email.mime.text import MIMEText
import os


class NotificationService:
    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.sender = os.getenv("SMTP_SENDER")
        self.password = os.getenv("SMTP_PASSWORD")

    async def send_email(self, to: str, subject: str, body: str):
        msg = MIMEText(body, "html")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = to
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.sender, self.password)
            server.sendmail(self.sender, [to], msg.as_string())

    async def notify_candidate_match(self, candidate_email: str, company: str):
        await self.send_email(
            to=candidate_email,
            subject=f"New Job Match: {company}",
            body=f"<p>You matched a job at <b>{company}</b>. Login to view details.</p>",
        )
