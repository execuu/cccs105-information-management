from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    ScanEvent,
    Student,
)


@dataclass(frozen=True)
class ScanResult:
    http_status: int
    payload: dict


def normalize_token(value: str) -> str:
    return (value or "").strip().upper()


def process_scan(uid: str, raw_payload: dict | None = None) -> ScanResult:
    uid = normalize_token(uid)
    raw_payload = raw_payload or {}

    if not uid:
        event = ScanEvent.objects.create(
            uid=uid,
            result=ScanEvent.Result.REJECTED,
            reason="missing_uid",
            raw_payload=raw_payload,
        )
        return ScanResult(
            400,
            {"result": event.result, "reason": event.reason},
        )

    with transaction.atomic():
        now = timezone.now()
        accepting_sessions = AttendanceSession.objects.select_for_update().filter(
            status=AttendanceSession.Status.OPEN,
            is_accepting_taps=True,
            ends_at__gte=now,
        )
        if not accepting_sessions.exists():
            event = ScanEvent.objects.create(
                uid=uid,
                result=ScanEvent.Result.REJECTED,
                reason="no_accepting_session",
                raw_payload=raw_payload,
            )
            return ScanResult(404, {"result": event.result, "reason": event.reason})

        session = (
            accepting_sessions.filter(
                class_section__enrollments__is_active=True,
                class_section__enrollments__student__rfid_uid=uid,
            )
            .select_related("class_section", "classroom")
            .order_by("starts_at", "id")
            .first()
        )
        student = Student.objects.filter(rfid_uid=uid).first()
        if session is None:
            event = ScanEvent.objects.create(
                uid=uid,
                student=student,
                result=ScanEvent.Result.REJECTED,
                reason="student_not_in_any_open_roster",
                raw_payload=raw_payload,
            )
            return ScanResult(404, {"result": event.result, "reason": event.reason})

        student = (
            Student.objects.select_for_update()
            .filter(rfid_uid=uid)
            .first()
        )
        if student is None:
            event = ScanEvent.objects.create(
                uid=uid,
                session=session,
                classroom=session.classroom,
                result=ScanEvent.Result.REJECTED,
                reason="student_not_in_any_open_roster",
                raw_payload=raw_payload,
            )
            return ScanResult(404, {"result": event.result, "reason": event.reason})

        record, _created = AttendanceRecord.objects.select_for_update().get_or_create(
            session=session,
            student=student,
            defaults={"status": AttendanceRecord.Status.ABSENT},
        )
        if record.tapped_at:
            event = ScanEvent.objects.create(
                uid=uid,
                classroom=session.classroom,
                session=session,
                student=student,
                result=ScanEvent.Result.DUPLICATE,
                reason="already_recorded",
                raw_payload=raw_payload,
            )
            return ScanResult(
                200,
                {
                    "result": event.result,
                    "status": record.status,
                    "student": student.full_name,
                    "reason": event.reason,
                },
            )

        status = session.status_for_tap(now)
        event = ScanEvent.objects.create(
            uid=uid,
            classroom=session.classroom,
            session=session,
            student=student,
            result=ScanEvent.Result.ACCEPTED,
            raw_payload=raw_payload,
        )
        record.status = status
        record.tapped_at = now
        record.source_scan = event
        record.save(update_fields=["status", "tapped_at", "source_scan", "updated_at"])
        return ScanResult(
            200,
            {
                "result": event.result,
                "status": record.status,
                "student": student.full_name,
                "section": session.class_section.section_code,
            },
        )
