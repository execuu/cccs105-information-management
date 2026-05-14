from __future__ import annotations

import csv
import json
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import CharField, Count, Q, Value
from django.db.models.functions import Concat
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from attendance.forms import (
    AdminClassSectionForm,
    AdminInstructorForm,
    AttendanceSessionForm,
    ClassSectionForm,
    ClassroomForm,
    EnrollmentForm,
    RosterImportForm,
    StudentForm,
    StudentSearchForm,
)
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
from attendance.services import process_scan


@csrf_exempt
@require_POST
def api_scan(request):
    if request.headers.get("X-Bridge-Key") != settings.BRIDGE_KEY:
        return JsonResponse({"detail": "Invalid bridge key."}, status=403)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON."}, status=400)

    result = process_scan(
        uid=payload.get("uid", ""),
        raw_payload=payload,
    )
    return JsonResponse(result.payload, status=result.http_status)


def get_instructor_or_raise(request) -> InstructorProfile:
    try:
        return request.user.instructor_profile
    except InstructorProfile.DoesNotExist as exc:
        raise PermissionDenied("This account does not have an instructor profile.") from exc


def get_admin_or_raise(request) -> User:
    if request.user.is_staff:
        return request.user
    raise PermissionDenied("This account does not have admin access.")


def deny_staff_attendance_mutation(request) -> None:
    if request.user.is_staff:
        raise PermissionDenied("Admins can view attendance but cannot operate sessions.")


def import_existing_students_to_roster(section: ClassSection, csv_body: str) -> tuple[int, list[str]]:
    reader = csv.DictReader(StringIO(csv_body))
    imported = 0
    missing: list[str] = []
    for row in reader:
        student_number = row.get("student_number", "").strip()
        if not student_number:
            continue
        student = Student.objects.filter(student_number=student_number).first()
        if student is None:
            missing.append(student_number)
            continue
        Enrollment.objects.get_or_create(class_section=section, student=student)
        imported += 1
    return imported, missing


