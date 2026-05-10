"""Watchtower NotifierService — instant / digest / owner-failure mail.

This module is independent from Step 1's :mod:`monitor.notifier` (which
handles keyword alerts via JSONL). The only shared idiom is "graceful skip
when SMTP is not configured" — credentials never leak into logs/DB; the
``alert_log`` table records the full audit trail (FR-NOTIF-005).

Design constraints (per ARCHITECT-BRIEF Step 4):

- **No external HTTP.** SMTP is the only outbound channel.
- **Stdlib only.** ``smtplib`` + ``email.mime`` + ``zoneinfo``.
- **Backoff schedule:** 60s / 300s / 900s on transient failures
  (FR-NOTIF-006). STARTTLS failures are fail-closed (no plaintext fallback).
- **Rate limit:** per-user 5min / 10 instant emails. The 11th instant email
  inside the window is collapsed into a single "묶음 (rolled-up)" mail
  (FR-NOTIF-007).
- **Suppress already-read items in digest** (FR-NOTIF-008).
- **No-recipient / no-subscribers** fast-path returns ``status='skipped'``
  with a meaningful detail.
"""

from __future__ import annotations

import logging
import re
import smtplib
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable, Iterable, Optional

try:  # zoneinfo is stdlib in 3.9+; fail soft if a CI variant lacks it.
    from zoneinfo import ZoneInfo
    _KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover - extremely defensive
    _KST = timezone(timedelta(hours=9))

from sqlalchemy import select

from app.db.models import (
    AlertLog,
    Category,
    Item,
    Site,
    Subscription,
    User,
)

logger = logging.getLogger(__name__)


# Retry schedule for transient SMTP failures (FR-NOTIF-006).
EMAIL_RETRY_BACKOFFS_SEC: tuple[int, int, int] = (60, 300, 900)
SMTP_TIMEOUT_SEC = 10  # NFR alignment with Step 1 notifier.

# Rate limit window — same FR-NOTIF-007 pattern as the spec.
RATE_LIMIT_WINDOW_SEC = 5 * 60
RATE_LIMIT_MAX = 10

# Limit ``error_message`` so credentials echoed by SMTP libraries can never
# overflow the column. AlertLog.error_message is String(500).
_ERROR_TRUNC = 480

