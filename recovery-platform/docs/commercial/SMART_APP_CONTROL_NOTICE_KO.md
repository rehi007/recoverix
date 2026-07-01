# Recoverix Smart App Control 제한 고지

제품명: Recoverix
버전: T.1.0.0

본 버전은 공인 코드서명 인증서가 적용되지 않을 수 있습니다.

Windows Smart App Control, Microsoft Defender SmartScreen 또는 조직 보안
정책은 알 수 없거나 서명되지 않은 실행파일, 설치파일, 스크립트, DLL,
임시 설치 구성요소를 차단할 수 있습니다.

따라서 일부 Windows 환경에서는 Recoverix 설치파일 또는 구성요소 실행이
차단될 수 있습니다.

## 설치 전 표시 권장 문구

```text
Recoverix T.1.0.0은 공인 코드서명 인증서가 적용되지 않은 상태로 배포될
수 있습니다.

이 경우 Windows Smart App Control, Microsoft Defender SmartScreen 또는
조직 보안 정책에 의해 설치파일 또는 일부 구성요소 실행이 차단될 수
있습니다.

이 제한사항을 이해하고 설치를 계속하시겠습니까?
```

## 체크박스 권장 문구

```text
[ ] 공인 코드서명 인증서가 적용되지 않은 Recoverix 구성요소는 Windows
    Smart App Control, SmartScreen 또는 조직 보안 정책에 의해 차단될 수
    있음을 이해했습니다.
```

## 운영 정책

차단이 발생하면 고객지원은 다음을 확인해야 합니다.

- Windows Smart App Control 상태
- Microsoft Defender SmartScreen 상태
- 조직 보안 정책 또는 App Control 정책
- 실행파일 또는 설치파일 경로
- 차단된 파일명
- Windows 이벤트 로그 또는 보안 기록

장기적으로 차단 가능성을 줄이려면 공인 코드서명 인증서 적용, 설치파일과
모든 실행 구성요소 서명, 배포 평판 축적, 고객지원 문서 제공이 필요합니다.