def filter_students_for_request(request):
    form = StudentSearchForm(request.GET or None)
    students = Student.objects.all()
    if form.is_valid():
        query = form.cleaned_data.get("q", "").strip()
        department = form.cleaned_data.get("department", "")
        if query:
            students = students.annotate(
                full_name_search=Concat(
                    "first_name",
                    Value(" "),
                    "middle_name",
                    Value(" "),
                    "last_name",
                    output_field=CharField(),
                )
            )
            field_filter = (
                Q(student_number__icontains=query)
                | Q(first_name__icontains=query)
                | Q(middle_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(rfid_uid__icontains=query)
                | Q(full_name_search__icontains=query)
            )
            combined_token_filter = Q()
            for token in query.split():
                combined_token_filter &= (
                    Q(first_name__icontains=token)
                    | Q(middle_name__icontains=token)
                    | Q(last_name__icontains=token)
                    | Q(full_name_search__icontains=token)
                )
            students = students.filter(field_filter | combined_token_filter)
        if department:
            students = students.filter(department=department)
    return form, students


def serialize_student(student: Student) -> dict:
    return {
        "id": student.pk,
        "student_number": student.student_number,
        "full_name": student.full_name,
        "department": student.department,
        "year_level": student.year_level,
        "rfid_uid": student.rfid_uid,
        "edit_url": reverse("management_student_update", args=[student.pk]),
        "delete_url": reverse("management_student_delete", args=[student.pk]),
    }


def serialize_section(section: ClassSection, *, include_instructor: bool) -> dict:
    payload = {
        "id": section.pk,
        "section_code": section.section_code,
        "subject": f"{section.subject_code} - {section.subject_title}",
        "department": section.department,
        "year_level": section.year_level,
        "is_active": section.is_active,
        "active_label": "Yes" if section.is_active else "No",
    }
    if include_instructor:
        payload["instructor"] = section.instructor.full_name
        payload["detail_url"] = reverse("management_class_detail", args=[section.pk])
    else:
        payload["detail_url"] = reverse("section_detail", args=[section.pk])
    return payload


def filter_instructor_sections(instructor: InstructorProfile, query: str):
    sections = ClassSection.objects.filter(instructor=instructor)
    if query:
        sections = sections.filter(
            Q(subject_code__icontains=query)
            | Q(subject_title__icontains=query)
            | Q(section_code__icontains=query)
        )
    return sections


def filter_management_sections(query: str):
    sections = ClassSection.objects.select_related("instructor", "instructor__user")
    if query:
        sections = sections.filter(
            Q(subject_code__icontains=query)
            | Q(subject_title__icontains=query)
            | Q(section_code__icontains=query)
            | Q(instructor__user__first_name__icontains=query)
            | Q(instructor__user__last_name__icontains=query)
        )
    return sections


def attendance_status_counts(records) -> dict:
    counts = records.order_by().values("status").annotate(total=Count("id"))
    return {row["status"]: row["total"] for row in counts}


def attendance_live_payload(session: AttendanceSession, *, read_only: bool) -> dict:
    records = session.records.select_related("student").order_by(
        "student__last_name",
        "student__first_name",
    )
    scans = session.scan_events.select_related("student", "classroom").order_by("-created_at", "-id")[:25]
    count_map = attendance_status_counts(records)
    return {
        "attendance_state": session.attendance_state_label,
        "read_only": read_only,
        "counts": {
            "present": count_map.get(AttendanceRecord.Status.PRESENT, 0),
            "late": count_map.get(AttendanceRecord.Status.LATE, 0),
            "absent": count_map.get(AttendanceRecord.Status.ABSENT, 0),
        },
        "records": [
            {
                "student_number": record.student.student_number,
                "student_name": record.student.full_name,
                "status": record.status,
                "tapped_at": timezone.localtime(record.tapped_at).strftime("%Y-%m-%d %H:%M:%S")
                if record.tapped_at
                else "-",
            }
            for record in records
        ],
        "scans": [
            {
                "uid": scan.uid,
                "result": scan.result,
                "detail": scan.student.full_name if scan.student else scan.reason,
            }
            for scan in scans
        ],
    }


@login_required
def dashboard(request):
    if request.user.is_staff:
        return redirect("management_dashboard")
    instructor = get_instructor_or_raise(request)
    today = timezone.localdate()
    sessions = (
        AttendanceSession.objects.filter(
            class_section__instructor=instructor,
            meeting_date=today,
        )
        .select_related("class_section", "classroom")
        .annotate(
            present_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.PRESENT)),
            late_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.LATE)),
            absent_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.ABSENT)),
        )
        .order_by("starts_at")
    )
    recent_scans = ScanEvent.objects.filter(
        session__class_section__instructor=instructor
    ).select_related("student", "session", "classroom")[:10]
    sections = ClassSection.objects.filter(instructor=instructor, is_active=True)
    return render(
        request,
        "attendance/dashboard.html",
        {
            "instructor": instructor,
            "sessions": sessions,
            "recent_scans": recent_scans,
            "sections": sections,
        },
    )


@login_required
def section_list(request):
    if request.user.is_staff:
        return redirect("management_class_list")
    instructor = get_instructor_or_raise(request)
    query = request.GET.get("q", "").strip()
    sections = filter_instructor_sections(instructor, query)
    return render(request, "attendance/section_list.html", {"sections": sections, "query": query})


@login_required
def section_search(request):
    if request.user.is_staff:
        return redirect("management_class_search")
    instructor = get_instructor_or_raise(request)
    sections = filter_instructor_sections(instructor, request.GET.get("q", "").strip())
    rows = [serialize_section(section, include_instructor=False) for section in sections]
    return JsonResponse({"count": len(rows), "sections": rows})


@login_required
def section_create(request):
    if request.user.is_staff:
        return redirect("management_class_create")
    raise PermissionDenied("Only admins can create class sections.")


@login_required
def section_detail(request, pk: int):
    if request.user.is_staff:
        return redirect("management_class_detail", pk=pk)
    instructor = get_instructor_or_raise(request)
    section = get_object_or_404(ClassSection, pk=pk, instructor=instructor)
    enrollment_form = EnrollmentForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "add_student":
        if enrollment_form.is_valid():
            enrollment = enrollment_form.save(commit=False)
            enrollment.class_section = section
            enrollment.save()
            messages.success(request, "Student added to roster.")
            return redirect("section_detail", pk=section.pk)
    enrollments = section.enrollments.select_related("student").order_by(
        "student__last_name",
        "student__first_name",
    )
    sessions = section.sessions.select_related("classroom")[:10]
    return render(
        request,
        "attendance/section_detail.html",
        {
            "section": section,
            "enrollments": enrollments,
            "sessions": sessions,
            "enrollment_form": enrollment_form,
        },
    )


