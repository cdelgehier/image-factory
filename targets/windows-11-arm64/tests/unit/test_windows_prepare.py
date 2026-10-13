import hashlib
import io
import json
import os
import platform
import select
import signal
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
from contextlib import suppress
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

RECIPE = Path(__file__).resolve().parents[2]
spec = spec_from_file_location("windows_prepare", RECIPE / "prepare.py")
assert spec is not None and spec.loader is not None
prepare = module_from_spec(spec)
spec.loader.exec_module(prepare)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_packer_progress_reaches_the_terminal_and_log_without_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], exit_code: int
) -> None:
    password = "Build-private-password-123"
    command = [
        sys.executable,
        "-c",
        "import os, sys; print('Starting VM', flush=True); "
        "print('Password: ' + os.environ['PKR_VAR_build_password'], "
        f"file=sys.stderr, flush=True); sys.exit({exit_code})",
    ]
    log_path = tmp_path / "packer.log"
    environment = os.environ | {"PKR_VAR_build_password": password}
    if exit_code:
        with pytest.raises(subprocess.CalledProcessError) as error:
            prepare.run_packer(
                command,
                root=tmp_path,
                environment=environment,
                log_path=log_path,
                password=password,
            )
        assert error.value.returncode == exit_code
    else:
        prepare.run_packer(
            command,
            root=tmp_path,
            environment=environment,
            log_path=log_path,
            password=password,
        )
    terminal = capsys.readouterr().out
    assert "Starting VM\n" in terminal
    assert "Password: <sensitive>\n" in terminal
    assert password not in terminal
    assert log_path.read_text() == terminal


def test_packer_progress_is_visible_before_the_process_finishes(tmp_path: Path) -> None:
    log_path = tmp_path / "packer.log"
    gate = tmp_path / "continue"
    child = (
        "import pathlib, sys, time; print('Waiting for WinRM', flush=True); "
        "gate = pathlib.Path(sys.argv[1]); "
        "\nwhile not gate.exists(): time.sleep(0.05)\n"
    )
    wrapper = (
        "import os, pathlib, runpy, sys; "
        "sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parents[2])); "
        "prepare = runpy.run_path(sys.argv[1]); "
        "prepare['run_packer']([sys.executable, '-c', sys.argv[2], sys.argv[3]], "
        "root=pathlib.Path.cwd(), environment=dict(os.environ), "
        "log_path=pathlib.Path(sys.argv[4]), password='Build-secret-1234')"
    )
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            wrapper,
            str(RECIPE / "prepare.py"),
            child,
            str(gate),
            str(log_path),
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            ready, _, _ = select.select([process.stdout], [], [], 10)
            assert ready, "Packer progress was buffered until the build ended"
            line = process.stdout.readline()
            assert line == "Waiting for WinRM\n", line
            assert process.poll() is None
            assert log_path.read_text() == line
        finally:
            gate.touch()
            process.communicate(timeout=10)
    assert process.returncode == 0


@pytest.mark.parametrize("repeat_interrupt", [False, True])
def test_packer_cancellation_waits_for_cleanup_and_keeps_the_last_messages(
    tmp_path: Path,
    repeat_interrupt: bool,
) -> None:
    private = tmp_path / "private-build"
    log_path = tmp_path / "packer.log"
    cleanup_started = tmp_path / "cleanup-started"
    finish_cleanup = tmp_path / "finish-cleanup"
    child = (
        "import os, pathlib, sys, time\n"
        "private = pathlib.Path(sys.argv[1])\n"
        "private.write_text(str(os.getpid()))\n"
        "print('Waiting for WinRM', flush=True)\n"
        "try:\n"
        "    time.sleep(30)\n"
        "except KeyboardInterrupt:\n"
        "    private.with_name('cleanup-started').touch()\n"
        "    while not private.with_name('finish-cleanup').exists():\n"
        "        time.sleep(0.01)\n"
        "    private.unlink()\n"
        "    print('Cleanly cancelled Build-secret-1234', flush=True)\n"
        "    sys.exit(130)\n"
    )
    wrapper = (
        "import os, pathlib, runpy, sys\n"
        "sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parents[2]))\n"
        "prepare = runpy.run_path(sys.argv[1])\n"
        "try:\n"
        "    prepare['run_packer']([sys.executable, '-c', sys.argv[2], sys.argv[3]], "
        "root=pathlib.Path.cwd(), environment=dict(os.environ), "
        "log_path=pathlib.Path(sys.argv[4]), password='Build-secret-1234')\n"
        "except KeyboardInterrupt:\n"
        "    sys.exit(130)\n"
    )
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            wrapper,
            str(RECIPE / "prepare.py"),
            child,
            str(private),
            str(log_path),
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        child_pid = None
        try:
            assert process.stdout is not None
            ready, _, _ = select.select([process.stdout], [], [], 10)
            assert ready
            assert process.stdout.readline() == "Waiting for WinRM\n"
            child_pid = int(private.read_text())
            os.kill(process.pid, signal.SIGINT)
            deadline = time.monotonic() + 10
            while not cleanup_started.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert cleanup_started.exists(), "The child did not start cleanup"
            if repeat_interrupt:
                # Task may forward the same terminal interrupt a second time.
                os.kill(process.pid, signal.SIGINT)
            finish_cleanup.touch()
            output, _ = process.communicate(timeout=10)
        finally:
            finish_cleanup.touch()
            if child_pid is not None:
                with suppress(ProcessLookupError):
                    os.killpg(child_pid, signal.SIGKILL)
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    assert process.returncode == 130
    assert not private.exists()
    assert output == "Cleanly cancelled <sensitive>\n"
    assert log_path.read_text() == "Waiting for WinRM\n" + output


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
    assert "Traceback" not in result.stderr


