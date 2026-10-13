"""Ask Windows to shut down before QEMU closes the guest disk."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from image_factory.targets import ROOT


@pytest.mark.parametrize(
    "guest,state,forced,requested",
    [
        ("windows", "running", False, True),
        ("windows", "stopped", False, False),
        ("windows", "not_created", False, False),
        ("windows", "running", True, False),
        ("debian", "running", False, False),
    ],
)
def test_vagrant_requests_a_guest_shutdown_before_stopping_qemu(
    guest: str, state: str, forced: bool, requested: bool
) -> None:
    """Exercise the real QEMU stop action with spies at its guest boundaries."""

    ruby = Path("/opt/vagrant/embedded/bin/ruby")
    libraries = list(Path("/opt/vagrant/embedded/gems/gems").glob("vagrant-*/lib"))
    qemu_libraries = list(
        (Path.home() / ".vagrant.d/gems").glob("*/gems/vagrant-qemu-*/lib")
    )
    if not ruby.exists() or not libraries or not qemu_libraries:
        pytest.skip("The shutdown check requires Vagrant and vagrant-qemu")
    prefix = (
        (ROOT / "targets/windows-11-arm64/Vagrantfile")
        .read_text()
        .split('Vagrant.configure("2")', 1)[0]
    )
    program = (
        'require "vagrant"\nrequire "vagrant-qemu/action/stop_instance"\n'
        + prefix
        + """
require "json"

class ShutdownSpy
  attr_reader :commands
  def initialize
    @commands = []
  end
  def execute(command)
    @commands << command
  end
  def stop(_options)
    @commands << "qemu-stop"
  end
end

class UIStub
  def info(_message)
  end
end

spy = ShutdownSpy.new
state = Struct.new(:id).new(ENV.fetch("IMAGE_FACTORY_VM_STATE").to_sym)
guest_config = Struct.new(:guest).new(ENV.fetch("IMAGE_FACTORY_VM_GUEST").to_sym)
config = Struct.new(:vm).new(guest_config)
provider_config = Struct.new(:control_port, :graceful_timeout).new(nil, 180)
provider = Struct.new(:driver).new(spy)
machine = Struct.new(:state, :communicate, :config, :provider_config, :provider, :ui).
  new(state, spy, config, provider_config, provider, UIStub.new)
environment = {machine: machine, ui: machine.ui,
  force_halt: ENV.fetch("IMAGE_FACTORY_FORCE_HALT") == "true"}
app = proc do |_environment|
  spy.commands << "finished"
end
VagrantPlugins::QEMU::Action::StopInstance.new(app, environment).call(environment)
puts JSON.generate(spy.commands)
"""
    )
    result = subprocess.run(
        [
            str(ruby),
            "-I",
            f"{libraries[0]}:{qemu_libraries[0]}",
            "-e",
            program,
        ],
        env=os.environ
        | {
            "GEM_HOME": "/opt/vagrant/embedded/gems",
            "GEM_PATH": "/opt/vagrant/embedded/gems",
            "IMAGE_FACTORY_VM_GUEST": guest,
            "IMAGE_FACTORY_VM_STATE": state,
            "IMAGE_FACTORY_FORCE_HALT": str(forced).lower(),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    commands = json.loads(result.stdout)
    assert commands[-2:] == ["qemu-stop", "finished"]
    if requested:
        assert len(commands) == 3
        assert "shutdown.exe /s" in commands[0]
        # Leave time for WinRM to return before Windows stops the service.
        delay = re.search(r"/t (\d+)", commands[0])
        assert delay and int(delay[1]) >= 5
    else:
        assert len(commands) == 2
