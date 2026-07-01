# xorg forensic GUI 부팅 경로 조사 (코드 수정 없음)

**작성 기준:** P0 `RECOVERIX_MERGED_ROOT=/mnt/rootfs-root` 적용 후  
**상태:** systemd debug 정상 / xorg forensic GUI handoff 직후 화면 정지  
**범위:** GRUB → initramfs → run-init → systemd → GDM → Recoverix forensic 유닛

---

## 0. 관측 로그 해석

xorg forensic GUI 로그 순서:

```text
premount / handoff (Recoverix initramfs)
  ↓
mount: mounting tmpfs on /root failed   ← mountroot (Recoverix 아님, 양쪽 메뉴 공통)
  ↓
handoff complete
  ↓
(이후 Recoverix [INFO] 없음 — initramfs quiet + run-init + systemd 전환)
```

`mount tmpfs on /root failed`는 `deploy/grub/recoverix-esp.cfg.template` L56 `root=tmpfs` + Debian `local_mount_root` 조합에서 **기대되는 비치명 오류**입니다. systemd debug에서도 동일하게 나올 수 있습니다.

handoff complete **이후** Recoverix 마일스톤이 끊기는 것은 **initramfs 단계 종료 직전/직후**이며, 이후는 **PID1 systemd** 영역입니다 (같은 initrd를 쓰는 systemd debug가 overlay로 부팅되므로 **run-init 자체는 xorg GUI에서도 통과했을 가능성이 큼**).

---

## 1. GRUB menuentry → kernel cmdline

### xorg forensic GUI (`recoverix-esp.cfg.template` L49–58)

```text
recoverix.root=1
recoverix.uuid=@RECOVERY_UUID@
recoverix.debug.xorg=1
root=tmpfs
ro quiet splash systemd.unit=graphical.target
```

### systemd debug (동일 파일 L86–97) — 비교용

```text
recoverix.root=1
recoverix.uuid=@RECOVERY_UUID@
recoverix.debug.xorg=1
recoverix.safe=1          ← xorg GUI에 없음
root=tmpfs
ro                        ← quiet splash 없음
systemd.unit=multi-user.target
systemd.log_level=debug systemd.log_target=console systemd.show_status=1
```

| 차이 | xorg forensic GUI | systemd debug |
|------|-------------------|---------------|
| `recoverix.safe=1` | **없음** | **있음** |
| `systemd.unit` | **graphical.target** | **multi-user.target** |
| `quiet splash` | **있음** | **없음** |
| systemd 콘솔 디버그 | 없음 | **있음** |

**initramfs 훅** (`recoverix-lib` `recoverix_parse_cmdline` L54–66): `recoverix.root` / `recoverix.debug`만 파싱.  
→ **premount·handoff 경로는 두 메뉴 동일** (`/mnt/rootfs-root`).

---

## 2. initramfs → run-init (공통)

| 단계 | 파일 | 라인 | 동작 |
|------|------|------|------|
| premount | `local-premount/recoverix-overlay` | L24, 119, 142 | overlay → `/mnt/rootfs-root` |
| mountroot | Debian `scripts/local` | — | `root=tmpfs` → `/root` 실패 (로그만) |
| handoff | `00-recoverix-handoff` | L71–72, 96–99 | `rootmnt=/mnt/rootfs-root`, `/conf/param.conf` |
| `/run` 이동 | `/usr/share/initramfs-tools/init` | **306** | `mount --move /run ${rootmnt}/run` |
| pivot | 동일 | **367** | `exec run-init … /mnt/rootfs-root /usr/sbin/init` |

---

## 3. run-init 이후 — `recoverix.debug.xorg=1`이 활성화하는 유닛·스크립트

정의·설치: `lib/runtime_gui_stack_install.sh` `runtime_install_gui_systemd_units()` **L843–1010**  
보조: `lib/recovery_ui_install.sh` **L126–131**, **L353–412**

### A. `ConditionKernelCommandLine=recoverix.debug.xorg=1` (유닛)

