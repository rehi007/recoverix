# forensic safe console vs xorg forensic GUI — systemd 유닛 차이 조사

**조사만 수행 (코드 수정 없음)**  
**기준 상태:** P0 `/mnt/rootfs-root` 적용 후 — systemd debug 정상, xorg forensic GUI handoff 직후 화면 정지

---

## 비교 대상

| 메뉴 | GRUB (`recoverix-esp.cfg.template`) | 핵심 cmdline |
|------|--------------------------------------|--------------|
| **forensic safe console** | L73–83 | `debug.xorg=1` + **`safe=1`** + **`multi-user.target`** + `quiet splash` |
| **xorg forensic GUI** | L49–58 | `debug.xorg=1` + **safe 없음** + **`graphical.target`** + `quiet splash` |

공통: `recoverix.root=1`, `recoverix.uuid=…`, `root=tmpfs`, initrd 동일.

---

## 1. `recoverix.safe=1` 때문에 실행되지 않는 systemd unit

`ConditionKernelCommandLine=!recoverix.safe=1` 이 있는 유닛 (safe console에서 **ConditionResult=false → job 미시작**):

| 유닛 | 정의 위치 | enable |
|------|-----------|--------|
| `recoverix-xorg-forensic-boot.service` | `runtime_gui_stack_install.sh` L884–885 | `sysinit.target` L996 |
| `recoverix-xorg-forensic-postboot.service` | L904–905 | `multi-user.target` L997 |
| `recoverix-xorg-watchdog.service` | L954–955 | `graphical.target` L999 |
| `recoverix-forensic-sudo.service` | `recovery_ui_install.sh` L359 | `sysinit`+`multi-user` L395–396 |
| `recoverix-forensic-sudo-selftest.service` | L381 | `multi-user.target` L397–398 |

**유닛 Condition은 없지만 스크립트에서 safe 시 SKIP** (getty 훅):

| 대상 | 파일 | 라인 |
|------|------|------|
| `getty@.service` `ExecStartPost` | `recovery_ui_install.sh` L126–128 → `72_recoverix_xorg_forensic_collect.sh` | L166–168 |

**safe=1이어도 Condition 통과하는 Recoverix 유닛** (단, 부팅 타깃에 따라 미실행):

| 유닛 | Condition | forensic safe console | xorg forensic GUI |
|------|-----------|----------------------|-------------------|
| `recoverix-xorg-forensic.service` | `debug.xorg=1` only (L935) | **graphical 미부팅** → 미실행 | **실행** |
| `recoverix-xorg-forensic-postboot.timer` | `debug.xorg=1` only (L919) | timer 동작, **서비스는 !safe로 스킵** | timer+서비스 실행 |
| `recoverix-gui-forensic.service` | **없음** (L861–876) | graphical 미부팅 → 미실행 | **실행** |
| `recoverix-rootfs-debug.service` | **없음** (L152–168) | **실행** (`After=multi-user`) | multi-user 경유 후 실행 |
| `recoverix-gui-fallback.timer` | **없음** | timer 동작, fallback 스크립트는 xorg 모드 스킵 | timer 동작, fallback 스킵 |

---

## 2. `graphical.target` 진입 전 Recoverix 전용 unit · 실행 순서

xorg forensic GUI만 `systemd.unit=graphical.target` (L57).  
**`Before=graphical.target` 또는 `Before=gdm` 이 있는 Recoverix 유닛은 2개뿐** (둘 다 `sysinit.target`에서 pull):

### ① `recoverix-xorg-forensic-boot.service` (`runtime_gui_stack_install.sh` L878–896)

```ini
[Unit]
DefaultDependencies=no
After=local-fs.target systemd-remount-fs.service
Before=graphical.target gdm.service gdm3.service
ConditionKernelCommandLine=recoverix.debug.xorg=1
ConditionKernelCommandLine=!recoverix.safe=1
# Requires= (없음)
# Wants= (없음)
[Install]
WantedBy=sysinit.target
```

### ② `recoverix-forensic-sudo.service` (`recovery_ui_install.sh` L353–372)

```ini
[Unit]
DefaultDependencies=no
After=local-fs.target systemd-remount-fs.service
Before=getty-pre.target getty.target getty@.service multi-user.target graphical.target
ConditionKernelCommandLine=!recoverix.safe=1
# Requires= (없음)
# Wants= (없음)
[Install]
WantedBy=multi-user.target   # + enable 시 sysinit.target.wants (L395–396)
```

**sysinit 단계 실행 순서 (코드 기준, Recoverix만):**

```text
local-fs.target + systemd-remount-fs.service
  ├─[병렬 가능] recoverix-xorg-forensic-boot.service
  └─[병렬 가능] recoverix-forensic-sudo.service
        (둘 다 완료 후에야 graphical.target / gdm 쪽 Before 제약 충족)
```