@login_required
def section_update(request, pk: int):
    if request.user.is_staff:
        return redirect("management_class_update", pk=pk)
    raise PermissionDenied("Only admins can edit class sections.")


@login_required
def section_delete(request, pk: int):
    if request.user.is_staff:
        return redirect("management_class_delete", pk=pk)
    raise PermissionDenied("Only admins can delete class sections.")


@login_required
def section_import_roster(request, pk: int):
    instructor = get_instructor_or_raise(request)
    section = get_object_or_404(ClassSection, pk=pk, instructor=instructor)
    form = RosterImportForm(request.POST or None)
    imported = 0
    if request.method == "POST" and form.is_valid():
        imported, missing = import_existing_students_to_roster(
            section,
            form.cleaned_data["csv_file"],
        )
        messages.success(request, f"Imported {imported} roster rows.")
        if missing:
            messages.warning(
                request,
                f"Skipped {len(missing)} missing students: {', '.join(missing)}.",
            )
        return redirect("section_detail", pk=section.pk)
    return render(
        request,
        "attendance/form.html",
        {"form": form, "title": f"Import Roster for {section.section_code}"},
    )


@login_required
def enrollment_delete(request, pk: int):
    instructor = get_instructor_or_raise(request)
    enrollment = get_object_or_404(
        Enrollment,
        pk=pk,
        class_section__instructor=instructor,
    )
    section_pk = enrollment.class_section_id
    if request.method == "POST":
        enrollment.delete()
        messages.success(request, "Student removed from roster.")
        return redirect("section_detail", pk=section_pk)
    return render(
        request,
        "attendance/confirm_delete.html",
        {"object": enrollment, "cancel_url": reverse("section_detail", args=[section_pk])},
    )


@login_required
def student_list(request):
    get_admin_or_raise(request)
    form, students = filter_students_for_request(request)
    return render(
        request,
        "attendance/student_list.html",
        {
            "students": students,
            "form": form,
            "create_url": "management_student_create",
            "update_url": "management_student_update",
            "delete_url": "management_student_delete",
        },
    )


@login_required
def management_student_search(request):
    get_admin_or_raise(request)
    _form, students = filter_students_for_request(request)
    rows = [serialize_student(student) for student in students[:50]]
    return JsonResponse({"count": len(rows), "students": rows})


@login_required
def student_create(request):
    get_admin_or_raise(request)
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student = form.save()
        messages.success(request, f"Created student {student.full_name}.")
        return redirect("management_student_list")
    return render(request, "attendance/form.html", {"form": form, "title": "Add Student"})


@login_required
def student_update(request, pk: int):
    get_admin_or_raise(request)
    student = get_object_or_404(Student, pk=pk)
    form = StudentForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        student = form.save()
        messages.success(request, f"Updated {student.full_name}.")
        return redirect("management_student_list")
    return render(request, "attendance/form.html", {"form": form, "title": "Edit Student"})


@login_required
def student_delete(request, pk: int):
    get_admin_or_raise(request)
    student = get_object_or_404(Student, pk=pk)
    if request.method == "POST":
        student.delete()
        messages.success(request, "Student deleted.")
        return redirect("management_student_list")
    return render(
        request,
        "attendance/confirm_delete.html",
        {"object": student, "cancel_url": reverse("management_student_list")},
    )


@login_required
def student_rfid_capture_latest(request):
    cutoff = timezone.now() - timedelta(minutes=2)
    scan = ScanEvent.objects.filter(created_at__gte=cutoff).order_by("-created_at", "-id").first()
    if scan is None:
        return JsonResponse({"reason": "no_recent_scan"}, status=404)
    return JsonResponse({"uid": scan.uid, "created_at": scan.created_at.isoformat()})


