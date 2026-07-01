# Recoverix Versioning Policy

Product: Recoverix
Initial commercial version: T.1.0.0

## Version Format

Recoverix commercial test builds use:

```text
T.major.minor.patch
```

Initial version:

```text
T.1.0.0
```

## Components to Version

- Installer
- Recovery Runtime
- EFI boot assets
- Windows Agent
- NVRAM writer
- Status and Guide app
- Uninstaller
- Commercial notices
- Third-party notice bundle

## Build Information

Each release should generate:

```text
build-info.json
```

Recommended fields:

```json
{
  "product": "Recoverix",
  "version": "T.1.0.0",
  "publisher": "FORYOUCOM",
  "support_phone": "1544-1879",
  "support_email": "help@foryoucom.co.kr",
  "build_date": "YYYY-MM-DD",
  "installer_file": "RecoverixSetup-T.1.0.0-x64.exe",
  "installer_sha256": "",
  "runtime_sha256": "",
  "status_app_sha256": "",
  "nvram_writer_sha256": "",
  "windows_agent_version": "",
  "source_revision": ""
}
```

## Customer-visible Version

Customer-facing screens should show:

```text
Recoverix T.1.0.0
FORYOUCOM
1544-1879
help@foryoucom.co.kr
```

## Update Compatibility

Updates must check the installed version before replacing:

- EFI boot assets
- Recovery Runtime image
- Windows Agent
- NVRAM writer
- status app

If the installed version is newer than the update package, the updater must stop
unless a forced downgrade mode is explicitly selected by support.

