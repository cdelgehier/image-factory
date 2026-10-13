"""Check forwarded ports with Vagrant's real Ruby networking helper."""

import shutil
import subprocess
from pathlib import Path

import pytest

from image_factory.targets import ROOT, all_targets


@pytest.mark.parametrize("name", [target.name for target in all_targets()])
def test_bundled_config_tells_closed_ports_from_active_listeners(name: str) -> None:
    ruby = Path("/opt/vagrant/embedded/bin/ruby")
    libraries = list(Path("/opt/vagrant/embedded/gems/gems").glob("vagrant-*/lib"))
    if not ruby.exists():
        located = shutil.which("ruby")
        if not located:
            pytest.skip("The port check requires Ruby")
        ruby = Path(located)
    if not libraries:
        pytest.skip("The port check requires Vagrant's installed Ruby helper")
    prefix = (
        (ROOT / "targets" / name / "Vagrantfile")
        .read_text()
        .split('Vagrant.configure("2")', 1)[0]
    )
    program = (
        """
require "socket"
require "vagrant/util/is_port_open"
"""
        + prefix
        + """
listener = TCPServer.new("127.0.0.1", 0)
port = listener.addr[1]
raise "An active listener was missed" unless Vagrant::Util::IsPortOpen.is_port_open?("127.0.0.1", port)
listener.close
raise "A closed port was reported as occupied" if Vagrant::Util::IsPortOpen.is_port_open?("127.0.0.1", port)
puts "Port checks passed"
"""
    )
    result = subprocess.run(
        [str(ruby), "-I", str(libraries[0]), "-e", program],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Port checks passed"
