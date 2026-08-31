"""Thin pywin32 adapter; imported only on Windows."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
import sys

import servicemanager
import win32event
import win32service
import win32serviceutil

from .config import Settings
from .logging_setup import configure_logging
from .runner import SaviorRunner


class OneHashSaviorService(win32serviceutil.ServiceFramework):
    _svc_name_ = "OneHashSaviorIntegration"
    _svc_display_name_ = "OneHash Savior Integration"
    _svc_description_ = "Synchronizes Savior biometric punches to OneHash HRMS."

    def __init__(self, args):
        super().__init__(args)
        self.wait_stop = win32event.CreateEvent(None, 0, 0, None)
        self.runner: SaviorRunner | None = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.runner:
            self.runner.stop()
        win32event.SetEvent(self.wait_stop)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg(f"{self._svc_name_} starting")
        try:
            settings = Settings.load()
            configure_logging(settings.log_file)
            self.runner = SaviorRunner.build(settings)
            self.runner.prepare()
            self.runner.run_forever()
        except Exception:
            logging.getLogger(__name__).exception("Windows Service stopped due to an error")
            servicemanager.LogErrorMsg(f"{self._svc_name_} stopped due to an error")
            raise


def handle_command_line() -> None:
    project_root = Path(__file__).resolve().parents[1]
    if any(command in sys.argv[1:] for command in ("install", "update")):
        service_executable = Path(sys.prefix) / "pythonservice.exe"
        dll_directory = Path(win32service.__file__).resolve().parent.parent / "pywin32_system32"
        python_tag = f"{sys.version_info.major}{sys.version_info.minor}"
        service_dlls = (
            dll_directory / f"pywintypes{python_tag}.dll",
            dll_directory / f"pythoncom{python_tag}.dll",
            Path(sys.base_prefix) / f"python{python_tag}.dll",
        )
        for source in service_dlls:
            if not source.is_file():
                raise RuntimeError(f"Required pywin32 service DLL not found: {source}")
            target = service_executable.parent / source.name
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
    service_class = str(
        project_root / "savior_client.windows_service.OneHashSaviorService"
    )
    win32serviceutil.HandleCommandLine(
        OneHashSaviorService,
        serviceClassString=service_class,
    )
