# Secure Boot

## 목적과 체인(개념)

UEFI에서 Secure Boot이 켜진 경우, 펌웨어가 **신뢰할 수 있는 서명**으로 연결된 **이전 단계(일반적으로 Microsoft CA 기반)**부터 **`shimx64.efi`(shim)·이후 로더(GRUB 등)** 순으로 검증 후 로드합니다.

## 레이어링(참고)

- **Shim (`shimx64.efi`)**: Microsoft 서명을 통한 초기 허브에 맞춰 GRUB 다음 단계까지 연결 가능.
- **GRUB**: 메뉴·타임아웃·recovery 항목 제공.

실제 신뢰 체인·서명 상태는 OEM 이미지·PK/KEK·제조 펌웨어에 따라 달라지며 모든 환경을 동등 보장할 수 없습니다.

## MOK(Machine Owner Key) 개요

운영자가 자체 신뢰 근거(MOK 목록 등)를 등록하여 개발 빌드를 로드할 수 있으나 관리 책임이 크고 **표준 배포 SKU 기본 포함 대상 아님**으로 설계합니다.

## 이 프로젝트가 단독으로 보장하지 않는 항목(IMPORTANT)

- PK/KEK/MOK **자동 운영**·무인 순환
- **제조 신뢰 보드**(하드웨어별)와의 **실시간 교정 자동화**
- 현장 출하 전 **무인 코드 서명·인증서 로테이션** 전 과정 완결

OEM은 **Microsoft 파트너 서명**(또는 동등 허브) 채널을 별도로 확보해야 할 수 있습니다.

## 구현 상태(제품 책임자 참고)

본 저장소는 **설계·경로 정책**(GRUB recovery 항목, Windows 회귀, `bootmgfw.efi` 존중)을 포함합니다.  
현장에서 Secure Boot 검증 거부 가능성 전부를 코드만으로 배제할 수 없으므로, [`UNSUPPORTED_ENVIRONMENTS.md`](UNSUPPORTED_ENVIRONMENTS.md) 범위를 검토하고 **실기 설치 테스트**를 별도로 정의해야 합니다.

## 관련

[`RECOVERYBOOT_POLICY.md`](RECOVERYBOOT_POLICY.md) · [`INSTALL.md`](INSTALL.md)
