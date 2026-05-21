# 파티션 정책 (Partition policy)

## 레이블·역할 개요

설계상 다음 **역할**(구체 레이블 문자열은 코드·`partition_manager.models` 기준)을 구분합니다.

| 역할 | 설명 |
|------|------|
| **EFI** | ESP. Windows·RecoveryBoot 진입점이 공존할 수 있음(경로·정책에 따름). |
| **MSR** | Microsoft Reserved. 드라이브 문자 없음. |
| **Windows** | Windows OS 볼륨(일반적으로 NTFS). |
| **RECOVERY_IMAGE** | 백업 이미지·매니페스트·해시·로그·상태 등 **가변 데이터** 저장. |
| **RECOVERY_LINUX** | Recovery Runtime(복구용 Linux) 배포. |

실제 파티션 레이블·파일시스템은 OEM 이미지 규격과 일치해야 하며, 여기서는 **논리 역할**만 정의합니다.

---

## 단일 Windows 부팅 디스크

- **다중 Windows 부팅 디스크**, **다중 OS**는 지원 대상이 아닙니다 ([`UNSUPPORTED_ENVIRONMENTS.md`](UNSUPPORTED_ENVIRONMENTS.md)).

---

## 하드코딩 금지

- **`Disk0`**, **`Partition1`**, **`Boot0000`** 등 **고정 슬롯/번호**에 의존한 식별은 **금지**합니다.
- 항상 **`lsblk`/WMI/동적 디스커버리** 등으로 **경로·레이블·GUID** 기반 식별을 사용합니다.

---

## Recovery 파티션 노출 정책

- **RECOVERY_IMAGE**·**RECOVERY_LINUX**는 사용자 데이터 드라이브로 노출하지 않는 편이 안전합니다(OEM은 기본 드라이브 문자 비할당·숨김 정책 권장).

---

## 관련 문서

[`INSTALL.md`](INSTALL.md) · [`BACKUP.md`](BACKUP.md) · [`UNSUPPORTED_ENVIRONMENTS.md`](UNSUPPORTED_ENVIRONMENTS.md)