def test_source_checksum_is_required_before_preparing_any_media(tmp_path: Path) -> None:
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"other media")
    expected = "sha256:" + "ab" * 32
    actual = "sha256:" + hashlib.sha256(iso.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="checksum") as error:
        prepare.verify_iso(iso, expected)
    assert str(iso) in str(error.value)
    assert f"Expected: {expected}" in str(error.value)
    assert f"Actual: {actual}" in str(error.value)
    prepare.verify_iso(iso, actual)


def test_missing_iso_is_reported_separately_from_a_checksum_error(
    tmp_path: Path,
) -> None:
    iso = tmp_path / "missing.iso"
    with pytest.raises(FileNotFoundError, match="ISO file not found") as error:
        prepare.verify_iso(iso, "sha256:" + "ab" * 32)
    assert str(iso) in str(error.value)


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
        self.driver_files: dict[str, bytes] = {}
        self.run_process = subprocess.run
        self.run_builder = prepare.run_packer

    def run(self, command: list[str], **options: object) -> None:
        """Use real extraction but avoid starting a Windows installation."""

        if command[0] == "tar":
            self.run_process(command, check=True)
        if command[0] == "hdiutil":
            Path(command[command.index("-o") + 1]).touch()
            seed = Path(command[-1])
            self.answer = (seed / "Autounattend.xml").read_text()
            self.driver_files = {
                str(path.relative_to(seed)): path.read_bytes()
                for path in (seed / "Drivers").rglob("*")
                if path.is_file()
            }

    def packer(
        self,
        command: list[str],
        *,
        root: Path,
        environment: dict[str, str],
        log_path: Path,
        password: str,
    ) -> None:
        """Run a small process through the real output and error handling."""

        self.password = environment["PKR_VAR_build_password"]
        self.command = command
        self.run_builder(
            [
                sys.executable,
                "-c",
                f"print('Packer build'); raise SystemExit({int(self.fail)})",
            ],
            root=root,
            environment=environment,
            log_path=log_path,
            password=password,
        )


@pytest.mark.parametrize("failure", [None, "extraction", "packer"])
def test_build_resolves_driver_links_and_removes_private_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
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
    drivers = tmp_path / "drivers.iso"
    driver_files = {
        "netkvm.inf": b"ARM64 network configuration",
        "netkvm.cat": b"signed driver catalogue",
        "netkvm.sys": b"ARM64 network driver",
    }
    # UTM shares driver files across Windows releases through hard links.
    with tarfile.open(drivers, "w") as archive:
        for name, content in driver_files.items():
            original = tarfile.TarInfo(f"Drivers/NetKVM/2k25/ARM64/{name}")
            original.size = len(content)
            archive.addfile(original, io.BytesIO(content))
            link = tarfile.TarInfo(f"Drivers/NetKVM/w11/ARM64/{name}")
            link.type = tarfile.LNKTYPE
            link.linkname = original.name
            archive.addfile(link)
        unrelated = tarfile.TarInfo("Drivers/NetKVM/w11/amd64/netkvm.sys")
        unrelated.size = len(b"x64 driver")
        archive.addfile(unrelated, io.BytesIO(b"x64 driver"))
    if failure == "extraction":
        drivers.write_bytes(b"invalid driver archive")
    tools = FakeBuildTools(failure == "packer")
    monkeypatch.setattr(prepare, "load_target", Mock(return_value=target))
    monkeypatch.setattr(prepare.platform, "system", Mock(return_value="Darwin"))
    monkeypatch.setattr(prepare.platform, "machine", Mock(return_value="arm64"))
    monkeypatch.setattr(prepare, "driver_media", Mock(return_value=drivers))
    monkeypatch.setattr(
        prepare.subprocess, "check_output", Mock(return_value=str(tmp_path / "qemu"))
    )
    monkeypatch.setattr(prepare.subprocess, "run", tools.run)
    monkeypatch.setattr(prepare, "run_packer", tools.packer)
    monkeypatch.setenv("WINDOWS_ISO", str(iso))
    monkeypatch.delenv("PACKER_LOG", raising=False)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            prepare.build()
    else:
        prepare.build()
    assert list((tmp_path / ".build").iterdir()) == []
    assert iso.read_bytes() == b"Windows installation media"
    if failure == "extraction":
        assert tools.answer == ""
        assert tools.password == ""
        assert tools.command == []
        return
    assert tools.password in tools.answer
    assert all(tools.password not in argument for argument in tools.command)
    assert tools.driver_files == {
        f"Drivers/NetKVM/w11/ARM64/{name}": content
        for name, content in driver_files.items()
    }
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


def test_unattended_setup_commands_fit_windows_path_limits() -> None:
    namespace = {"u": "urn:schemas-microsoft-com:unattend"}
    answer = ET.parse(RECIPE / "Autounattend.xml")
    paths = answer.findall(".//u:RunSynchronousCommand/u:Path", namespace)
    assert paths
    # Windows rejects the whole answer file when one command exceeds this limit.
    assert all(item.text and len(item.text) <= 259 for item in paths)


@pytest.mark.parametrize("filename", ["Autounattend.xml", "Sysprep.xml"])
def test_setup_and_clones_set_the_region_before_the_first_login(filename: str) -> None:
    namespace = {"u": "urn:schemas-microsoft-com:unattend"}
    answer = ET.parse(RECIPE / filename)
    region = answer.find(
        ".//u:settings[@pass='oobeSystem']/"
        "u:component[@name='Microsoft-Windows-International-Core']",
        namespace,
    )
    assert region is not None, "OOBE must not wait for a region or keyboard selection"
    for setting in ("InputLocale", "SystemLocale", "UILanguage", "UserLocale"):
        assert region.findtext(f"u:{setting}", namespaces=namespace) == "en-US"
