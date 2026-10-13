# Use a Windows desktop and exchange files

This example adds Remote Desktop and an encrypted SMB share to the private
Windows box. It uses QEMU with HVF on Apple Silicon.

## Start the VM

First [build, test and import the Windows box](../../README.md#quick-start).
Copy this directory into your project, then run:

```sh
vagrant up --provider=qemu
```

The box supplies HTTPS WinRM and its initial `vagrant` account. The first boot
prepares a new Windows identity and can take several minutes.

This example reserves host ports `55986` for WinRM, `13389` for RDP and `1445`
for SMB. If another VM uses them, change the host ports before starting this VM.
Keep WinRM's `auto_correct: false` setting: QEMU does not keep a corrected port
for later Vagrant commands. The provisioner uses the existing admin WinRM
connection, so you do not need to open the desktop to finish provisioning.

## Open the desktop

Connect a Remote Desktop client to `127.0.0.1:13389`. Use the `vagrant` account
and its guest password. The initial password is `vagrant`; use your changed
password if you changed it in Windows. Network Level Authentication stays on.

With a supported client installed, you can also run `vagrant rdp`.
Install your application inside Windows or add a PowerShell provisioner to
the `Vagrantfile`. Start desktop applications after signing in: WinRM runs in
a separate session, so a graphical application started there may have no visible
window. For a portable package, copy its executable and create a shortcut during
provisioning, then open the shortcut from the desktop.

ARM64 Windows can run many x64 applications through emulation;
check the application you need, including its drivers and integrations.

## Exchange documents

Save guest documents in `C:\LabData`. From macOS, open the authenticated share:

```sh
open 'smb://127.0.0.1:1445/LabData'
```

Sign in with the guest's `vagrant` account and password. You can copy files in
both directions. SMB encryption is required, and host ports listen on loopback.
The guest firewall allows these connections from QEMU's gateway, `10.0.2.2`.

This folder belongs to the VM disk. Copy and check your documents on the host
before replacing or destroying the VM. For printing, Microsoft Print to PDF
can save a document in `C:\LabData`, then you can print it from macOS.

## Stop the VM

```sh
vagrant halt
```

Restart with `vagrant up --provider=qemu`. The existing VM keeps its identity
and documents. To change Vagrant's WinRM password setting after changing the
guest password, export `IMAGE_FACTORY_WINDOWS_PASSWORD` before running commands.
