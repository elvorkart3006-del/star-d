"""Export an opt-in Windows Task Scheduler definition without registering a task."""

import argparse
import csv
import re
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path
from xml.etree import ElementTree as xml

import dc_daily_report as daily
import paper_trading as paper


NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
TASK_FILENAME = "DC_Paper_Daily_Report.task.xml"


def make_task(sid, executable, project, first_day, *, refresh=True):
    if not re.fullmatch(r"S-1-(?:[0-9]+-)*[0-9]+", sid):
        raise ValueError("A valid current Windows user SID is required.")
    project = Path(project).resolve()
    executable = Path(executable).resolve()
    script = project / "dc_daily_report.py"
    task = xml.Element("Task", {"xmlns": NAMESPACE, "version": "1.2"})

    def put(parent, name, value):
        xml.SubElement(parent, name).text = value

    registration = xml.SubElement(task, "RegistrationInfo")
    put(registration, "Description", "Archive DC 5% PAPER reports. Optional historical Kite reads only.")
    triggers = xml.SubElement(task, "Triggers")
    calendar = xml.SubElement(triggers, "CalendarTrigger")
    boundary = datetime.combine(first_day, time(18), paper.IST).isoformat()
    put(calendar, "StartBoundary", boundary)
    put(calendar, "Enabled", "true")
    put(xml.SubElement(calendar, "ScheduleByDay"), "DaysInterval", "1")
    logon = xml.SubElement(triggers, "LogonTrigger")
    put(logon, "Enabled", "true")
    put(logon, "UserId", sid)

    principal = xml.SubElement(xml.SubElement(task, "Principals"), "Principal", {"id": "CurrentUser"})
    put(principal, "UserId", sid)
    put(principal, "LogonType", "InteractiveToken")
    put(principal, "RunLevel", "LeastPrivilege")
    settings = xml.SubElement(task, "Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"),
        ("WakeToRun", "false"),
        ("ExecutionTimeLimit", "PT15M"),
    ):
        put(settings, name, value)
    action = xml.SubElement(xml.SubElement(task, "Actions", {"Context": "CurrentUser"}), "Exec")
    put(action, "Command", str(executable))
    arguments = f'-B "{script}" --catch-up --scheduled'
    if refresh:
        arguments += " --refresh"
    put(action, "Arguments", arguments)
    put(action, "WorkingDirectory", str(project))
    xml.indent(task)
    return xml.tostring(task, encoding="utf-16", xml_declaration=True)


def current_user_sid():
    result = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, check=True,
    )
    return next(csv.reader(result.stdout.strip().splitlines()))[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-only", action="store_true", help="Use saved files, with no Kite data refresh.")
    parser.add_argument("--output", type=Path, help="Task XML file to create.")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("Export this task on the Windows account where it will run.")
    output = args.output or daily.REPORT_DIR / TASK_FILENAME
    definition = make_task(
        current_user_sid(), sys.executable, Path(__file__).resolve().parent,
        paper.now_ist().date(), refresh=not args.local_only,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("xb") as file:
            file.write(definition)
    except FileExistsError:
        parser.error("The task XML already exists. Use --output to export a separate copy.")
    print(f"Task definition saved: {output.resolve()}")
    print("Import it in Windows Task Scheduler to opt in to the daily run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
