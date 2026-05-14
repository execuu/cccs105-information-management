from __future__ import annotations

from django import forms
from django.contrib.auth.models import User

from attendance.models import (
    AttendanceSession,
    ClassSection,
    Classroom,
    Department,
    Enrollment,
    InstructorProfile,
    Student,
)


class TailwindFormMixin:
    input_class = (
        "w-full rounded-md border border-line bg-white px-3 py-2 text-sm text-ink "
        "shadow-sm outline-none transition focus:border-accent focus:ring-2 "
        "focus:ring-blue-100"
    )

    def style_fields(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "h-4 w-4 rounded border-line text-accent")
            else:
                widget.attrs.setdefault("class", self.input_class)
        return self


class ClassSectionForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = ClassSection
        fields = [
            "subject_code",
            "subject_title",
            "department",
            "year_level",
            "section_letter",
            "is_active",
        ]
        widgets = {
            "section_letter": forms.TextInput(attrs={"maxlength": 1}),
            "year_level": forms.NumberInput(attrs={"min": 1, "max": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()

    def clean_section_letter(self):
        return self.cleaned_data["section_letter"].strip().upper()


class AdminClassSectionForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = ClassSection
        fields = [
            "instructor",
            "subject_code",
            "subject_title",
            "department",
            "year_level",
            "section_letter",
            "is_active",
        ]
        widgets = {
            "section_letter": forms.TextInput(attrs={"maxlength": 1}),
            "year_level": forms.NumberInput(attrs={"min": 1, "max": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["instructor"].queryset = InstructorProfile.objects.select_related(
            "user"
        ).order_by("user__last_name", "user__first_name")
        self.style_fields()

    def clean_section_letter(self):
        return self.cleaned_data["section_letter"].strip().upper()


class AdminInstructorForm(TailwindFormMixin, forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password = forms.CharField(required=False, widget=forms.PasswordInput)
    employee_id = forms.CharField(max_length=32)
    department = forms.ChoiceField(choices=Department.choices)
    contact_number = forms.CharField(required=False, max_length=32)

    def __init__(self, *args, profile: InstructorProfile | None = None, **kwargs):
        self.profile = profile
        initial = kwargs.pop("initial", {})
        if profile is not None:
            initial = {
                **initial,
                "username": profile.user.username,
                "first_name": profile.user.first_name,
                "last_name": profile.user.last_name,
                "email": profile.user.email,
                "employee_id": profile.employee_id,
                "department": profile.department,
                "contact_number": profile.contact_number,
            }
        super().__init__(*args, initial=initial, **kwargs)
        if profile is None:
            self.fields["password"].required = True
        else:
            self.fields["password"].help_text = "Leave blank to keep the current password."
        self.style_fields()

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        users = User.objects.filter(username=username)
        if self.profile is not None:
            users = users.exclude(pk=self.profile.user_id)
        if users.exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean_employee_id(self):
        employee_id = self.cleaned_data["employee_id"].strip().upper()
        profiles = InstructorProfile.objects.filter(employee_id=employee_id)
        if self.profile is not None:
            profiles = profiles.exclude(pk=self.profile.pk)
        if profiles.exists():
            raise forms.ValidationError("An instructor with this employee ID already exists.")
        return employee_id

    def save(self) -> InstructorProfile:
        data = self.cleaned_data
        if self.profile is None:
            user = User.objects.create_user(
                username=data["username"],
                password=data["password"],
                first_name=data["first_name"].strip(),
                last_name=data["last_name"].strip(),
                email=data["email"].strip(),
                is_staff=False,
            )
            return InstructorProfile.objects.create(
                user=user,
                employee_id=data["employee_id"],
                department=data["department"],
                contact_number=data["contact_number"].strip(),
            )

        user = self.profile.user
        user.username = data["username"]
        user.first_name = data["first_name"].strip()
        user.last_name = data["last_name"].strip()
        user.email = data["email"].strip()
        user.is_staff = False
        if data["password"]:
            user.set_password(data["password"])
        user.save()
        self.profile.employee_id = data["employee_id"]
        self.profile.department = data["department"]
        self.profile.contact_number = data["contact_number"].strip()
        self.profile.save()
        return self.profile


class ClassroomForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Classroom
        fields = ["name", "scanner_code", "location", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class StudentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Student
        fields = [
            "student_number",
            "first_name",
            "middle_name",
            "last_name",
            "department",
            "year_level",
            "rfid_uid",
            "email",
            "contact_number",
        ]
        widgets = {
            "year_level": forms.NumberInput(attrs={"min": 1, "max": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class AttendanceSessionForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = AttendanceSession
        fields = [
            "class_section",
            "classroom",
            "meeting_date",
            "starts_at",
            "ends_at",
            "grace_minutes",
            "status",
            "notes",
        ]
        widgets = {
            "meeting_date": forms.DateInput(attrs={"type": "date"}),
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, instructor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instructor is not None:
            self.fields["class_section"].queryset = ClassSection.objects.filter(
                instructor=instructor,
                is_active=True,
            )
        self.style_fields()


class EnrollmentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ["student", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = Student.objects.order_by("last_name", "first_name")
        self.style_fields()


class RosterImportForm(TailwindFormMixin, forms.Form):
    csv_file = forms.CharField(
        label="CSV data",
        widget=forms.Textarea(
            attrs={
                "rows": 10,
                "placeholder": (
                    "student_number,first_name,middle_name,last_name,department,"
                    "year_level,rfid_uid,email"
                ),
            }
        ),
        help_text=(
            "Paste CSV with columns: student_number, first_name, middle_name, "
            "last_name, department, year_level, rfid_uid, email."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["csv_file"].widget.attrs.setdefault("class", self.input_class + " font-mono")


class StudentSearchForm(TailwindFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    department = forms.ChoiceField(
        required=False,
        choices=[("", "All departments"), *Department.choices],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()
