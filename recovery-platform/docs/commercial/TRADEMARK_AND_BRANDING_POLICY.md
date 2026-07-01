# Recoverix Trademark and Branding Policy

Product: Recoverix
Version: T.1.0.0
Publisher: FORYOUCOM

## Customer-facing Branding

Customer-facing product names, installer screens, boot menus, manuals, status
app pages, desktop shortcuts, and marketing text must use:

```text
Recoverix
Recoverix Recovery Runtime
Recoverix 상태확인 및 안내
FORYOUCOM
```

They must not describe the product as an Ubuntu product, Ubuntu edition, Ubuntu
distribution, Ubuntu recovery solution, or Canonical-certified product.

## Required Cleanup Before Commercial Release

Remove or replace customer-visible references such as:

- `Ubuntu Boot Manager`
- `Ubuntu runtime`
- `Ubuntu recovery`
- `Ubuntu-based product`
- Ubuntu logos or wallpapers
- Canonical logos
- distribution branding shown in the recovery UI

Recommended replacement for boot status labels:

```text
Existing OS Boot Manager
Host OS Boot Manager
Windows Boot Manager
Recoverix Boot Manager
```

## Development-only References

Development scripts and internal build notes may mention Ubuntu or Jammy where
technically necessary, but they must not be shipped as customer documentation
unless reviewed and converted into third-party notice language.

Build logs, diagnostic logs, investigation notes, test artifacts, and local
system reports must be excluded from customer packages.

## Official Policy Reference

Canonical's intellectual property policy states that modified Ubuntu
redistribution associated with Canonical trademarks requires approval,
certification, or provision by Canonical. Otherwise, trademarks must be removed
and replaced. The policy does not remove rights granted by open source licenses
for individual components.

Reference:

https://canonical.com/legal/intellectual-property-policy

