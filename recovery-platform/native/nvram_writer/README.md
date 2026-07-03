# recoverix-nvram-writer

Windows native helper for Recoverix UEFI NVRAM repair.

Purpose:

- Read `BootOrder` and `Boot####` variables from the UEFI global namespace.
- Find the Windows Boot Manager entry and reuse its ESP device path.
- Create a real `Recoverix Hotkey Boot (복구 핫키 대기용)` entry pointing to `\EFI\RecoveryBoot\shimx64.efi`.
- Create a real `Start Recoverix (복구 모드 직접 진입)` entry pointing to `\EFI\RecoverixDirect\shimx64.efi`.
- Reorder `BootOrder` as `Recoverix Hotkey Boot (복구 핫키 대기용) -> Windows Boot Manager -> Start Recoverix (...) -> remaining entries`.

This helper intentionally does not modify `bootmgfw.efi` or files on the ESP.

Build from Linux with MinGW:

```bash
make -C recovery-platform/native/nvram_writer
```

Output:

```text
recoverix-nvram-writer.exe
```
