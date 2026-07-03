#include <windows.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cwchar>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

constexpr wchar_t kEfiGlobalGuid[] = L"{8BE4DF61-93CA-11D2-AA0D-00E098032B8C}";
constexpr uint32_t kLoadOptionActive = 0x00000001;
constexpr DWORD kEfiVariableNonVolatile = 0x00000001;
constexpr DWORD kEfiVariableBootServiceAccess = 0x00000002;
constexpr DWORD kEfiVariableRuntimeAccess = 0x00000004;
constexpr DWORD kVariableAttributes =
    kEfiVariableNonVolatile |
    kEfiVariableBootServiceAccess |
    kEfiVariableRuntimeAccess;

constexpr uint8_t kMediaDevicePath = 0x04;
constexpr uint8_t kMediaFilePathSubType = 0x04;
constexpr uint8_t kEndDevicePath = 0x7f;
constexpr uint8_t kEndEntireSubType = 0xff;

const std::wstring kRecoveryDescription = L"Recoverix Hotkey Boot (복구 핫키 대기용)";
const std::wstring kRecoveryPath = L"\\EFI\\RecoveryBoot\\shimx64.efi";
const std::wstring kDirectRecoveryDescription = L"Start Recoverix (복구 모드 직접 진입)";
const std::wstring kDirectRecoveryPath = L"\\EFI\\RecoverixDirect\\shimx64.efi";
const std::wstring kWindowsPathNeedle = L"\\efi\\microsoft\\boot\\bootmgfw.efi";
const std::wstring kRecoveryPathNeedle = L"\\efi\\recoveryboot\\shimx64.efi";
const std::wstring kDirectRecoveryPathNeedle = L"\\efi\\recoverixdirect\\shimx64.efi";

struct LoadOption {
  uint32_t attributes = 0;
  uint16_t file_path_list_length = 0;
  std::wstring description;
  std::vector<uint8_t> device_path;
  std::vector<uint8_t> optional_data;
};

struct BootEntry {
  uint16_t id = 0;
  LoadOption option;
};

std::wstring to_lower(std::wstring s) {
  std::transform(s.begin(), s.end(), s.begin(), [](wchar_t c) {
    return static_cast<wchar_t>(std::towlower(c));
  });
  return s;
}

std::wstring last_error_message(DWORD err = GetLastError()) {
  wchar_t* buffer = nullptr;
  DWORD len = FormatMessageW(
      FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
      nullptr,
      err,
      MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
      reinterpret_cast<LPWSTR>(&buffer),
      0,
      nullptr);
  std::wstring msg = len && buffer ? std::wstring(buffer, len) : L"(no message)";
  if (buffer) {
    LocalFree(buffer);
  }
  while (!msg.empty() && (msg.back() == L'\r' || msg.back() == L'\n')) {
    msg.pop_back();
  }
  std::wstringstream ss;
  ss << L"0x" << std::hex << std::uppercase << err << L" " << msg;
  return ss.str();
}

void log_line(const std::wstring& msg) {
  std::wcout << msg << std::endl;
}

std::wstring env_or_default(const wchar_t* name, const std::wstring& fallback) {
  wchar_t buffer[32768]{};
  const DWORD capacity = static_cast<DWORD>(sizeof(buffer) / sizeof(buffer[0]));
  DWORD len = GetEnvironmentVariableW(name, buffer, capacity);
  if (len == 0 || len >= capacity) {
    return fallback;
  }
  return std::wstring(buffer, len);
}

bool ensure_directory(const std::wstring& path) {
  if (path.empty()) {
    return false;
  }
  if (CreateDirectoryW(path.c_str(), nullptr)) {
    return true;
  }
  DWORD err = GetLastError();
  return err == ERROR_ALREADY_EXISTS;
}

std::wstring program_data_recoverix_root() {
  return env_or_default(L"ProgramData", L"C:\\ProgramData") + L"\\Recoverix";
}

void append_recoverix_log(const std::wstring& filename, const std::wstring& msg) {
  const std::wstring root = program_data_recoverix_root();
  const std::wstring logs = root + L"\\logs";
  ensure_directory(root);
  ensure_directory(logs);
  const std::wstring path = logs + L"\\" + filename;
  FILE* fp = _wfopen(path.c_str(), L"a, ccs=UTF-8");
  if (!fp) {
    return;
  }
  fwprintf(fp, L"%ls\r\n", msg.c_str());
  fclose(fp);
}