@login_required
def management_dashboard(request):
    get_admin_or_raise(request)
    today = timezone.localdate()
    recent_sessions = (
        AttendanceSession.objects.select_related("class_section", "class_section__instructor__user", "classroom")
        .annotate(
            present_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.PRESENT)),
            late_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.LATE)),
            absent_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.ABSENT)),
        )
        .order_by("-starts_at")[:8]
    )
    return render(
        request,
        "attendance/management/dashboard.html",
        {
            "summary": {
                "instructor_count": InstructorProfile.objects.count(),
                "active_class_count": ClassSection.objects.filter(is_active=True).count(),
                "student_count": Student.objects.count(),
                "room_count": Classroom.objects.filter(is_active=True).count(),
                "today_session_count": AttendanceSession.objects.filter(meeting_date=today).count(),
            },
            "recent_sessions": recent_sessions,
        },
    )


@login_required
def management_instructor_list(request):
    get_admin_or_raise(request)
    instructors = InstructorProfile.objects.select_related("user").order_by(
        "user__last_name",
        "user__first_name",
    )
    return render(
        request,
        "attendance/management/instructor_list.html",
        {"instructors": instructors},
    )


@login_required
def management_instructor_create(request):
    get_admin_or_raise(request)
    form = AdminInstructorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        profile = form.save()
        messages.success(request, f"Created instructor {profile.full_name}.")
        return redirect("management_instructor_list")
    return render(
        request,
        "attendance/form.html",
        {"form": form, "title": "Create Instructor"},
    )


@login_required
def management_instructor_update(request, pk: int):
    get_admin_or_raise(request)
    profile = get_object_or_404(InstructorProfile.objects.select_related("user"), pk=pk)
    form = AdminInstructorForm(request.POST or None, profile=profile)
    if request.method == "POST" and form.is_valid():
        profile = form.save()
        messages.success(request, f"Updated instructor {profile.full_name}.")
        return redirect("management_instructor_list")
    return render(
        request,
        "attendance/form.html",
        {"form": form, "title": "Edit Instructor"},
    )


