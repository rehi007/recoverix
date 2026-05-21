# 백업(Backup Engine)

## 개요

백업은 **Recovery Image**(정책상 `RECOVERY_IMAGE` 레이블 볼륨)에 GPT 메타데이터 스냅샷·EFI FAT 이미지·Windows NTFS 볼륨 이미지를 남기는 것을 목표로 합니다. 구현에서는 **partclone** 계열 명령(예: partclone.ntfs, partclone.dd/FAT류) 문자열 형태가 계획에 포함될 수 있습니다.

## 백업 흐름(논리)

1. 레이블/토폴로지 디스커버리(BitLocker 상태 포함)
2. **preflight 검증**(BitLocker ON 등 차단 가능)
3. 백업 **계획(dry-run)** — 예상 명령·대상 파일 경로 노출
4. 사용자/OS 정책에 따른 확인 phrase·`--apply --confirm`(참조 구현에서는 별도 강제)
5. GPT/EFI/Windows 순서로 이미지 쓰기(정책에 따름)
6. **매니페스트** 기록 디스크·파티션 ID 등의 메타데이터
7. **SHA256**(또는 정책 해시 파일) 작성
8. `backup_complete`/완료 마커 또는 동등 마커로 **valid 백업** 상태 확정
9. **incomplete_backup** 마커는 실패·중단 시 남으며 이후 검증 **`FAIL CLOSED`**

## Recovery Image 디렉터리 역할(요약)

| 영역 | 용도 |
|------|------|
| `metadata/` 또는 동등 경로 | GPT 백업 등 |
| `images/` 또는 동등 경로 | partclone 이미지(EFI/Windows 등) |
| `state/` | incomplete 마커·런타임 상태 |
| 해시 파일 | 무결성 검증용 |
| 매니페스트 | 디바이스·해시 검증 근거 |

정확한 상대경로 이름은 버전별 구현 매칭 필요.

## 정책

- **유효 백업이 이미 있으면** 백업 메뉴/동작은 **비활성화**될 수 있음(UI는 보이지만 실행 불가).
- **destructive 덮어쓰기**는 설계 단계별 가드(write_guard)·확인 절차로 제한합니다.
- **incomplete_backup** 존재 시 복구는 차단 또는 엄격한 실패 처리.
- 파티션 **번호 하드코딩**(Disk0 등) 및 **부트 슬롯 하드코딩**(Boot0000)은 허용되지 않음.

## 관련 문서

- [`BITLOCKER_POLICY.md`](BITLOCKER_POLICY.md) · [`PARTITION_POLICY.md`](PARTITION_POLICY.md) · [`RESTORE.md`](RESTORE.md)
