# 비지원 환경 (Unsupported environments)

아래 조합 또는 구성은 **지원 범위에 포함되지 않습니다.** 예상대로 동작하지 않거나 **데이터 손실·부팅 불가** 상태가 될 수 있습니다.

- **Legacy BIOS** — GPT/UEFI Boot·RecoveryBoot 전제 불일치
- **MBR** — 디스크 레이아웃 규격 불일치
- **Dual-boot / Multi-boot** — 단일 Windows 부팅 디스크 정책 위반
- **Multi-OS** — 디스커버리·위험이 비결정적
- **RAID**(하드웨어·소프트웨어) — 블록 계층 비표준
- **Dynamic Disk**(Windows 동적 디스크)
- **BitLocker ON** — 백업/복구/EFI/BootOrder repair 차단이 전제
- **Windows 부팅 디스크 다중**(Multiple Windows boot disks)
- 임의 **raw clone** 후 **디바이스 ID가 매니페스트와 불일치**한 환경

사용자 임의 UEFI 펌웨어/보안 설정 변경은 책임·SLA 분리 검토 후 지원 불가 처리할 수 있습니다.

자세한 지원 매트리스는 [`INSTALL.md`](INSTALL.md), [`PARTITION_POLICY.md`](PARTITION_POLICY.md)를 참고하십시오.
