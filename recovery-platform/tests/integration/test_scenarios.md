# 통합 테스트 시나리오 (Integration test scenarios)

> **전제**  
> 아래 시나리오는 **실제 백업/복구 적용 전** 검증·드라이런·정책 확인을 위한 문서입니다.  
> **실제 `backup`/`restore` `--apply` 테스트는 이 문서 범위가 아니며**, 별도 **manual destructive test** 문서와 **테스트 전용 PC/VM**에서만 수행해야 합니다.  
> **하드코딩 금지**: `Disk0`, `Partition1`, `Boot0000` 등 고정 디스크/부트 슬롯에 의존하지 않습니다(코드·스크립트에서도 동일).

---

## 1. 정상 Windows 부팅

- RecoveryBoot가 **타임아웃** 후 Windows Boot Manager로 넘김(chainload/표시 순서 정책에 따름).
- **Windows Boot Manager** → Windows 커널 부팅 성공.
- **검증 포인트**: `bootmgfw.efi` 미손상, BootOrder에서 Windows 항목이 펌웨어 경로 기준으로 식별됨.

## 2. RecoveryBoot 핫키 진입 (F5)

- 부팅 시 **F5** 입력 시 RecoveryBoot → **Recovery Linux / Recovery Runtime** 진입.
- TUI 메인 메뉴(시스템 상태, 백업, 복구, …) 표시.
- **검증 포인트**: Recovery Runtime은 Linux 전용 정책 유지; Windows에서 TUI 미실행.

## 3. BitLocker ON 차단

- **설치/EFI 쓰기/백업/복구/BootOrder repair** 등 쓰기 경로는 정책에 따라 **차단**되거나 `--apply` 불가.
- Windows Agent: BitLocker ON 시 **repair 차단** 및 로그(`BitLocker enabled. BootOrder repair blocked.` 등).
- **검증 포인트**: `run_integration_checks` / planners는 dry-run·검증만; BitLocker ON은 별도 런타임에서 write 금지.

## 4. Recovery Image 없음

- **백업 메뉴**: 토폴로지·마운트에 따라 enabled 가능.
- **복구 메뉴**: **visible**이나 **실행(restore) disabled** (유효 이미지/검증 없음).
- **검증 포인트**: `validate_restore` / 플래너 `execution_allowed=false`.

## 5. incomplete_backup 존재

- **restore disabled**, UI/플래너에 **failure reason** 표시(예: incomplete marker).
- **검증 포인트**: FAIL CLOSED, 자동 복구 시도 없음.

## 6. manifest hash mismatch

- `validate_restore` **FAIL**.
- restore **disabled**.
- **검증 포인트**: 계획만 생성 가능하나 `restore_allowed=false`.

## 7. image hash mismatch

- `validate_restore` **FAIL**.
- restore **disabled**.

## 8. device_id mismatch

- `validate_restore` **FAIL** (디스크/디바이스 identity 불일치).
- restore **disabled**.

## 9. valid backup 존재

- **백업**: 정책상 **disabled**(이미 valid backup 존재).
- **복구**: 검증 통과 시 **enabled** 가능(메뉴는 이유를 함께 표시).

## 10. restore dry-run

- `planned_commands`(또는 동등 계획 필드) 생성.
- **`execution_allowed=false`** (또는 동등 플래그), **destructive 작업 없음**.
- **검증 포인트**: `restore_planner --dry-run`만 허용, `--apply` 거부.

## 11. restore apply safety

- **phrase 불일치** → 실행 차단.
- **`--apply`만** → 차단(예: `--confirm` 없음).
- **`--confirm` 없음** → 차단.
- **검증 포인트**: integration 러너는 `--apply` 옵션 자체를 노출하지 않음.

## 12. restore 실패

- `rollback_required=true` 등 상태 전이(정책 문서·`recovery_state` 요약 기준).
- **`restore_in_progress=false`**로 정리, **무한 자동 재시도 없음**.

## 13. RecoveryBoot 3회 실패 (Windows-first)

- **Windows-first** 정책 활성화, RecoveryBoot **반복 실패 루프 완화**.
- **검증 포인트**: 실패 카운터·BootOrder/BootNext 보존 정책은 런타임 문서와 일치.

## 14. BootOrder drift (Windows Agent)

- Agent가 **drift 감지**(BootOrder·항목 유무 변화).
- **dry-run**: NVRAM/EFI **수정 없음**.
- **`--apply`/`repair`**: 정책·BitLocker·권한 충족 시에만 수행(통합 러너는 repair apply 미포함).

## 15. Windows Boot Manager missing

- **FAIL CLOSED**: 복구 플랜·BootOrder repair는 **Windows 부팅 가능성 우선**.
- RecoveryBoot만 있고 Windows 항목이 없으면 **복구 불가·위험 상황**으로 처리.

---

## 자동 점검과의 매핑

| 시나리오 주제 | 자동 점검 (`run_integration_checks.py`) |
|----------------|------------------------------------------|
| 3, 10, 11 | planners dry-run, integration에 `--apply` 없음 |
| 4–9 | restore/backup 플래너·validation 요약(환경 의존) |
| 14 | Windows에서 firmware/planner; Linux에서는 SKIPPED |
| 하드코딩·정책 | `hardcoding_policy_audit` 체크 |
| 진단 수집 | `collect_diagnostics.py` (이미지/대량 해시 미포함, 마스킹) |

## 자동 점검 CLI

```bash
cd recovery-platform
PYTHONPATH=. python3 tools/run_integration_checks.py --dry-run
PYTHONPATH=. python3 tools/run_integration_checks.py --dry-run --json
PYTHONPATH=. python3 tools/collect_diagnostics.py --output diagnostics/diagnostics.json
```

- 리포트 기본 경로: `diagnostics/integration_report.json`
- **`--apply`는 통합 러너에 없음** (dry-run 전용).
- 실제 backup/restore 적용은 **manual destructive test** 문서만 참고.