| 유닛 | 파일(생성) | Condition | WantedBy | ExecStart |
|------|------------|-----------|----------|-----------|
| `recoverix-xorg-forensic-boot.service` | `runtime_gui_stack_install.sh` L878–896 | `debug.xorg=1` **+** `!safe=1` (L884–885) | **sysinit.target** (L896, enable L996) | `recoverix-xorg-forensic-collect --boot-diagnostics` (L891) |
| `recoverix-xorg-forensic-postboot.service` | L899–914 | `debug.xorg=1` **+** `!safe=1` (L904–905) | **multi-user.target** (enable L997) | `--postboot-diagnostics` (L911) |
| `recoverix-xorg-forensic-postboot.timer` | L916–928 | `debug.xorg=1` only (L919) | **timers.target** (L1000) | 45s 후 postboot (L922–923) |
| `recoverix-xorg-forensic.service` | L930–947 | `debug.xorg=1` only (L935) | **graphical.target** (L998) | `--gui-trace` (L941) |
| `recoverix-xorg-watchdog.service` | L949–967 | `debug.xorg=1` **+** `!safe=1` (L954–955) | **graphical.target** (L999) | `recoverix-xorg-watchdog` (L961) |

### B. `recoverix.debug.xorg=1`로 **런타임 분기** (유닛 Condition 없음, 스크립트 내부)

| 대상 | 파일 | 라인 | 동작 |
|------|------|------|------|
| getty `ExecStartPost` | `recovery_ui_install.sh` | L126–128 | `--postboot-diagnostics` (getty 뜰 때) |
| `recoverix-gui-fallback-check` | `71_recoverix_gui_fallback_check.sh` | L16–19 | xorg forensic면 **multi-user fallback 스킵** |
| `recoverix-xorg-watchdog` | `74_recoverix_xorg_watchdog.sh` | L20–23 | cmdline guard |
| `recoverix-xorg-forensic-collect` | `72_recoverix_xorg_forensic_collect.sh` | L35–37, L166–168 | safe면 전체 SKIP |

### C. `recoverix.debug.xorg=1`과 **연관**되나 유닛 Condition은 `!safe`만

| 유닛 | 파일 | Condition | WantedBy | ExecStart |
|------|------|-----------|----------|-----------|
| `recoverix-forensic-sudo.service` | `recovery_ui_install.sh` L353–372 | **`!recoverix.safe=1`** (L359) | **sysinit.target + multi-user.target** (L395–396) | `recoverix-forensic-sudo-enable start` + selftest (L365–366) |
| `recoverix-forensic-sudo-selftest.service` | L374–391 | `!safe=1` + marker (L380–381) | multi-user.target | selftest (L385) |

헬퍼 `75_recoverix_forensic_sudo_enable.sh` L168–172: `recoverix.root=1` **또는** `recoverix.debug.xorg=1`일 때만 활성.

### D. graphical 부팅 시 항상 (debug.xorg **조건 없음**)

| 유닛 | `runtime_gui_stack_install.sh` | ExecStart |
|------|-------------------------------|-----------|
| `recoverix-gui-forensic.service` | L861–876, WantedBy **graphical.target** | `recoverix-gui-forensic-collect` (L870) |
| `gdm.service` / `gdm3.service` | L527–536 | 표준 GDM |
| `recoverix-gui-fallback.timer` | L981–990, L1004 | 120s 후 fallback check |

---

## 4. WantedBy=sysinit.target / graphical.target 유닛 목록

### `WantedBy=sysinit.target` (Recoverix)

| 유닛 | enable 위치 | xorg GUI | systemd debug (`safe=1`) |
|------|-------------|----------|--------------------------|
| `recoverix-xorg-forensic-boot.service` | `runtime_gui_stack_install.sh` L996 | **실행** | **스킵** (`!safe` 실패) |
| `recoverix-forensic-sudo.service` | `recovery_ui_install.sh` L395–396 | **실행** | **스킵** (`!safe` 실패) |

### `WantedBy=graphical.target` (Recoverix)

| 유닛 | enable | xorg GUI | systemd debug |
|------|--------|----------|-----------------|
| `recoverix-xorg-forensic.service` | L998 | **실행** (`debug.xorg`) | 유닛 조건 통과하나 **graphical 미부팅** |
| `recoverix-xorg-watchdog.service` | L999 | **실행** | **스킵** (`safe=1`) |
| `recoverix-gui-forensic.service` | L1004 chroot enable | graphical 시 실행 | multi-user라 **미실행** |

### `WantedBy=multi-user.target` (관련)

- `recoverix-xorg-forensic-postboot.service` (L997)
- `recoverix-forensic-sudo.service` / selftest
- `recoverix-rootfs-debug.service` (`recovery_ui_install.sh` L167–171)
- getty autologin (`recoverix.root=1`, L118)

