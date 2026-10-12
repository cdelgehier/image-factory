import hashlib
import json
import os
import platform
import subprocess
import xml.etree.ElementTree as ET
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

RECIPE = Path(__file__).resolve().parents[2]
spec = spec_from_file_location("windows_prepare", RECIPE / "prepare.py")
assert spec is not None and spec.loader is not None
prepare = module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_build_command_checks_prerequisites_before_preparing_media() -> None:
    environment = os.environ | {"IMAGE_FACTORY_TARGET": "windows-11-arm64"}
    environment.pop("WINDOWS_ISO", None)
    environment.pop("PACKER_LOG", None)
    result = subprocess.run(
        ["bash", str(RECIPE / "build.sh")],
        cwd=RECIPE.parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    expected = (
        "Set WINDOWS_ISO"
        if platform.system() == "Darwin" and platform.machine() == "arm64"
        else "requires macOS on Apple Silicon"
    )
    assert result.returncode != 0
    assert expected in result.stderr


def test_source_checksum_is_required_before_preparing_any_media(tmp_path: Path) -> None:
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"other media")
    with pytest.raises(ValueError, match="checksum"):
        prepare.verify_iso(iso, "sha256:" + "ab" * 32)
    prepare.verify_iso(iso, "sha256:" + hashlib.sha256(iso.read_bytes()).hexdigest())


def test_answer_file_uses_arm64_pro_and_escapes_the_temporary_password() -> None:
    password = 'Build<&secret>"123'
    answer = prepare.render_answer(RECIPE / "Autounattend.xml", password)
    root = ET.fromstring(answer)
    namespace = {"u": "urn:schemas-microsoft-com:unattend"}
    assert {
        item.get("processorArchitecture")
        for item in root.findall(".//u:component", namespace)
    } == {"arm64"}
    assert root.findall(".//u:Password/u:Value", namespace)[0].text == password
    edition = root.find(".//u:InstallFrom/u:MetaData/u:Value", namespace)
    logons = root.find(".//u:AutoLogon/u:LogonCount", namespace)
    assert edition is not None and edition.text == "Windows 11 Pro"
    assert logons is not None and logons.text == "1"


@pytest.mark.parametrize("password", ["", "short", "bad\x00password123"])
def test_answer_file_rejects_an_invalid_password(password: str) -> None:
    with pytest.raises(ValueError, match="password"):
        prepare.render_answer(RECIPE / "Autounattend.xml", password)


def test_windows_stays_out_of_public_build_and_source_matrices() -> None:
    from image_factory.catalogue import add_version
    from image_factory.targets import all_targets, enabled_targets, load_target

    target = load_target("windows-11-arm64")
    assert target.username == "vagrant"
    assert target.communicator == "winrm"
    assert target in all_targets()
    assert target not in enabled_targets()
    with pytest.raises(ValueError, match="local"):
        add_version(None, "v0.2.0", "ab" * 32, target)


def test_windows_media_pin_is_official_and_the_driver_pin_is_fixed() -> None:
    source = json.loads((RECIPE / "source.pkrvars.json").read_text())
    assert (
        source["source_url"]
        == "https://www.microsoft.com/en-us/software-download/windows11arm64"
    )
    assert source["source_build"] == "26300.9457"
    drivers = json.loads((RECIPE / "drivers.json").read_text())
    assert drivers["url"].startswith(
        "https://github.com/utmapp/qemu/releases/download/"
    )
    assert len(drivers["sha256"]) == 64


@pytest.mark.parametrize("corrupt", [False, True])
def test_driver_download_is_verified_before_it_enters_the_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corrupt: bool,
) -> None:
    import io
    from unittest.mock import Mock

    recipe = tmp_path / "recipe"
    recipe.mkdir()
    data = b"trusted ARM64 driver media"
    checksum = hashlib.sha256(data).hexdigest()
    (recipe / "drivers.json").write_text(
        json.dumps(
            {
                "url": "https://github.com/utmapp/qemu/releases/download/pinned/drivers.iso",
                "sha256": checksum,
            }
        )
    )
    response = io.BytesIO(b"changed media" if corrupt else data)
    network = Mock(return_value=response)
    monkeypatch.setattr(prepare, "urlopen", network)
    cache = tmp_path / "cache"
    if corrupt:
        with pytest.raises(ValueError, match="checksum"):
            prepare.driver_media(recipe, cache)
        assert list(cache.iterdir()) == []
    else:
        path = prepare.driver_media(recipe, cache)
        assert path.read_bytes() == data
        assert prepare.driver_media(recipe, cache) == path
        assert network.call_count == 1
        path.write_bytes(b"changed cache")
        with pytest.raises(ValueError, match="checksum"):
            prepare.driver_media(recipe, cache)


