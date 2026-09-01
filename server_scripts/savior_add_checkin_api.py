employee_code = frappe.form_dict.get("employee_code")
timestamp = frappe.form_dict.get("timestamp")
device_id = frappe.form_dict.get("device_id") or "Savior"
log_type = frappe.form_dict.get("log_type")

if not employee_code or not timestamp:
    frappe.throw("Employee Code and Timestamp are required.")

employee_code = str(employee_code).strip()
if not employee_code:
    frappe.throw("Employee Code is required.")

timestamp = frappe.utils.get_datetime(timestamp).replace(microsecond=0)

if log_type:
    log_type = str(log_type).strip().upper()
    if log_type not in ["IN", "OUT"]:
        frappe.throw("Log Type must be IN or OUT.")

employees = frappe.get_all(
    "Employee",
    filters={"custom_emp_code": employee_code, "status": "Active"},
    fields=["name"],
    limit_page_length=2
)

if not employees:
    frappe.throw("No active Employee is mapped to the supplied EMP Code.")

if len(employees) > 1:
    frappe.throw("More than one active Employee is mapped to the supplied EMP Code.")

employee = employees[0]["name"]
normalized_log_type = log_type or ""
existing = frappe.get_all(
    "Employee Checkin",
    filters={
        "employee": employee,
        "time": timestamp,
        "log_type": normalized_log_type
    },
    fields=["name", "time", "log_type"],
    limit_page_length=1
)

if existing:
    frappe.flags = {
        "protocol_version": 1,
        "created": False,
        "duplicate": True,
        "checkin": existing[0]["name"],
        "employee": employee,
        "timestamp": str(existing[0]["time"]),
        "log_type": existing[0]["log_type"] or ""
    }
else:
    doc = frappe.get_doc({
        "doctype": "Employee Checkin",
        "employee": employee,
        "time": timestamp,
        "device_id": device_id,
        "log_type": log_type or None,
        "skip_auto_attendance": 0
    })
    doc.insert()
    stored_timestamp = frappe.utils.get_datetime(doc.time).replace(microsecond=0)
    if stored_timestamp != timestamp:
        frappe.throw("Stored Employee Checkin timestamp does not match the supplied timestamp.")
    frappe.flags = {
        "protocol_version": 1,
        "created": True,
        "duplicate": False,
        "checkin": doc.name,
        "employee": employee,
        "timestamp": str(stored_timestamp),
        "log_type": doc.log_type or ""
    }
