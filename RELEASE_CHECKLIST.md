# Recoverix Release Checklist

릴리즈 전마다 아래를 확인합니다.

## Source

- [ ] `git status` 확인
- [ ] 의도하지 않은 파일 변경 없음
- [ ] 대용량 설치파일이 Git staging에 포함되지 않음
- [ ] `.gitignore`가 release/commercial 및 releases 폴더를 제외함

## Version

- [ ] 제품 버전 결정
- [ ] `VERSION_HISTORY.md` 업데이트
- [ ] 설치파일 버전과 문서 버전 일치
- [ ] 필요 시 Git tag 생성

## Runtime

- [ ] 변경 파일을 `/recovery/build/rootfs`에 반영
- [ ] `sudo ./10_build_squashfs.sh`
- [ ] `sudo ./deploy/30_stage_host_boot.sh`
- [ ] `sudo ./deploy/40_stage_esp_runtime.sh`
- [ ] `/recovery/build/runtime/runtime.squashfs`와 `/boot/recoverix/runtime.squashfs` 해시 일치

## Installer

- [ ] `build_commercial_package.py --version <VERSION>` 실행
- [ ] EXE 생성 확인
- [ ] ZIP 필요 여부 결정
- [ ] `sha256sum` 기록
- [ ] 설치파일 내부 `build-info.json` 확인
- [ ] 설치파일 내부 `install_recoverix.ps1` 정책 확인

## Tests

- [ ] Python tests 통과
- [ ] 신규 설치 테스트
- [ ] 업데이트 테스트
- [ ] 다운그레이드 차단 테스트
- [ ] 제거 테스트
- [ ] q 핫키 부팅 테스트
- [ ] F12 직접 복구 진입 테스트
- [ ] 백업 테스트
- [ ] 복원 테스트

## Archive

- [ ] `releases/<VERSION>/` 생성
- [ ] 설치파일 복사
- [ ] `SHA256SUMS.txt` 생성
- [ ] `RELEASE_NOTES.md` 작성
- [ ] 테스트 결과 저장
- [ ] 외장하드 또는 별도 PC에 백업

## GitHub

- [ ] 소스 커밋
- [ ] 태그 생성
- [ ] 원격 push
- [ ] 설치파일은 Git commit에 넣지 않음
- [ ] 필요 시 GitHub Releases 또는 별도 배포 저장소에 설치파일 업로드