# Strip ``password=...`` style fragments from any text we persist, mirroring
# the Step 1 notifier's redaction policy.
_PASSWORD_REDACT_RE = re.compile(
    r"(?i)(password\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)


def _redact(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    cleaned = _PASSWORD_REDACT_RE.sub(r"\1[REDACTED]", text)
    if len(cleaned) > _ERROR_TRUNC:
        cleaned = cleaned[: _ERROR_TRUNC - 1] + "…"
    return cleaned


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------


class _StarttlsFailedError(Exception):
    """Raised when STARTTLS upgrade fails — fail closed, do not retry over plaintext."""

    def __init__(self, original: BaseException) -> None:
        super().__init__("STARTTLS failed")
        self.original = original


# ---------------------------------------------------------------------------
# NotifierService
# ---------------------------------------------------------------------------


class NotifierService:
    """SMTP routing for instant + digest + owner-failure mails."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        smtp_config: dict | None,
        *,
        ui_base_url: str | None = None,
        sleep: Callable[[float], None] | None = None,
        smtp_factory: Callable[..., smtplib.SMTP] | None = None,
        backoffs: tuple[int, ...] = EMAIL_RETRY_BACKOFFS_SEC,
    ) -> None:
        self._session_factory = session_factory
        self._smtp = dict(smtp_config or {})
        self._ui_base = (ui_base_url or "http://localhost:8000").rstrip("/")
        self._sleep = sleep or time.sleep
        self._smtp_factory = smtp_factory or smtplib.SMTP
        self._backoffs = tuple(backoffs)

        # Per-user rate-limit buckets. Each entry is a deque of monotonic-ish
        # epoch seconds (UTC). Phase 1 is single-process so an in-memory
        # deque is sufficient (Decision §5).
        self._rl_lock = threading.Lock()
        self._rl_buckets: dict[str, deque[float]] = {}

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _smtp_ready(self) -> bool:
        """True iff every required SMTP field is non-empty."""
        return all(
            self._smtp.get(k)
            for k in ("server", "port", "user", "password")
        )

    def _from_email(self) -> str:
        return (
            self._smtp.get("from_email")
            or self._smtp.get("user")
            or "watchtower@localhost"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_instant(self, item_ids: Iterable[str]) -> dict[str, Any]:
        """Send instant alerts for the given items (FR-NOTIF-001/002).

        Each ``channel='instant'`` subscriber to the item's category receives
        one email per item, subject to a per-user rate limit. Every attempt
        — sent, failed, or skipped — produces an ``alert_log`` row.
        """
        item_ids = [iid for iid in item_ids if iid]
        if not item_ids:
            return {"sent": 0, "failed": 0, "skipped": 0, "rolled_up": 0}

        sent = failed = skipped = rolled_up = 0
        with self._session_factory() as session:
            # Resolve items + their site/category in one shot for efficiency.
            rows = session.execute(
                select(Item, Site, Category)
                .join(Site, Item.site_id == Site.id)
                .join(Category, Site.category_id == Category.id)
                .where(Item.id.in_(item_ids))
            ).all()
            if not rows:
                return {"sent": 0, "failed": 0, "skipped": 0, "rolled_up": 0}

            # Subscribers are recomputed per-category so we don't load every
            # subscription up front.
            cat_subscribers: dict[str, list[User]] = {}
            for _item, _site, cat in rows:
                if cat.id in cat_subscribers:
                    continue
                subs = session.execute(
                    select(User)
                    .join(Subscription, Subscription.user_id == User.id)
                    .where(
                        Subscription.category_id == cat.id,
                        Subscription.subscribed.is_(True),
                        Subscription.channel == "instant",
                    )
                ).scalars().all()
                cat_subscribers[cat.id] = list(subs)

            for item, site, cat in rows:
                subscribers = cat_subscribers.get(cat.id, [])
                if not subscribers:
                    continue

                for user in subscribers:
                    if not user.email:
                        self._log(
                            session,
                            user_id=user.id,
                            item_id=item.id,
                            channel="instant",
                            status="skipped",
                            error_message="user has no email",
                        )
                        skipped += 1
                        continue

                    # Rate limit (FR-NOTIF-007): 11th+ inside 5min → roll-up.
                    if not self._rate_limit_admit(user.id):
                        self._send_rolled_up_digest(
                            session, user, item, site, cat
                        )
                        rolled_up += 1
                        continue

                    if not self._smtp_ready():
                        self._log(
                            session,
                            user_id=user.id,
                            item_id=item.id,
                            channel="instant",
                            status="skipped",
                            error_message="SMTP not configured",
                        )
                        skipped += 1
                        continue

                    subject, html, text = self._build_instant_mail(
                        item, site, cat
                    )
                    ok, err = self._send_with_retry(user.email, subject, html, text)
                    self._log(
                        session,
                        user_id=user.id,
                        item_id=item.id,
                        channel="instant",
                        status="sent" if ok else "failed",
                        error_message=None if ok else err,
                    )
                    if ok:
                        sent += 1
                    else:
                        failed += 1

            session.commit()
        return {
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "rolled_up": rolled_up,
        }

    def send_digest(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Send the daily digest at the configured trigger time (FR-NOTIF-003).

        Items in scope:
            - detected_at within the last 24h
            - not yet read by the recipient (FR-NOTIF-008)
            - belong to a category the recipient has channel='digest' on
        """
        now = now or _now_utc()
        cutoff = now - timedelta(hours=24)
        sent = failed = skipped = 0

        with self._session_factory() as session:
            # Fetch every digest subscription joined to its user.
            sub_rows = session.execute(
                select(Subscription, User)
                .join(User, Subscription.user_id == User.id)
                .where(
                    Subscription.subscribed.is_(True),
                    Subscription.channel == "digest",
                )
            ).all()
            if not sub_rows:
                return {"sent": 0, "failed": 0, "skipped": 0, "users": 0}

            # Bucket subscriptions by user.
            by_user: dict[str, dict[str, Any]] = {}
            for sub, user in sub_rows:
                bucket = by_user.setdefault(
                    user.id, {"user": user, "category_ids": set()}
                )
                bucket["category_ids"].add(sub.category_id)

            users_processed = 0
            for uid, bucket in by_user.items():
                user: User = bucket["user"]
                cat_ids = list(bucket["category_ids"])
                if not user.email or not cat_ids:
                    skipped += 1
                    self._log(
                        session,
                        user_id=uid,
                        item_id=None,
                        channel="digest",
                        status="skipped",
                        error_message="user has no email",
                        detail=None,
                    )
                    users_processed += 1
                    continue

                # Pull candidate items in those categories detected within 24h.
                rows = session.execute(
                    select(Item, Site, Category)
                    .join(Site, Item.site_id == Site.id)
                    .join(Category, Site.category_id == Category.id)
                    .where(
                        Site.category_id.in_(cat_ids),
                        Item.detected_at >= cutoff,
                    )
                    .order_by(Item.detected_at.desc())
                ).all()

                grouped: dict[str, list[tuple[Item, Site, Category]]] = {}
                total = 0
                for item, site, cat in rows:
                    if item.is_read_by(uid):  # FR-NOTIF-008
                        continue
                    grouped.setdefault(cat.id, []).append((item, site, cat))
                    total += 1

                if total == 0:
                    skipped += 1
                    self._log(
                        session,
                        user_id=uid,
                        item_id=None,
                        channel="digest",
                        status="skipped",
                        error_message="no new items",
                        detail="0 items in 0 categories",
                    )
                    users_processed += 1
                    continue

                if not self._smtp_ready():
                    self._log(
                        session,
                        user_id=uid,
                        item_id=None,
                        channel="digest",
                        status="skipped",
                        error_message="SMTP not configured",
                        detail=f"{total} items in {len(grouped)} categories",
                    )
                    skipped += 1
                    users_processed += 1
                    continue

                subject, html, text = self._build_digest_mail(
                    grouped, total, now
                )
                ok, err = self._send_with_retry(
                    user.email, subject, html, text
                )
                self._log(
                    session,
                    user_id=uid,
                    item_id=None,
                    channel="digest",
                    status="sent" if ok else "failed",
                    error_message=None if ok else err,
                    detail=f"{total} items in {len(grouped)} categories",
                )
                if ok:
                    sent += 1
                else:
                    failed += 1
                users_processed += 1

            session.commit()
        return {
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "users": users_processed,
        }

    def send_owner_failure(
        self, site_id: str, consecutive_failures: int
    ) -> dict[str, Any]:
        """Email the category owner when a site hits FAILURE_THRESHOLD."""
        with self._session_factory() as session:
            site = session.get(Site, site_id)
            if site is None:
                return {"sent": 0, "failed": 0, "skipped": 1, "reason": "site_missing"}
            cat = session.get(Category, site.category_id)
            owner = (
                session.get(User, cat.owner_user_id)
                if cat is not None and cat.owner_user_id
                else None
            )
            recipient_email = owner.email if owner and owner.email else None
            owner_user_id = owner.id if owner is not None else (
                cat.owner_user_id if cat is not None else None
            )

            if not recipient_email:
                # Without a resolvable owner user_id we can't satisfy the
                # alert_log FK; log a logger.warning and return skipped so
                # the worker continues without DB pollution.
                if not owner_user_id:
                    logger.warning(
                        "owner failure for site=%s — no owner user resolved", site_id,
                    )
                    return {
                        "sent": 0, "failed": 0, "skipped": 1,
                        "reason": "no_owner_user",
                    }
                self._log(
                    session,
                    user_id=owner_user_id,
                    item_id=None,
                    channel="owner_failure",
                    status="skipped",
                    error_message="owner has no email",
                    detail=f"site={site_id}",
                )
                session.commit()
                return {"sent": 0, "failed": 0, "skipped": 1, "reason": "no_owner_email"}

            # By here owner_user_id is guaranteed non-empty (recipient_email truthy).
            assert owner_user_id is not None
            if not self._smtp_ready():
                self._log(
                    session,
                    user_id=owner_user_id,
                    item_id=None,
                    channel="owner_failure",
                    status="skipped",
                    error_message="SMTP not configured",
                    detail=f"site={site_id} streak={consecutive_failures}",
                )
                session.commit()
                return {"sent": 0, "failed": 0, "skipped": 1, "reason": "smtp_disabled"}

            subject, html, text = self._build_owner_failure_mail(
                site, cat, consecutive_failures
            )
            ok, err = self._send_with_retry(recipient_email, subject, html, text)
            self._log(
                session,
                user_id=owner_user_id,
                item_id=None,
                channel="owner_failure",
                status="sent" if ok else "failed",
                error_message=None if ok else err,
                detail=f"site={site_id} streak={consecutive_failures}",
            )
            session.commit()
            return {
                "sent": 1 if ok else 0,
                "failed": 0 if ok else 1,
                "skipped": 0,
                "reason": "ok" if ok else "smtp_error",
            }

    # ------------------------------------------------------------------
    # Rate limit
    # ------------------------------------------------------------------

    def _rate_limit_admit(self, user_id: str) -> bool:
        """Return True if this user can receive another instant mail.

        Sliding 5-minute / 10-mail window per user. The 11th call returns
        False so the caller can roll the event up into a digest entry.
        """
        now = time.time()
        with self._rl_lock:
            bucket = self._rl_buckets.setdefault(user_id, deque())
            cutoff = now - RATE_LIMIT_WINDOW_SEC
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= RATE_LIMIT_MAX:
                return False
            bucket.append(now)
            return True

    def _send_rolled_up_digest(
        self,
        session: Any,
        user: User,
        item: Item,
        site: Site,
        cat: Category,
    ) -> None:
        """Persist a placeholder log row when the user is rate-limited."""
        self._log(
            session,
            user_id=user.id,
            item_id=item.id,
            channel="digest",
            status="skipped",
            error_message="rate limit — rolled up",
            detail=f"site={site.id} cat={cat.id}",
        )

    # ------------------------------------------------------------------
    # Mail templates
    # ------------------------------------------------------------------

    def _build_instant_mail(
        self, item: Item, site: Site, cat: Category
    ) -> tuple[str, str, str]:
        subject = f"[Watchtower 즉시] {cat.name} — {site.name}"
        if len(subject) > 100:
            subject = subject[:97] + "..."

        title = item.title or "(제목 없음)"
        summary = item.summary or "(요약 없음)"
        link = item.url or site.url or self._ui_base

        text = (
            "새로운 업데이트가 감지되었습니다.\n\n"
            f"카테고리: {cat.name}\n"
            f"사이트: {site.name}\n"
            f"제목: {title}\n"
            f"요약: {summary}\n\n"
            f"원문: {link}\n"
            f"Watchtower에서 보기: {self._ui_base}/ui\n\n"
            "---\n"
            f"알림이 너무 많다면 {self._ui_base}/ui 에서 채널을 '다이제스트'로 변경하세요.\n"
        )
        html = (
            "<p>새로운 업데이트가 감지되었습니다.</p>"
            f"<p><b>카테고리:</b> {_html_escape(cat.name)}<br>"
            f"<b>사이트:</b> {_html_escape(site.name)}<br>"
            f"<b>제목:</b> {_html_escape(title)}<br>"
            f"<b>요약:</b> {_html_escape(summary)}</p>"
            f"<p><a href=\"{_html_escape(link)}\">원문 보기</a> · "
            f"<a href=\"{_html_escape(self._ui_base)}/ui\">Watchtower</a></p>"
            f"<hr><p style=\"color:#888;font-size:12px\">알림이 너무 많다면 "
            f"<a href=\"{_html_escape(self._ui_base)}/ui\">설정</a>에서 채널을 "
            "'다이제스트'로 변경하세요.</p>"
        )
        return subject, html, text

    def _build_digest_mail(
        self,
        grouped: dict[str, list[tuple[Item, Site, Category]]],
        total: int,
        now: datetime,
    ) -> tuple[str, str, str]:
        date_kst = now.astimezone(_KST).date().isoformat()
        subject = f"[Watchtower 일간] {date_kst} 외부 모니터링 요약 ({total}건)"
        if len(subject) > 100:
            subject = subject[:97] + "..."

        text_lines = [
            f"지난 24시간 동안 신규 업데이트 {total}건이 감지되었습니다.",
            "",
        ]
        html_blocks: list[str] = [
            f"<p>지난 24시간 동안 신규 업데이트 <b>{total}</b>건이 감지되었습니다.</p>"
        ]
        for cat_id, entries in grouped.items():
            if not entries:
                continue
            cat = entries[0][2]
            text_lines.append(f"▼ {cat.name} ({len(entries)}건)")
            html_blocks.append(
                f"<h3>{_html_escape(cat.name)} ({len(entries)}건)</h3><ol>"
            )
            for idx, (item, site, _cat) in enumerate(entries, start=1):
                text_lines.append(
                    f"  {idx}. [{item.type}] {site.name} — {item.title}"
                )
                if item.summary:
                    snippet = item.summary[:120]
                    text_lines.append(f"     {snippet}")
                if item.url:
                    text_lines.append(f"     {item.url}")
                html_blocks.append(
                    f"<li><b>[{_html_escape(item.type)}]</b> "
                    f"{_html_escape(site.name)} — "
                    f"<a href=\"{_html_escape(item.url or site.url or '#')}\">"
                    f"{_html_escape(item.title)}</a>"
                    + (
                        f"<br><span style=\"color:#666\">{_html_escape(item.summary[:160])}</span>"
                        if item.summary else ""
                    )
                    + "</li>"
                )
            html_blocks.append("</ol>")
            text_lines.append("")
        text_lines.append(f"Watchtower 전체 보기: {self._ui_base}/ui")
        html_blocks.append(
            f"<p><a href=\"{_html_escape(self._ui_base)}/ui\">Watchtower 전체 보기</a></p>"
        )
        return subject, "".join(html_blocks), "\n".join(text_lines)

    def _build_owner_failure_mail(
        self,
        site: Site,
        cat: Category | None,
        consecutive_failures: int,
    ) -> tuple[str, str, str]:
        subject = f"[Watchtower 경고] {site.name} {consecutive_failures}회 연속 수집 실패"
        if len(subject) > 100:
            subject = subject[:97] + "..."

        cat_name = cat.name if cat is not None else "(unknown)"
        last_ok = (
            site.last_ok_at.isoformat()
            if site.last_ok_at is not None
            else "(없음)"
        )
        text = (
            f"사이트: {site.name} ({site.id})\n"
            f"카테고리: {cat_name}\n"
            f"최근 실패: {consecutive_failures}회 연속\n"
            f"마지막 정상 시각: {last_ok}\n\n"
            "사이트 URL/selector를 점검해주세요.\n"
            f"Watchtower 헬스체크: {self._ui_base}/api/health\n"
        )
        html = (
            f"<p><b>사이트:</b> {_html_escape(site.name)} ({_html_escape(site.id)})<br>"
            f"<b>카테고리:</b> {_html_escape(cat_name)}<br>"
            f"<b>최근 실패:</b> {consecutive_failures}회 연속<br>"
            f"<b>마지막 정상 시각:</b> {_html_escape(last_ok)}</p>"
            "<p>사이트 URL/selector를 점검해주세요.</p>"
            f"<p><a href=\"{_html_escape(self._ui_base)}/api/health\">"
            "Watchtower 헬스체크</a></p>"
        )
        return subject, html, text

    # ------------------------------------------------------------------
    # SMTP send + retry
    # ------------------------------------------------------------------

    def _send_with_retry(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> tuple[bool, Optional[str]]:
        """Send one email with up to len(self._backoffs) retries.

        Returns ``(ok, error_message)``. Errors are redacted before return so
        callers can persist the value verbatim.
        """
        from_email = self._from_email()
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")

        last_err: Optional[str] = None
        max_attempts = max(1, len(self._backoffs))
        for attempt in range(max_attempts):
            try:
                self._send_one(msg)
                return True, None
            except _StarttlsFailedError as exc:
                # Fail closed — never retry on plaintext.
                logger.warning(
                    "STARTTLS failed for %s: %s",
                    to_email, type(exc.original).__name__,
                )
                return False, "STARTTLS failed"
            except Exception as exc:
                last_err = _redact(f"{type(exc).__name__}: {exc}")
                logger.warning(
                    "Watchtower mail send failed (attempt %d/%d): %s",
                    attempt + 1, max_attempts, type(exc).__name__,
                )
                if attempt + 1 < max_attempts:
                    self._sleep(self._backoffs[attempt])
                    continue
        return False, last_err or "send failed"

    def _send_one(self, msg: EmailMessage) -> None:
        server = self._smtp.get("server")
        port = int(self._smtp.get("port") or 0)
        user = self._smtp.get("user")
        password = self._smtp.get("password")
        if not (server and port and user):
            raise RuntimeError("SMTP not configured")

        with self._smtp_factory(server, port, timeout=SMTP_TIMEOUT_SEC) as smtp:
            smtp.ehlo()
            try:
                smtp.starttls()
            except Exception as starttls_exc:
                try:
                    smtp.quit()
                except Exception:
                    pass
                raise _StarttlsFailedError(starttls_exc) from starttls_exc
            smtp.ehlo()
            if password:
                smtp.login(user, password)
            smtp.send_message(msg)

    # ------------------------------------------------------------------
    # AlertLog persistence
    # ------------------------------------------------------------------

    def _log(
        self,
        session: Any,
        *,
        user_id: str,
        item_id: Optional[str],
        channel: str,
        status: str,
        error_message: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> AlertLog:
        row = AlertLog(
            id=uuid.uuid4().hex,
            user_id=user_id or "",
            item_id=item_id,
            channel=channel,
            sent_at=_now_utc(),
            status=status,
            error_message=_redact(error_message),
            detail=_redact(detail),
        )
        session.add(row)
        return row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _html_escape(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
