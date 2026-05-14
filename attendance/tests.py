from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    ClassSection,
    Classroom,
    Enrollment,
    InstructorProfile,
    ScanEvent,
    Student,
)


class AttendanceFactoryMixin:
    def create_instructor(self, username: str = "teacher") -> InstructorProfile:
        user = User.objects.create_user(
            username=username,
            password="pass12345",
            first_name="Ada",
            last_name="Lovelace",
            email=f"{username}@example.test",
        )
        return InstructorProfile.objects.create(
            user=user,
            employee_id=f"EMP-{username.upper()}",
            department="BSCS",
        )

    def create_section(
        self,
        instructor: InstructorProfile,
        *,
        section_letter: str = "A",
        subject_code: str = "CCCS105",
    ) -> ClassSection:
        return ClassSection.objects.create(
            instructor=instructor,
            subject_code=subject_code,
            subject_title="Information Management",
            department="BSCS",
            year_level=2,
            section_letter=section_letter,
        )

    def create_student(self, uid: str = "A1B2C3D4") -> Student:
        return Student.objects.create(
            student_number="2026-0001",
            first_name="Juan",
            middle_name="Reyes",
            last_name="Dela Cruz",
            department="BSCS",
            year_level=2,
            rfid_uid=uid,
            email="juan@example.test",
        )


class ClassSectionModelTests(AttendanceFactoryMixin, TestCase):
    def test_section_code_is_generated_from_department_year_and_letter(self):
        instructor = self.create_instructor()

        section = self.create_section(instructor)

        self.assertEqual(section.section_code, "BSCS-2A")
        self.assertEqual(str(section), "CCCS105 - BSCS-2A")

    def test_student_full_name_omits_blank_middle_name(self):
        student = Student.objects.create(
            student_number="2026-0002",
            first_name="Maria",
            middle_name="",
            last_name="Santos",
            department="BSIT",
            year_level=3,
            rfid_uid="BB22",
        )

        self.assertEqual(student.full_name, "Maria Santos")


class SessionSnapshotTests(AttendanceFactoryMixin, TestCase):
    def test_creating_session_snapshots_roster_as_absent_records(self):
        instructor = self.create_instructor()
        section = self.create_section(instructor)
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        student = self.create_student()
        Enrollment.objects.create(class_section=section, student=student)

        session = AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() + timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
        )

        records = AttendanceRecord.objects.filter(session=session)
        self.assertEqual(records.count(), 1)
        self.assertEqual(records.get().status, AttendanceRecord.Status.ABSENT)