bool write_text_file_utf16_compatible(const std::wstring& path, const std::string& text) {
  FILE* fp = _wfopen(path.c_str(), L"wb");
  if (!fp) {
    return false;
  }
  const size_t written = fwrite(text.data(), 1, text.size(), fp);
  fclose(fp);
  return written == text.size();
}

int run_diskpart_extend_filesystem(bool dry_run) {
  const std::wstring root = program_data_recoverix_root();
  const std::wstring state_dir = root + L"\\state";
  ensure_directory(root);
  ensure_directory(state_dir);

  const std::wstring script = state_dir + L"\\extend-c-filesystem.diskpart";
  const std::string script_text = "select volume C\r\nextend filesystem\r\nexit\r\n";

  if (dry_run) {
    log_line(L"DRY-RUN: would run diskpart extend filesystem for C:");
    append_recoverix_log(L"repair.log", L"DRY-RUN: diskpart extend filesystem for C:");
    return 0;
  }

  if (!write_text_file_utf16_compatible(script, script_text)) {
    const std::wstring msg = L"ERROR: failed to write diskpart script: " + script;
    log_line(msg);
    append_recoverix_log(L"error.log", msg);
    return 20;
  }

  const std::wstring command = L"diskpart.exe /s \"" + script + L"\"";
  log_line(L"Running Windows filesystem extend: " + command);
  append_recoverix_log(L"repair.log", L"running: " + command);
  int rc = _wsystem(command.c_str());
  std::wstringstream ss;
  ss << L"diskpart extend filesystem rc=" << rc;
  log_line(ss.str());
  append_recoverix_log(rc == 0 ? L"repair.log" : L"error.log", ss.str());
  return rc == 0 ? 0 : 21;
}

bool enable_system_environment_privilege() {
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token)) {
    log_line(L"ERROR: OpenProcessToken failed: " + last_error_message());
    return false;
  }

  TOKEN_PRIVILEGES tp{};
  if (!LookupPrivilegeValueW(nullptr, SE_SYSTEM_ENVIRONMENT_NAME, &tp.Privileges[0].Luid)) {
    log_line(L"ERROR: LookupPrivilegeValue failed: " + last_error_message());
    CloseHandle(token);
    return false;
  }

  tp.PrivilegeCount = 1;
  tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
  if (!AdjustTokenPrivileges(token, FALSE, &tp, sizeof(tp), nullptr, nullptr)) {
    log_line(L"ERROR: AdjustTokenPrivileges failed: " + last_error_message());
    CloseHandle(token);
    return false;
  }

  DWORD err = GetLastError();
  CloseHandle(token);
  if (err == ERROR_NOT_ALL_ASSIGNED) {
    log_line(L"ERROR: SeSystemEnvironmentPrivilege is not assigned to this process");
    return false;
  }
  return true;
}

bool read_firmware_var(const std::wstring& name, std::vector<uint8_t>& data, DWORD* attrs = nullptr) {
  data.assign(65536, 0);
  DWORD local_attrs = 0;
  DWORD size = GetFirmwareEnvironmentVariableExW(
      name.c_str(), kEfiGlobalGuid, data.data(), static_cast<DWORD>(data.size()), &local_attrs);
  if (size == 0) {
    return false;
  }
  data.resize(size);
  if (attrs) {
    *attrs = local_attrs;
  }
  return true;
}

bool write_firmware_var(const std::wstring& name, const std::vector<uint8_t>& data) {
  if (!SetFirmwareEnvironmentVariableExW(
          name.c_str(),
          kEfiGlobalGuid,
          const_cast<uint8_t*>(data.data()),
          static_cast<DWORD>(data.size()),
          kVariableAttributes)) {
    log_line(L"ERROR: SetFirmwareEnvironmentVariableExW(" + name + L") failed: " + last_error_message());
    return false;
  }
  return true;
}

std::wstring boot_var_name(uint16_t id) {
  wchar_t buf[16]{};
  std::swprintf(buf, 16, L"Boot%04X", id);
  return buf;
}