**forensic safe console:** 위 2개 **모두 Condition 실패 → sysinit Recoverix job 없음** → `multi-user.target` + getty autologin 경로.

---

## 3. `recoverix-forensic-sudo.service` 상세

**유닛** (`recovery_ui_install.sh` L361–368):

| 항목 | 값 |
|------|-----|
| `ExecStartPre` | `/bin/mkdir -p /var/log /run/recoverix` (L364) |
| `ExecStart` | `/usr/local/sbin/recoverix-forensic-sudo-enable start` (L365) |
| `ExecStartPost` | `/usr/local/sbin/recoverix-forensic-sudo-enable selftest` (L366) |
| `Type` | `oneshot` + `RemainAfterExit=yes` |

### `ExecStart` → `start` (`75_recoverix_forensic_sudo_enable.sh`)

| 단계 | 함수 | 라인 | 동작 |
|------|------|------|------|
| 1 | `recoverix_forensic_sudo_enable` | L155–206 | 진입 |
| 2 | `forensic_trace_cmdline` | L159–160, L40–47 | cmdline 로그 |
| 3 | `recoverix_safe_mode_detected` | L162–166 | **safe면 SKIP exit 0** |
| 4 | `recoverix_forensic_cmdline_active` | L168–172 | root 또는 debug.xorg 없으면 SKIP |
| 5 | staging 확인 | L174–177 | `/etc/recoverix/staging/sudoers-recoverix-forensic` |
| 6 | `forensic_trace_sudoers_file` | L179, L49–64 | `visudo -cf` |
| 7 | `install` | L181–184 | → `/etc/sudoers.d/99-recoverix-forensic` |
| 8 | `forensic_trace_sudoers_file` (active) | L189–193 | visudo 재검증 |
| 9 | `recoverix_forensic_sudo_verify` | L198–201, L87–153 | **recoverix 사용자로 `sudo -n true` + `sudo -n journalctl --version`** |
| 10 | marker | L203–204 | `/run/recoverix/forensic-sudo-enabled` |

### `ExecStartPost` → `selftest` (L213–232)

- safe / non-forensic → SKIP (L218–225)
- 아니면 `recoverix_forensic_sudo_verify` **재실행** (L226–231)

**추가 경로 (xorg GUI만):** `gdm.service.d/recoverix-forensic-sudo.conf` (`recovery_ui_install.sh` L405–411)

```ini
ExecStartPre=-/usr/local/sbin/recoverix-forensic-sudo-enable start
```

GDM 기동 시 **sysinit에서 이미 돌았던 enable을 또 호출** (Condition: `recoverix.root`).

**forensic safe console:** 유닛 Condition 실패 + getty에 forensic-sudo **미결합** (검증 L945–948).

---

## 4. `recoverix-xorg-forensic-boot.service` 전체 추적

**유닛** (`runtime_gui_stack_install.sh` L887–891):

```text
ExecStart=/usr/local/sbin/recoverix-xorg-forensic-collect --boot-diagnostics
```

### 스크립트 진입 (`72_recoverix_xorg_forensic_collect.sh`)

| 단계 | 라인 | 동작 |
|------|------|------|
| safe 검사 | L166–168 | safe면 **전체 exit 0** (xorg GUI는 통과) |
| case | L172–174 | `--boot-diagnostics` → `recoverix_xorg_forensic_systemd_diagnostics "boot-diagnostics"` |

### `recoverix_xorg_forensic_systemd_diagnostics()` (L46–130)

**`append_cmd` (외부 명령 실행 + `/var/log/recoverix-xorg.log` 기록):**

| # | 라인 | 명령 |
|---|------|------|
| 1 | L59 | `systemctl get-default` |
| 2 | L60 | `systemctl is-active graphical.target` |
| 3 | L61 | `systemctl is-active multi-user.target` |
| 4 | L62 | `systemctl is-active default.target` |
| 5 | L64–69 | `readlink -f /etc/systemd/system/default.target`, `ls -l` |
| 6 | L71 | **`systemctl status gdm.service --no-pager`** |
| 7 | L72 | **`systemctl status gdm3.service --no-pager`** |
| 8 | L73 | **`systemctl status display-manager.service --no-pager`** |
| 9 | L75–80 | `ls -l` gdm unit 파일 |
| 10 | L82 | `systemctl list-dependencies graphical.target` |
| 11 | L83 | `systemctl list-dependencies multi-user.target` |
| 12 | L85–87 | `systemctl status graphical.target`, `systemctl show graphical.target …` |

**`journalctl` (GDM 로그 수집):**