@override_settings(BRIDGE_KEY="test-bridge-key")
class ScanApiTests(AttendanceFactoryMixin, TestCase):
    def post_scan(self, uid: str) -> Client:
        return self.client.post(
            reverse("api_scan"),
            data=json.dumps({"uid": uid}),
            content_type="application/json",
            HTTP_X_BRIDGE_KEY="test-bridge-key",
        )

    def create_active_session(
        self,
        *,
        starts_delta_minutes: int = -5,
        accepting: bool = True,
        uid: str = "A1B2C3D4",
        section_letter: str = "A",
        subject_code: str = "CCCS105",
    ):
        instructor = self.create_instructor(username=f"teacher-{section_letter.lower()}")
        section = self.create_section(
            instructor,
            section_letter=section_letter,
            subject_code=subject_code,
        )
        classroom = Classroom.objects.create(
            name=f"Room {section_letter}",
            scanner_code=f"ROOM-{section_letter}",
        )
        student = self.create_student(uid=uid)
        Enrollment.objects.create(class_section=section, student=student)
        session = AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() + timedelta(minutes=starts_delta_minutes),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
            is_accepting_taps=accepting,
        )
        return session, student

    def test_uid_only_scan_marks_enrolled_student_present_within_grace_period(self):
        session, student = self.create_active_session(starts_delta_minutes=-5)

        response = self.post_scan(student.rfid_uid)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], "accepted")
        record = AttendanceRecord.objects.get(session=session, student=student)
        self.assertEqual(record.status, AttendanceRecord.Status.PRESENT)
        self.assertIsNotNone(record.tapped_at)

    def test_pre_start_tap_is_present_after_session_is_opened_for_taps(self):
        session, student = self.create_active_session(starts_delta_minutes=10)

        response = self.post_scan(student.rfid_uid)

        self.assertEqual(response.status_code, 200)
        record = AttendanceRecord.objects.get(session=session, student=student)
        self.assertEqual(record.status, AttendanceRecord.Status.PRESENT)

    def test_scan_marks_enrolled_student_late_after_grace_period(self):
        session, student = self.create_active_session(starts_delta_minutes=-30)

        response = self.post_scan(student.rfid_uid)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "late")
        record = AttendanceRecord.objects.get(session=session, student=student)
        self.assertEqual(record.status, AttendanceRecord.Status.LATE)

    def test_not_accepting_session_rejects_scan_and_keeps_absent_record(self):
        session, student = self.create_active_session(
            starts_delta_minutes=-5,
            accepting=False,
        )

        response = self.post_scan(student.rfid_uid)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["reason"], "no_accepting_session")
        record = AttendanceRecord.objects.get(session=session, student=student)
        self.assertEqual(record.status, AttendanceRecord.Status.ABSENT)
        self.assertIsNone(record.tapped_at)

    def test_uid_in_multiple_accepting_sessions_routes_to_earliest_start(self):
        instructor = self.create_instructor()
        student = self.create_student(uid="MULTI123")
        classroom = Classroom.objects.create(name="Shared Room", scanner_code="SHARED")
        later_section = self.create_section(
            instructor,
            section_letter="B",
            subject_code="CCCS106",
        )
        earlier_section = self.create_section(
            instructor,
            section_letter="C",
            subject_code="CCCS107",
        )
        Enrollment.objects.create(class_section=later_section, student=student)
        Enrollment.objects.create(class_section=earlier_section, student=student)
        later_session = AttendanceSession.objects.create(
            class_section=later_section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
            is_accepting_taps=True,
        )
        earlier_session = AttendanceSession.objects.create(
            class_section=earlier_section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=10),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
            is_accepting_taps=True,
        )

        response = self.post_scan(student.rfid_uid)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["section"], earlier_section.section_code)
        self.assertEqual(
            AttendanceRecord.objects.get(session=earlier_session, student=student).status,
            AttendanceRecord.Status.PRESENT,
        )
        self.assertEqual(
            AttendanceRecord.objects.get(session=later_session, student=student).status,
            AttendanceRecord.Status.ABSENT,
        )

    def test_unknown_uid_is_rejected_and_logged(self):
        self.create_active_session()

        response = self.post_scan("NOPE123")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["reason"], "student_not_in_any_open_roster")
        event = ScanEvent.objects.latest("created_at")
        self.assertEqual(event.result, ScanEvent.Result.REJECTED)
        self.assertEqual(event.reason, "student_not_in_any_open_roster")

    def test_missing_active_session_is_rejected_and_logged(self):
        response = self.post_scan("A1B2C3D4")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["reason"], "no_accepting_session")
        self.assertEqual(ScanEvent.objects.latest("created_at").reason, "no_accepting_session")

    def test_duplicate_scan_keeps_first_tap_as_official_record(self):
        session, student = self.create_active_session(starts_delta_minutes=-5)
        first_response = self.post_scan(student.rfid_uid)

        second_response = self.post_scan(student.rfid_uid)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.json()["result"], "duplicate")
        self.assertEqual(
            AttendanceRecord.objects.filter(session=session, student=student).count(),
            1,
        )
        self.assertEqual(ScanEvent.objects.filter(uid=student.rfid_uid).count(), 2)

    def test_invalid_bridge_key_is_forbidden(self):
        response = self.client.post(
            reverse("api_scan"),
            data=json.dumps({"uid": "A1B2C3D4"}),
            content_type="application/json",
            HTTP_X_BRIDGE_KEY="wrong",
        )

        self.assertEqual(response.status_code, 403)


