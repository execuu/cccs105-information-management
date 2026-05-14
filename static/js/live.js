(function () {
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function debounce(callback, wait) {
    let timer = null;
    return function debounced() {
      window.clearTimeout(timer);
      timer = window.setTimeout(callback, wait);
    };
  }

  function tableEmptyRow(message, colspan) {
    return `<tr><td class="px-4 py-6 text-muted" colspan="${colspan}">${escapeHtml(message)}</td></tr>`;
  }

  function renderStudents(payload) {
    if (!payload.students.length) {
      return tableEmptyRow("No students found.", 6);
    }
    return payload.students
      .map(
        (student) => `
          <tr>
            <td class="px-4 py-3">${escapeHtml(student.student_number)}</td>
            <td class="px-4 py-3 font-medium">${escapeHtml(student.full_name)}</td>
            <td class="px-4 py-3">${escapeHtml(student.department)}</td>
            <td class="px-4 py-3">${escapeHtml(student.year_level)}</td>
            <td class="px-4 py-3">${escapeHtml(student.rfid_uid)}</td>
            <td class="px-4 py-3 text-right">
              <a class="text-accent" href="${escapeHtml(student.edit_url)}">Edit</a>
              <a class="ml-2 text-red-700" href="${escapeHtml(student.delete_url)}">Delete</a>
            </td>
          </tr>
        `
      )
      .join("");
  }

  function renderSections(payload, includeInstructor) {
    if (!payload.sections.length) {
      return tableEmptyRow("No classes found.", includeInstructor ? 6 : 5);
    }
    return payload.sections
      .map((section) => {
        const instructorCell = includeInstructor
          ? `<td class="px-4 py-3">${escapeHtml(section.instructor)}</td>`
          : "";
        const activeOrYearCell = includeInstructor
          ? `<td class="px-4 py-3">${escapeHtml(section.active_label)}</td>`
          : `<td class="px-4 py-3">${escapeHtml(section.year_level)}</td>`;
        return `
          <tr>
            <td class="px-4 py-3 font-medium">${escapeHtml(section.section_code)}</td>
            <td class="px-4 py-3">${escapeHtml(section.subject)}</td>
            ${instructorCell}
            <td class="px-4 py-3">${escapeHtml(section.department)}</td>
            ${activeOrYearCell}
            <td class="px-4 py-3 text-right"><a class="text-accent" href="${escapeHtml(section.detail_url)}">Open</a></td>
          </tr>
        `;
      })
      .join("");
  }

  function setupLiveSearch() {
    document.querySelectorAll("[data-live-search-url]").forEach((form) => {
      const target = document.querySelector(form.dataset.liveSearchTarget);
      if (!target) return;

      async function refresh() {
        const params = new URLSearchParams(new FormData(form));
        const response = await fetch(`${form.dataset.liveSearchUrl}?${params.toString()}`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return;
        const payload = await response.json();
        if (form.dataset.liveSearchKind === "students") {
          target.innerHTML = renderStudents(payload);
        } else if (form.dataset.liveSearchKind === "management-sections") {
          target.innerHTML = renderSections(payload, true);
        } else if (form.dataset.liveSearchKind === "instructor-sections") {
          target.innerHTML = renderSections(payload, false);
        }
      }

      const debouncedRefresh = debounce(refresh, 250);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        refresh();
      });
      form.querySelectorAll("input, select").forEach((control) => {
        control.addEventListener("input", debouncedRefresh);
        control.addEventListener("change", refresh);
      });
    });
  }

  function renderRecords(records) {
    if (!records.length) {
      return tableEmptyRow("No attendance records yet.", 3);
    }
    return records
      .map(
        (record) => `
          <tr>
            <td class="px-4 py-3">${escapeHtml(record.student_number)} - ${escapeHtml(record.student_name)}</td>
            <td class="px-4 py-3">${escapeHtml(record.status)}</td>
            <td class="px-4 py-3">${escapeHtml(record.tapped_at)}</td>
          </tr>
        `
      )
      .join("");
  }

  function renderScans(scans) {
    if (!scans.length) {
      return '<p class="px-4 py-6 text-sm text-muted">No scans for this session.</p>';
    }
    return scans
      .map(
        (scan) => `
          <div class="px-4 py-3 text-sm">
            <div class="flex justify-between gap-2">
              <span class="font-medium">${escapeHtml(scan.uid)}</span>
              <span>${escapeHtml(scan.result)}</span>
            </div>
            <p class="mt-1 text-muted">${escapeHtml(scan.detail)}</p>
          </div>
        `
      )
      .join("");
  }

  function setupLiveAttendance() {
    document.querySelectorAll("[data-live-attendance-url]").forEach((container) => {
      const recordsTarget = container.querySelector("[data-live-records]");
      const scansTarget = container.querySelector("[data-live-scans]");
      const stateTarget = container.querySelector("[data-live-attendance-state]");
      let inFlight = false;

      async function refresh() {
        if (document.hidden || inFlight) return;
        inFlight = true;
        try {
          const response = await fetch(container.dataset.liveAttendanceUrl, {
            headers: { Accept: "application/json" },
          });
          if (!response.ok) return;
          const payload = await response.json();
          if (stateTarget) stateTarget.textContent = payload.attendance_state;
          Object.entries(payload.counts).forEach(([status, count]) => {
            const target = container.querySelector(`[data-live-count="${status}"]`);
            if (target) target.textContent = count;
          });
          if (recordsTarget) recordsTarget.innerHTML = renderRecords(payload.records);
          if (scansTarget) scansTarget.innerHTML = renderScans(payload.scans);
        } finally {
          inFlight = false;
        }
      }

      refresh();
      const interval = window.setInterval(refresh, 2000);
      document.addEventListener("visibilitychange", refresh);
      window.addEventListener("beforeunload", () => window.clearInterval(interval));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupLiveSearch();
    setupLiveAttendance();
  });
})();