bool parse_load_option(const std::vector<uint8_t>& data, LoadOption& out) {
  if (data.size() < 6) {
    return false;
  }
  out.attributes =
      static_cast<uint32_t>(data[0]) |
      (static_cast<uint32_t>(data[1]) << 8) |
      (static_cast<uint32_t>(data[2]) << 16) |
      (static_cast<uint32_t>(data[3]) << 24);
  out.file_path_list_length = static_cast<uint16_t>(data[4] | (data[5] << 8));

  size_t offset = 6;
  out.description.clear();
  while (offset + 1 < data.size()) {
    wchar_t ch = static_cast<wchar_t>(data[offset] | (data[offset + 1] << 8));
    offset += 2;
    if (ch == L'\0') {
      break;
    }
    out.description.push_back(ch);
  }
  if (offset > data.size()) {
    return false;
  }
  if (offset + out.file_path_list_length > data.size()) {
    return false;
  }
  out.device_path.assign(data.begin() + static_cast<ptrdiff_t>(offset),
                         data.begin() + static_cast<ptrdiff_t>(offset + out.file_path_list_length));
  out.optional_data.assign(data.begin() + static_cast<ptrdiff_t>(offset + out.file_path_list_length),
                           data.end());
  return true;
}

void append_u16(std::vector<uint8_t>& out, uint16_t v) {
  out.push_back(static_cast<uint8_t>(v & 0xff));
  out.push_back(static_cast<uint8_t>((v >> 8) & 0xff));
}

void append_u32(std::vector<uint8_t>& out, uint32_t v) {
  out.push_back(static_cast<uint8_t>(v & 0xff));
  out.push_back(static_cast<uint8_t>((v >> 8) & 0xff));
  out.push_back(static_cast<uint8_t>((v >> 16) & 0xff));
  out.push_back(static_cast<uint8_t>((v >> 24) & 0xff));
}

std::vector<uint8_t> serialize_load_option(const LoadOption& opt) {
  std::vector<uint8_t> out;
  append_u32(out, opt.attributes);
  append_u16(out, opt.file_path_list_length);
  for (wchar_t ch : opt.description) {
    append_u16(out, static_cast<uint16_t>(ch));
  }
  append_u16(out, 0);
  out.insert(out.end(), opt.device_path.begin(), opt.device_path.end());
  out.insert(out.end(), opt.optional_data.begin(), opt.optional_data.end());
  return out;
}

std::wstring file_path_from_device_path(const std::vector<uint8_t>& dp) {
  size_t off = 0;
  while (off + 4 <= dp.size()) {
    uint8_t type = dp[off];
    uint8_t subtype = dp[off + 1];
    uint16_t len = static_cast<uint16_t>(dp[off + 2] | (dp[off + 3] << 8));
    if (len < 4 || off + len > dp.size()) {
      break;
    }
    if (type == kMediaDevicePath && subtype == kMediaFilePathSubType) {
      std::wstring path;
      for (size_t p = off + 4; p + 1 < off + len; p += 2) {
        wchar_t ch = static_cast<wchar_t>(dp[p] | (dp[p + 1] << 8));
        if (ch == L'\0') {
          break;
        }
        path.push_back(ch);
      }
      return path;
    }
    if (type == kEndDevicePath) {
      break;
    }
    off += len;
  }
  return L"";
}

std::vector<uint8_t> make_file_path_node(const std::wstring& path) {
  std::vector<uint8_t> node;
  uint16_t len = static_cast<uint16_t>(4 + ((path.size() + 1) * 2));
  node.push_back(kMediaDevicePath);
  node.push_back(kMediaFilePathSubType);
  append_u16(node, len);
  for (wchar_t ch : path) {
    append_u16(node, static_cast<uint16_t>(ch));
  }
  append_u16(node, 0);
  return node;
}

std::vector<uint8_t> make_end_node() {
  return {kEndDevicePath, kEndEntireSubType, 0x04, 0x00};
}

bool replace_file_path_node(
    const std::vector<uint8_t>& source,
    const std::wstring& new_path,
    std::vector<uint8_t>& out) {
  size_t off = 0;
  while (off + 4 <= source.size()) {
    uint8_t type = source[off];
    uint8_t subtype = source[off + 1];
    uint16_t len = static_cast<uint16_t>(source[off + 2] | (source[off + 3] << 8));
    if (len < 4 || off + len > source.size()) {
      return false;
    }
    if (type == kMediaDevicePath && subtype == kMediaFilePathSubType) {
      out.assign(source.begin(), source.begin() + static_cast<ptrdiff_t>(off));
      auto fp = make_file_path_node(new_path);
      out.insert(out.end(), fp.begin(), fp.end());
      auto end = make_end_node();
      out.insert(out.end(), end.begin(), end.end());
      return true;
    }
    if (type == kEndDevicePath) {
      break;
    }
    off += len;
  }
  return false;
}