class StudentSearchTests(AttendanceFactoryMixin, TestCase):
    def setUp(self):
        User.objects.create_user(
            username="admin",
            password="pass12345",
            first_name="Admin",
            last_name="User",
            is_staff=True,
        )
        self.client.login(username="admin", password="pass12345")
        self.target = Student.objects.create(
            student_number="2026-0901",
            first_name="John",
            middle_name="Benedict Adorable",
            last_name="Martinez",
            department="BSCS",
            year_level=2,
            rfid_uid="JOHN0901",
            email="john@example.test",
        )
        self.other = Student.objects.create(
            student_number="2026-0902",
            first_name="Maria",
            middle_name="Luisa",
            last_name="Santos",
            department="BSCS",
            year_level=2,
            rfid_uid="MARIA0902",
            email="maria@example.test",
        )

    def search_student_numbers(self, query: str) -> set[str]:
        response = self.client.get(reverse("student_list"), {"q": query})
        self.assertEqual(response.status_code, 200)
        return {student.student_number for student in response.context["students"]}

    def test_full_exact_combined_name_matches(self):
        results = self.search_student_numbers("John Benedict Adorable Martinez")

        self.assertIn(self.target.student_number, results)
        self.assertNotIn(self.other.student_number, results)

    def test_first_and_last_combined_name_matches(self):
        results = self.search_student_numbers("John Martinez")

        self.assertIn(self.target.student_number, results)

    def test_middle_and_last_combined_name_matches(self):
        results = self.search_student_numbers("Benedict Martinez")

        self.assertIn(self.target.student_number, results)

    def test_existing_single_field_search_still_matches(self):
        results = self.search_student_numbers("2026-0901")

        self.assertIn(self.target.student_number, results)
        self.assertNotIn(self.other.student_number, results)


