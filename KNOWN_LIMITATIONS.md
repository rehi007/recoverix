# Recoverix Known Limitations

## Code Signing

현재 공인 코드서명 인증서를 사용하지 않습니다.

영향:

- Windows Smart App Control 또는 SmartScreen에서 차단될 수 있습니다.
- 사용자에게 차단 가능성을 고지해야 합니다.

## Windows Partition Shrink

설치 시 Windows C: 파티션을 축소해야 합니다.

실패할 수 있는 조건:

- hibernation 활성
- pagefile이 파티션 끝부분에 위치
- 시스템 복원 지점 또는 shadow copy
- Windows 업데이트 잔여 파일
- 파일시스템 dirty 상태
- BitLocker 활성

대표 오류:

```text
Windows partition cannot be shrunk enough.
```

조치:

```bat
powercfg /h off
chkdsk C: /f
```

추가로 pagefile 임시 해제, 복원 지점 삭제, 재부팅 후 다시 설치합니다.

## BitLocker

BitLocker가 켜져 있으면 설치/백업/복원 안정성을 보장하기 어렵습니다.

정책:

- 설치 전 BitLocker 꺼짐 필요
- BitLocker 감지 시 차단 또는 경고

## Storage Capacity Display

RECOVERY_LINUX와 RECOVERY_IMAGE는 Windows 탐색기에서 일반 드라이브처럼 보이지 않을 수 있습니다.

사용자가 보는 C: 용량은 설치 후 줄어든 것처럼 보입니다.

상태확인 프로그램에서 다음을 설명해야 합니다.

- 복구 솔루션이 별도 파티션을 사용함
- 제조사 GB와 Windows GiB 표기가 다름
- 디스크 복사/파티션 수정 시 복구가 실패할 수 있음

## Backup Image Portability

partclone 기반 백업은 원칙적으로 백업 당시 Windows 파티션 요구 조건을 만족하는 대상에 복원해야 합니다.

대상 Windows 파티션이 필요한 크기보다 작으면 복원을 차단합니다.

## Windows Reinstall

사용자가 C:를 포맷하고 Windows를 재설치하면 Windows Agent와 상태확인 프로그램이 사라질 수 있습니다.

Recoverix EFI/fallback 구조가 남아 있으면 F12 또는 fallback 진입 가능성이 있지만, Windows Agent 자동 복구는 다시 설치해야 합니다.

## Ubuntu Runtime Redistribution

Recoverix는 Ubuntu 기반 런타임을 포함합니다.

필요 조치:

- 오픈소스 라이선스 고지 포함
- 소스 제공 안내 포함
- Ubuntu 상표/브랜딩 노출 최소화
- 최종 상용 배포 전 법무 검토 권장

## Data Loss Notice

백업/복원/파티션 작업은 데이터 손실 가능성이 있습니다.

설치 전과 복원 전 사용자에게 중요 데이터 별도 백업을 안내해야 합니다.
