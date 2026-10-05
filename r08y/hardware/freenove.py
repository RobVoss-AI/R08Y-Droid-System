"""Bridge to the official Freenove FNK0043 Python modules.

The official kit code owns the board-specific PWM channels, GPIO assignments,
and PCB-version handling. R08Y loads that installed code at runtime and exposes
its public classes through a narrow adapter boundary.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

REPO_DIRNAME = "Freenove_4WD_Smart_Car_Kit_for_Raspberry_Pi"
REQUIRED_MODULES = ("motor", "servo", "ultrasonic", "adc")


def _candidate_paths(explicit_path: Optional[str] = None) -> Iterable[Path]:
    if explicit_path:
        yield Path(explicit_path).expanduser()
    env_path = os.environ.get("R08Y_FREENOVE_PATH")
    if env_path:
        yield Path(env_path).expanduser()
    yield Path.home() / REPO_DIRNAME / "Code" / "Server"
    yield Path("/opt/freenove/Code/Server")
    yield Path("/opt") / REPO_DIRNAME / "Code" / "Server"


def discover_freenove_path(explicit_path: Optional[str] = None) -> Optional[Path]:
    """Return a verified Freenove Server directory, if one is installed."""
    seen = set()
    for candidate in _candidate_paths(explicit_path):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_dir() and all((resolved / f"{name}.py").is_file() for name in REQUIRED_MODULES):
            return resolved
    return None


@contextmanager
def _temporary_sys_path(path: Path):
    value = str(path)
    inserted = value not in sys.path
    if inserted:
        sys.path.insert(0, value)
    try:
        yield
    finally:
        if inserted and value in sys.path:
            sys.path.remove(value)


class FreenoveLibrary:
    """Load selected classes from a verified official Freenove checkout."""

    def __init__(self, path: Optional[str] = None):
        resolved = discover_freenove_path(path)
        if resolved is None:
            searched = ", ".join(str(p) for p in _candidate_paths(path))
            raise FileNotFoundError(
                "Official Freenove Code/Server directory not found. "
                f"Set freenove.library_path or R08Y_FREENOVE_PATH. Searched: {searched}"
            )
        self.path = resolved

    def load_module(self, module_name: str) -> ModuleType:
        if module_name not in REQUIRED_MODULES:
            raise ValueError(f"Unsupported Freenove module: {module_name}")
        path_id = hashlib.sha256(str(self.path).encode()).hexdigest()[:12]
        alias = f"_r08y_freenove_{path_id}_{module_name}"
        existing = sys.modules.get(alias)
        if existing is not None:
            return existing

        module_file = self.path / f"{module_name}.py"
        spec = importlib.util.spec_from_file_location(alias, module_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load Freenove module at {module_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        try:
            with _temporary_sys_path(self.path):
                spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(alias, None)
            raise
        return module

    def load_class(self, module_name: str, class_name: str):
        module = self.load_module(module_name)
        try:
            return getattr(module, class_name)
        except AttributeError as exc:
            raise ImportError(
                f"Freenove module {module_name}.py does not provide {class_name}"
            ) from exc

    def module_status(self) -> dict[str, bool]:
        return {name: (self.path / f"{name}.py").is_file() for name in REQUIRED_MODULES}

    def validate_parameter_file(self) -> tuple[Path, int]:
        """Return the validated Freenove parameter file and PCB version."""
        params_path = self.path / "params.json"
        if not params_path.is_file():
            raise FileNotFoundError(
                f"{params_path} is missing. Run the official Freenove setup/test once "
                "and confirm the PCB version before enabling light sensors."
            )
        try:
            params = json.loads(params_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid Freenove parameter file: {params_path}") from exc
        required = ("Connect_Version", "Pcb_Version", "Pi_Version")
        invalid = [name for name in required if params.get(name) not in (1, 2)]
        if invalid:
            raise ValueError(
                f"Freenove parameters are not confirmed in {params_path}: "
                + ", ".join(invalid)
            )
        version = params["Pcb_Version"]
        return params_path, version

    @contextmanager
    def parameter_working_directory(self):
        """Use the Freenove directory only while its ADC reads params.json.

        The official ParameterManager uses a relative filename. Callers must
        use this during sequential startup, before other R08Y tasks begin.
        """
        self.validate_parameter_file()

        previous = Path.cwd()
        os.chdir(self.path)
        try:
            yield
        finally:
            os.chdir(previous)
