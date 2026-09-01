"""Thin pywin32 adapter; imported only on Windows."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
import sys


def _service_home() -> Path:
    service_home = Path(sys.executable).resolve().parent
    if service_home.name.lower() == "scripts":
        service_home = service_home.parent
    return service_home


def _stage_service_host_files() -> None:
    service_home = _service_home()
    site_packages = service_home / "Lib" / "site-packages"
    win32_directory = site_packages / "win32"
    dll_directory = site_packages / "pywin32_system32"
    python_tag = f"{sys.version_info.major}{sys.version_info.minor}"
    service_files = (
        win32_directory / "servicemanager.pyd",
        dll_directory / f"pywintypes{python_tag}.dll",
        dll_directory / f"pythoncom{python_tag}.dll",
        Path(sys.base_prefix) / f"python{python_tag}.dll",
    )
    for source in service_files:
        if not source.is_file():
            raise RuntimeError(f"Required pywin32 service file not found: {source}")
        target = service_home / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)


def _add_service_site_paths() -> None:
    site_packages = _service_home() / "Lib" / "site-packages"
    for path in (site_packages / "win32" / "lib", site_packages / "win32", site_packages):
        path_text = str(path)
        if path.is_dir() and path_text not in sys.path:
            sys.path.insert(0, path_text)


if any(command in sys.argv[1:] for command in ("install", "update")):
    _stage_service_host_files()
_add_service_site_paths()

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
    service_class = str(
        project_root / "savior_client.windows_service.OneHashSaviorService"
    )
    win32serviceutil.HandleCommandLine(
        OneHashSaviorService,
        serviceClassString=service_class,
    )
