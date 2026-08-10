"""Failure notification adapters."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from garmin_sheets_sync.ports import FailureContext

logger = logging.getLogger(__name__)


class LogAlertSink:
    def notify_failure(self, context: FailureContext) -> None:
        logger.error(
            "sync_failed source=%s destination=%s start=%s end=%s error_type=%s message=%s",
            context.source,
            context.destination,
            context.window.start,
            context.window.end,
            context.error_type,
            context.message,
        )


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    host: str
    port: int
    starttls: bool
    username: str | None
    password: str | None
    sender: str
    recipient: str


class SmtpAlertSink:
    def __init__(self, settings: SmtpSettings) -> None:
        self._settings = settings

    def notify_failure(self, context: FailureContext) -> None:
        message = EmailMessage()
        message["Subject"] = "Garmin Sheets sync failed"
        message["From"] = self._settings.sender
        message["To"] = self._settings.recipient
        message.set_content(
            "\n".join(
                (
                    "The Garmin to Google Sheets sync failed.",
                    f"Source: {context.source}",
                    f"Destination: {context.destination}",
                    f"Window: {context.window.start} through {context.window.end}",
                    f"Error: {context.error_type}: {context.message}",
                )
            )
        )
        with smtplib.SMTP(self._settings.host, self._settings.port, timeout=30) as client:
            if self._settings.starttls:
                client.starttls()
            if self._settings.username:
                client.login(self._settings.username, self._settings.password or "")
            client.send_message(message)
