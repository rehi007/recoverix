#!/usr/bin/env bash
# Parse pre_purge_gate logs → category actions and summary JSON/txt.

_PREFLIGHT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=exit_codes.sh
source "${_PREFLIGHT_LIB_DIR}/exit_codes.sh"

# Map gate log category (column 2) to remediation class.
_preflight_classify_row() {
  local status="$1"
  local cat="$2"
  local detail="$3"
  local action_class="GENERAL"

  case "$cat" in
    host_safety) action_class="HOST_SAFETY" ;;
    boot_critical_purge_list|boot_critical_installed) action_class="BOOT_CRITICAL" ;;
    boot_critical_simulate) action_class="APT_REMV_CRITICAL" ;;
    dependency_tree) action_class="DEPENDENCY" ;;
    dependency_check_skipped) action_class="DEPENDENCY_SKIPPED" ;;
    kernel_safety) action_class="KERNEL" ;;
    efi_runtime) action_class="EFI" ;;
    bootability) action_class="BOOTABILITY" ;;
    gtk_runtime) action_class="GTK" ;;
    squashfs_readiness) action_class="SQUASHFS_READY" ;;
    *) action_class="GENERAL" ;;
  esac

  printf '%s' "$action_class"
}

_preflight_action_message() {
  local class="$1"
  case "$class" in
    HOST_SAFETY)
      printf '%s' "즉시 중단. ROOTFS·config.env·mount 상태를 확인하세요. host Ubuntu를 chroot 대상으로 지정했는지 검사합니다."
      ;;
    BOOT_CRITICAL)
      printf '%s' "purge 목록에 KEEP_KERNEL_FLAVOR 커널 패키지가 포함되었거나 boot_critical 패턴과 충돌합니다. 구 커널만 tier1에 두고 keep 커널(예: linux-image-\${KEEP_KERNEL_FLAVOR})은 제외한 뒤 purge plan을 재생성하세요."
      ;;
    APT_REMV_CRITICAL)
      printf '%s' "apt simulation에서 필수 패키지가 REMV 대상입니다. purge plan 수정 전까지 실제 purge를 금지합니다."
      ;;
    DEPENDENCY)
      printf '%s' "apt-rdepends/rdepends 충돌입니다. 삭제 후보 패키지를 재검토하고 dependency_tree 로그를 확인하세요."
      ;;
    DEPENDENCY_SKIPPED)
      printf '%s' "apt-rdepends 미설치로 dependency 검사가 생략되었습니다. minimal runtime 정책상 optional이며 purge를 차단하지 않습니다."
      ;;
    KERNEL)
      printf '%s' "KEEP_KERNEL_FLAVOR와 /boot·/lib/modules가 일치하지 않습니다. config.env의 KEEP_KERNEL_FLAVOR를 수정하거나 커널 부트 아티팩트를 보존하세요."
      ;;
    EFI)
      printf '%s' "rootfs 트리에 EFI 파일이 없거나 경로가 다릅니다. 빌드 트리에서는 WARN일 수 있습니다(실제 ESP는 별도 마운트). squashfs/배포 전 실제 ESP 레이아웃을 확인하세요."
      ;;
    BOOTABILITY)
      printf '%s' "update-initramfs 또는 grub-mkconfig가 실패했습니다. 실제 Tier1 purge를 금지하고 부트 체인을 먼저 복구하세요."
      ;;
    GTK)
      printf '%s' "Python GTK Recovery UI가 동작하지 않습니다. python3-gi, gir1.2-gtk-3.0, libgtk-3-0, X11 최소 스택 유지가 필요합니다."
      ;;
    SQUASHFS_READY)
      printf '%s' "warnable broken symlink(UNKNOWN/BOOT_CRITICAL/GTK_CRITICAL) 또는 dpkg/ldconfig 문제입니다. sudo ./09_repair_rootfs_integrity.sh 후 classification 리포트를 확인하고 preflight를 재실행하세요."
      ;;
    SIZE)
      printf '%s' "정보성 용량 리포트입니다. FAIL로 처리하지 않습니다."
      ;;
    *)
      printf '%s' "로그를 확인하고 해당 검사 항목을 수동으로 해결하세요."
      ;;
  esac
}

