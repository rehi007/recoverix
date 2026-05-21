# Manual test matrix (Destructive / Integration)

**용도:** 회귀·현장 검증 시나리오 목록. 결과는 `tests/manual/test_result_template.md`와 함께 기록합니다.

| Test ID | Scenario | Expected Result | PASS/FAIL |
|---------|----------|-----------------|-----------|
| M-INT-01 | Integration checks `--dry-run` | `PASS` or `PASS_WITH_WARNINGS` | |
| M-SAF-01 | `safety_audit` | `PASS` or `PASS_WITH_WARNINGS` | |
| M-GAT-01 | `pre_destructive_gate` | `PASS` before any `--apply` | |
| M-BAK-01 | First backup dry-run | Plan only; no writes | |
| M-BAK-02 | First backup `--apply --confirm` | Manifest + hashes; no incomplete marker | |
| M-VAL-01 | `validate_restore` | `allowed=true` when backup valid | |
| M-RES-01 | Restore dry-run | No executor write | |
| M-RES-02 | First restore (small file) | File restored; OS boot OK | |
| M-RB-01 | Interrupted restore | Single rollback policy; clear logging | |
| M-RB-02 | Hash mismatch | Restore blocked fail-closed | |
| M-RB-03 | Manifest corruption | `validate_restore` reject | |
| M-EFI-01 | bootmgfw present (ESP mounted) | `verify_windows_boot_manager` PASS | |
| M-EFI-02 | RecoveryBoot shim on ESP | `EFI/RecoveryBoot/shimx64.efi` present | |
| M-BOOT-01 | BootOrder drift detection | Planner / agent coherent output | |
| M-WAG-01 | Windows Agent inspect only | Dry-run path; no unconfirmed bcdedit write | |

**비고:** 운영자는 행별 **PASS/FAIL**과 로그 패스를 남기고, **FAIL 시 destructive 금지** 정책을 적용합니다.