| # | 라인 | 명령 |
|---|------|------|
| J1 | L89–95 | **`journalctl -b -u gdm.service --no-pager`** |
| J2 | L97–103 | **`journalctl -b -u gdm3.service --no-pager`** |
| J3 | L105–108 | **`journalctl -b -u display-manager.service --no-pager`** |

**Recoverix 유닛 상태 스냅샷 (L110–116):**

- `systemctl status recoverix-xorg-forensic-boot.service`
- `recoverix-xorg-forensic-postboot.service`
- `recoverix-xorg-forensic.service`
- `recoverix-xorg-watchdog.service`

**요약 스냅샷 (L118–127):** graphical/multi-user/gdm 활성 여부.

**특징:** GDM이 **아직 기동 전** sysinit에서 gdm/graphical 상태·journal을 조회 (`Before=gdm` L883). D-Bus/journald 준비 상태에 따라 **지연** 가능.

---

## 5. `graphical.target` 전에 실패·block 가능한 Recoverix 전용 서비스

| 서비스 | block 메커니즘 | safe console | xorg GUI |
|--------|--------------|--------------|----------|
| **`recoverix-xorg-forensic-boot.service`** | `Before=graphical.target gdm*` — oneshot **완료 전** graphical 진입 지연 | **미실행** | **실행** |
| **`recoverix-forensic-sudo.service`** | `Before=graphical.target` — visudo + runuser/sudo 검증 | **미실행** | **실행** |
| `recoverix-forensic-sudo-selftest.service` | `Before=getty*` (graphical **직전**은 아님) | 미실행 | multi-user 이후 |
| `recoverix-xorg-forensic-postboot.service` | `After=multi-user` (graphical **전** 아님) | 미실행 | multi-user 후 |
| `recoverix-rootfs-debug.service` | `After=multi-user` | 실행 | 실행 |

**graphical 직전 Recoverix block 후보만 추출하면 2개:** `recoverix-xorg-forensic-boot`, `recoverix-forensic-sudo`.

---

## 6. `systemd-analyze critical-chain` 기준 Recoverix 트리 (코드 유도)

실기기 `systemd-analyze` 출력은 없음. 유닛 `After`/`Before`/`WantedBy`로 **xorg forensic GUI** 기준 재구성:

```text
graphical.target
│
├─[Before 제약] recoverix-xorg-forensic-boot.service  ★ Recoverix
│     After: local-fs.target, systemd-remount-fs.service
│     ExecStart: recoverix-xorg-forensic-collect --boot-diagnostics
│
├─[Before 제약] recoverix-forensic-sudo.service  ★ Recoverix
│     After: local-fs.target, systemd-remount-fs.service
│     ExecStart: forensic-sudo-enable start
│     ExecStartPost: forensic-sudo-enable selftest
│
├─ display-manager.service → gdm.service
│     └─ drop-in: ExecStartPre=- forensic-sudo-enable start  ★ Recoverix (GDM 시 재호출)
│
├─ recoverix-xorg-forensic.service  ★ (After gdm, graphical, boot)
├─ recoverix-gui-forensic.service  ★ (After gdm, graphical)
└─ recoverix-xorg-watchdog.service  ★ (After xorg-forensic, gdm)

multi-user.target  (graphical 의존 경로)
├─ recoverix-xorg-forensic-postboot.service  ★
├─ recoverix-forensic-sudo-selftest.service  ★
└─ recoverix-rootfs-debug.service  ★

timers.target
├─ recoverix-xorg-forensic-postboot.timer  ★ (45s)
└─ recoverix-gui-fallback.timer  (120s, xorg 모드에서 fallback 스킵)
```

**Requires=:** Recoverix 유닛에 **명시 없음** (전부 `Wants` 또는 `WantedBy`만).

---

## 7. forensic safe console vs xorg forensic GUI — 실행 유닛 차이 요약

| Recoverix 구성요소 | forensic safe console | xorg forensic GUI |
|--------------------|----------------------|-------------------|
| sysinit `xorg-forensic-boot` | ✗ (`safe`) | ✓ |
| sysinit `forensic-sudo` | ✗ (`safe`) | ✓ |
| `forensic-sudo-selftest` (unit) | ✗ | ✓ |
| `xorg-forensic-postboot` | ✗ | ✓ (+ timer) |
| `xorg-forensic` (gui-trace) | ✗ (multi-user) | ✓ |
| `xorg-watchdog` | ✗ | ✓ |
| `gui-forensic` | ✗ | ✓ |
| `rootfs-debug` | ✓ | ✓ |
| getty autologin | ✓ (즉시 경로) | graphical/plymouth 뒤 |
| getty `ExecStartPost` collect | 호출되나 **스크립트 SKIP** | getty 시 postboot 수집 |
| GDM + Xorg | ✗ (주 경로) | ✓ |
| gdm `ExecStartPre` forensic-sudo | GDM 미기동 | ✓ |

