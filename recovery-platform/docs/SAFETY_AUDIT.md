# Recoverix 안전 정적 감사 (19단계)

## 목적

실제 사용자 PC 또는 테스트 환경에서 **`backup --apply`**, **`restore --apply`**, **BootOrder repair `--apply`** 같은 파괴적 작업을 수행하기 **전**, 저장소 소스 전체가 복구 참조 문헌·정책(Disk 번호 고정 금지, 이중 확인, BitLocker, 검증 순서 등)과 어긋나지 않는지 **오프라인으로** 검증합니다.

## 감사 대상

| 구분 | 파일·범위 |
|------|-----------|
| 통합 감사 | `tools/safety_audit.py` |
| 구조 정책 | `tools/code_policy_audit.py` (`run_restore`, `restore_executor`, `run_backup`, `image_validation`, `windows_agent/` 등) |
| 명령 sink | `tools/destructive_command_audit.py` (`run_command` / `subprocess.run` 분류; 지정 디렉터리·일부 tools) |
| 패턴 규격 | `tools/forbidden_audit_patterns.json` |

스캔은 기본적으로 `.py`, `.md`, `.json`, `.yaml` 등 텍스트 자산 위주입니다(`.venv` 등은 제외).

## 금지 패턴 요약

`tools/safety_audit.py` 및 JSON 설정에 포함된 검사에는 다음 클래스가 포함됩니다.

- 디스크/슬롯 하드코딩: **`Disk0` / `Disk 0`**, **`Partition1` / `Partition 1`**, **`Boot0000`**, **`/dev/sda`**, **`/dev/nvme0n1`**
- 부트·명령 남용: **`bcdedit /set … bootnext`** 형태(BootNext 강제 의심), **`format partition`**, **`mkfs.`**
- 복구 파이프라인: `run_restore`에 **`authorize_restore_execution()`** 누락·순서 오류, `RestoreExecutor.execute`에서 **`validate_restore`**가 EFI 백업·partclone **이후**에 오는 경우
- 백업: `--apply`·`--confirm`, `finalize_backup_manifest` 누락
- Windows Agent: **ext4/squashfs**, **partclone**, **`recovery_state.json` 직접 조작** 등(도큐스트링 제거 후 본문만 검사)
- `destructive_command_audit`: **`dry_run=False`** 인 `run_command`에 **`confirmed=True`** 없음 → **`DESTRUCTIVE_UNGUARDED`(FAIL)**

추가 정책(로깅 오류 시 복구 실패 전파 금지, restore 자동 retry 금지, BootNext 강제 금지 등)은 코드 리뷰·`code_policy_audit` 및 패턴 검사의 조합으로 다룹니다.

## 허용 예외

- **`whitelist_exact_rel_paths`**: 패턴 검색에서 해당 파일 전체 스킵(감사 스크립트·통합 헬퍼·패턴 자체 등).
- **`whitelist_relative_path_prefixes`**, **`tests/`**, **`docs/`**, 이름이 `test_*.py` 인 파일:
  - 위 금지 토큰이 **설명·테스트 픽스처**로 나오면 패턴 결과는 **`WARN`** 처리됩니다.
  - **실행 코드**(그 외 경로의 `.py` 등)에서는 동일 문자열도 **`FAIL`** 입니다.

## PASS / FAIL 판정

| 상태 | 의미 |
|------|------|
| **PASS** | 코드 경로 패턴 **`FAIL`** 없음, 정책 **`FAIL`** 없음, **`DESTRUCTIVE_UNGUARDED`** 없음, 그리고 정책·명령 감사에 경고만 없거나 무시 가능한 수준 |
| **PASS_WITH_WARNINGS** | 문서·테스트·`SUBPROCESS_RUN_REVIEW` 등 **경고만** 존재(코드 패턴 및 정책 **FAIL 없음**) |
| **FAIL** | 생산 코드 패턴 매치 **`FAIL`**, 구조 정책 **`FAIL`**, 또는 **`DESTRUCTIVE_UNGUARDED`** 1건 이상 |

### 운영 원칙

**FAIL 이면** 실제 환경에서 다음을 금지합니다.

- 실제 **`backup --apply`**
- 실제 **`restore --apply`**
- **BootOrder repair `--apply`(에이전트/태스크의 쓰기 경로)**

**PASS 또는 PASS_WITH_WARNINGS** 일 때만 다음 실제 테스트 단계로 진행합니다.

## 실제 테스트 전 필수 실행

저장소 루트(`recovery-platform/` 기준):

```bash
PYTHONPATH=. python3 tools/safety_audit.py
```

JSON까지 표준 출력으로 보려면:

```bash
PYTHONPATH=. python3 tools/safety_audit.py --json
```

구성 검사 개별 실행:

```bash
PYTHONPATH=. python3 tools/code_policy_audit.py
PYTHONPATH=. python3 tools/destructive_command_audit.py
```

감사 스크립트 자체 단위 테스트(`tests/test_safety_audit.py`, 표준 `unittest`):

```bash
PYTHONPATH=. python3 -m unittest tests.test_safety_audit -v
```

## 리포트 해석


- 기본 산출물: **`diagnostics/safety_audit_report.json`** (`.gitignore`에 의해 저장소에는 커밋되지 않음).
- `pattern_scan.hits`: 위험 문자열 발생 위치; `severity`가 **`WARN`** 이면 주로 문서/테스트, **`FAIL`** 이면 생산 코드로 간주되는 경로입니다.
- `code_policy_audit.findings`: `RS*`, `RE*`, `BK*`, `VL*`, `WA*` 같은 ID별 구조 검사 결과.
- `destructive_command_audit`: 필드 **`complete`** 가 **`false`** 면 미완성 감사로 간주되어 **전체 상태는 FAIL** 입니다(`engine_version`, `tracked_sinks` 포함).
- 생산 코드( `docs/`·`tests/` 가 아닌 경로 )에서 **`findings[].category === "DESTRUCTIVE_UNGUARDED"`** 가 하나라도 있으면 `destructive_command_audit.status` 는 **FAIL** 입니다.
- 같은 **`DESTRUCTIVE_UNGUARDED`** 라도 **`docs/`·`tests/`** 내 예시 코드는 저장소 전체 상태를 FAIL 로 만들지 않고 **경고(PASS_WITH_WARNINGS)** 로만 처리됩니다(`counts_as_fail: false`).
- 명령·파일 **`APPLY_GUARDED`** 는 `_assert_authorized`, `_run_confirmed`, `_ensure_write_allowed`, 인터랙티브 확인류, 또는 정책상 명시 허용 목록 등의 휴리스틱에 의존합니다.

## 한계

- 정적 분석이므로 **호출 순서**(예: 상위에서 이미 권한 확인 후 하위 헬퍼만 호출)를 완전히 증명하지는 못하며, `POLICY_APPLY_FUNCTIONS` 명시 허목에 일부 상태 저장·롤백 헬퍼가 포함됩니다.
- 일부 **`subprocess.run`** 호출은 런타임 문자열이라 탐지가 불가능하며 **`opaque/other argv`** 로 **APPLY_GUARDED** 로 보수 처리됩니다.
- 문자열 패턴 검사는 **주석·문자열 리터럴**에도 반응합니다. 생산 코드에 금지 토큰을 **설명 목적**으로만 두지 않도록 유지해야 합니다.
