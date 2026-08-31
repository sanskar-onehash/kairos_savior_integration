employee_code = frappe.form_dict.get("employee_code")
timestamp = frappe.form_dict.get("timestamp")
device_id = frappe.form_dict.get("device_id") or "Savior"
log_type = frappe.form_dict.get("log_type")

if not employee_code or not timestamp:
    frappe.throw("Employee Code and Timestamp are required.")

employee_code = str(employee_code).strip()
if not employee_code:
    frappe.throw("Employee Code is required.")

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

frappe.call(
    "hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field",
    employee_field_value=employees[0]["name"],
    employee_fieldname="name",
    timestamp=timestamp,
    device_id=device_id,
    log_type=log_type,
    skip_auto_attendance=0
)

frappe.flags = {"result": {"created": True}}
