# Configuration (제품 빌드·배포)

이 디렉터리에는 **운영 변수·버전 명세**(제품 SKU·릴리스 번들 포함) 파일을 두는 공간입니다.

## 파일

| 파일 | 목적 |
|------|------|
| `product_manifest.json` | 빌드/검증용 제품 버전·필수 문서·패키지 목록 |

빌드 스크립트(`scripts/build_release.py`)와 검증 스크립트(`scripts/validate_release.py`)는 **`product_manifest.json`** 을 참조합니다.

## 주의

- **비밀·라이선스 키**는 저장소가 아닌 **보안 비밀 관리**(CI secret / 내부 vault)에서만 제공하십시오.
