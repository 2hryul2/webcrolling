"""Notification — email + JSONL file logging."""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from app.database import append_jsonl
from app.models import AlertLog, ExternalEvent

logger = logging.getLogger(__name__)


class Notifier:
    """Routes events to email (urgent/watch) and always logs to file."""

    def __init__(self, smtp_config: dict, alert_log_path: str) -> None:
        self.smtp_config = smtp_config or {}
        self.alert_log_path = alert_log_path

    def notify(self, event: ExternalEvent) -> AlertLog:
        """Main entry — send email if applicable and always log to file."""
        # File logging always happens
        file_log: AlertLog
        email_log: Optional[AlertLog] = None

        if event.severity in ("urgent", "watch"):
            email_log = self._send_email(event)

        # Always file-log
        file_log = self._log_to_file(
            event,
            status="sent",
            channel="file",
        )

        # If email was attempted, return its result; else file log.
        if email_log is not None:
            return email_log
        return file_log

    def _send_email(self, event: ExternalEvent) -> AlertLog:
        """Send email via SMTP. Returns AlertLog reflecting outcome."""
        recipient = self.smtp_config.get("alert_email") or ""
        smtp_server = self.smtp_config.get("server")
        smtp_port = self.smtp_config.get("port")
        smtp_user = self.smtp_config.get("user")
        smtp_password = self.smtp_config.get("password")

        # Graceful — missing config means we can't send.
        if not (smtp_server and smtp_port and smtp_user and recipient):
            logger.warning(
                "SMTP not configured, skipping email for event %s", event.external_id
            )
            log = AlertLog(
                event_id=event.external_id,
                channel="email",
                recipient=recipient or "(unset)",
                sent_at=datetime.now(timezone.utc),
                status="failed",
                error_message="SMTP not configured",
            )
            append_jsonl(self.alert_log_path, log)
            return log

        msg = EmailMessage()
        msg["Subject"] = f"[{event.severity.upper()}] {event.title}"
        msg["From"] = smtp_user
        msg["To"] = recipient
        body_lines = [
            f"Source: {event.source}",
            f"Severity: {event.severity}",
            f"Published: {event.published_at.isoformat()}",
            f"URL: {event.url}",
            "",
            event.summary or "(no summary)",
        ]
        if event.matched_keywords:
            body_lines.append("")
            body_lines.append("Matched keywords: " + ", ".join(event.matched_keywords))
        msg.set_content("\n".join(body_lines))

        try:
            with smtplib.SMTP(smtp_server, int(smtp_port), timeout=5) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls()
                except Exception:
                    pass
                if smtp_password:
                    smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
            log = AlertLog(
                event_id=event.external_id,
                channel="email",
                recipient=recipient,
                sent_at=datetime.now(timezone.utc),
                status="sent",
            )
        except Exception as exc:
            logger.warning("Email send failed for %s: %s", event.external_id, exc)
            log = AlertLog(
                event_id=event.external_id,
                channel="email",
                recipient=recipient,
                sent_at=datetime.now(timezone.utc),
                status="failed",
                error_message=str(exc),
            )

        append_jsonl(self.alert_log_path, log)
        return log

    def _log_to_file(
        self,
        event: ExternalEvent,
        status: str = "sent",
        channel: str = "file",
        error_message: Optional[str] = None,
    ) -> AlertLog:
        """Append an AlertLog row to the JSONL alert log."""
        log = AlertLog(
            event_id=event.external_id,
            channel=channel,  # type: ignore[arg-type]
            recipient=self.alert_log_path,
            sent_at=datetime.now(timezone.utc),
            status=status,  # type: ignore[arg-type]
            error_message=error_message,
        )
        ok = append_jsonl(self.alert_log_path, log)
        if not ok:
            log = log.model_copy(update={"status": "failed", "error_message": "append_jsonl failed"})
        return log