@login_required
def management_instructor_delete(request, pk: int):
    get_admin_or_raise(request)
    profile = get_object_or_404(InstructorProfile.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        profile.user.delete()
        messages.success(request, "Instructor deleted.")
        return redirect("management_instructor_list")
    return render(
        request,
        "attendance/confirm_delete.html",
        {"object": profile, "cancel_url": reverse("management_instructor_list")},
    )


@login_required
def management_class_list(request):
    get_admin_or_raise(request)
    query = request.GET.get("q", "").strip()
    sections = filter_management_sections(query)
    return render(
        request,
        "attendance/management/class_list.html",
        {"sections": sections, "query": query},
    )


@login_required
def management_class_search(request):
    get_admin_or_raise(request)
    sections = filter_management_sections(request.GET.get("q", "").strip())
    rows = [serialize_section(section, include_instructor=True) for section in sections[:50]]
    return JsonResponse({"count": len(rows), "sections": rows})


@login_required
def management_class_create(request):
    get_admin_or_raise(request)
    form = AdminClassSectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        section = form.save()
        messages.success(request, f"Created class section {section.section_code}.")
        return redirect("management_class_detail", pk=section.pk)
    return render(
        request,
        "attendance/form.html",
        {"form": form, "title": "Create Class"},
    )


@login_required
def management_class_detail(request, pk: int):
    get_admin_or_raise(request)
    section = get_object_or_404(
        ClassSection.objects.select_related("instructor", "instructor__user"),
        pk=pk,
    )
    enrollment_form = EnrollmentForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "add_student":
        if enrollment_form.is_valid():
            enrollment = enrollment_form.save(commit=False)
            enrollment.class_section = section
            enrollment.save()
            messages.success(request, "Student added to roster.")
            return redirect("management_class_detail", pk=section.pk)
    enrollments = section.enrollments.select_related("student").order_by(
        "student__last_name",
        "student__first_name",
    )
    sessions = section.sessions.select_related("classroom")[:10]
    return render(
        request,
        "attendance/management/class_detail.html",
        {
            "section": section,
            "enrollments": enrollments,
            "sessions": sessions,
            "enrollment_form": enrollment_form,
        },
    )


@login_required
def management_class_update(request, pk: int):
    get_admin_or_raise(request)
    section = get_object_or_404(ClassSection, pk=pk)
    form = AdminClassSectionForm(request.POST or None, instance=section)
    if request.method == "POST" and form.is_valid():
        section = form.save()
        messages.success(request, f"Updated {section.section_code}.")
        return redirect("management_class_detail", pk=section.pk)
    return render(
        request,
        "attendance/form.html",
        {"form": form, "title": "Edit Class"},
    )


@login_required
def management_class_delete(request, pk: int):
    get_admin_or_raise(request)
    section = get_object_or_404(ClassSection, pk=pk)
    if request.method == "POST":
        section.delete()
        messages.success(request, "Class section deleted.")
        return redirect("management_class_list")
    return render(
        request,
        "attendance/confirm_delete.html",
        {"object": section, "cancel_url": reverse("management_class_detail", args=[section.pk])},
    )


@login_required
def management_class_import_roster(request, pk: int):
    get_admin_or_raise(request)
    section = get_object_or_404(ClassSection, pk=pk)
    form = RosterImportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        imported, missing = import_existing_students_to_roster(
            section,
            form.cleaned_data["csv_file"],
        )
        messages.success(request, f"Imported {imported} roster rows.")
        if missing:
            messages.warning(
                request,
                f"Skipped {len(missing)} missing students: {', '.join(missing)}.",
            )
        return redirect("management_class_detail", pk=section.pk)
    return render(
        request,
        "attendance/form.html",
        {"form": form, "title": f"Import Roster for {section.section_code}"},
    )


@login_required
def management_enrollment_delete(request, pk: int):
    get_admin_or_raise(request)
    enrollment = get_object_or_404(Enrollment, pk=pk)
    section_pk = enrollment.class_section_id
    if request.method == "POST":
        enrollment.delete()
        messages.success(request, "Student removed from roster.")
        return redirect("management_class_detail", pk=section_pk)
    return render(
        request,
        "attendance/confirm_delete.html",
        {
            "object": enrollment,
            "cancel_url": reverse("management_class_detail", args=[section_pk]),
        },
    )


@login_required
def management_room_list(request):
    get_admin_or_raise(request)
    rooms = Classroom.objects.all()
    return render(request, "attendance/management/room_list.html", {"rooms": rooms})


@login_required
def management_room_create(request):
    get_admin_or_raise(request)
    form = ClassroomForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        room = form.save()
        messages.success(request, f"Created room {room.name}.")
        return redirect("management_room_list")
    return render(request, "attendance/form.html", {"form": form, "title": "Create Room"})


@login_required
def management_room_update(request, pk: int):
    get_admin_or_raise(request)
    room = get_object_or_404(Classroom, pk=pk)
    form = ClassroomForm(request.POST or None, instance=room)
    if request.method == "POST" and form.is_valid():
        room = form.save()
        messages.success(request, f"Updated room {room.name}.")
        return redirect("management_room_list")
    return render(request, "attendance/form.html", {"form": form, "title": "Edit Room"})


@login_required
def management_room_delete(request, pk: int):
    get_admin_or_raise(request)
    room = get_object_or_404(Classroom, pk=pk)
    if request.method == "POST":
        room.delete()
        messages.success(request, "Room deleted.")
        return redirect("management_room_list")
    return render(
        request,
        "attendance/confirm_delete.html",
        {"object": room, "cancel_url": reverse("management_room_list")},
    )


@login_required
def management_attendance_list(request):
    get_admin_or_raise(request)
    sessions = (
        AttendanceSession.objects.select_related("class_section", "class_section__instructor__user", "classroom")
        .annotate(
            present_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.PRESENT)),
            late_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.LATE)),
            absent_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.ABSENT)),
        )
        .order_by("-starts_at")
    )
    return render(
        request,
        "attendance/management/attendance_list.html",
        {"sessions": sessions},
    )


@login_required
def management_attendance_detail(request, pk: int):
    get_admin_or_raise(request)
    session = get_object_or_404(
        AttendanceSession.objects.select_related(
            "class_section",
            "class_section__instructor__user",
            "classroom",
        ),
        pk=pk,
    )
    records = session.records.select_related("student")
    scans = session.scan_events.select_related("student", "classroom")[:25]
    count_map = attendance_status_counts(records)
    return render(
        request,
        "attendance/management/attendance_detail.html",
        {
            "session": session,
            "records": records,
            "scans": scans,
            "present_count": count_map.get(AttendanceRecord.Status.PRESENT, 0),
            "late_count": count_map.get(AttendanceRecord.Status.LATE, 0),
            "absent_count": count_map.get(AttendanceRecord.Status.ABSENT, 0),
        },
    )


