import json

import pytest

from r08y.hardware.freenove import FreenoveLibrary, discover_freenove_path


def test_freenove_library_loads_only_verified_tree(tmp_path):
    for module_name in ("motor", "servo", "ultrasonic", "adc"):
        (tmp_path / f"{module_name}.py").write_text(
            f"class Marker:\n    module = '{module_name}'\n"
        )

    assert discover_freenove_path(str(tmp_path)) == tmp_path.resolve()
    library = FreenoveLibrary(str(tmp_path))
    marker = library.load_class("motor", "Marker")
    assert marker.module == "motor"
    assert all(library.module_status().values())


def test_incomplete_tree_is_not_accepted(tmp_path):
    (tmp_path / "motor.py").write_text("class Ordinary_Car: pass\n")
    assert discover_freenove_path(str(tmp_path)) is None


def test_adc_parameter_file_must_confirm_pcb_version(tmp_path):
    for module_name in ("motor", "servo", "ultrasonic", "adc"):
        (tmp_path / f"{module_name}.py").write_text("class Marker: pass\n")
    library = FreenoveLibrary(str(tmp_path))

    with pytest.raises(FileNotFoundError):
        with library.parameter_working_directory():
            pass

    (tmp_path / "params.json").write_text(json.dumps({"Pcb_Version": 2}))
    with pytest.raises(ValueError):
        with library.parameter_working_directory():
            pass

    (tmp_path / "params.json").write_text(
        json.dumps({"Connect_Version": 2, "Pcb_Version": 2, "Pi_Version": 1})
    )
    with library.parameter_working_directory():
        assert True
