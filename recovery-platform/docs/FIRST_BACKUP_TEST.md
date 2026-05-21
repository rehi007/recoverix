# 첫 Backup 테스트 절차 (First Backup)

**전제:** `docs/PRE_DESTRUCTIVE_CHECKLIST.md` 전부 및 `PYTHONPATH=. python3 tools/pre_destructive_gate.py` **PASS**.

**주의:** 이 문서는 **절차 기술용**입니다. 실제 명령은 배포판·CLI 도움말과 일치해야 합니다.

---

## 1. 테스트 파일 준비 (선택, 추적용)

- Windows에서 복구 대상 디렉터리에 **소량의 식별 가능한 테스트 파일** 생성 (파일명·해시 메모).

## 2. Windows 정상 부팅 확인

- 최신 상태로 로그온 확인.

## 3. Recovery Runtime 진입

- 설계된 방법(Recovery Linux 등)으로 런타임 부팅.
- 메뉴에서 **RECOVERY_IMAGE** 마운트·디스커버리 상태 확인.

## 4. Backup dry-run

- **항상 dry-run 우선.** 계획 출력·예상 디렉터리·도구 명령이 정책과 맞는지 확인.

## 5. Backup `--apply --confirm`

- 운영자 이중 확인 문구 및 정책에 맞는 플래그로만 실행.
- 실행 중 단말·세션 종료 금지.

## 6. Manifest 생성 확인

- `recovery-manifest`/해시 파일 등 **문서된 산출물** 존재 확인.

## 7. SHA256 검증

- 매니페스트에 명시된 해시와 이미지 파일 일치 확인 (도구 제공 시 해당 도구 사용).

## 8. Recovery Runtime 재부팅

- 이미지와 메타 일관 유지 확인.

## 9. Windows 재부팅

- OS 정상 진입 확인.

## 10. Backup validation 요약 확인

메뉴 또는 진단 출력에서 확인:

| 항목 | 기대 |
|------|------|
| `incomplete_backup` | **없음** |
| 백업 상태 | **valid / 완료**로 간주 가능 |
| restore 허용 | 정책상 **enabled** (또는 메뉴에 따른 허용) |
| `rollback_required` | **false** 유지 |

**실패 시:** 새 destructive 작업 금지, 로그 수집 후 `ROLLBACK_TEST_PLAN.md`·`EFI_RECOVERY_PLAN.md` 참고.
