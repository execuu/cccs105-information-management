from django.contrib import admin

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


@admin.register(InstructorProfile)
class InstructorProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "employee_id", "department", "contact_number")
    search_fields = ("user__first_name", "user__last_name", "employee_id")
    list_filter = ("department",)


@admin.register(Classroom)
class ClassroomAdmin(admin.ModelAdmin):
    list_display = ("name", "scanner_code", "location", "is_active")
    search_fields = ("name", "scanner_code", "location")
    list_filter = ("is_active",)


@admin.register(ClassSection)
class ClassSectionAdmin(admin.ModelAdmin):
    list_display = (
        "subject_code",
        "subject_title",
        "section_code",
        "department",
        "year_level",
        "section_letter",
        "instructor",
        "is_active",
    )
    search_fields = ("subject_code", "subject_title", "section_code")
    list_filter = ("department", "year_level", "section_letter", "is_active")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "student_number",
        "last_name",
        "first_name",
        "department",
        "year_level",
        "rfid_uid",
    )
    search_fields = ("student_number", "first_name", "middle_name", "last_name", "rfid_uid")
    list_filter = ("department", "year_level")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "class_section", "is_active", "enrolled_at")
    search_fields = ("student__student_number", "student__last_name", "class_section__section_code")
    list_filter = ("is_active", "class_section__department", "class_section__year_level")


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = (
        "class_section",
        "classroom",
        "meeting_date",
        "starts_at",
        "ends_at",
        "grace_minutes",
        "status",
        "is_accepting_taps",
    )
    search_fields = ("class_section__section_code", "classroom__name", "classroom__scanner_code")
    list_filter = ("status", "is_accepting_taps", "meeting_date", "class_section__department")


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("student", "session", "status", "tapped_at")
    search_fields = ("student__student_number", "student__last_name", "session__class_section__section_code")
    list_filter = ("status", "session__meeting_date")


@admin.register(ScanEvent)
class ScanEventAdmin(admin.ModelAdmin):
    list_display = ("uid", "scanner_code", "result", "reason", "student", "created_at")
    search_fields = ("uid", "scanner_code", "student__student_number", "reason")
    list_filter = ("result", "reason", "created_at")
