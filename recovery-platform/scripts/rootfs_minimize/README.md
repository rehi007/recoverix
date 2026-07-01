# rootfs_minimize — Quick start

```bash
# Copy or symlink into build tree (optional)
# ln -sf /path/to/recoverix/recovery-platform/scripts/rootfs_minimize /recovery/build/rootfs_minimize

cd /home/for/recoverix/recovery-platform/scripts/rootfs_minimize
# or: cd /recovery/build/rootfs_minimize

sudo ./06_snapshot_rootfs.sh pre-minimize
sudo ./01_analyze_rootfs.sh
sudo ./02_generate_purge_plan.sh tier1
sudo ./08_preflight_run.sh tier1             # all-in-one preflight (recommended)
# SQUASHFS_READY WARN 시:
sudo ./09_repair_rootfs_integrity.sh
sudo SKIP_SNAPSHOT=1 ./08_preflight_run.sh tier1
# exit 0 only:
sudo APT_DRY_RUN=0 ./03_minimize_apply.sh tier1   # runs 08 automatically
```

Full guide: [`../../docs/ROOTFS_MINIMIZE.md`](../../docs/ROOTFS_MINIMIZE.md)