@login_required
def management_attendance_live_data(request, pk: int):
    get_admin_or_raise(request)
    session = get_object_or_404(
        AttendanceSession.objects.select_related(
            "class_section",
            "class_section__instructor__user",
            "classroom",
        ),
        pk=pk,
    )
    return JsonResponse(attendance_live_payload(session, read_only=True))


@login_required
def session_list(request):
    if request.user.is_staff:
        return redirect("management_attendance_list")
    instructor = get_instructor_or_raise(request)
    now = timezone.now()
    sessions = (
        AttendanceSession.objects.filter(class_section__instructor=instructor)
        .select_related("class_section", "classroom")
        .annotate(
            present_count=Count(
                "records",
                filter=Q(records__status=AttendanceRecord.Status.PRESENT),
            ),
            late_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.LATE)),
            absent_count=Count("records", filter=Q(records__status=AttendanceRecord.Status.ABSENT)),
        )
        .order_by("class_section__section_code", "starts_at")
    )

    groups_by_section = {}
    summary = {
        "accepting_count": 0,
        "current_upcoming_count": 0,
        "history_count": 0,
        "class_count": 0,
    }
    for session in sessions:
        section = session.class_section
        group = groups_by_section.setdefault(
            section.pk,
            {
                "section": section,
                "all_sessions": [],
                "current_upcoming_sessions": [],
                "recent_sessions": [],
                "focus_session": None,
                "secondary_sessions": [],
                "hidden_recent_count": 0,
            },
        )
        group["all_sessions"].append(session)
        if session.ends_at >= now:
            group["current_upcoming_sessions"].append(session)
            summary["current_upcoming_count"] += 1
            if session.status == AttendanceSession.Status.OPEN and session.is_accepting_taps:
                summary["accepting_count"] += 1
        else:
            group["recent_sessions"].append(session)
            summary["history_count"] += 1

    session_groups = list(groups_by_section.values())
    summary["class_count"] = len(session_groups)
    for group in session_groups:
        group["current_upcoming_sessions"].sort(key=lambda session: (session.starts_at, session.pk))
        group["recent_sessions"].sort(
            key=lambda session: (session.starts_at, session.pk),
            reverse=True,
        )
        current_upcoming = group["current_upcoming_sessions"]
        group["focus_session"] = current_upcoming[0] if current_upcoming else None
        group["secondary_sessions"] = group["current_upcoming_sessions"][1:4]
        group["hidden_recent_count"] = max(len(group["recent_sessions"]) - 3, 0)
        group["recent_sessions"] = group["recent_sessions"][:3]

    def group_sort_key(group):
        focus_session = group["focus_session"]
        if focus_session is not None:
            return (0, focus_session.starts_at.timestamp(), group["section"].section_code)
        latest_recent = group["recent_sessions"][0] if group["recent_sessions"] else None
        if latest_recent is not None:
            return (1, -latest_recent.starts_at.timestamp(), group["section"].section_code)
        return (2, float("inf"), group["section"].section_code)

    session_groups.sort(key=group_sort_key)
    return render(
        request,
        "attendance/session_list.html",
        {
            "session_groups": session_groups,
            "session_summary": summary,
        },
    )


@login_required
def session_create(request):
    deny_staff_attendance_mutation(request)
    instructor = get_instructor_or_raise(request)
    form = AttendanceSessionForm(request.POST or None, instructor=instructor)
    if request.method == "POST" and form.is_valid():
        session = form.save()
        messages.success(request, "Attendance session created.")
        return redirect("session_detail", pk=session.pk)
    return render(request, "attendance/form.html", {"form": form, "title": "Create Attendance Session"})