class InstructorWorkflowTests(AttendanceFactoryMixin, TestCase):
    def login_instructor(self, username: str = "teacher") -> InstructorProfile:
        instructor = self.create_instructor(username=username)
        self.client.login(username=username, password="pass12345")
        return instructor

    def test_dashboard_only_lists_current_instructors_sessions(self):
        instructor = self.login_instructor()
        own_section = self.create_section(instructor)
        other_instructor = self.create_instructor(username="other")
        other_section = self.create_section(other_instructor, section_letter="B")
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        own_session = AttendanceSession.objects.create(
            class_section=own_section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
        )
        AttendanceSession.objects.create(
            class_section=other_section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
        )

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, own_section.section_code)
        self.assertContains(response, own_session.class_section.subject_code)
        self.assertNotContains(response, other_section.section_code)

    def test_session_list_groups_by_class_and_prioritizes_current_upcoming(self):
        instructor = self.login_instructor()
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        soon_section = self.create_section(
            instructor,
            section_letter="A",
            subject_code="CCCS101",
        )
        later_section = self.create_section(
            instructor,
            section_letter="B",
            subject_code="CCCS102",
        )
        history_section = self.create_section(
            instructor,
            section_letter="C",
            subject_code="CCCS103",
        )
        now = timezone.now()
        old_session = AttendanceSession.objects.create(
            class_section=history_section,
            classroom=classroom,
            meeting_date=timezone.localdate(now - timedelta(days=2)),
            starts_at=now - timedelta(days=2, hours=1),
            ends_at=now - timedelta(days=2),
            grace_minutes=15,
        )
        later_session = AttendanceSession.objects.create(
            class_section=later_section,
            classroom=classroom,
            meeting_date=timezone.localdate(now + timedelta(days=2)),
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=2),
            grace_minutes=15,
        )
        soon_session = AttendanceSession.objects.create(
            class_section=soon_section,
            classroom=classroom,
            meeting_date=timezone.localdate(now + timedelta(hours=1)),
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=3),
            grace_minutes=15,
        )

        response = self.client.get(reverse("session_list"))

        groups = response.context["session_groups"]
        self.assertEqual(
            [group["section"] for group in groups],
            [soon_section, later_section, history_section],
        )
        self.assertEqual(groups[0]["focus_session"], soon_session)
        self.assertEqual(groups[1]["focus_session"], later_session)
        self.assertIsNone(groups[2]["focus_session"])
        self.assertEqual(groups[2]["recent_sessions"], [old_session])
        self.assertContains(response, "Current & Upcoming")
        self.assertContains(response, soon_section.section_code)

    def test_session_list_summary_counts_accepting_upcoming_and_history(self):
        instructor = self.login_instructor()
        section = self.create_section(instructor)
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        now = timezone.now()
        AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(now),
            starts_at=now - timedelta(minutes=10),
            ends_at=now + timedelta(hours=1),
            grace_minutes=15,
            is_accepting_taps=True,
        )
        AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(now + timedelta(days=1)),
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=2),
            grace_minutes=15,
        )
        AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(now - timedelta(days=1)),
            starts_at=now - timedelta(days=1, hours=2),
            ends_at=now - timedelta(days=1),
            grace_minutes=15,
        )

        response = self.client.get(reverse("session_list"))

        self.assertEqual(
            response.context["session_summary"],
            {
                "accepting_count": 1,
                "current_upcoming_count": 2,
                "history_count": 1,
                "class_count": 1,
            },
        )

    def test_csv_roster_import_enrolls_existing_students_and_reports_missing_students(self):
        instructor = self.login_instructor()
        section = self.create_section(instructor)
        existing = Student.objects.create(
            student_number="2026-0101",
            first_name="Ana",
            middle_name="",
            last_name="Cruz",
            department="BSCS",
            year_level=2,
            rfid_uid="RFID0101",
            email="ana@example.test",
        )
        csv_body = (
            "student_number,first_name,middle_name,last_name,department,year_level,rfid_uid,email\n"
            "2026-0101,Ana,,Cruz,BSCS,2,RFID0101,ana@example.test\n"
            "2026-0102,Ben,Lopez,Santos,BSCS,2,RFID0102,ben@example.test\n"
        )

        response = self.client.post(
            reverse("section_import_roster", args=[section.pk]),
            data={"csv_file": csv_body},
        )

        self.assertRedirects(response, reverse("section_detail", args=[section.pk]))
        self.assertEqual(Student.objects.filter(student_number__startswith="2026-010").count(), 1)
        self.assertTrue(
            Enrollment.objects.filter(class_section=section, student=existing).exists()
        )
        self.assertFalse(Student.objects.filter(student_number="2026-0102").exists())

    def test_instructor_cannot_create_classes_or_students(self):
        self.login_instructor()

        class_response = self.client.get(reverse("section_create"))
        student_response = self.client.get(reverse("student_create"))

        self.assertEqual(class_response.status_code, 403)
        self.assertEqual(student_response.status_code, 403)

    def test_attendance_export_returns_csv_rows(self):
        instructor = self.login_instructor()
        section = self.create_section(instructor)
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        student = self.create_student()
        Enrollment.objects.create(class_section=section, student=student)
        session = AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
        )

        response = self.client.get(reverse("session_export", args=[session.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode()
        self.assertIn("student_number,full_name,status,tapped_at", body)
        self.assertIn(student.student_number, body)

    def test_instructor_can_open_own_session_for_attendance_taps(self):
        instructor = self.login_instructor()
        section = self.create_section(instructor)
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        session = AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
        )

        response = self.client.post(reverse("session_open_attendance", args=[session.pk]))

        self.assertRedirects(response, reverse("session_detail", args=[session.pk]))
        session.refresh_from_db()
        self.assertTrue(session.is_accepting_taps)

    def test_instructor_can_close_own_session_for_attendance_taps(self):
        instructor = self.login_instructor()
        section = self.create_section(instructor)
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        session = AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
            is_accepting_taps=True,
        )

        response = self.client.post(reverse("session_close_attendance", args=[session.pk]))

        self.assertRedirects(response, reverse("session_detail", args=[session.pk]))
        session.refresh_from_db()
        self.assertFalse(session.is_accepting_taps)

    def test_instructor_cannot_open_another_instructors_session(self):
        self.login_instructor(username="teacher")
        other_instructor = self.create_instructor(username="other")
        other_section = self.create_section(other_instructor, section_letter="B")
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        session = AttendanceSession.objects.create(
            class_section=other_section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
        )

        response = self.client.post(reverse("session_open_attendance", args=[session.pk]))

        self.assertEqual(response.status_code, 404)
        session.refresh_from_db()
        self.assertFalse(session.is_accepting_taps)


class AdminInterfaceTests(AttendanceFactoryMixin, TestCase):
    def login_admin(self) -> User:
        admin = User.objects.create_user(
            username="admin",
            password="pass12345",
            first_name="Admin",
            last_name="User",
            is_staff=True,
        )
        self.client.login(username="admin", password="pass12345")
        return admin

    def test_staff_dashboard_redirects_to_management_dashboard(self):
        self.login_admin()

        response = self.client.get(reverse("dashboard"))

        self.assertRedirects(response, reverse("management_dashboard"))

    def test_instructor_cannot_access_management_dashboard(self):
        instructor = self.create_instructor()
        self.client.login(username=instructor.user.username, password="pass12345")

        response = self.client.get(reverse("management_dashboard"))

        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_instructor_profile(self):
        self.login_admin()

        response = self.client.post(
            reverse("management_instructor_create"),
            data={
                "username": "newteacher",
                "first_name": "New",
                "last_name": "Teacher",
                "email": "newteacher@example.test",
                "password": "pass12345",
                "employee_id": "EMP-NEW",
                "department": "BSCS",
                "contact_number": "09170000000",
            },
        )

        profile = InstructorProfile.objects.get(employee_id="EMP-NEW")
        self.assertRedirects(response, reverse("management_instructor_list"))
        self.assertEqual(profile.user.username, "newteacher")
        self.assertFalse(profile.user.is_staff)
        self.assertTrue(profile.user.check_password("pass12345"))

    def test_admin_can_create_class_assigned_to_instructor(self):
        self.login_admin()
        instructor = self.create_instructor()

        response = self.client.post(
            reverse("management_class_create"),
            data={
                "instructor": instructor.pk,
                "subject_code": "CCCS201",
                "subject_title": "Advanced Databases",
                "department": "BSCS",
                "year_level": 3,
                "section_letter": "C",
                "is_active": "on",
            },
        )

        section = ClassSection.objects.get(subject_code="CCCS201")
        self.assertRedirects(response, reverse("management_class_detail", args=[section.pk]))
        self.assertEqual(section.instructor, instructor)
        self.assertEqual(section.section_code, "BSCS-3C")

    def test_admin_can_create_student(self):
        self.login_admin()

        response = self.client.post(
            reverse("management_student_create"),
            data={
                "student_number": "2026-0701",
                "first_name": "Lia",
                "middle_name": "",
                "last_name": "Reyes",
                "department": "BSCS",
                "year_level": 2,
                "rfid_uid": "ADMIN0701",
                "email": "lia@example.test",
                "contact_number": "",
            },
        )

        self.assertRedirects(response, reverse("management_student_list"))
        self.assertTrue(Student.objects.filter(student_number="2026-0701").exists())

    def test_admin_can_view_attendance_but_cannot_open_it_from_management(self):
        self.login_admin()
        instructor = self.create_instructor()
        section = self.create_section(instructor)
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        session = AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
        )

        detail_response = self.client.get(reverse("management_attendance_detail", args=[session.pk]))
        open_response = self.client.post(reverse("session_open_attendance", args=[session.pk]))

        self.assertContains(detail_response, section.section_code)
        self.assertContains(detail_response, "Read-only")
        self.assertEqual(open_response.status_code, 403)
        session.refresh_from_db()
        self.assertFalse(session.is_accepting_taps)


