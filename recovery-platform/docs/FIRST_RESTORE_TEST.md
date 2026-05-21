# 첫 Restore 테스트 절차 (First Restore)

**전제:**

1. 성공적인 **backup** 완료 (매니페스트·해시 검증 포함).
2. `validate_restore` **PASS**.
3. `docs/PRE_DESTRUCTIVE_CHECKLIST.md` 및 **pre-destructive gate PASS**.

**명확한 제한:**

- **첫 restore에서는 OS 파손·전체 패티션 덮어쓰기 테스트를 하지 않는다.**
- **작은 테스트 파일** 또는 비파괴적으로 검증 가능한 대상만 사용한다.

---

## 사전 준비

1. Windows에서 **작은 테스트 파일** 생성 후 위치·해시 기록.
2. 최신 backup이 완료되었음을 게이트·메뉴로 확인.

## Restore 테스트 단계

1. **테스트 파일 삭제** (Windows 또는 의도적으로 접근 불가 상태로 변경).
2. **Recovery Runtime** 진입.
3. **Restore dry-run** (`--apply` 없이 계획·검증만).
4. 정책상 모든 조건 만족 시에만 **`--apply --confirm` 및 확인 문구**로 restore 실행.
5. 완료 후 **재부팅**.
6. **Windows 정상 부팅** 확인.
7. **테스트 파일 복구** 여부 확인 (존재·내용 해시 비교).

## 실패·중단 시

- 로그 디렉터리(`recovery_root/logs` 등) 수집.
- `rollback_required`/불완전 마커 여부 확인.
- **먼저** `docs/EMERGENCY_BOOT_RECOVERY.md` 순서 준수 (파괴적 restore 재시도 금지).
