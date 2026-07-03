# Recoverix Test Checklist

테스트 결과는 날짜, PC, 설치파일 버전, 결과를 함께 기록합니다.

## Baseline Result

기준일: 2026-07-03

기준 설치파일:

- `RecoverixSetup-T.1.0.0-x64.exe`
- `RecoverixSetup-T.1.0.1-x64.exe`
- `RecoverixSetup-T.1.0.2-x64.exe`

현재 확인된 사항:

- 백업 성공
- `T.1.1.9` 기준 계산식에서 백업 공간 문제 개선 확인
- 테스트용 `T.1.0.0`, `T.1.0.1`, `T.1.0.2` 설치파일 생성 완료

## Installer Tests

- [ ] 신규 설치 성공
- [ ] 설치 후 바탕화면 Recoverix 상태확인 아이콘 생성
- [ ] 상태확인 프로그램 실행
- [ ] 프로그램 추가/제거 목록 표시
- [ ] 같은 버전 재실행 시 복구 설치 흐름 확인
- [ ] 낮은 버전 설치 시 다운그레이드 차단
- [ ] 높은 버전 설치 시 업데이트 흐름 확인
- [ ] 제거 실행 시 Windows 구성요소 제거
- [ ] 제거 후 복구 파티션과 백업 이미지 유지

## Boot Tests

- [ ] 아무 키 없이 부팅 시 Windows 진입
- [ ] `q`/`Q` 입력 시 Recoverix GUI 진입
- [ ] F12에서 Recoverix Hotkey Boot 선택 시 핫키 대기 흐름 확인
- [ ] F12에서 Start Recoverix 선택 시 Recoverix GUI 직접 진입
- [ ] Windows Agent가 NVRAM 삭제 후 RecoveryBoot 항목 복구

## Backup Tests

- [ ] 백업 이미지 없는 상태에서 백업 생성
- [ ] 백업 진행률 표시
- [ ] 백업 완료 화면 표시
- [ ] 백업 이미지 존재 상태에서 백업 메뉴 비활성/안내 확인
- [ ] `RECOVERY_IMAGE` 공간 부족 시 명확한 오류 표시

## Restore Tests

- [ ] 백업 이미지 존재 시 복원 메뉴 활성
- [ ] 복원 전 경고 확인
- [ ] 복원 진행률 표시
- [ ] 복원 완료 화면 표시
- [ ] 복원 후 Windows 부팅 확인

## Admin Tests

- [ ] `Ctrl + A`로 관리자 모드 진입
- [ ] `Ctrl + A`로 일반 모드 복귀
- [ ] 로그 보기
- [ ] 부팅 상태 보기
- [ ] 백업 삭제
- [ ] Windows partition preparation 기능 동작 확인

## Windows Shrink Troubleshooting

설치 실패 메시지 예:

```text
Windows partition cannot be shrunk enough.
```

조치:

- [ ] `powercfg /h off`
- [ ] pagefile 임시 해제
- [ ] 시스템 복원 지점 삭제
- [ ] `chkdsk C: /f`
- [ ] 재부팅
- [ ] C: 볼륨 축소 가능 공간 재확인
