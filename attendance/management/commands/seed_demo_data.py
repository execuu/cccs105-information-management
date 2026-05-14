from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    ClassSection,
    Classroom,
    Department,
    Enrollment,
    InstructorProfile,
    ScanEvent,
    Student,
)


DEPARTMENTS = [Department.BSCS, Department.BSIT, Department.BSIS, Department.BLIS]
SUBJECTS = [
    ("CCCS105", "Information Management"),
    ("CCCS106", "Application Development"),
    ("ITEC101", "Platform Technologies"),
    ("ISYS102", "Systems Analysis"),
    ("LIBS101", "Library Systems"),
]


class Command(BaseCommand):
    help = "Seed realistic demo data for the RFID attendance system."

    def handle(self, *args, **options):
        with transaction.atomic():
            instructors = self.create_instructors()
            classrooms = self.create_classrooms()
            students = self.create_students()
            sections = self.create_sections(instructors)
            self.create_enrollments(sections, students)
            sessions = self.create_sessions(sections, classrooms)
            self.create_scan_events(sessions)

        self.stdout.write(self.style.SUCCESS("Demo data seeded."))
        self.stdout.write("Default instructor login: instructor01 / InstructorPass123!")

    def create_instructors(self) -> list[InstructorProfile]:
        instructors: list[InstructorProfile] = []
        for index in range(1, 51):
            username = f"instructor{index:02d}"
            user, _ = User.objects.update_or_create(
                username=username,
                defaults={
                    "first_name": f"Instructor{index:02d}",
                    "last_name": "Demo",
                    "email": f"{username}@example.test",
                    "is_staff": index <= 2,
                },
            )
            user.set_password("InstructorPass123!")
            user.save(update_fields=["password", "first_name", "last_name", "email", "is_staff"])
            profile, _ = InstructorProfile.objects.update_or_create(
                user=user,
                defaults={
                    "employee_id": f"EMP-{index:04d}",
                    "department": DEPARTMENTS[(index - 1) % len(DEPARTMENTS)],
                    "contact_number": f"09{index:09d}"[:11],
                },
            )
            instructors.append(profile)
        return instructors

    def create_classrooms(self) -> list[Classroom]:
        classrooms: list[Classroom] = []
        for index in range(1, 51):
            classroom, _ = Classroom.objects.update_or_create(
                scanner_code=f"ROOM-{index:03d}",
                defaults={
                    "name": f"Room {100 + index}",
                    "location": f"Academic Building {((index - 1) % 5) + 1}",
                    "is_active": True,
                },
            )
            classrooms.append(classroom)
        return classrooms

    def create_students(self) -> list[Student]:
        first_names = ["Ana", "Ben", "Carla", "Dino", "Ela", "Francis", "Gia", "Hugo"]
        last_names = ["Cruz", "Santos", "Reyes", "Garcia", "Lopez", "Torres", "Flores", "Ramos"]
        students: list[Student] = []
        for index in range(1, 61):
            department = DEPARTMENTS[(index - 1) % len(DEPARTMENTS)]
            student, _ = Student.objects.update_or_create(
                student_number=f"2026-{index:04d}",
                defaults={
                    "first_name": first_names[(index - 1) % len(first_names)],
                    "middle_name": "M" if index % 3 == 0 else "",
                    "last_name": last_names[(index - 1) % len(last_names)],
                    "department": department,
                    "year_level": ((index - 1) % 4) + 1,
                    "rfid_uid": f"RFID{index:06d}",
                    "email": f"student{index:04d}@example.test",
                    "contact_number": f"09{(900000000 + index):09d}"[:11],
                },
            )
            students.append(student)
        return students

    def create_sections(self, instructors: list[InstructorProfile]) -> list[ClassSection]:
        sections: list[ClassSection] = []
        for index in range(1, 51):
            department = DEPARTMENTS[(index - 1) % len(DEPARTMENTS)]
            subject_code, subject_title = SUBJECTS[(index - 1) % len(SUBJECTS)]
            section, _ = ClassSection.objects.update_or_create(
                instructor=instructors[index - 1],
                subject_code=subject_code,
                department=department,
                year_level=((index - 1) % 4) + 1,
                section_letter=chr(ord("A") + ((index - 1) % 26)),
                defaults={
                    "subject_title": subject_title,
                    "is_active": True,
                },
            )
            sections.append(section)
        return sections

    def create_enrollments(self, sections: list[ClassSection], students: list[Student]) -> None:
        for index, student in enumerate(students):
            section = sections[index % len(sections)]
            Enrollment.objects.update_or_create(
                class_section=section,
                student=student,
                defaults={"is_active": True},
            )

    def create_sessions(
        self,
        sections: list[ClassSection],
        classrooms: list[Classroom],
    ) -> list[AttendanceSession]:
        sessions: list[AttendanceSession] = []
        base = timezone.now().replace(minute=0, second=0, microsecond=0)
        for index, section in enumerate(sections, start=1):
            starts_at = base - timedelta(minutes=30) + timedelta(days=index % 10)
            ends_at = starts_at + timedelta(hours=2)
            session, _ = AttendanceSession.objects.update_or_create(
                class_section=section,
                meeting_date=starts_at.date(),
                starts_at=starts_at,
                defaults={
                    "classroom": classrooms[(index - 1) % len(classrooms)],
                    "ends_at": ends_at,
                    "grace_minutes": 15 + (index % 3) * 5,
                    "status": AttendanceSession.Status.OPEN,
                    "notes": "Demo attendance session.",
                },
            )
            sessions.append(session)
        return sessions

    def create_scan_events(self, sessions: list[AttendanceSession]) -> None:
        for index, session in enumerate(sessions, start=1):
            record = session.records.select_related("student").first()
            if record is None:
                continue
            status = AttendanceRecord.Status.PRESENT if index % 3 else AttendanceRecord.Status.LATE
            event, _ = ScanEvent.objects.update_or_create(
                uid=record.student.rfid_uid,
                scanner_code=session.classroom.scanner_code,
                session=session,
                student=record.student,
                defaults={
                    "classroom": session.classroom,
                    "result": ScanEvent.Result.ACCEPTED,
                    "reason": "",
                    "raw_payload": {
                        "uid": record.student.rfid_uid,
                        "scanner_code": session.classroom.scanner_code,
                    },
                },
            )
            record.status = status
            record.tapped_at = session.starts_at + timedelta(minutes=5 if status == "present" else 30)
            record.source_scan = event
            record.save(update_fields=["status", "tapped_at", "source_scan", "updated_at"])
