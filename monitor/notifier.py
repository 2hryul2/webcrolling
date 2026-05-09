"""Notification — email + JSONL file logging."""

from __future__ import annotations

import logging
import re
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from app.database import append_jsonl
from app.models import AlertLog, ExternalEvent

logger = logging.getLogger(__name__)

# SEC-2 / SEC-3 — redact password-shaped tokens before they land in JSONL.
_PASSWORD_REDACT_RE = re.compile(
    r"(?i)(password\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)


def _redact_password_substrings(text: Optional[str]) -> Optional[str]:
    """Strip password=... patterns from arbitrary text.

    Used on AlertLog.error_message before persistence so partial credential
    fragments echoed by SMTP never reach disk.
    """
    if not text:
        return text
    return _PASSWORD_REDACT_RE.sub(r"\1[REDACTED]", text)


class Notifier:
    """Routes events to email (urgent/watch) and always logs to file."""

    SMTP_TIMEOUT_SECONDS = 10  # NFR-4
    EMAIL_RETRY_BACKOFFS = (1, 2, 4)  # FR / 3.3.2

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
        """Send email via SMTP. Returns AlertLog reflecting outcome.

        - 10s timeout per attempt, 3 attempts with exponential backoff (1/2/4s).
        - STARTTLS fail-closed: never logs in over plaintext.
        - Logs only the exception class name; full string only inside AlertLog.
        - AlertLog.error_message is run through password redaction before persist.
        """
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

        msg = self._build_message(event, smtp_user, recipient)

        last_error: Optional[str] = None
        for attempt_idx, backoff in enumerate(
            self.EMAIL_RETRY_BACKOFFS, start=1
        ):
            try:
                self._send_one_attempt(
                    smtp_server,
                    int(smtp_port),
                    smtp_user,
                    smtp_password,
                    msg,
                    event=event,
                )
                # success
                log = AlertLog(
                    event_id=event.external_id,
                    channel="email",
                    recipient=recipient,
                    sent_at=datetime.now(timezone.utc),
                    status="sent",
                )
                append_jsonl(self.alert_log_path, log)
                return log
            except _StarttlsFailedError as exc:
                # Fail closed — do NOT retry over plaintext.
                logger.warning(
                    "STARTTLS failed for %s: %s",
                    event.external_id,
                    type(exc.original).__name__,
                )
                log = AlertLog(
                    event_id=event.external_id,
                    channel="email",
                    recipient=recipient,
                    sent_at=datetime.now(timezone.utc),
                    status="failed",
                    error_message="STARTTLS failed",
                )
                append_jsonl(self.alert_log_path, log)
                return log
            except Exception as exc:
                logger.warning(
                    "Email send failed for %s: %s",
                    event.external_id,
                    type(exc).__name__,
                )
                last_error = _redact_password_substrings(str(exc))
                if attempt_idx < len(self.EMAIL_RETRY_BACKOFFS):
                    time.sleep(backoff)
                    continue

        # All attempts exhausted.
        log = AlertLog(
            event_id=event.external_id,
            channel="email",
            recipient=recipient,
            sent_at=datetime.now(timezone.utc),
            status="failed",
            error_message=last_error or "send failed",
        )
        append_jsonl(self.alert_log_path, log)
        return log

    def _build_message(
        self, event: ExternalEvent, smtp_user: str, recipient: str
    ) -> EmailMessage:
        msg = EmailMessage()
        subject = f"[{event.severity.upper()}] {event.title}"
        # Edge D: cap subject length to ~100 chars (RFC 5322 recommendation).
        if len(subject) > 100:
            subject = subject[:97] + "..."
        msg["Subject"] = subject
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
        return msg

    def _send_one_attempt(
        self,
        smtp_server: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: Optional[str],
        msg: EmailMessage,
        event: ExternalEvent,
    ) -> None:
        """One SMTP transaction. Raises on any failure.

        Wraps STARTTLS failures in _StarttlsFailedError so the caller can
        distinguish "do not fall back to plaintext" from generic retryable
        network errors.
        """
        with smtplib.SMTP(smtp_server, smtp_port, timeout=self.SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.ehlo()
            try:
                smtp.starttls()
            except Exception as starttls_exc:
                # Fail closed — NEVER call smtp.login on a plaintext channel.
                try:
                    smtp.quit()
                except Exception:
                    pass
                raise _StarttlsFailedError(starttls_exc) from starttls_exc

            smtp.ehlo()
            if smtp_password:
                smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)

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
            error_message=_redact_password_substrings(error_message),
        )
        ok = append_jsonl(self.alert_log_path, log)
        if not ok:
            log = log.model_copy(update={"status": "failed", "error_message": "append_jsonl failed"})
        return log


class _StarttlsFailedError(Exception):
    """Raised when STARTTLS upgrade fails — no plaintext fallback allowed."""

    def __init__(self, original: BaseException) -> None:
        super().__init__("STARTTLS failed")
        self.original = original
