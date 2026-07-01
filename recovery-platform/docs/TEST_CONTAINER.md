# Test Container

GTK 의존성까지 포함한 테스트 실행은 현재 작업 환경보다 별도 Ubuntu 컨테이너가 더 안정적입니다.

## 목적

- `pytest` 실행
- `python3-gi` / GTK 3 바인딩 포함
- 로컬 시스템 패키지 상태와 분리된 재현 가능한 테스트 환경 제공

## 빌드

```bash
docker build -f Dockerfile.test -t recoverix-test .
```

## 실행

```bash
docker run --rm recoverix-test
```

작업 중인 소스 트리를 그대로 마운트해서 실행하려면:

```bash
docker run --rm -v "$PWD":/workspace -w /workspace recoverix-test python3 -m pytest -q
```

GTK 호환성 테스트만 먼저 확인하려면:

```bash
docker run --rm -v "$PWD":/workspace -w /workspace recoverix-test \
  python3 -m pytest -q tests/test_gtk_compat.py
```

## 포함 패키지

- `python3`
- `python3-pytest`
- `python3-gi`
- `gir1.2-gtk-3.0`

이 컨테이너는 프로젝트의 테스트 실행용이며, 실제 Recovery Runtime 부팅 환경과 동일한 이미지는 아닙니다.
