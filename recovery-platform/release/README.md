# Release bundle (산출 디렉터리)

빌드 스크립트는 **`release/dist/`** 아래에 제품 번들 트리를 생성합니다 (`scripts/build_release.py`).

기대 구조 (**`release/dist/`** 이하):

```text
release/dist/
├── windows_agent/        # 패키지 복사
├── recovery_runtime/
├── grub/                  # 설정·생성기
├── efi/                   # shimx64.efi, grubx64.efi, grub.cfg (boot_manager/assets 에서 복사)
├── manifests/             # bundle_manifest.json 및 제품 검증용 메타
├── docs/                  # 제품 매뉴얼 Markdown
├── tools/                 # 진단·통합 도구(run_integration_checks 등)
├── backup_engine/         # 런타임 의존 패키지(선택 포함)
├── restore_engine/
├── rollback/
├── validation/
├── boot_manager/
├── partition_manager/
├── common/
└── config/
```

소스 저장소에는 **`release/dist/` 내용물을 커밋하지 않습니다** (.gitignore).  
서명 바이너리·SKU별 값은 빌드 파이프라인에서 채워집니다.

## 검증 및 패키징

```bash
PYTHONPATH=. python3 scripts/build_release.py
PYTHONPATH=. python3 scripts/validate_release.py --root release/dist
PYTHONPATH=. python3 scripts/package_release.py --root release/dist
```

## 하위 디렉터리 placeholder

실제 패키지는 **`release/dist/`** 생성 후 확인하세요. 이 저장소에서는 경로 명만 준비합니다.