**가장 큰 구조 차이:** safe console은 **sysinit Recoverix 2개를 끄고** `multi-user`+getty로 가고, xorg GUI는 **동일 2개가 graphical/GDM 전에 반드시 실행**됩니다.

---

## 8. xorg forensic GUI “멈춤” — Recoverix 서비스 우선순위

handoff 직후 화면 정지 + safe console / systemd debug(`safe`) 성공 패턴을 함께 보면:

### P1 — `recoverix-xorg-forensic-boot.service`

- **근거:** `Before=graphical.target gdm*` (L883); xorg GUI만 실행; safe/systemd-debug는 **스킵**.
- **위험:** sysinit에서 **GDM 미기동 상태**로 `systemctl status gdm*`, `journalctl -u gdm*` 다수 호출 (L71–108).
- **파일:** `runtime_gui_stack_install.sh` L878–896 → `72_recoverix_xorg_forensic_collect.sh` L46–130.

### P2 — `recoverix-forensic-sudo.service`

- **근거:** `Before=graphical.target` (L358); xorg GUI만 실행; `ExecStart`+`ExecStartPost`에서 **visudo + runuser/su + sudo 검증 2회** (L365–366, L87–153).
- **위험:** oneshot **Timeout 미설정**; PAM/사용자 DB 지연 시 graphical 진입 지연; GDM drop-in에서 **재호출** (L410).
- **파일:** `recovery_ui_install.sh` L353–372 → `75_recoverix_forensic_sudo_enable.sh` L155–232.

### P3 — `gdm.service` + `recoverix-forensic-sudo.conf` drop-in (Recoverix가 붙인 GDM 경로)

- **근거:** graphical 필수; Recoverix `ExecStartPre=- forensic-sudo-enable start` (L410); boot 유닛 완료 후 **첫 GUI 진입점**.
- **위험:** DRM/Xorg/plymouth(`quiet splash` L57)와 결합 시 검은 화면·VT 무응답; Recoverix는 GDM 전후 수집 유닛을 더 얹음.
- **참고:** Recoverix **전용 unit 파일은 아니나** xorg GUI에서만 이 drop-in이 실질 실행.

*(graphical **이후** 유닛인 `recoverix-xorg-forensic.service` / `recoverix-xorg-watchdog.service`는 handoff 직후 정지 설명에는 **우선순위 낮음**.)*

---

## 9. 런타임 확인 (코드 변경 없이)

xorg GUI 부팅 직후 (가능하면 serial):

```bash
systemctl show recoverix-xorg-forensic-boot.service -p ActiveState,Result,ConditionResult
systemctl show recoverix-forensic-sudo.service -p ActiveState,Result,ConditionResult
systemctl show graphical.target gdm.service -p ActiveState,SubState

tail -80 /var/log/recoverix-xorg.log
tail -40 /var/log/recoverix-forensic-sudo.log

systemd-analyze critical-chain graphical.target
systemd-analyze blame | grep recoverix
```

**대조 부팅:** `Recoverix Runtime (forensic safe console)` — 위 Recoverix sysinit 2개 `ConditionResult=no` 기대.

---

## 10. 한 줄 결론

**forensic safe console**은 `recoverix.safe=1`로 **sysinit의 `recoverix-xorg-forensic-boot`·`recoverix-forensic-sudo`를 끄고** `multi-user`+getty로 가며, **xorg forensic GUI**는 **`safe` 없이 위 2개가 `Before=graphical.target`로 GDM 전에 실행**되는 것이 저장소상 최대 차이입니다. handoff 직후 정지와 가장 잘 맞는 Recoverix 후보는 **P1 boot-diagnostics**, **P2 forensic-sudo**, **P3 GDM+drop-in** 순입니다.

---

## 관련 파일 (저장소)

| 영역 | 경로 |
|------|------|
| GRUB 메뉴 | `scripts/runtime_image/deploy/grub/recoverix-esp.cfg.template` |
| GUI/Xorg 유닛 생성 | `scripts/runtime_image/lib/runtime_gui_stack_install.sh` |
| forensic sudo · getty | `scripts/runtime_image/lib/recovery_ui_install.sh` |
| xorg 수집 스크립트 | `scripts/runtime_image/deploy/72_recoverix_xorg_forensic_collect.sh` |
| forensic sudo 스크립트 | `scripts/runtime_image/deploy/75_recoverix_forensic_sudo_enable.sh` |
| 이전 부팅 조사 | `docs/XORG_FORENSIC_GUI_BOOT_INVESTIGATION.md` |