@login_required
def session_detail(request, pk: int):
    if request.user.is_staff:
        return redirect("management_attendance_detail", pk=pk)
    instructor = get_instructor_or_raise(request)
    session = get_object_or_404(
        AttendanceSession.objects.select_related("class_section", "classroom"),
        pk=pk,
        class_section__instructor=instructor,
    )
    records = session.records.select_related("student")
    scans = session.scan_events.select_related("student", "classroom")[:25]
    count_map = attendance_status_counts(records)
    now = timezone.now()
    return render(
        request,
        "attendance/session_detail.html",
        {
            "session": session,
            "records": records,
            "scans": scans,
            "present_count": count_map.get(AttendanceRecord.Status.PRESENT, 0),
            "late_count": count_map.get(AttendanceRecord.Status.LATE, 0),
            "absent_count": count_map.get(AttendanceRecord.Status.ABSENT, 0),
            "attendance_state": session.attendance_state_label,
            "can_open_attendance": (
                session.status == AttendanceSession.Status.OPEN
                and not session.is_accepting_taps
                and now <= session.ends_at
            ),
            "can_close_attendance": session.is_accepting_taps and now <= session.ends_at,
        },
    )


@login_required
def session_live_data(request, pk: int):
    if request.user.is_staff:
        return redirect("management_attendance_live_data", pk=pk)
    instructor = get_instructor_or_raise(request)
    session = get_object_or_404(
        AttendanceSession.objects.select_related("class_section", "classroom"),
        pk=pk,
        class_section__instructor=instructor,
    )
    return JsonResponse(attendance_live_payload(session, read_only=False))


@login_required
@require_POST
def session_open_attendance(request, pk: int):
    deny_staff_attendance_mutation(request)
    instructor = get_instructor_or_raise(request)
    session = get_object_or_404(
        AttendanceSession,
        pk=pk,
        class_section__instructor=instructor,
    )
    if session.status != AttendanceSession.Status.OPEN:
        messages.error(request, "Only open sessions can accept taps.")
    elif timezone.now() > session.ends_at:
        messages.error(request, "Ended sessions cannot accept taps.")
    elif not session.is_accepting_taps:
        session.is_accepting_taps = True
        session.save(update_fields=["is_accepting_taps", "updated_at"])
        messages.success(request, "Attendance is now accepting taps.")
    return redirect("session_detail", pk=session.pk)


@login_required
@require_POST
def session_close_attendance(request, pk: int):
    deny_staff_attendance_mutation(request)
    instructor = get_instructor_or_raise(request)
    session = get_object_or_404(
        AttendanceSession,
        pk=pk,
        class_section__instructor=instructor,
    )
    if session.is_accepting_taps:
        session.is_accepting_taps = False
        session.save(update_fields=["is_accepting_taps", "updated_at"])
        messages.success(request, "Attendance is no longer accepting taps.")
    return redirect("session_detail", pk=session.pk)


@login_required
def session_update(request, pk: int):
    deny_staff_attendance_mutation(request)
    instructor = get_instructor_or_raise(request)
    session = get_object_or_404(AttendanceSession, pk=pk, class_section__instructor=instructor)
    form = AttendanceSessionForm(request.POST or None, instance=session, instructor=instructor)
    if request.method == "POST" and form.is_valid():
        session = form.save()
        messages.success(request, "Attendance session updated.")
        return redirect("session_detail", pk=session.pk)
    return render(request, "attendance/form.html", {"form": form, "title": "Edit Attendance Session"})


@login_required
def session_delete(request, pk: int):
    deny_staff_attendance_mutation(request)
    instructor = get_instructor_or_raise(request)
    session = get_object_or_404(AttendanceSession, pk=pk, class_section__instructor=instructor)
    if request.method == "POST":
        session.delete()
        messages.success(request, "Attendance session deleted.")
        return redirect("session_list")
    return render(request, "attendance/confirm_delete.html", {"object": session, "cancel_url": reverse("session_detail", args=[session.pk])})


@login_required
def session_export(request, pk: int):
    if request.user.is_staff:
        return redirect("management_attendance_detail", pk=pk)
    instructor = get_instructor_or_raise(request)
    session = get_object_or_404(AttendanceSession, pk=pk, class_section__instructor=instructor)
    response = HttpResponse(content_type="text/csv")
    filename = f"attendance-{session.class_section.section_code}-{session.meeting_date}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(["student_number", "full_name", "status", "tapped_at"])
    for record in session.records.select_related("student").order_by(
        "student__last_name",
        "student__first_name",
    ):
        writer.writerow(
            [
                record.student.student_number,
                record.student.full_name,
                record.status,
                record.tapped_at.isoformat() if record.tapped_at else "",
            ]
        )
    return response
