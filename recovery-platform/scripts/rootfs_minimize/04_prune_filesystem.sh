#!/usr/bin/env bash
# Non-apt filesystem pruning inside rootfs only (docs, man, cache, locales).
# Usage: sudo ./04_prune_filesystem.sh

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"

log "Pruning filesystem under ${ROOTFS_RESOLVED}"

prune_dir_contents() {
  local p="$1"
  if [[ -d "$p" ]]; then
    log "prune dir: $p"
    find "$p" -mindepth 1 -delete 2>/dev/null || true
  fi
}

# Documentation / help
prune_dir_contents "${ROOTFS_RESOLVED}/usr/share/doc"
prune_dir_contents "${ROOTFS_RESOLVED}/usr/share/man"
prune_dir_contents "${ROOTFS_RESOLVED}/usr/share/info"
prune_dir_contents "${ROOTFS_RESOLVED}/usr/share/help"
prune_dir_contents "${ROOTFS_RESOLVED}/usr/share/gnome/help"
prune_dir_contents "${ROOTFS_RESOLVED}/usr/share/libreoffice/help" 2>/dev/null || true

# APT / caches
prune_dir_contents "${ROOTFS_RESOLVED}/var/cache/apt/archives"
rm -f "${ROOTFS_RESOLVED}/var/lib/apt/lists/"* 2>/dev/null || true
mkdir -p "${ROOTFS_RESOLVED}/var/lib/apt/lists/partial"

# Journal (keep tiny config)
if [[ -d "${ROOTFS_RESOLVED}/var/log/journal" ]]; then
  log "truncate journal"
  rm -rf "${ROOTFS_RESOLVED}/var/log/journal/"* 2>/dev/null || true
fi
mkdir -p "${ROOTFS_RESOLVED}/etc/systemd/journald.conf.d"
cat > "${ROOTFS_RESOLVED}/etc/systemd/journald.conf.d/recoverix-minimal.conf" <<'EOF'
[Journal]
Storage=volatile
SystemMaxUse=16M
RuntimeMaxUse=8M
EOF

# Locales — keep en + ko only
if command -v locale-gen >/dev/null 2>&1 && [[ -f "${ROOTFS_RESOLVED}/etc/locale.gen" ]]; then
  log "locale minimize (en_US, ko_KR)"
  sed -i 's/^[^#].*/#&/' "${ROOTFS_RESOLVED}/etc/locale.gen"
  sed -i 's/^# \(en_US.UTF-8\)/\1/' "${ROOTFS_RESOLVED}/etc/locale.gen" || true
  sed -i 's/^# \(ko_KR.UTF-8\)/\1/' "${ROOTFS_RESOLVED}/etc/locale.gen" || true
  grep -q 'en_US.UTF-8' "${ROOTFS_RESOLVED}/etc/locale.gen" || echo 'en_US.UTF-8 UTF-8' >> "${ROOTFS_RESOLVED}/etc/locale.gen"
  grep -q 'ko_KR.UTF-8' "${ROOTFS_RESOLVED}/etc/locale.gen" || echo 'ko_KR.UTF-8 UTF-8' >> "${ROOTFS_RESOLVED}/etc/locale.gen"
fi
# Remove bulky locale-langpack trees not needed at runtime
rm -rf "${ROOTFS_RESOLVED}/usr/share/locale-langpack" 2>/dev/null || true

# Optional: shrink noto CJK extra if present (GTK may still need basic fonts)
# Comment out if Korean UI breaks:
# rm -rf "${ROOTFS_RESOLVED}/usr/share/fonts/opentype/noto/NotoSansCJK-*" 2>/dev/null || true

log "filesystem prune complete"