std::vector<uint16_t> parse_boot_order(const std::vector<uint8_t>& data) {
  std::vector<uint16_t> ids;
  for (size_t i = 0; i + 1 < data.size(); i += 2) {
    ids.push_back(static_cast<uint16_t>(data[i] | (data[i + 1] << 8)));
  }
  return ids;
}

std::vector<uint8_t> serialize_boot_order(const std::vector<uint16_t>& ids) {
  std::vector<uint8_t> out;
  for (uint16_t id : ids) {
    append_u16(out, id);
  }
  return out;
}

bool contains_id(const std::vector<uint16_t>& ids, uint16_t id) {
  return std::find(ids.begin(), ids.end(), id) != ids.end();
}

bool read_boot_entry(uint16_t id, BootEntry& entry) {
  std::vector<uint8_t> data;
  if (!read_firmware_var(boot_var_name(id), data)) {
    return false;
  }
  LoadOption opt;
  if (!parse_load_option(data, opt)) {
    return false;
  }
  entry.id = id;
  entry.option = std::move(opt);
  return true;
}

bool is_windows_entry(const BootEntry& entry) {
  auto path = to_lower(file_path_from_device_path(entry.option.device_path));
  auto desc = to_lower(entry.option.description);
  return path.find(kWindowsPathNeedle) != std::wstring::npos ||
         desc.find(L"windows boot manager") != std::wstring::npos ||
         desc.find(L"windows 부팅 관리자") != std::wstring::npos;
}

bool is_entry_for_path(const BootEntry& entry, const std::wstring& path_needle) {
  auto path = to_lower(file_path_from_device_path(entry.option.device_path));
  return path.find(path_needle) != std::wstring::npos;
}

bool is_recovery_entry(const BootEntry& entry) {
  return is_entry_for_path(entry, kRecoveryPathNeedle);
}

bool is_direct_recovery_entry(const BootEntry& entry) {
  return is_entry_for_path(entry, kDirectRecoveryPathNeedle);
}

uint16_t find_free_boot_id(const std::vector<uint16_t>& reserved = {}) {
  for (uint32_t id = 0; id <= 0xffff; ++id) {
    if (contains_id(reserved, static_cast<uint16_t>(id))) {
      continue;
    }
    std::vector<uint8_t> data;
    if (!read_firmware_var(boot_var_name(static_cast<uint16_t>(id)), data)) {
      return static_cast<uint16_t>(id);
    }
  }
  return 0xffff;
}

LoadOption make_option_from_windows(
    const BootEntry& windows_entry,
    const std::wstring& description,
    const std::wstring& path) {
  LoadOption opt;
  opt.attributes = kLoadOptionActive;
  opt.description = description;
  opt.optional_data.clear();
  if (!replace_file_path_node(windows_entry.option.device_path, path, opt.device_path)) {
    throw std::runtime_error("Windows Boot Manager device path has no file path node");
  }
  opt.file_path_list_length = static_cast<uint16_t>(opt.device_path.size());
  return opt;
}

uint16_t ensure_boot_entry(
    const wchar_t* label,
    BootEntry* existing_entry,
    const BootEntry& windows_entry,
    const std::wstring& description,
    const std::wstring& path,
    const std::vector<uint16_t>& reserved,
    bool dry_run) {
  if (existing_entry) {
    const uint16_t existing_id = existing_entry->id;
    log_line(description + L" already exists: " + boot_var_name(existing_id));
    if (existing_entry->option.description != description) {
      LoadOption renamed_option = existing_entry->option;
      renamed_option.description = description;
      auto data = serialize_load_option(renamed_option);
      log_line(L"Renaming " + boot_var_name(existing_id) + L" to " + description);
      if (!dry_run && !write_firmware_var(boot_var_name(existing_id), data)) {
        throw std::runtime_error("failed to rename boot entry");
      }
    }
    return existing_id;
  }

  uint16_t id = find_free_boot_id(reserved);
  LoadOption option = make_option_from_windows(windows_entry, description, path);
  auto data = serialize_load_option(option);
  log_line(L"Creating " + boot_var_name(id) + L" " + description + L" -> " + path);
  if (!dry_run && !write_firmware_var(boot_var_name(id), data)) {
    std::string narrow = "failed to create ";
    for (const wchar_t* p = label; *p; ++p) {
      narrow.push_back(static_cast<char>(*p & 0x7f));
    }
    throw std::runtime_error(narrow);
  }
  return id;
}

