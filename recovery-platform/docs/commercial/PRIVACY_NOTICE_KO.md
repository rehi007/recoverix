# Recoverix 개인정보 및 로그 고지 초안

제품명: Recoverix
버전: T.1.0.0
제공자: FORYOUCOM
연락처: 1544-1879
이메일: help@foryoucom.co.kr

본 문서는 Recoverix 설치파일과 상태확인 프로그램에 포함할 개인정보 및
로그 고지 초안입니다.

## 1. 기본 원칙

Recoverix는 복구 기능 수행, 설치 상태 확인, 장애 진단, 고객지원 목적의
로그를 생성할 수 있습니다. 기본 정책은 로컬 저장입니다.

Recoverix는 사용자의 문서, 사진, 동영상, 개인 파일 내용을 수집하기 위한
제품이 아닙니다.

## 2. 로컬에 저장될 수 있는 정보

다음 정보가 로컬 PC에 저장될 수 있습니다.

- PC 이름 또는 사용자 프로필 이름
- Windows 버전 및 부팅 방식
- CPU, 메모리, 그래픽, 저장장치 모델 정보
- 디스크 및 파티션 구조
- EFI/NVRAM 부팅 항목 상태
- Recoverix 설치 상태
- 백업 생성 여부 및 백업 시각
- 복원 실행 여부 및 복원 시각
- 오류 코드, 실패 단계, 진단 로그
- Recoverix Agent 실행 로그

## 3. 저장 위치

Windows 환경:

```text
C:\ProgramData\Recoverix\logs
C:\ProgramData\Recoverix\state
C:\ProgramData\Recoverix\config
```

복구 환경:

```text
/boot/recoverix
RECOVERY_IMAGE 내부 로그 영역
```

## 4. 외부 전송

Recoverix는 기본적으로 로그를 자동 외부 전송하지 않습니다.

고객지원 과정에서 사용자가 직접 로그를 제공하거나, 원격지원에 동의한
경우에만 진단 정보가 FORYOUCOM에 제공될 수 있습니다.

## 5. 보관 및 삭제

로그는 장애 분석과 제품 상태 확인을 위해 보관될 수 있습니다. Recoverix
제거 시 제거 프로그램은 가능한 범위에서 로그와 상태 파일 삭제 옵션을
제공해야 합니다.

단, 고객지원 이력, 보증 처리 이력, 법령상 보관이 필요한 정보는 별도
정책에 따라 보관될 수 있습니다.

## 6. 문의

개인정보 및 로그 처리 관련 문의:

- FORYOUCOM
- 전화: 1544-1879
- 이메일: help@foryoucom.co.kr

