-- RFID Classroom Attendance System schema for MariaDB/MySQL
CREATE DATABASE IF NOT EXISTS CCCS105 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE CCCS105;

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS attendance_attendancerecord;
DROP TABLE IF EXISTS attendance_scanevent;
DROP TABLE IF EXISTS attendance_enrollment;
DROP TABLE IF EXISTS attendance_attendancesession;
DROP TABLE IF EXISTS attendance_classsection;
DROP TABLE IF EXISTS attendance_student;
DROP TABLE IF EXISTS attendance_classroom;
DROP TABLE IF EXISTS attendance_instructorprofile;
DROP TABLE IF EXISTS auth_user;

CREATE TABLE auth_user (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  password VARCHAR(128) NOT NULL,
  last_login DATETIME NULL,
  is_superuser TINYINT(1) NOT NULL DEFAULT 0,
  username VARCHAR(150) NOT NULL UNIQUE,
  first_name VARCHAR(150) NOT NULL DEFAULT '',
  last_name VARCHAR(150) NOT NULL DEFAULT '',
  email VARCHAR(254) NOT NULL DEFAULT '',
  is_staff TINYINT(1) NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  date_joined DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE attendance_instructorprofile (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  employee_id VARCHAR(32) NOT NULL UNIQUE,
  department VARCHAR(4) NOT NULL,
  contact_number VARCHAR(32) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  user_id BIGINT NOT NULL UNIQUE,
  CONSTRAINT instructor_user_fk FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE attendance_classroom (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(80) NOT NULL UNIQUE,
  scanner_code VARCHAR(40) NOT NULL UNIQUE,
  location VARCHAR(120) NOT NULL DEFAULT '',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE attendance_student (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  student_number VARCHAR(32) NOT NULL UNIQUE,
  first_name VARCHAR(80) NOT NULL,
  middle_name VARCHAR(80) NOT NULL DEFAULT '',
  last_name VARCHAR(80) NOT NULL,
  department VARCHAR(4) NOT NULL,
  year_level SMALLINT UNSIGNED NOT NULL,
  rfid_uid VARCHAR(64) NOT NULL UNIQUE,
  email VARCHAR(254) NOT NULL DEFAULT '',
  contact_number VARCHAR(32) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  CONSTRAINT student_year_chk CHECK (year_level BETWEEN 1 AND 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE attendance_classsection (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  subject_code VARCHAR(20) NOT NULL,
  subject_title VARCHAR(120) NOT NULL,
  department VARCHAR(4) NOT NULL,
  year_level SMALLINT UNSIGNED NOT NULL,
  section_letter VARCHAR(1) NOT NULL,
  section_code VARCHAR(12) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  instructor_id BIGINT NOT NULL,
  CONSTRAINT classsection_instructor_fk FOREIGN KEY (instructor_id) REFERENCES attendance_instructorprofile(id) ON DELETE CASCADE,
  CONSTRAINT classsection_unique UNIQUE (instructor_id, subject_code, department, year_level, section_letter),
  CONSTRAINT classsection_year_chk CHECK (year_level BETWEEN 1 AND 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX classsection_section_code_idx ON attendance_classsection(section_code);

CREATE TABLE attendance_attendancesession (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  meeting_date DATE NOT NULL,
  starts_at DATETIME NOT NULL,
  ends_at DATETIME NOT NULL,
  grace_minutes SMALLINT UNSIGNED NOT NULL DEFAULT 15,
  status VARCHAR(12) NOT NULL DEFAULT 'open',
  is_accepting_taps TINYINT(1) NOT NULL DEFAULT 0,
  notes TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  classroom_id BIGINT NOT NULL,
  class_section_id BIGINT NOT NULL,
  CONSTRAINT session_classroom_fk FOREIGN KEY (classroom_id) REFERENCES attendance_classroom(id) ON DELETE RESTRICT,
  CONSTRAINT session_classsection_fk FOREIGN KEY (class_section_id) REFERENCES attendance_classsection(id) ON DELETE CASCADE,
  CONSTRAINT session_unique UNIQUE (class_section_id, meeting_date, starts_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX session_classroom_idx ON attendance_attendancesession(classroom_id);

CREATE TABLE attendance_enrollment (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  enrolled_at DATETIME NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  class_section_id BIGINT NOT NULL,
  student_id BIGINT NOT NULL,
  CONSTRAINT enrollment_classsection_fk FOREIGN KEY (class_section_id) REFERENCES attendance_classsection(id) ON DELETE CASCADE,
  CONSTRAINT enrollment_student_fk FOREIGN KEY (student_id) REFERENCES attendance_student(id) ON DELETE CASCADE,
  CONSTRAINT enrollment_unique UNIQUE (class_section_id, student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX enrollment_student_idx ON attendance_enrollment(student_id);

CREATE TABLE attendance_scanevent (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  uid VARCHAR(64) NOT NULL,
  scanner_code VARCHAR(40) NOT NULL DEFAULT '',
  result VARCHAR(12) NOT NULL,
  reason VARCHAR(80) NOT NULL DEFAULT '',
  raw_payload JSON NOT NULL,
  created_at DATETIME NOT NULL,
  classroom_id BIGINT NULL,
  session_id BIGINT NULL,
  student_id BIGINT NULL,
  CONSTRAINT scan_classroom_fk FOREIGN KEY (classroom_id) REFERENCES attendance_classroom(id) ON DELETE SET NULL,
  CONSTRAINT scan_session_fk FOREIGN KEY (session_id) REFERENCES attendance_attendancesession(id) ON DELETE SET NULL,
  CONSTRAINT scan_student_fk FOREIGN KEY (student_id) REFERENCES attendance_student(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX scan_uid_idx ON attendance_scanevent(uid);
CREATE INDEX scan_session_idx ON attendance_scanevent(session_id);

CREATE TABLE attendance_attendancerecord (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  status VARCHAR(12) NOT NULL DEFAULT 'absent',
  tapped_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  session_id BIGINT NOT NULL,
  student_id BIGINT NOT NULL,
  source_scan_id BIGINT NULL,
  CONSTRAINT record_session_fk FOREIGN KEY (session_id) REFERENCES attendance_attendancesession(id) ON DELETE CASCADE,
  CONSTRAINT record_student_fk FOREIGN KEY (student_id) REFERENCES attendance_student(id) ON DELETE CASCADE,
  CONSTRAINT record_scan_fk FOREIGN KEY (source_scan_id) REFERENCES attendance_scanevent(id) ON DELETE SET NULL,
  CONSTRAINT record_unique UNIQUE (session_id, student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX record_student_idx ON attendance_attendancerecord(student_id);

SET FOREIGN_KEY_CHECKS = 1;