int run_repair(bool dry_run) {
  if (!enable_system_environment_privilege()) {
    return 3;
  }

  std::vector<uint8_t> order_data;
  if (!read_firmware_var(L"BootOrder", order_data)) {
    log_line(L"ERROR: failed to read BootOrder: " + last_error_message());
    return 4;
  }
  auto order = parse_boot_order(order_data);

  std::vector<BootEntry> entries;
  for (uint16_t id : order) {
    BootEntry entry;
    if (read_boot_entry(id, entry)) {
      entries.push_back(std::move(entry));
    }
  }
  for (uint32_t id = 0; id <= 0xffff && id < 256; ++id) {
    if (contains_id(order, static_cast<uint16_t>(id))) {
      continue;
    }
    BootEntry entry;
    if (read_boot_entry(static_cast<uint16_t>(id), entry)) {
      entries.push_back(std::move(entry));
    }
  }

  BootEntry* windows_entry = nullptr;
  BootEntry* recovery_entry = nullptr;
  BootEntry* direct_recovery_entry = nullptr;
  for (auto& entry : entries) {
    if (!windows_entry && is_windows_entry(entry)) {
      windows_entry = &entry;
    }
    if (!recovery_entry && is_recovery_entry(entry)) {
      recovery_entry = &entry;
    }
    if (!direct_recovery_entry && is_direct_recovery_entry(entry)) {
      direct_recovery_entry = &entry;
    }
  }

  if (!windows_entry) {
    log_line(L"ERROR: Windows Boot Manager entry not found");
    return 5;
  }

  uint16_t recovery_id = 0;
  uint16_t direct_recovery_id = 0;
  try {
    recovery_id = ensure_boot_entry(
        L"RecoveryBoot",
        recovery_entry,
        *windows_entry,
        kRecoveryDescription,
        kRecoveryPath,
        {},
        dry_run);
    direct_recovery_id = ensure_boot_entry(
        L"RecoverixDirect",
        direct_recovery_entry,
        *windows_entry,
        kDirectRecoveryDescription,
        kDirectRecoveryPath,
        {recovery_id},
        dry_run);
  } catch (const std::exception& exc) {
    std::cerr << "ERROR: " << exc.what() << std::endl;
    return 7;
  }

  std::vector<uint16_t> new_order;
  new_order.push_back(recovery_id);
  new_order.push_back(windows_entry->id);
  new_order.push_back(direct_recovery_id);
  for (uint16_t id : order) {
    if (id != recovery_id && id != windows_entry->id && id != direct_recovery_id) {
      new_order.push_back(id);
    }
  }

  log_line(L"Writing BootOrder: " + kRecoveryDescription + L" -> Windows Boot Manager -> " +
           kDirectRecoveryDescription + L" -> remaining");
  if (!dry_run && !write_firmware_var(L"BootOrder", serialize_boot_order(new_order))) {
    return 8;
  }

  log_line(dry_run ? L"DRY-RUN complete" : kRecoveryDescription + L" NVRAM repair complete");
  return 0;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  bool dry_run = false;
  bool skip_filesystem_extend = false;
  bool filesystem_extend_only = false;
  for (int i = 1; i < argc; ++i) {
    std::wstring arg = argv[i];
    if (arg == L"--dry-run") {
      dry_run = true;
    } else if (arg == L"--skip-filesystem-extend") {
      skip_filesystem_extend = true;
    } else if (arg == L"--filesystem-extend-only") {
      filesystem_extend_only = true;
    } else if (arg == L"--help" || arg == L"-h") {
      std::wcout
          << L"recoverix-nvram-writer.exe [--dry-run] [--skip-filesystem-extend] "
          << L"[--filesystem-extend-only]" << std::endl;
      return 0;
    }
  }

  if (filesystem_extend_only) {
    return run_diskpart_extend_filesystem(dry_run);
  }

  int fs_rc = 0;
  if (!skip_filesystem_extend) {
    fs_rc = run_diskpart_extend_filesystem(dry_run);
    if (fs_rc != 0) {
      log_line(L"WARNING: Windows filesystem extend did not complete; continuing NVRAM repair");
    }
  }

  int repair_rc = run_repair(dry_run);
  if (repair_rc != 0) {
    return repair_rc;
  }
  return 0;
}