analyze_gate_log() {
  local gate_log="$1"
  local summary_txt="$2"
  local summary_json="$3"
  local tier="${4:-tier1}"

  local pass=0 warn=0 fail=0 skip=0
  declare -A fail_items=()
  declare -A warn_items=()

  if declare -f rootfs_count_gate_status &>/dev/null; then
    rootfs_count_gate_status "$gate_log" gate
    pass="${gate_PASS:-0}"
    warn="${gate_WARN:-0}"
    fail="${gate_FAIL:-0}"
    skip="${gate_SKIP:-0}"
  fi

  while IFS=$'\t' read -r status cat detail _rest; do
    [[ -z "$status" ]] && continue
    case "$status" in
      SKIP)
        continue
        ;;
      WARN)
        if [[ "$cat" == "dependency_tree" && "$detail" == *DEPENDENCY_CHECK_SKIPPED* ]]; then
          continue
        fi
        local wc
        wc="$(_preflight_classify_row "$status" "$cat" "$detail")"
        warn_items["$wc"]="${warn_items[$wc]:-}${cat}: ${detail}\n"
        ;;
      FAIL)
        local fc
        fc="$(_preflight_classify_row "$status" "$cat" "$detail")"
        [[ "$fc" == "SIZE" ]] && continue
        fail_items["$fc"]="${fail_items[$fc]:-}${cat}: ${detail}\n"
        ;;
    esac
  done < "$gate_log"

  local purge_allowed="true"
  local exit_code=$RC_PASS
  if declare -f rootfs_gate_exit_from_counts &>/dev/null; then
    rootfs_gate_exit_from_counts "$fail" "$warn"
    exit_code=$?
  else
    if [[ $fail -gt 0 ]]; then
      exit_code=$RC_FAIL
    elif [[ $warn -gt 0 ]]; then
      exit_code=$RC_WARN
    fi
  fi
  if [[ $fail -gt 0 ]]; then
    purge_allowed="false"
  elif [[ $warn -gt 0 ]]; then
    purge_allowed="false"
  fi

  local ts
  ts="$(date -u +%Y%m%dT%H%M%SZ)"

  {
    echo "=== Recoverix Rootfs Preflight Summary ==="
    echo "timestamp: ${ts}"
    echo "tier: ${tier}"
    echo "gate_log: ${gate_log}"
    echo
    echo "counts:"
    echo "  PASS: ${pass}"
    echo "  WARN: ${warn}"
    echo "  FAIL: ${fail}"
    echo "  SKIP: ${skip}"
    echo
    echo "purge_allowed: ${purge_allowed}"
    echo "recommended_exit_code: ${exit_code}"
    echo "  0 = PASS only — Tier1 purge may proceed after review"
    echo "  1 = WARN only — default BLOCK (use FORCE_WARN=1 on apply to override)"
    echo "  2 = FAIL present — Tier1 purge FORBIDDEN"
    echo
    if [[ $fail -gt 0 ]]; then
      echo "=== FAIL by category ==="
      for fc in "${!fail_items[@]}"; do
        echo "[${fc}]"
        echo -e "${fail_items[$fc]}"
        echo "조치: $(_preflight_action_message "$fc")"
        echo
      done
    fi
    if [[ $warn -gt 0 ]]; then
      echo "=== WARN by category ==="
      for wc in "${!warn_items[@]}"; do
        echo "[${wc}]"
        echo -e "${warn_items[$wc]}"
        echo "조치: $(_preflight_action_message "$wc")"
        echo
      done
    fi
    echo "=== 다음 단계 ==="
    if [[ $fail -gt 0 ]]; then
      echo "1. 위 FAIL 조치를 모두 해결"
      echo "2. sudo ./08_preflight_run.sh ${tier} 재실행"
      echo "3. PASS(exit 0) 확인 후에만:"
      echo "   sudo APT_DRY_RUN=0 ./03_minimize_apply.sh ${tier}"
    elif [[ $warn -gt 0 ]]; then
      if [[ -n "${warn_items[SQUASHFS_READY]:-}" ]]; then
        echo "1. rootfs 무결성 repair:"
        echo "   sudo ./09_repair_rootfs_integrity.sh"
        echo "   sudo SKIP_SNAPSHOT=1 ./08_preflight_run.sh ${tier}"
      else
        echo "1. WARN 원인 검토(특히 EFI는 rootfs 트리 미포함일 수 있음)"
        echo "2. 해결 후 preflight 재실행 권장"
        echo "3. 정말 진행 시: FORCE_WARN=1 (권장하지 않음)"
      fi
    else
      echo "Preflight PASS — Tier1 purge 후보:"
      echo "  sudo APT_DRY_RUN=0 ./03_minimize_apply.sh ${tier}"
    fi
  } > "$summary_txt"

  # JSON (minimal, no jq dependency)
  {
    printf '{'
    printf '"timestamp":"%s",' "$ts"
    printf '"tier":"%s",' "$tier"
    printf '"gate_log":"%s",' "$gate_log"
    printf '"counts":{"pass":%s,"warn":%s,"fail":%s,"skip":%s},' "$pass" "$warn" "$fail" "$skip"
    printf '"purge_allowed":%s,' "$purge_allowed"
    printf '"recommended_exit_code":%s,' "$exit_code"
    printf '"fail_categories":['
    local first=1
    for fc in "${!fail_items[@]}"; do
      [[ $first -eq 0 ]] && printf ','
      first=0
      printf '{"category":"%s","action":"%s"}' "$fc" "$(_preflight_action_message "$fc" | sed 's/"/\\"/g')"
    done
    printf '],'
    printf '"warn_categories":['
    first=1
    for wc in "${!warn_items[@]}"; do
      [[ $first -eq 0 ]] && printf ','
      first=0
      printf '{"category":"%s","action":"%s"}' "$wc" "$(_preflight_action_message "$wc" | sed 's/"/\\"/g')"
    done
    printf ']'
    printf '}\n'
  } > "$summary_json"

  export PREFLIGHT_EXIT_CODE=$exit_code
  export PREFLIGHT_PASS=$pass
  export PREFLIGHT_WARN=$warn
  export PREFLIGHT_FAIL=$fail
  export PREFLIGHT_PURGE_ALLOWED=$purge_allowed
}

find_latest_gate_log() {
  local tier="$1"
  local report_dir="$2"
  ls -t "${report_dir}"/pre_purge_gate_${tier}_*.log 2>/dev/null | head -1
}
