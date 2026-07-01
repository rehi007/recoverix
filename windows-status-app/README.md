# Recoverix Status App

Windows read-only status and guide application for Recoverix.

This project is intentionally separated from `recovery-platform`.

Scope:

- Show PC hardware information available through Windows.
- Show the current Windows boot disk and partition layout.
- Explain why storage capacity can look smaller after Recoverix installation.
- Show a Recoverix user guide.

Non-scope:

- No backup execution.
- No restore execution.
- No partition modification.
- No EFI/NVRAM modification.
- No task scheduler modification.

Build from Ubuntu:

```bash
make -C windows-status-app
```

Output:

```text
windows-status-app/build/RecoverixStatus.exe
```

Create a distributable folder:

```bash
make -C windows-status-app package
```

Code signing:

The package step supports Authenticode signing when PowerShell and `signtool.exe`
are available. Certificate material must not be stored in this repository.

Required environment variables for production signing:

```text
RECOVERIX_CODESIGN_PFX=C:\Secure\Recoverix\recoverix_codesign.pfx
RECOVERIX_CODESIGN_PASSWORD=<pfx password>
RECOVERIX_TIMESTAMP_URL=http://timestamp.digicert.com
```

Development package:

```bash
make package
```

If the variables or signing tool are not available, `make package` creates an
unsigned development build and prints a warning. Unsigned builds can be blocked
by Windows Smart App Control or SmartScreen and are not suitable for customer
deployment.

Production signed package:

```powershell
make package-signed
```

`package-signed` fails when PowerShell, `signtool.exe`, the PFX path, or the PFX
password is missing. Use this target for customer builds intended to pass Smart
App Control and SmartScreen reputation checks.

Package output:

```text
windows-status-app/build/package/
  RecoverixStatus.exe
  sign_status_app.ps1
  verify_signature.ps1
  install_status_app.cmd
  create_desktop_shortcut.ps1
```

Windows install test:

1. Copy `build/package` to the Windows system.
2. Run `install_status_app.cmd`.
3. Open the desktop shortcut named `Recoverix 상태 확인`.
