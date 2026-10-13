"""Keep early TLS disconnects retryable without hiding other WinRM errors."""

import os
import subprocess
from pathlib import Path

import pytest

from image_factory.targets import ROOT


@pytest.mark.parametrize(
    "failure,expected", [("closed", "waiting"), ("invalid-tls", "failed")]
)
def test_box_waits_for_winrm_after_a_tls_disconnect(
    failure: str, expected: str
) -> None:
    ruby = Path("/opt/vagrant/embedded/bin/ruby")
    libraries = list(Path("/opt/vagrant/embedded/gems/gems").glob("vagrant-*/lib"))
    if not ruby.exists() or not libraries:
        pytest.skip("The WinRM check requires Vagrant's installed Ruby tools")
    prefix = (
        (ROOT / "targets/windows-11-arm64/Vagrantfile")
        .read_text()
        .split('Vagrant.configure("2")', 1)[0]
    )
    program = (
        'require "vagrant"\n'
        + prefix
        + """
communicator_class = Vagrant.plugin("2").manager.communicators.get(:winrm)
class LocalWinRMCommunicator < communicator_class
  def create_shell
    config = @machine.config.winrm
    VagrantPlugins::CommunicatorWinRM::WinRMShell.new("127.0.0.1", config.port, config)
  end
end

listener = TCPServer.new("127.0.0.1", 0)
config = Vagrant.plugin("2").manager.config.get(:winrm).new
config.finalize!
config.port = listener.addr[1]
config.transport = :ssl
config.ssl_peer_verification = false
config.max_tries = 1
config.retry_delay = 1
config.timeout = 3
machine = Struct.new(:config).new(Struct.new(:winrm).new(config))
server = Thread.new do
  client = listener.accept
  client.readpartial(8192)
  if ENV.fetch("IMAGE_FACTORY_TLS_FAILURE") == "invalid-tls"
    client.write("HTTP/1.0 200 OK\\r\\n\\r\\n")
  end
  client.close
end
begin
  communicator = LocalWinRMCommunicator.new(machine)
  puts communicator.ready? ? "ready" : "waiting"
rescue OpenSSL::SSL::SSLError, VagrantPlugins::CommunicatorWinRM::Errors::SSLError
  puts "failed"
ensure
  server.join(1)
  server.kill if server.alive?
  listener.close
end
"""
    )
    result = subprocess.run(
        [str(ruby), "-I", str(libraries[0]), "-e", program],
        env=os.environ
        | {
            "GEM_HOME": "/opt/vagrant/embedded/gems",
            "GEM_PATH": "/opt/vagrant/embedded/gems",
            "IMAGE_FACTORY_TLS_FAILURE": failure,
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected, result.stderr
