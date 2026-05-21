# Manual destructive test 결과 기록 템플릿

복사 후 날짜별 파일로 저장 (예: `results/YYYY-MM-DD_M-BAK-02.md`).

---

## 메타데이터

| 필드 | 값 |
|------|-----|
| Tester | |
| 기록 일시 (UTC 가능) | |
| Machine model | |
| Firmware version | |
| Disk model / capacity | |
| Windows version | |
| Secure Boot | ON / OFF / UNKNOWN |
| BitLocker | ON / OFF / UNKNOWN |
| Test ID (`manual_test_matrix.md` 참조) | |

## 시나리오

| 필드 | 값 |
|------|-----|
| Test scenario 요약 | |
| Expected behavior (문서 인용 가능) | |
| Actual behavior | |
| 최종 결과 | PASS / FAIL |

## 증거

- 수집한 로그 경로 (예: `RECOVERY_IMAGE/logs/*.log`):  
  - 
- 스크린샷·사진 참조 ID:  
  - 

## 특이사항·후속 조치

- 

---

**실패 시:** 새 destructive 테스트 중단 여부 명시 후 `PRE_DESTRUCTIVE_CHECKLIST.md`부터 재실행 여부 표시.