---

## 5. 실행 흐름 (xorg forensic GUI)

```text
GRUB (recoverix-esp.cfg.template L49-58)
  → kernel + initrd
  → premount: overlay @ /mnt/rootfs-root
  → mountroot: root=tmpfs /root 실패 (비치명)
  → handoff: rootmnt=/mnt/rootfs-root, param.conf
  → /init L306: mount --move /run → /mnt/rootfs-root/run
  → /init L367: run-init → systemd (PID1)
       cmdline: systemd.unit=graphical.target, quiet splash

sysinit.target
  → recoverix-xorg-forensic-boot.service (--boot-diagnostics)
  → recoverix-forensic-sudo.service (start + selftest)

multi-user.target (의존 일부)
  → recoverix-xorg-forensic-postboot.service (+ timer 45s)

graphical.target
  → gdm.service / gdm3.service
  → recoverix-xorg-forensic.service (--gui-trace)
  → recoverix-gui-forensic.service
  → recoverix-xorg-watchdog.service (20s Xorg 대기 → 없으면 multi-user isolate)
```

### 유닛별 상세 (After / Before / ExecStart)

**① `recoverix-xorg-forensic-boot.service`** (`runtime_gui_stack_install.sh` L878–896)

- **After:** `local-fs.target`, `systemd-remount-fs.service`
- **Before:** `graphical.target`, `gdm.service`, `gdm3.service`
- **ExecStart:** `/usr/local/sbin/recoverix-xorg-forensic-collect --boot-diagnostics`
- **실행 내용:** `72_recoverix_xorg_forensic_collect.sh` `recoverix_xorg_forensic_systemd_diagnostics()` L46–130

**② `recoverix-forensic-sudo.service`** (`recovery_ui_install.sh` L353–372)

- **Before:** `getty-pre.target`, `getty.target`, `graphical.target`
- **ExecStart:** `recoverix-forensic-sudo-enable start` + **ExecStartPost selftest**

**③ GDM** (`runtime_gui_stack_install.sh` L527–536)

- rootfs 기본: `graphical.target` + `gdm.service` enable

**④ `recoverix-xorg-forensic.service`** (L930–947)

- **After:** `graphical.target`, `gdm.service`, `gdm3.service`, `recoverix-xorg-forensic-boot.service`
- **ExecStart:** `--gui-trace` → `recoverix_xorg_forensic_gui_trace()` L136–164

**⑤ `recoverix-gui-forensic.service`** (L861–876)

- **Condition 없음** (debug.xorg 불필요)
- **After:** `gdm.service`, `graphical.target`
- **ExecStart:** `/usr/local/sbin/recoverix-gui-forensic-collect`

**⑥ `recoverix-xorg-forensic-postboot.service`** (L899–914)

- **After:** `multi-user.target`
- timer **45s** — 초기 hang 직후보다 늦음

**⑦ `recoverix-xorg-watchdog.service`** (L949–967)

- **After:** `recoverix-xorg-forensic.service`
- **ExecStart:** `74_recoverix_xorg_watchdog.sh` — Xorg 20s 대기 후 없으면 `systemctl isolate multi-user.target` (L82)

---

## 6. systemd hang 가능 지점

| 순위 | 지점 | 근거 |
|------|------|------|
| **1** | **`graphical.target` + `quiet splash`** | xorg GUI만 `splash`. plymouth가 VT·콘솔 점유 → handoff 직후 검은 화면·F2–F4 무응답과 일치 |
| **2** | **GDM → Xorg/DRM** | `graphical.target` 직접 부팅. GDM/Xorg 대기 시 장시간 무출력 |
| **3** | **`recoverix-xorg-forensic-boot` @ sysinit** | `Before=graphical.target`. sysinit에서 journalctl/gdm 수집 — 지연 가능 |
| **4** | **`recoverix-forensic-sudo` @ sysinit** | xorg GUI만 실행 (`!safe`). visudo·selftest |
| **5** | **`recoverix-xorg-watchdog`** | 20s 후 multi-user 전환 (즉시 hang 아님) |
| **6** | **initramfs `mount tmpfs /root`** | 비치명. hang 원인으로 보기 어려움 |

**handoff complete 직후 “정지”** (저장소 관점):

1. initramfs Recoverix 로그 종료 (정상)
2. `quiet` + plymouth + `graphical.target` 조합으로 출력 없는 구간
3. 실제 block은 sysinit forensic 수집 또는 GDM/Xorg 쪽 가능성

