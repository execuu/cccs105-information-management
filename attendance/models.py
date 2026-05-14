from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class Department(models.TextChoices):
    BSCS = "BSCS", "Bachelor of Science in Computer Science"
    BSIT = "BSIT", "Bachelor of Science in Information Technology"
    BSIS = "BSIS", "Bachelor of Science in Information Systems"
    BLIS = "BLIS", "Bachelor of Library and Information Sciences"


section_letter_validator = RegexValidator(
    regex=r"^[A-Z]$",
    message="Section letter must be one uppercase letter from A to Z.",
)


class InstructorProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="instructor_profile")
    employee_id = models.CharField(max_length=32, unique=True)
    department = models.CharField(max_length=4, choices=Department.choices)
    contact_number = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__last_name", "user__first_name"]

    @property
    def full_name(self) -> str:
        name = self.user.get_full_name().strip()
        return name or self.user.username

    def __str__(self) -> str:
        return f"{self.full_name} ({self.employee_id})"


class Classroom(models.Model):
    name = models.CharField(max_length=80, unique=True)
    scanner_code = models.CharField(max_length=40, unique=True)
    location = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.scanner_code = self.scanner_code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} ({self.scanner_code})"


class ClassSection(models.Model):
    instructor = models.ForeignKey(
        InstructorProfile,
        on_delete=models.CASCADE,
        related_name="class_sections",
    )
    subject_code = models.CharField(max_length=20)
    subject_title = models.CharField(max_length=120)
    department = models.CharField(max_length=4, choices=Department.choices)
    year_level = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(4)]
    )
    section_letter = models.CharField(max_length=1, validators=[section_letter_validator])
    section_code = models.CharField(max_length=12, editable=False, db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["department", "year_level", "section_letter", "subject_code"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "instructor",
                    "subject_code",
                    "department",
                    "year_level",
                    "section_letter",
                ],
                name="unique_instructor_subject_section",
            )
        ]

    def clean(self):
        super().clean()
        self.section_letter = (self.section_letter or "").strip().upper()
        if self.section_letter and not self.section_letter.isalpha():
            raise ValidationError({"section_letter": "Section letter must be A-Z."})

    def save(self, *args, **kwargs):
        self.department = (self.department or "").strip().upper()
        self.section_letter = (self.section_letter or "").strip().upper()
        self.section_code = f"{self.department}-{self.year_level}{self.section_letter}"
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.subject_code} - {self.section_code}"


class Student(models.Model):
    student_number = models.CharField(max_length=32, unique=True)
    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80)
    department = models.CharField(max_length=4, choices=Department.choices)
    year_level = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(4)]
    )
    rfid_uid = models.CharField(max_length=64, unique=True)
    email = models.EmailField(blank=True)
    contact_number = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name", "student_number"]

    def save(self, *args, **kwargs):
        self.department = (self.department or "").strip().upper()
        self.rfid_uid = (self.rfid_uid or "").strip().upper()
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(part.strip() for part in parts if part and part.strip())

    def __str__(self) -> str:
        return f"{self.student_number} - {self.full_name}"


class Enrollment(models.Model):
    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="enrollments")
    enrolled_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["class_section__section_code", "student__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_section", "student"],
                name="unique_student_per_class_section",
            )
        ]

    def __str__(self) -> str:
        return f"{self.student.full_name} in {self.class_section}"


class AttendanceSession(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    class_section = models.ForeignKey(
        ClassSection,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    classroom = models.ForeignKey(Classroom, on_delete=models.PROTECT, related_name="sessions")
    meeting_date = models.DateField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    grace_minutes = models.PositiveSmallIntegerField(default=15)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    is_accepting_taps = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_section", "meeting_date", "starts_at"],
                name="unique_section_session_start",
            )
        ]

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValidationError({"ends_at": "End time must be after start time."})

    def is_active_at(self, moment=None) -> bool:
        moment = moment or timezone.now()
        return (
            self.status == self.Status.OPEN
            and self.is_accepting_taps
            and moment <= self.ends_at
        )

    @property
    def has_ended(self) -> bool:
        return timezone.now() > self.ends_at

    @property
    def attendance_state_label(self) -> str:
        if self.has_ended:
            return "Ended"
        if self.status == self.Status.OPEN and self.is_accepting_taps:
            return "Accepting taps"
        return "Attendance closed"

    def status_for_tap(self, moment=None) -> str:
        moment = moment or timezone.now()
        grace_cutoff = self.starts_at + timedelta(minutes=self.grace_minutes)
        if moment <= grace_cutoff:
            return AttendanceRecord.Status.PRESENT
        return AttendanceRecord.Status.LATE

    def __str__(self) -> str:
        return f"{self.class_section} on {self.meeting_date:%Y-%m-%d}"


class ScanEvent(models.Model):
    class Result(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        DUPLICATE = "duplicate", "Duplicate"

    uid = models.CharField(max_length=64)
    scanner_code = models.CharField(max_length=40, blank=True, default="")
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_events",
    )
    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_events",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_events",
    )
    result = models.CharField(max_length=12, choices=Result.choices)
    reason = models.CharField(max_length=80, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.uid = (self.uid or "").strip().upper()
        self.scanner_code = (self.scanner_code or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.uid} {self.result} {self.reason}".strip()


class AttendanceRecord(models.Model):
    class Status(models.TextChoices):
        ABSENT = "absent", "Absent"
        PRESENT = "present", "Present"
        LATE = "late", "Late"

    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.CASCADE,
        related_name="records",
    )
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="attendance_records")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ABSENT)
    tapped_at = models.DateTimeField(null=True, blank=True)
    source_scan = models.ForeignKey(
        ScanEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["student__last_name", "student__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "student"],
                name="unique_attendance_record_per_student_session",
            )
        ]

    def __str__(self) -> str:
        return f"{self.student.full_name} - {self.session} - {self.status}"


def create_absent_records_for_session(session: AttendanceSession) -> None:
    enrollments = Enrollment.objects.filter(
        class_section=session.class_section,
        is_active=True,
    ).select_related("student")
    AttendanceRecord.objects.bulk_create(
        [
            AttendanceRecord(session=session, student=enrollment.student)
            for enrollment in enrollments
        ],
        ignore_conflicts=True,
    )


@receiver(post_save, sender=AttendanceSession, dispatch_uid="snapshot_session_roster")
def snapshot_session_roster(sender, instance: AttendanceSession, created: bool, **kwargs):
    if created:
        create_absent_records_for_session(instance)


@receiver(post_save, sender=Enrollment, dispatch_uid="snapshot_enrollment_to_open_sessions")
def snapshot_enrollment_to_open_sessions(sender, instance: Enrollment, created: bool, **kwargs):
    if not created or not instance.is_active:
        return
    sessions = AttendanceSession.objects.filter(
        class_section=instance.class_section,
        status=AttendanceSession.Status.OPEN,
        starts_at__gte=timezone.now() - timedelta(hours=8),
    )
    AttendanceRecord.objects.bulk_create(
        [
            AttendanceRecord(session=session, student=instance.student)
            for session in sessions
        ],
        ignore_conflicts=True,
    )
