# Windows Agent

## 목적

Windows 에이전트는 **EFI/NVRAM 관측·BootOrder 정합성 점검·(정책 충족 시) repair**만 수행합니다. Recovery Runtime의 **백업·복구·무결성 검증 본체를 대체하지 않습니다.**

## 주요 구성

| 항목 | 설명 |
|------|------|
| 스케줄 작업 | `RecoveryBootMonitor` — **SYSTEM** 계정, Highest privileges(문서·스크립트 기준) |
| 진입점 | `python -m windows_agent.agent` (배포에서는 `sys.executable` 고정 권장) |
| BootOrder | `bcdedit` 등 **읽기 우선**·repair는 **별도 apply** |

## BootOrder 모니터링·repair

- 예약 작업은 Windows 부팅 후 1분 뒤 1회 실행하고 종료합니다.
- 반복 실행은 하지 않습니다.
- 실행 시 **펌웨어 나열 결과·BootOrder 스냅샷**을 비교해 **drift**를 기록합니다.
- **dry-run(기본 계획)** 에서는 **NVRAM 수정 없음**.
- **apply** 경로는 관리자/SYSTEM·**BitLocker OFF** 등 정책을 만족할 때만 허용합니다.

## 테스트 Windows 등록

관리자 권한 PowerShell 또는 CMD에서 다음 중 하나를 실행합니다.

```cmd
windows_agent\install_recoveryboot_monitor.cmd
```

```powershell
powershell -ExecutionPolicy Bypass -File .\windows_agent\install_recoveryboot_monitor.ps1
```

등록 확인:

```cmd
schtasks /Query /TN RecoveryBootMonitor /V /FO LIST
```

## 금지 정책

- **ext4 / squashfs 마운트 및 Recovery Image 볼륨 직접 수정 금지**
- **partclone·restore 실행 금지**
- **`recovery_state.json` 직접 수정 금지**
- **`bootmgfw.efi` 덮어쓰기 금지**

## 로그

`%ProgramData%\RecoveryBoot\logs\` 아래 `windows_agent.log`, `bootorder.log`, `repair.log`, `error.log` 등 ([`LOGGING_POLICY.md`](LOGGING_POLICY.md)).

## 관련 코드(참고)

- `windows_agent/agent.py`, `fix_bootorder_task.py`, `task_scheduler.py`, `install_task.py`