systemd debug 성공 이유: 같은 initrd + **`recoverix.safe=1`** (sysinit forensic/sudo 비활성) + **`multi-user.target`** + 콘솔 autologin.

---

## 7. `recoverix.safe=1` 차이

| 항목 | xorg forensic GUI | systemd debug (`safe=1`) |
|------|-------------------|--------------------------|
| `recoverix-xorg-forensic-boot` | **ON** | **OFF** (`!safe`, L885) |
| `recoverix-xorg-forensic-postboot` | **ON** | **OFF** (`!safe`, L905) |
| `recoverix-xorg-watchdog` | **ON** | **OFF** (`!safe`, L955) |
| `recoverix-xorg-forensic` (`--gui-trace`) | **ON** | multi-user라 graphical 체인 미진입 |
| `recoverix-forensic-sudo` @ sysinit | **ON** | **OFF** (`!safe`, L359) |
| `recoverix-xorg-forensic-collect` | 전체 실행 | L166–168 **SKIP** |
| `recoverix-gui-fallback` | xorg면 스킵 (L16–19) | debug.xorg 있으면 동일 스킵 |
| 부팅 타깃 | **graphical** | **multi-user** |
| 콘솔 | splash·plymouth | **show_status + autologin getty** |

`recoverix.safe=1`은 initramfs가 아니라 **sysinit/graphical 초기 Recoverix 유닛 대부분을 끄는 회귀 격리 스위치** (`72_recoverix_xorg_forensic_collect.sh` L39–43).

---

## 8. xorg forensic GUI만 달라지는 코드 경로

| 구간 | 차이 여부 |
|------|-----------|
| GRUB / initramfs / handoff / run-init | **동일** |
| kernel → systemd 파라미터 | **`systemd.unit`**, **`safe`**, **`quiet splash`**, systemd debug 로그 |
| sysinit Recoverix 유닛 | xorg GUI: boot-diagnostics + forensic-sudo / systemd debug: 스킵 |
| boot path | xorg: graphical → GDM / systemd debug: multi-user → getty autologin |
| graphical Recoverix 유닛 | xorg GUI만 xorg-forensic + gui-forensic + watchdog 체인 |
| fallback | xorg: watchdog (`71` L16–19) / 일반: 120s timer |

---

## 9. 런타임 확인 명령 (코드 변경 없이)

```bash
cat /proc/cmdline | tr ' ' '\n' | grep -E 'recoverix|systemd.unit'
findmnt /

systemctl status recoverix-xorg-forensic-boot.service
systemctl status recoverix-forensic-sudo.service
systemctl status graphical.target gdm.service recoverix-xorg-forensic.service

tail -100 /var/log/recoverix-xorg.log
tail -50 /var/log/recoverix-gui.log
```

**비교 부팅:** `Recoverix Runtime (forensic safe console)` — `debug.xorg=1` + `safe=1` + `multi-user` (`recoverix-esp.cfg.template` L73–83).

---

## 10. 한 줄 결론

handoff complete 이후 “멈춤”은 initramfs Recoverix 코드가 아니라 run-init 이후 **`systemd.unit=graphical.target` + `quiet splash` + (`safe` 없이) sysinit의 xorg forensic boot 수집·forensic-sudo + GDM/graphical** 경로에서 발생하는 것으로 보는 것이 저장소 코드와 일치한다. systemd debug는 **`recoverix.safe=1` + `multi-user.target` + 콘솔 디버그**로 그 경로 대부분을 우회해 성공한다.

---

## 관련 파일 (저장소)

| 영역 | 경로 |
|------|------|
| GRUB 메뉴 | `recovery-platform/scripts/runtime_image/deploy/grub/recoverix-esp.cfg.template` |
| initramfs premount/handoff | `initramfs-hooks/scripts/local-premount/recoverix-overlay`, `init-bottom/00-recoverix-handoff` |
| systemd 유닛 생성 | `lib/runtime_gui_stack_install.sh`, `lib/recovery_ui_install.sh` |
| 런타임 스크립트 | `deploy/70_*.sh`, `71_*.sh`, `72_*.sh`, `74_*.sh`, `75_*.sh` |
| P0 merged 경로 | `overlay/recoverix-overlay-paths.conf` (`/mnt/rootfs-root`) |
