# BitLocker 정책

플랫폼은 BitLocker가 켜져 있거나 상태를 안전하게 확정할 수 없을 때 발생할 수 있는 **부팅 루프·데이터 손실 리스크**를 줄이기 위해 쓰기 경로를 차단합니다.

---

## BitLocker **ON** (또는 정책상 동등한 보호 활성)인 경우

다음 작업은 **차단**합니다(또는 경고 로그만 허용하고 수정은 하지 않음).

| 기능 | 상태 |
|------|------|
| 백업 | **blocked** |
| 복구(실행 포함) | **blocked** |
| EFI 파티션 수정(쓰기) | **blocked** |
| BootOrder·펌웨어 항목 **repair** (Windows Agent 포함) | **blocked** |

Windows Agent 구현에서도 BitLocker ON 시 **상태 점검·경고·로그만** 하고 NVRAM/EFI 쓰기 repair는 수행하지 않아야 합니다.

---

## 차단 이유(요약)

### TPM PCR·측정값과의 불일치 가능성

디스크 레이아웃·부트 구성 변경 시 BitLocker·TPM 측 검증과 충돌할 수 있어 **복구 불능·부팅 불가** 위험이 증가합니다.

### EFI 수정 위험

BitLocker가 보호하는 환경에서 ESP를 수정하면 Windows 볼륨 접근·부트 체인과 상호작용하며 예기치 않은 상태가 될 수 있습니다.

### 회복 루프 위험

BootOrder·EFI 변경이 잘못되면 **복구 모드 반복** 또는 **Windows 미진입** 상태로 이어질 수 있습니다.

---

운영 세부(그룹 정책·일시 일시정지·복구 키 입력 절차 등)는 OEM·고객 보안 정책에 따릅니다.

- [`INSTALL.md`](INSTALL.md)
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- [`WINDOWS_AGENT.md`](WINDOWS_AGENT.md)