class FakeBuildTools:
    """Stand in for macOS tools and Packer while using real private setup files."""

    def __init__(self, fail: bool) -> None:
        """Record generated setup data and choose the simulated build result."""

        self.fail = fail
        self.password = ""
        self.command: list[str] = []
        self.answer = ""

    def run(self, command: list[str], **options: object) -> None:
        """Create a seed placeholder and simulate Packer's success or failure."""

        import subprocess
        from typing import cast

        if command[0] == "hdiutil":
            Path(command[command.index("-o") + 1]).touch()
            self.answer = (Path(command[-1]) / "Autounattend.xml").read_text()
        if command[:2] == ["packer", "build"]:
            environment = cast(dict[str, str], options["env"])
            self.password = environment["PKR_VAR_build_password"]
            self.command = command
            if self.fail:
                raise subprocess.CalledProcessError(1, command)


@pytest.mark.parametrize("fail", [False, True])
def test_private_setup_files_are_removed_after_packer_success_or_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail: bool,
) -> None:
    import shutil
    import subprocess
    from unittest.mock import Mock

    from image_factory.targets import load_target

    target = load_target("windows-11-arm64").model_copy(update={"root": tmp_path})
    shutil.copytree(RECIPE, target.directory)
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"Windows installation media")
    source = json.loads((target.directory / "source.pkrvars.json").read_text())
    source["source_checksum"] = "sha256:" + hashlib.sha256(iso.read_bytes()).hexdigest()
    (target.directory / "source.pkrvars.json").write_text(json.dumps(source))
    firmware = tmp_path / "qemu/share/qemu"
    firmware.mkdir(parents=True)
    (firmware / "edk2-arm-vars.fd").write_bytes(b"fresh firmware state")
    tools = FakeBuildTools(fail)
    monkeypatch.setattr(prepare, "load_target", Mock(return_value=target))
    monkeypatch.setattr(prepare.platform, "system", Mock(return_value="Darwin"))
    monkeypatch.setattr(prepare.platform, "machine", Mock(return_value="arm64"))
    monkeypatch.setattr(
        prepare, "driver_media", Mock(return_value=tmp_path / "drivers.iso")
    )
    monkeypatch.setattr(
        prepare.subprocess, "check_output", Mock(return_value=str(tmp_path / "qemu"))
    )
    monkeypatch.setattr(prepare.subprocess, "run", tools.run)
    monkeypatch.setenv("WINDOWS_ISO", str(iso))
    monkeypatch.delenv("PACKER_LOG", raising=False)
    if fail:
        with pytest.raises(subprocess.CalledProcessError):
            prepare.build()
    else:
        prepare.build()
    assert list((tmp_path / ".build").iterdir()) == []
    assert tools.password in tools.answer
    assert all(tools.password not in argument for argument in tools.command)
    assert iso.read_bytes() == b"Windows installation media"
    assert (tmp_path / "reports/windows-11-arm64-packer.log").is_file()


@pytest.mark.parametrize(
    "pin",
    [
        {
            "url": "http://github.com/utmapp/qemu/releases/download/pinned/drivers.iso",
            "sha256": "ab" * 32,
        },
        {"url": "https://example.com/drivers.iso", "sha256": "ab" * 32},
        {
            "url": "https://github.com/utmapp/qemu/releases/download/pinned/drivers.iso",
            "sha256": "abc",
        },
        {
            "url": "https://github.com/utmapp/qemu/releases/download/pinned/drivers.iso",
            "sha256": None,
        },
    ],
)
def test_driver_media_rejects_invalid_metadata_before_a_download(
    tmp_path: Path, pin: dict
) -> None:
    (tmp_path / "drivers.json").write_text(json.dumps(pin))
    with pytest.raises(ValueError):
        prepare.driver_media(tmp_path, tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_sysprep_answer_uses_arm64_and_only_public_lab_credentials() -> None:
    namespace = {"u": "urn:schemas-microsoft-com:unattend"}
    answer = ET.parse(RECIPE / "Sysprep.xml")
    assert {
        item.get("processorArchitecture")
        for item in answer.findall(".//u:component", namespace)
    } == {"arm64"}
    assert [
        item.text for item in answer.findall(".//u:Password/u:Value", namespace)
    ] == ["vagrant"]
    assert answer.find(".//u:AutoLogon", namespace) is None
