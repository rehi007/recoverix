# Recoverix Version History

이 문서는 Codex 대화 기억에 의존하지 않기 위한 기준 기록입니다.

## Current Baseline

기준 제품명: Recoverix

회사 정보:

- 회사명: FORYOUCOM
- 연락처: 1544-1879
- 이메일: help@foryoucom.co.kr

기준 코드 상태:

- 기준 기능 버전: `T.1.0.0`
- 테스트용 동일 기능 설치파일: `T.1.0.0`, `T.1.0.1`, `T.1.0.2`
- 세 설치파일은 같은 코드와 같은 기능을 사용하며, 버전 비교 테스트용으로 버전 번호만 다릅니다.

## T.1.0.0 Baseline

주요 기능:

- Windows 64-bit, UEFI, GPT 환경용 상용 복구 솔루션
- Recoverix Boot Manager 핫키 부팅
- `q`/`Q` 입력 시 복구 GUI 진입
- 아무 키 입력이 없으면 Windows 기본 부팅
- F12 직접 복구 진입 항목 지원
- Windows Agent와 NVRAM writer를 통한 RecoveryBoot 항목 자동 복구
- GUI 기반 시스템 상태, 백업, 복원, 로그, 관리자 메뉴
- 상태확인 프로그램 포함
- 설치/재설치/업데이트/다운그레이드 차단 로직 포함

파티션 정책:

- `RECOVERY_LINUX`: 4GB
- `RECOVERY_IMAGE`: `max(Windows used space * 0.75 + 10GB, 35GB)`
- Windows 파티션 뒤쪽 미할당 예비 공간: 100MB

백업 공간 정책:

- 백업 필요 공간: `Windows current used space * 0.75 + 2GiB`
- EFI/GPT 및 소량의 메타데이터 공간은 별도 계산에 포함됩니다.

사용자 안내 정책:

- 설치에는 최소 약 40GB 이상의 확보 가능한 공간이 필요하다고 안내합니다.
- 실제 사용 공간은 Windows 사용량에 따라 자동 계산됩니다.
- 복구/백업 공간은 Windows 탐색기에서 일반 드라이브처럼 보이지 않을 수 있습니다.
- 설치 후 C: 용량이 줄어든 것처럼 보일 수 있습니다.

## Test Installers

보관 위치:

```text
releases/T.1.0.0/
```

파일:

```text
RecoverixSetup-T.1.0.0-x64.exe
RecoverixSetup-T.1.0.1-x64.exe
RecoverixSetup-T.1.0.2-x64.exe
SHA256SUMS.txt
```

해시:

```text
056e365bb3be999a0e3cb21c32d881b480568de1c2959322d3b0dba835698298  RecoverixSetup-T.1.0.0-x64.exe
e56fdcd951126275b7e1510d6051f84546ca6ab52a0ed2fb1c5db09b224e3e17  RecoverixSetup-T.1.0.1-x64.exe
729edd7161731d9e501fa8e47e6aef1e17d5da17dca9a9b4178e9f1580c270a0  RecoverixSetup-T.1.0.2-x64.exe
```

## Update Rule

새 기능을 만들면 다음 순서로 기록합니다.

1. 코드 수정
2. 테스트 실행
3. 런타임 이미지 재빌드
4. 설치파일 새 버전 생성
5. 이 문서에 변경 내용 추가
6. `TEST_CHECKLIST.md`에 테스트 결과 기록
7. Git 커밋 및 태그 생성
