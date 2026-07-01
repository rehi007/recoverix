# Recoverix Commercial Release Documents

Product: Recoverix
Version: T.1.0.0
Publisher: FORYOUCOM
Support phone: 1544-1879
Support email: help@foryoucom.co.kr
Language: Korean

This directory contains commercial release planning documents, customer-facing
notice drafts, and installer packaging policies for Recoverix.

These files are documentation and release policy drafts. They do not change the
runtime, backup, restore, EFI, NVRAM, or Windows Agent implementation.

## Installer Output

The customer-facing installer should be a single executable:

```text
RecoverixSetup-T.1.0.0-x64.exe
```

Internal release validation may additionally produce:

```text
dist/
  RecoverixSetup-T.1.0.0-x64.exe
  RecoverixSetup-T.1.0.0-x64/
  checksums.txt
  build-info.json
  ThirdPartyNotices.txt
  SourceOffer.txt
```

## Required Installer Payload

- Recoverix Recovery Runtime components
- Recoverix EFI files
- fallback `EFI\Boot\bootx64.efi`
- `recoverix-nvram-writer.exe`
- Windows Agent
- Task Scheduler registration script
- Permission hardening script
- Recoverix Status and Guide application
- Customer notices and license documents
- Open source notice and source offer files
- Uninstaller and repair assets

The installer must not include a customer backup image. It creates the storage
area where a first backup image can be generated later.

## Partition Policy

The commercial installer should create or reserve the following layout on the
Windows system disk:

```text
[existing EFI system partition: keep as-is]
[existing MSR: keep as-is]
[Windows C:]
[100 MB unallocated space]
[RECOVERY_LINUX: 4 GB]
[RECOVERY_IMAGE: calculated]
```

`RECOVERY_IMAGE` size policy:

```text
RECOVERY_IMAGE = max(Windows used space * 0.75 + 5 GB, 45 GB)
```

The 100 MB unallocated space behind the Windows partition is not a partition.
It is reserved for minor partition-size mismatch handling after disk copy or
similar migration scenarios.

## Legal and Compliance Position

Recoverix is a FORYOUCOM product. Customer-facing text, installer screens,
menus, guides, and marketing material must not use Ubuntu branding as a product
identity.

Open source notices and source offer instructions must be shipped with the
product. They are notices and delivery obligations, not separate customer
consent checkboxes. The customer consents to the overall Recoverix EULA and
installation risk notices, which reference these files.

Official references used for this policy:

- Canonical Intellectual Property Rights Policy:
  https://canonical.com/legal/intellectual-property-policy
- Microsoft Smart App Control overview:
  https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview
- Microsoft Smart App Control FAQ:
  https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions

