# Recoverix Build Instructions

이 문서는 현재 개발 PC 기준 빌드 절차입니다.

## Working Directory

```bash
cd /home/for/recoverix
```

## Test

핵심 테스트:

```bash
pytest -q \
  recovery-platform/tests/test_space_estimation.py \
  recovery-platform/tests/test_run_backup.py \
  recovery-platform/tests/test_recovery_menu.py \
  recovery-platform/tests/test_release_gate.py
```

기준 결과:

```text
76 passed
```

## Runtime Rootfs 반영

소스 변경 후 런타임 rootfs에 필요한 파일을 반영합니다. 예:

```bash
sudo install -m 0644 \
  recovery-platform/backup_engine/space_estimation.py \
  /recovery/build/rootfs/usr/local/lib/recoverix/backup_engine/space_estimation.py
```

변경한 파일이 여러 개면 같은 방식으로 rootfs 경로에 반영해야 합니다.

## Runtime Image Build

```bash
cd /home/for/recoverix/recovery-platform/scripts/runtime_image

sudo ./10_build_squashfs.sh
sudo ./deploy/30_stage_host_boot.sh
sudo ./deploy/40_stage_esp_runtime.sh
```

검증:

```bash
sha256sum /recovery/build/runtime/runtime.squashfs /boot/recoverix/runtime.squashfs
findmnt -R /recovery/build/rootfs || true
```

두 `runtime.squashfs` 해시가 같아야 합니다.

## Commercial Installer Build

ZIP 없이 EXE만 만들 때:

```bash
python3 recovery-platform/scripts/build_commercial_package.py \
  --version T.1.0.0 \
  --no-zip
```

ZIP과 EXE를 모두 만들 때:

```bash
python3 recovery-platform/scripts/build_commercial_package.py \
  --version T.1.0.0
```

산출물 위치:

```text
recovery-platform/release/commercial/
```

## Test Installer Set

같은 코드로 3개 버전을 만드는 테스트용 절차:

```bash
for ver in T.1.0.0 T.1.0.1 T.1.0.2; do
  python3 recovery-platform/scripts/build_commercial_package.py --version "$ver" --no-zip
  rm -rf "recovery-platform/release/commercial/RecoverixSetup-${ver}-x64"
done
```

## Release Archive

설치파일은 Git에 직접 넣지 않습니다. 로컬 보관 폴더:

```text
releases/T.1.0.0/
```

해시 생성:

```bash
sha256sum releases/T.1.0.0/*.exe > releases/T.1.0.0/SHA256SUMS.txt
```
