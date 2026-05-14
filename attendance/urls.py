from django.urls import path

from attendance import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("management/", views.management_dashboard, name="management_dashboard"),
    path(
        "management/instructors/",
        views.management_instructor_list,
        name="management_instructor_list",
    ),
    path(
        "management/instructors/new/",
        views.management_instructor_create,
        name="management_instructor_create",
    ),
    path(
        "management/instructors/<int:pk>/edit/",
        views.management_instructor_update,
        name="management_instructor_update",
    ),
    path(
        "management/instructors/<int:pk>/delete/",
        views.management_instructor_delete,
        name="management_instructor_delete",
    ),
    path("management/classes/", views.management_class_list, name="management_class_list"),
    path(
        "management/classes/search/",
        views.management_class_search,
        name="management_class_search",
    ),
    path(
        "management/classes/new/",
        views.management_class_create,
        name="management_class_create",
    ),
    path(
        "management/classes/<int:pk>/",
        views.management_class_detail,
        name="management_class_detail",
    ),
    path(
        "management/classes/<int:pk>/edit/",
        views.management_class_update,
        name="management_class_update",
    ),
    path(
        "management/classes/<int:pk>/delete/",
        views.management_class_delete,
        name="management_class_delete",
    ),
    path(
        "management/classes/<int:pk>/import-roster/",
        views.management_class_import_roster,
        name="management_class_import_roster",
    ),
    path(
        "management/enrollments/<int:pk>/delete/",
        views.management_enrollment_delete,
        name="management_enrollment_delete",
    ),
    path("management/students/", views.student_list, name="management_student_list"),
    path(
        "management/students/search/",
        views.management_student_search,
        name="management_student_search",
    ),
    path("management/students/new/", views.student_create, name="management_student_create"),
    path(
        "management/students/<int:pk>/edit/",
        views.student_update,
        name="management_student_update",
    ),
    path(
        "management/students/<int:pk>/delete/",
        views.student_delete,
        name="management_student_delete",
    ),
    path("management/rooms/", views.management_room_list, name="management_room_list"),
    path("management/rooms/new/", views.management_room_create, name="management_room_create"),
    path(
        "management/rooms/<int:pk>/edit/",
        views.management_room_update,
        name="management_room_update",
    ),
    path(
        "management/rooms/<int:pk>/delete/",
        views.management_room_delete,
        name="management_room_delete",
    ),
    path(
        "management/attendance/",
        views.management_attendance_list,
        name="management_attendance_list",
    ),
    path(
        "management/attendance/<int:pk>/",
        views.management_attendance_detail,
        name="management_attendance_detail",
    ),
    path(
        "management/attendance/<int:pk>/live/",
        views.management_attendance_live_data,
        name="management_attendance_live_data",
    ),
    path("sections/", views.section_list, name="section_list"),
    path("sections/search/", views.section_search, name="section_search"),
    path("sections/new/", views.section_create, name="section_create"),
    path("sections/<int:pk>/", views.section_detail, name="section_detail"),
    path("sections/<int:pk>/edit/", views.section_update, name="section_update"),
    path("sections/<int:pk>/delete/", views.section_delete, name="section_delete"),
    path(
        "sections/<int:pk>/import-roster/",
        views.section_import_roster,
        name="section_import_roster",
    ),
    path("enrollments/<int:pk>/delete/", views.enrollment_delete, name="enrollment_delete"),
    path("students/", views.student_list, name="student_list"),
    path("students/new/", views.student_create, name="student_create"),
    path(
        "students/rfid-capture/latest/",
        views.student_rfid_capture_latest,
        name="student_rfid_capture_latest",
    ),
    path("students/<int:pk>/edit/", views.student_update, name="student_update"),
    path("students/<int:pk>/delete/", views.student_delete, name="student_delete"),
    path("sessions/", views.session_list, name="session_list"),
    path("sessions/new/", views.session_create, name="session_create"),
    path(
        "sessions/<int:pk>/open-attendance/",
        views.session_open_attendance,
        name="session_open_attendance",
    ),
    path(
        "sessions/<int:pk>/close-attendance/",
        views.session_close_attendance,
        name="session_close_attendance",
    ),
    path("sessions/<int:pk>/", views.session_detail, name="session_detail"),
    path("sessions/<int:pk>/live/", views.session_live_data, name="session_live_data"),
    path("sessions/<int:pk>/edit/", views.session_update, name="session_update"),
    path("sessions/<int:pk>/delete/", views.session_delete, name="session_delete"),
    path("sessions/<int:pk>/export/", views.session_export, name="session_export"),
    path("api/scan/", views.api_scan, name="api_scan"),
]