@override_settings(BRIDGE_KEY="test-bridge-key")
class LiveUpdateTests(AttendanceFactoryMixin, TestCase):
    def login_admin(self) -> User:
        admin = User.objects.create_user(
            username="admin-live",
            password="pass12345",
            first_name="Admin",
            last_name="Live",
            is_staff=True,
        )
        self.client.login(username=admin.username, password="pass12345")
        return admin

    def login_instructor(self, username: str = "teacher-live") -> InstructorProfile:
        instructor = self.create_instructor(username=username)
        self.client.login(username=instructor.user.username, password="pass12345")
        return instructor

    def post_scan(self, uid: str):
        return self.client.post(
            reverse("api_scan"),
            data=json.dumps({"uid": uid}),
            content_type="application/json",
            HTTP_X_BRIDGE_KEY="test-bridge-key",
        )

    def test_admin_student_live_search_filters_as_json(self):
        self.login_admin()
        target = Student.objects.create(
            student_number="2026-0901",
            first_name="John",
            middle_name="Benedict Adorable",
            last_name="Martinez",
            department="BSCS",
            year_level=2,
            rfid_uid="JOHN0901",
        )
        Student.objects.create(
            student_number="2026-0902",
            first_name="Maria",
            middle_name="",
            last_name="Santos",
            department="BSCS",
            year_level=2,
            rfid_uid="MARIA0902",
        )

        response = self.client.get(reverse("management_student_search"), {"q": "John Martinez"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["students"][0]["student_number"], target.student_number)
        self.assertEqual(payload["students"][0]["full_name"], target.full_name)

    def test_admin_class_live_search_filters_by_instructor_as_json(self):
        self.login_admin()
        instructor = self.create_instructor(username="ada")
        instructor.user.first_name = "Ada"
        instructor.user.last_name = "Byron"
        instructor.user.save()
        section = self.create_section(instructor, subject_code="CCCS201")
        other_instructor = self.create_instructor(username="grace")
        other_instructor.user.first_name = "Grace"
        other_instructor.user.last_name = "Hopper"
        other_instructor.user.save()
        self.create_section(other_instructor, section_letter="B", subject_code="CCCS202")

        response = self.client.get(reverse("management_class_search"), {"q": "Ada"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["sections"][0]["section_code"], section.section_code)
        self.assertEqual(payload["sections"][0]["instructor"], instructor.full_name)

    def test_instructor_section_live_search_only_returns_assigned_classes(self):
        instructor = self.login_instructor()
        own_section = self.create_section(instructor, subject_code="CCCS301")
        other_instructor = self.create_instructor(username="other-live")
        self.create_section(other_instructor, section_letter="B", subject_code="CCCS301")

        response = self.client.get(reverse("section_search"), {"q": "CCCS301"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["sections"][0]["section_code"], own_section.section_code)

    def test_instructor_attendance_live_data_updates_after_scan_without_refresh(self):
        instructor = self.login_instructor()
        section = self.create_section(instructor)
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        student = self.create_student(uid="LIVE1001")
        Enrollment.objects.create(class_section=section, student=student)
        session = AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
            is_accepting_taps=True,
        )

        before_response = self.client.get(reverse("session_live_data", args=[session.pk]))
        scan_response = self.post_scan(student.rfid_uid)
        after_response = self.client.get(reverse("session_live_data", args=[session.pk]))

        self.assertEqual(before_response.json()["counts"]["absent"], 1)
        self.assertEqual(scan_response.status_code, 200)
        payload = after_response.json()
        self.assertEqual(payload["counts"]["present"], 1)
        self.assertEqual(payload["counts"]["absent"], 0)
        self.assertEqual(payload["records"][0]["status"], "present")
        self.assertEqual(payload["scans"][0]["uid"], student.rfid_uid)

    def test_instructor_attendance_live_data_counts_all_records_with_same_status(self):
        instructor = self.login_instructor()
        section = self.create_section(instructor)
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        john = Student.objects.create(
            student_number="2026-1001",
            first_name="John",
            middle_name="Benedict Adorable",
            last_name="Martinez",
            department="BSCS",
            year_level=2,
            rfid_uid="JOHN1001",
        )
        test_user = Student.objects.create(
            student_number="2026-1002",
            first_name="Test",
            middle_name="User",
            last_name="Test",
            department="BSCS",
            year_level=2,
            rfid_uid="TEST1002",
        )
        Enrollment.objects.create(class_section=section, student=john)
        Enrollment.objects.create(class_section=section, student=test_user)
        session = AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
            is_accepting_taps=True,
        )

        before_response = self.client.get(reverse("session_live_data", args=[session.pk]))
        john_response = self.post_scan(john.rfid_uid)
        test_user_response = self.post_scan(test_user.rfid_uid)
        after_response = self.client.get(reverse("session_live_data", args=[session.pk]))

        self.assertEqual(before_response.json()["counts"]["absent"], 2)
        self.assertEqual(john_response.status_code, 200)
        self.assertEqual(test_user_response.status_code, 200)
        payload = after_response.json()
        self.assertEqual(payload["counts"]["present"], 2)
        self.assertEqual(payload["counts"]["absent"], 0)
        self.assertEqual(len(payload["records"]), 2)

    def test_admin_attendance_live_data_can_read_all_sessions(self):
        self.login_admin()
        instructor = self.create_instructor()
        section = self.create_section(instructor)
        classroom = Classroom.objects.create(name="Room 101", scanner_code="ROOM-101")
        student = self.create_student(uid="ADMINLIVE")
        Enrollment.objects.create(class_section=section, student=student)
        session = AttendanceSession.objects.create(
            class_section=section,
            classroom=classroom,
            meeting_date=timezone.localdate(),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(hours=2),
            grace_minutes=15,
        )

        response = self.client.get(reverse("management_attendance_live_data", args=[session.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["counts"]["absent"], 1)
        self.assertEqual(payload["records"][0]["student_number"], student.student_number)


class RfidCaptureTests(AttendanceFactoryMixin, TestCase):
    def login_instructor(self) -> InstructorProfile:
        instructor = self.create_instructor()
        self.client.login(username=instructor.user.username, password="pass12345")
        return instructor

    def test_latest_scan_uid_endpoint_returns_recent_uid(self):
        self.login_instructor()
        ScanEvent.objects.create(
            uid="abc123",
            scanner_code="",
            result=ScanEvent.Result.REJECTED,
            reason="student_not_in_any_open_roster",
        )

        response = self.client.get(reverse("student_rfid_capture_latest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["uid"], "ABC123")

    def test_latest_scan_uid_endpoint_ignores_stale_scan(self):
        self.login_instructor()
        stale = ScanEvent.objects.create(
            uid="old123",
            scanner_code="",
            result=ScanEvent.Result.REJECTED,
            reason="student_not_in_any_open_roster",
        )
        ScanEvent.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(minutes=3)
        )

        response = self.client.get(reverse("student_rfid_capture_latest"))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["reason"], "no_recent_scan")

    def test_latest_scan_uid_endpoint_requires_login(self):
        response = self.client.get(reverse("student_rfid_capture_latest"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
