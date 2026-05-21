# 장애 대응(Troubleshooting)

각 항목: **증상 → 원인(가설) → 대응 → 로그 참조**

공통 로그 위치 요약은 [`LOGGING_POLICY.md`](LOGGING_POLICY.md) 및 Windows 에이전트 프로그램 데이터 경로를 참조합니다.

---

## RecoveryBoot 항목 누락(`RecoveryBoot missing`)

| | |
|--|--|
| 증상 | 부팅 메뉴에 Recovery 진입 불가 또는 펌웨어 대상 검색 결과 없음 |
| 원인 | ESP 배포 미완료·설치 불완료·외부 디스크 복구·Firmware reset |
| 대응 | ESP 경로 존재·도구 무결 검증 재진행 후 RecoveryBoot 레이블 재등록. Windows Agent 진단 검토 [`WINDOWS_AGENT.md`](WINDOWS_AGENT.md). |
| 로그 | Windows: `recoveryboot` 디렉터리 진단 출력·`repair.log`; Linux 런타임: 무결 검증 결과 |

---

## BootOrder 변주(BootOrder drift)

| | |
|--|--|
| 증상 | Recovery가 첫 순서였는데 업데이트 후 Windows만 남거나 순서 변경 |
| 원인 | Windows Update/Patch·Firmware update·외부 디스크 복귀 변경 |
| 대응 | Windows Agent 폴링·**dry-run** 계획 확인 후 허용 정책에서만 **`--apply` repair**(BitLocker 차단 포함). 자세히 [`WINDOWS_AGENT.md`](WINDOWS_AGENT.md). |
| 로그 | `bootorder.log`/`repair.log` |

---

## Secure Boot 검증실패 (`Secure Boot failure`)

| | |
|--|--|
| 증상 | 진입 즉시 보안 검증 차단 또는 OEM 메시지 출력 |
| 원인 | 체인에 서명되지 않은 바이너리·설정 차이 등 |
| 대응 | [`SECURE_BOOT.md`](SECURE_BOOT.md)·OEM 신뢰목록 검토. 사용자가 무단 UEFI 수정 전제 시 본 지원 대상외로 분류 가능. |

---

## BitLocker 상태 감지(`BitLocker detected`)

| | |
|--|--|
| 증상 | 플래너/에이전트가 백업·복구·EFI 쓰기·repair를 거부 출력 |
| 원인 | BitLocker ON 상태 |
| 대응 | **일시적인 볼륨 암복호**(비즈니스·보안 허범)·또는 **비지원** 범위로 분류 가능—[`BITLOCKER_POLICY.md`](BITLOCKER_POLICY.md). 상용 지원에서는 **사전 상태 스캔**으로 설계. |

---

## Recovery Image 레이블/마운트 없음 (`Recovery Image missing`)

| | |
|--|--|
| 증상 | 백업/복구 메뉴나 디스커버리 결과에서 대상 레이블 볼륨 못 찾음 |
| 원인 | 디스커버리 레이블 누락 또는 파티션 삭제 또는 외형 오프라인 상태 |
| 대응 | [`PARTITION_POLICY.md`](PARTITION_POLICY.md)·하드코딩 대신 디스커버리 교정 레이블. |

---

## 매니페스트 불일치(`manifest mismatch`)

| | |
|--|--|
| 증상 | 검증 결과에 복구 허비 메시지·해시 무결 결과 실패 출력 |
| 원인 | 백업완결 전 중단 또는 손실된 파일 패치 또는 위조 수정 |
| 대응 | **재백업** 또는 이전 회전 백업 사용. 새 복구 시도 차단 상태 유지. |

---

## `device_id`/디스크 GUID 불일치

| | |
|--|--|
| 증상 | 복구 시도 허브에서 디스크 식별 실패 결과 |
| 원인 | 대상 디스크 교환·클로닝·UUID 재생산 |
| 대응 | **대상 디스크 정합성**(원본과 동등하다는 증명) 검토 없이 진행 불가 설계 준수. |

---

## incomplete backup 존재

| | |
|--|--|
| 증상 | 복구 비활성화·예비 실패 카테고리 |
| 원인 | 백업 중단 또는 실패 |
| 대응 | incomplete 마커 정리 또는 성공 종료 새 백업 수행까지 복구 정지. |

---

## Windows Boot Manager 항목 누락

| | |
|--|--|
| 증상 | 펌웨어 열거에서 불가능 상태 |
| 원인 | 수동 삭제 또는 손실된 ESP Windows 경로 또는 클린룸 손실 |
| 대응 | **FAIL CLOSED**—먼저 **Windows 진입 회복**(미디어/OS 별도 접근) 채택. 플래너/에이전트는 복구가 아닙니다. |

---

## restore blocked 결과

일반 채널이 정책상 안전하지 않았을 경우 남는 카테고리입니다. 상태 JSON·복구 상태 필드 검토 필요.

더 구체 증표는 해당 환경 **진단 JSON** 출력(`collect_diagnostics` 등) 포함 권장.
