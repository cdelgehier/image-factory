"""Ask Windows to shut down before QEMU closes the guest disk."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from image_factory.targets import ROOT


@pytest.mark.parametrize(
    "action,state,forced,requested",
    [
        ("halt", "running", False, True),
        ("reload", "running", False, True),
        ("destroy", "running", False, True),
        ("halt", "stopped", False, False),
        ("destroy", "not_created", False, False),
        ("halt", "running", True, False),
    ],
)
def test_vagrant_requests_a_guest_shutdown_before_stopping_qemu(
    action: str, state: str, forced: bool, requested: bool
) -> None:
    """Run the bundled triggers through Vagrant's real configuration loader."""

    ruby = Path("/opt/vagrant/embedded/bin/ruby")
    libraries = list(Path("/opt/vagrant/embedded/gems/gems").glob("vagrant-*/lib"))
    if not ruby.exists() or not libraries:
        pytest.skip("The shutdown check requires Vagrant's installed Ruby tools")
    program = """
require "vagrant"
require "json"

class ShutdownSpy
  attr_reader :commands
  def initialize
    @commands = []
  end
  def execute(command)
    @commands << command
  end
end

definitions = Vagrant::Config.capture_configures do
  load ENV.fetch("IMAGE_FACTORY_BOX_CONFIG")
end
config = Vagrant::Config::V2::Loader.load(definitions.first[1])
config.finalize!
communicator = ShutdownSpy.new
state = Struct.new(:id).new(ENV.fetch("IMAGE_FACTORY_VM_STATE").to_sym)
machine = Struct.new(:state, :communicate).new(state, communicator)
environment = {force_halt: ENV.fetch("IMAGE_FACTORY_FORCE_HALT") == "true"}
config.trigger.before_triggers.each do |trigger|
  if trigger.command == ENV.fetch("IMAGE_FACTORY_VM_ACTION").to_sym
    trigger.ruby_block.call(environment, machine)
  end
end
puts JSON.generate(communicator.commands)
"""
    result = subprocess.run(
        [str(ruby), "-I", str(libraries[0]), "-e", program],
        env=os.environ
        | {
            "GEM_HOME": "/opt/vagrant/embedded/gems",
            "GEM_PATH": "/opt/vagrant/embedded/gems",
            "IMAGE_FACTORY_BOX_CONFIG": str(
                ROOT / "targets/windows-11-arm64/Vagrantfile"
            ),
            "IMAGE_FACTORY_VM_ACTION": action,
            "IMAGE_FACTORY_VM_STATE": state,
            "IMAGE_FACTORY_FORCE_HALT": str(forced).lower(),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    commands = json.loads(result.stdout)
    if requested:
        assert len(commands) == 1
        assert "shutdown.exe /s" in commands[0]
        # Leave time for WinRM to return before Windows stops the service.
        delay = re.search(r"/t (\d+)", commands[0])
        assert delay and int(delay[1]) >= 5
    else:
        assert commands == []
