#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <windowsx.h>
#include <commctrl.h>
#include <oleauto.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <wbemidl.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cwchar>
#include <iomanip>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

namespace {

using Row = std::map<std::wstring, std::wstring>;

constexpr int kButtonPc = 1001;
constexpr int kButtonDisk = 1002;
constexpr int kButtonGuide = 1003;
constexpr int kButtonRefresh = 1004;
constexpr int kButtonExit = 1005;
constexpr int kAppIconResourceId = 101;

enum class Page {
  PcInfo,
  DiskInfo,
  Guide,
};

struct DiskSegment {
  std::wstring label;
  std::wstring role;
  std::wstring size_text;
  std::wstring windows_text;
  std::wstring tooltip;
  uint64_t start = 0;
  uint64_t size = 0;
  COLORREF fill = RGB(238, 242, 247);
  COLORREF border = RGB(184, 195, 209);
  bool gap = false;
  RECT rect{};
};

struct DiskViewModel {
  std::wstring note;
  std::wstring disk_title;
  std::wstring disk_detail;
  std::wstring capacity_text;
  std::wstring hidden_text;
  std::wstring windows_gap_text;
  std::vector<DiskSegment> segments;
  uint64_t total_size = 0;
};

struct InfoRow {
  std::wstring key;
  std::wstring value;
};

struct InfoCard {
  std::wstring title;
  std::wstring body;
  std::vector<InfoRow> rows;
  std::vector<std::wstring> steps;
  bool warning = false;
  bool accent = false;
};

struct PageViewModel {
  std::wstring note;
  std::vector<InfoCard> cards;
};

HINSTANCE g_instance = nullptr;
HWND g_main = nullptr;
HWND g_title = nullptr;
HWND g_subtitle = nullptr;
HWND g_header = nullptr;
HWND g_content = nullptr;
HWND g_page_view = nullptr;
HWND g_disk_view = nullptr;
HWND g_tooltip = nullptr;
HWND g_btn_pc = nullptr;
HWND g_btn_disk = nullptr;
HWND g_btn_guide = nullptr;
HWND g_btn_refresh = nullptr;
HWND g_btn_exit = nullptr;
HFONT g_font = nullptr;
HFONT g_title_font = nullptr;
HFONT g_header_font = nullptr;
HBRUSH g_background = nullptr;
HBRUSH g_sidebar_background = nullptr;
Page g_current_page = Page::PcInfo;
PageViewModel g_page_model;
DiskViewModel g_disk_model;
int g_disk_hover_index = -1;
int g_page_scroll_y = 0;
int g_page_content_height = 0;
int g_disk_scroll_y = 0;
int g_disk_content_height = 0;
bool g_tooltip_added = false;
std::wstring g_tooltip_text;

std::wstring trim(std::wstring value) {
  const auto first = value.find_first_not_of(L" \t\r\n");
  if (first == std::wstring::npos) {
    return L"";
  }
  const auto last = value.find_last_not_of(L" \t\r\n");
  return value.substr(first, last - first + 1);
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
  msg = trim(msg);
  std::wstringstream ss;
  ss << L"0x" << std::hex << std::uppercase << err << L" " << msg;
  return ss.str();
}

std::wstring hresult_message(HRESULT hr) {
  wchar_t* buffer = nullptr;
  DWORD len = FormatMessageW(
      FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
      nullptr,
      static_cast<DWORD>(hr),
      MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
      reinterpret_cast<LPWSTR>(&buffer),
      0,
      nullptr);
  std::wstring msg = len && buffer ? std::wstring(buffer, len) : L"(no message)";
  if (buffer) {
    LocalFree(buffer);
  }
  msg = trim(msg);
  std::wstringstream ss;
  ss << L"0x" << std::hex << std::uppercase << static_cast<DWORD>(hr) << L" " << msg;
  return ss.str();
}

std::wstring env_or_default(const wchar_t* name, const std::wstring& fallback) {
  wchar_t buffer[32768]{};
  DWORD len = GetEnvironmentVariableW(name, buffer, static_cast<DWORD>(std::size(buffer)));
  if (len == 0 || len >= std::size(buffer)) {
    return fallback;
  }
  return std::wstring(buffer, len);
}

bool path_exists(const std::wstring& path) {
  const DWORD attrs = GetFileAttributesW(path.c_str());
  return attrs != INVALID_FILE_ATTRIBUTES;
}

std::wstring yes_no(bool value) {
  return value ? L"확인됨" : L"확인되지 않음";
}

uint64_t parse_u64(const std::wstring& value) {
  std::wstring text = trim(value);
  if (text.empty()) {
    return 0;
  }
  try {
    return std::stoull(text);
  } catch (...) {
    return 0;
  }
}

std::wstring format_bytes(uint64_t bytes) {
  if (bytes == 0) {
    return L"정보 없음";
  }
  const wchar_t* units[] = {L"B", L"KiB", L"MiB", L"GiB", L"TiB"};
  double value = static_cast<double>(bytes);
  int unit = 0;
  while (value >= 1024.0 && unit < 4) {
    value /= 1024.0;
    ++unit;
  }
  std::wstringstream ss;
  ss << std::fixed << std::setprecision(unit == 0 ? 0 : 1) << value << L" " << units[unit];
  return ss.str();
}

std::wstring format_bytes_text(const std::wstring& raw) {
  const uint64_t bytes = parse_u64(raw);
  if (bytes == 0) {
    return raw.empty() ? L"정보 없음" : raw;
  }
  return format_bytes(bytes);
}

std::wstring format_windows_capacity(uint64_t bytes) {
  return format_bytes(bytes);
}

int nearest_common_value(double value, const std::vector<int>& common_values) {
  if (value <= 0.0 || common_values.empty()) {
    return 0;
  }
  int best = common_values.front();
  double best_delta = std::abs(value - static_cast<double>(best));
  for (int candidate : common_values) {
    const double delta = std::abs(value - static_cast<double>(candidate));
    if (delta < best_delta) {
      best = candidate;
      best_delta = delta;
    }
  }
  return best;
}

std::wstring format_product_storage_capacity(uint64_t bytes) {
  if (bytes == 0) {
    return L"정보 없음";
  }
  const double decimal_gb = static_cast<double>(bytes) / 1000000000.0;
  const std::vector<int> common_gb = {
      32, 64, 120, 128, 240, 250, 256, 480, 500, 512,
      960, 1000, 1024, 1920, 2000, 2048, 3840, 4000, 4096,
      7680, 8000, 8192, 12000, 16000, 20000};
  int rounded_gb = nearest_common_value(decimal_gb, common_gb);
  if (rounded_gb == 0) {
    rounded_gb = static_cast<int>(std::round(decimal_gb));
  }
  if (rounded_gb >= 1000) {
    if (rounded_gb % 1000 == 0) {
      return std::to_wstring(rounded_gb / 1000) + L" TB";
    }
    if (rounded_gb % 1024 == 0) {
      return std::to_wstring(rounded_gb / 1024) + L" TB";
    }
  }
  return std::to_wstring(rounded_gb) + L" GB";
}

std::wstring format_product_memory_capacity(uint64_t bytes) {
  if (bytes == 0) {
    return L"정보 없음";
  }
  const double gib = static_cast<double>(bytes) / 1073741824.0;
  const std::vector<int> common_gb = {
      1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 512, 1024};
  const int rounded_gb = nearest_common_value(gib, common_gb);
  if (rounded_gb >= 1024 && rounded_gb % 1024 == 0) {
    return std::to_wstring(rounded_gb / 1024) + L" TB";
  }
  return std::to_wstring(rounded_gb) + L" GB";
}

std::wstring format_memory_display(const std::wstring& raw) {
  const uint64_t bytes = parse_u64(raw);
  if (bytes == 0) {
    return raw.empty() ? L"정보 없음" : raw;
  }
  return format_product_memory_capacity(bytes) + L" (Windows 표시 기준: " + format_windows_capacity(bytes) + L")";
}

std::wstring row_value(const Row& row, const std::wstring& key);

std::wstring lower_copy(std::wstring value) {
  std::transform(value.begin(), value.end(), value.begin(), [](wchar_t c) {
    return static_cast<wchar_t>(std::towlower(c));
  });
  return value;
}

std::wstring storage_kind_label(const Row& row) {
  const std::wstring media = lower_copy(row_value(row, L"MediaType"));
  const std::wstring model = lower_copy(row_value(row, L"Model"));
  const std::wstring iface = lower_copy(row_value(row, L"InterfaceType"));
  if (media.find(L"ssd") != std::wstring::npos || model.find(L"ssd") != std::wstring::npos ||
      model.find(L"nvme") != std::wstring::npos || iface.find(L"nvme") != std::wstring::npos) {
    return L"SSD";
  }
  if (media.find(L"hdd") != std::wstring::npos || model.find(L"hdd") != std::wstring::npos) {
    return L"HDD";
  }
  return L"저장장치";
}

std::wstring format_storage_display(const Row& row) {
  const uint64_t bytes = parse_u64(row_value(row, L"Size"));
  if (bytes == 0) {
    return format_bytes_text(row_value(row, L"Size"));
  }
  return format_product_storage_capacity(bytes) + L" " + storage_kind_label(row) +
         L" (Windows 표시 기준: " + format_windows_capacity(bytes) + L")";
}

std::wstring first_non_empty(const Row& row, const std::vector<std::wstring>& fields) {
  for (const auto& field : fields) {
    const auto it = row.find(field);
    if (it != row.end() && !trim(it->second).empty()) {
      return trim(it->second);
    }
  }
  return L"정보 없음";
}

std::wstring row_value(const Row& row, const std::wstring& key) {
  const auto it = row.find(key);
  if (it == row.end() || trim(it->second).empty()) {
    return L"정보 없음";
  }
  return trim(it->second);
}

std::wstring wmi_date(const std::wstring& value) {
  if (value.size() < 14) {
    return value.empty() ? L"정보 없음" : value;
  }
  return value.substr(0, 4) + L"-" + value.substr(4, 2) + L"-" + value.substr(6, 2);
}

std::wstring variant_to_wstring(const VARIANT& vt) {
  switch (vt.vt) {
    case VT_EMPTY:
    case VT_NULL:
      return L"";
    case VT_BSTR:
      return vt.bstrVal ? std::wstring(vt.bstrVal, SysStringLen(vt.bstrVal)) : L"";
    case VT_BOOL:
      return vt.boolVal == VARIANT_TRUE ? L"true" : L"false";
    case VT_I1:
      return std::to_wstring(vt.cVal);
    case VT_UI1:
      return std::to_wstring(vt.bVal);
    case VT_I2:
      return std::to_wstring(vt.iVal);
    case VT_UI2:
      return std::to_wstring(vt.uiVal);
    case VT_I4:
    case VT_INT:
      return std::to_wstring(vt.lVal);
    case VT_UI4:
    case VT_UINT:
      return std::to_wstring(vt.ulVal);
    case VT_I8:
      return std::to_wstring(static_cast<long long>(vt.llVal));
    case VT_UI8:
      return std::to_wstring(static_cast<unsigned long long>(vt.ullVal));
    case VT_R4:
      return std::to_wstring(vt.fltVal);
    case VT_R8:
      return std::to_wstring(vt.dblVal);
    default:
      return L"";
  }
}

class WmiConnection {
 public:
  WmiConnection() = default;
  WmiConnection(const WmiConnection&) = delete;
  WmiConnection& operator=(const WmiConnection&) = delete;

  ~WmiConnection() {
    if (services_) {
      services_->Release();
    }
    if (locator_) {
      locator_->Release();
    }
    if (com_initialized_) {
      CoUninitialize();
    }
  }

  bool connect(std::wstring* error) {
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (SUCCEEDED(hr)) {
      com_initialized_ = true;
    } else if (hr != RPC_E_CHANGED_MODE) {
      if (error) {
        *error = L"COM 초기화 실패: " + hresult_message(hr);
      }
      return false;
    }

    hr = CoInitializeSecurity(
        nullptr,
        -1,
        nullptr,
        nullptr,
        RPC_C_AUTHN_LEVEL_DEFAULT,
        RPC_C_IMP_LEVEL_IMPERSONATE,
        nullptr,
        EOAC_NONE,
        nullptr);
    if (FAILED(hr) && hr != RPC_E_TOO_LATE) {
      if (error) {
        *error = L"COM 보안 초기화 실패: " + hresult_message(hr);
      }
      return false;
    }

    hr = CoCreateInstance(CLSID_WbemLocator, nullptr, CLSCTX_INPROC_SERVER, IID_IWbemLocator,
                          reinterpret_cast<void**>(&locator_));
    if (FAILED(hr)) {
      if (error) {
        *error = L"WMI Locator 생성 실패: " + hresult_message(hr);
      }
      return false;
    }

    BSTR ns = SysAllocString(L"ROOT\\CIMV2");
    hr = locator_->ConnectServer(ns, nullptr, nullptr, nullptr, 0, nullptr, nullptr, &services_);
    SysFreeString(ns);
    if (FAILED(hr)) {
      if (error) {
        *error = L"WMI 연결 실패: " + hresult_message(hr);
      }
      return false;
    }

    hr = CoSetProxyBlanket(services_, RPC_C_AUTHN_WINNT, RPC_C_AUTHZ_NONE, nullptr,
                           RPC_C_AUTHN_LEVEL_CALL, RPC_C_IMP_LEVEL_IMPERSONATE, nullptr,
                           EOAC_NONE);
    if (FAILED(hr)) {
      if (error) {
        *error = L"WMI 권한 설정 실패: " + hresult_message(hr);
      }
      return false;
    }
    return true;
  }

  std::vector<Row> query(const std::wstring& statement,
                         const std::vector<std::wstring>& fields,
                         std::wstring* error) const {
    std::vector<Row> rows;
    if (!services_) {
      if (error) {
        *error = L"WMI 서비스가 연결되지 않았습니다.";
      }
      return rows;
    }

    IEnumWbemClassObject* enumerator = nullptr;
    BSTR lang = SysAllocString(L"WQL");
    BSTR query_text = SysAllocString(statement.c_str());
    HRESULT hr = services_->ExecQuery(
        lang,
        query_text,
        WBEM_FLAG_FORWARD_ONLY | WBEM_FLAG_RETURN_IMMEDIATELY,
        nullptr,
        &enumerator);
    SysFreeString(lang);
    SysFreeString(query_text);

    if (FAILED(hr)) {
      if (error) {
        *error = L"WMI 조회 실패: " + hresult_message(hr) + L"\r\n쿼리: " + statement;
      }
      return rows;
    }

    while (enumerator) {
      IWbemClassObject* object = nullptr;
      ULONG returned = 0;
      hr = enumerator->Next(WBEM_INFINITE, 1, &object, &returned);
      if (FAILED(hr) || returned == 0) {
        break;
      }

      Row row;
      for (const auto& field : fields) {
        VARIANT vt;
        VariantInit(&vt);
        hr = object->Get(field.c_str(), 0, &vt, nullptr, nullptr);
        if (SUCCEEDED(hr)) {
          row[field] = variant_to_wstring(vt);
        }
        VariantClear(&vt);
      }
      rows.push_back(std::move(row));
      object->Release();
    }

    enumerator->Release();
    return rows;
  }

 private:
  bool com_initialized_ = false;
  IWbemLocator* locator_ = nullptr;
  IWbemServices* services_ = nullptr;
};

std::vector<Row> query_or_note(const WmiConnection& wmi,
                               const std::wstring& statement,
                               const std::vector<std::wstring>& fields,
                               std::wstringstream& out) {
  std::wstring error;
  auto rows = wmi.query(statement, fields, &error);
  if (!error.empty()) {
    out << L"\r\n[정보 조회 제한]\r\n" << error << L"\r\n";
  }
  return rows;
}

void append_kv(std::wstringstream& out, const std::wstring& key, const std::wstring& value) {
  constexpr size_t kKeyWidth = 24;
  std::wstring padded_key = key;
  if (padded_key.size() < kKeyWidth) {
    padded_key += std::wstring(kKeyWidth - padded_key.size(), L' ');
  }
  out << L"  " << padded_key << L": " << (trim(value).empty() ? L"정보 없음" : trim(value)) << L"\r\n";
}

void append_section(std::wstringstream& out, const std::wstring& title) {
  out << L"\r\n" << title << L"\r\n";
  out << L"------------------------------------------------------------\r\n";
}

void add_info_card(const std::wstring& title,
                   std::vector<InfoRow> rows,
                   const std::wstring& body = L"",
                   bool warning = false,
                   bool accent = false) {
  InfoCard card{};
  card.title = title;
  card.rows = std::move(rows);
  card.body = body;
  card.warning = warning;
  card.accent = accent;
  g_page_model.cards.push_back(std::move(card));
}

void add_step_card(const std::wstring& title,
                   const std::wstring& body,
                   std::vector<std::wstring> steps) {
  InfoCard card{};
  card.title = title;
  card.body = body;
  card.steps = std::move(steps);
  card.accent = true;
  g_page_model.cards.push_back(std::move(card));
}

void add_note_card(const std::wstring& body) {
  g_page_model.note = body;
}

std::wstring nonempty_or_dash(const std::wstring& value) {
  const auto trimmed = trim(value);
  return trimmed.empty() ? L"정보 없음" : trimmed;
}

std::wstring build_pc_info() {
  std::wstringstream out;
  g_page_model = PageViewModel{};
  add_note_card(
      L"이 정보는 Windows 장치 정보를 기준으로 표시됩니다. 부품 제조사, 모델명, 용량 표기는 "
      L"제조사별 표기 방식과 Windows 인식 방식에 따라 실제 제품 표기와 일부 다를 수 있습니다.");
  out << L"Recoverix 상태 확인 - PC 정보\r\n";
  out << L"안내: 이 정보는 Windows 장치 정보를 기준으로 표시됩니다.\r\n";
  out << L"부품 제조사, 모델명, 용량 표기는 제조사별 표기 방식과 Windows 인식 방식에 따라\r\n";
  out << L"실제 제품 표기와 일부 다를 수 있습니다.\r\n";

  WmiConnection wmi;
  std::wstring error;
  if (!wmi.connect(&error)) {
    out << L"\r\nWMI 연결에 실패했습니다.\r\n" << error << L"\r\n";
    add_info_card(L"정보 조회 실패", {}, L"WMI 연결에 실패했습니다.\r\n" + error, true);
    return out.str();
  }

  auto os_rows = query_or_note(wmi,
      L"SELECT Caption, Version, BuildNumber, OSArchitecture, InstallDate FROM Win32_OperatingSystem",
      {L"Caption", L"Version", L"BuildNumber", L"OSArchitecture", L"InstallDate"}, out);
  auto cs_rows = query_or_note(wmi,
      L"SELECT Name, Manufacturer, Model, TotalPhysicalMemory FROM Win32_ComputerSystem",
      {L"Name", L"Manufacturer", L"Model", L"TotalPhysicalMemory"}, out);
  auto board_rows = query_or_note(wmi,
      L"SELECT Manufacturer, Product, Version FROM Win32_BaseBoard",
      {L"Manufacturer", L"Product", L"Version"}, out);
  auto bios_rows = query_or_note(wmi,
      L"SELECT Manufacturer, SMBIOSBIOSVersion, Version, ReleaseDate FROM Win32_BIOS",
      {L"Manufacturer", L"SMBIOSBIOSVersion", L"Version", L"ReleaseDate"}, out);
  auto cpu_rows = query_or_note(wmi,
      L"SELECT Name, Manufacturer, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed FROM Win32_Processor",
      {L"Name", L"Manufacturer", L"NumberOfCores", L"NumberOfLogicalProcessors", L"MaxClockSpeed"}, out);
  auto mem_rows = query_or_note(wmi,
      L"SELECT DeviceLocator, Manufacturer, PartNumber, Capacity, Speed FROM Win32_PhysicalMemory",
      {L"DeviceLocator", L"Manufacturer", L"PartNumber", L"Capacity", L"Speed"}, out);
  auto gpu_rows = query_or_note(wmi,
      L"SELECT Name, AdapterRAM, DriverVersion FROM Win32_VideoController",
      {L"Name", L"AdapterRAM", L"DriverVersion"}, out);
  auto disk_rows = query_or_note(wmi,
      L"SELECT Index, Model, Manufacturer, InterfaceType, MediaType, Size, SerialNumber FROM Win32_DiskDrive",
      {L"Index", L"Model", L"Manufacturer", L"InterfaceType", L"MediaType", L"Size", L"SerialNumber"}, out);
  auto nic_rows = query_or_note(wmi,
      L"SELECT Name, Manufacturer, NetConnectionID, MACAddress, PhysicalAdapter FROM Win32_NetworkAdapter WHERE PhysicalAdapter=True",
      {L"Name", L"Manufacturer", L"NetConnectionID", L"MACAddress", L"PhysicalAdapter"}, out);

  if (!cs_rows.empty()) {
    add_info_card(L"PC 기본 정보",
                  {
                      {L"PC 이름", row_value(cs_rows[0], L"Name")},
                      {L"관리 업체", L"FORYOUCOM"},
                      {L"제품 구분", L"Recoverix 보호 PC"},
                  });
  }
  if (!os_rows.empty()) {
    add_info_card(L"Windows",
                  {
                      {L"제품명", row_value(os_rows[0], L"Caption")},
                      {L"버전", row_value(os_rows[0], L"Version") + L" / Build " +
                                   row_value(os_rows[0], L"BuildNumber")},
                      {L"아키텍처", row_value(os_rows[0], L"OSArchitecture")},
                      {L"설치일", wmi_date(row_value(os_rows[0], L"InstallDate"))},
                  });
  }
  std::vector<InfoRow> board_bios_rows;
  if (!board_rows.empty()) {
    board_bios_rows.push_back({L"메인보드 제조사", row_value(board_rows[0], L"Manufacturer")});
    board_bios_rows.push_back({L"메인보드 모델", row_value(board_rows[0], L"Product")});
    board_bios_rows.push_back({L"메인보드 버전", row_value(board_rows[0], L"Version")});
  }
  if (!bios_rows.empty()) {
    board_bios_rows.push_back({L"BIOS 제조사", row_value(bios_rows[0], L"Manufacturer")});
    board_bios_rows.push_back({L"BIOS 버전", first_non_empty(bios_rows[0], {L"SMBIOSBIOSVersion", L"Version"})});
    board_bios_rows.push_back({L"BIOS 날짜", wmi_date(row_value(bios_rows[0], L"ReleaseDate"))});
  }
  if (!board_bios_rows.empty()) {
    add_info_card(L"메인보드 / BIOS", std::move(board_bios_rows));
  }
  if (cpu_rows.empty()) {
    add_info_card(L"CPU", {}, L"CPU 정보가 없습니다.", true);
  }
  for (size_t i = 0; i < cpu_rows.size(); ++i) {
    add_info_card(L"CPU " + std::to_wstring(i + 1),
                  {
                      {L"제조사", row_value(cpu_rows[i], L"Manufacturer")},
                      {L"모델명", row_value(cpu_rows[i], L"Name")},
                      {L"코어", row_value(cpu_rows[i], L"NumberOfCores")},
                      {L"스레드", row_value(cpu_rows[i], L"NumberOfLogicalProcessors")},
                      {L"최대 클럭", row_value(cpu_rows[i], L"MaxClockSpeed") + L" MHz"},
                  });
  }
  if (mem_rows.empty()) {
    add_info_card(L"RAM 슬롯", {}, L"RAM 슬롯 정보가 없습니다.", true);
  }
  for (size_t i = 0; i < mem_rows.size(); ++i) {
    add_info_card(L"RAM 슬롯 " + std::to_wstring(i + 1),
                  {
                      {L"위치", row_value(mem_rows[i], L"DeviceLocator")},
                      {L"제조사", row_value(mem_rows[i], L"Manufacturer")},
                      {L"부품번호", row_value(mem_rows[i], L"PartNumber")},
                      {L"용량", format_memory_display(row_value(mem_rows[i], L"Capacity"))},
                      {L"속도", row_value(mem_rows[i], L"Speed") + L" MHz"},
                  });
  }
  if (gpu_rows.empty()) {
    add_info_card(L"그래픽", {}, L"그래픽 정보가 없습니다.", true);
  }
  for (size_t i = 0; i < gpu_rows.size(); ++i) {
    add_info_card(L"그래픽 장치 " + std::to_wstring(i + 1),
                  {
                      {L"이름", row_value(gpu_rows[i], L"Name")},
                      {L"VRAM", format_bytes_text(row_value(gpu_rows[i], L"AdapterRAM"))},
                      {L"드라이버", row_value(gpu_rows[i], L"DriverVersion")},
                  });
  }
  if (disk_rows.empty()) {
    add_info_card(L"저장장치", {}, L"저장장치 정보가 없습니다.", true);
  }
  for (const auto& row : disk_rows) {
    add_info_card(L"저장장치 Disk " + row_value(row, L"Index"),
                  {
                      {L"제조사", row_value(row, L"Manufacturer")},
                      {L"모델명", row_value(row, L"Model")},
                      {L"인터페이스", row_value(row, L"InterfaceType")},
                      {L"종류", row_value(row, L"MediaType")},
                      {L"전체 용량", format_storage_display(row)},
                      {L"시리얼", row_value(row, L"SerialNumber")},
                  });
  }
  if (nic_rows.empty()) {
    add_info_card(L"네트워크", {}, L"네트워크 장치 정보가 없습니다.", true);
  }
  for (const auto& row : nic_rows) {
    add_info_card(L"네트워크 - " + row_value(row, L"Name"),
                  {
                      {L"제조사", row_value(row, L"Manufacturer")},
                      {L"연결 이름", row_value(row, L"NetConnectionID")},
                      {L"MAC", row_value(row, L"MACAddress")},
                  });
  }

  append_section(out, L"PC 기본 정보");
  if (!cs_rows.empty()) {
    append_kv(out, L"PC 이름", row_value(cs_rows[0], L"Name"));
    append_kv(out, L"관리 업체", L"FORYOUCOM");
    append_kv(out, L"제품 구분", L"Recoverix 보호 PC");
  }
  if (!os_rows.empty()) {
    append_kv(out, L"Windows", row_value(os_rows[0], L"Caption"));
    append_kv(out, L"버전", row_value(os_rows[0], L"Version") + L" / Build " + row_value(os_rows[0], L"BuildNumber"));
    append_kv(out, L"아키텍처", row_value(os_rows[0], L"OSArchitecture"));
    append_kv(out, L"설치일", wmi_date(row_value(os_rows[0], L"InstallDate")));
  }

  append_section(out, L"메인보드 / BIOS");
  if (!board_rows.empty()) {
    append_kv(out, L"메인보드 제조사", row_value(board_rows[0], L"Manufacturer"));
    append_kv(out, L"메인보드 모델", row_value(board_rows[0], L"Product"));
    append_kv(out, L"메인보드 버전", row_value(board_rows[0], L"Version"));
  }
  if (!bios_rows.empty()) {
    append_kv(out, L"BIOS 제조사", row_value(bios_rows[0], L"Manufacturer"));
    append_kv(out, L"BIOS 버전", first_non_empty(bios_rows[0], {L"SMBIOSBIOSVersion", L"Version"}));
    append_kv(out, L"BIOS 날짜", wmi_date(row_value(bios_rows[0], L"ReleaseDate")));
  }

  append_section(out, L"CPU");
  if (cpu_rows.empty()) {
    out << L"  CPU 정보 없음\r\n";
  }
  for (size_t i = 0; i < cpu_rows.size(); ++i) {
    out << L"  CPU " << (i + 1) << L"\r\n";
    append_kv(out, L"    제조사", row_value(cpu_rows[i], L"Manufacturer"));
    append_kv(out, L"    모델명", row_value(cpu_rows[i], L"Name"));
    append_kv(out, L"    코어", row_value(cpu_rows[i], L"NumberOfCores"));
    append_kv(out, L"    스레드", row_value(cpu_rows[i], L"NumberOfLogicalProcessors"));
    append_kv(out, L"    최대 클럭", row_value(cpu_rows[i], L"MaxClockSpeed") + L" MHz");
  }

  append_section(out, L"RAM 슬롯");
  if (mem_rows.empty()) {
    out << L"  RAM 슬롯 정보 없음\r\n";
  }
  for (size_t i = 0; i < mem_rows.size(); ++i) {
    out << L"  슬롯 " << (i + 1) << L"\r\n";
    append_kv(out, L"    위치", row_value(mem_rows[i], L"DeviceLocator"));
    append_kv(out, L"    제조사", row_value(mem_rows[i], L"Manufacturer"));
    append_kv(out, L"    부품번호", row_value(mem_rows[i], L"PartNumber"));
    append_kv(out, L"    용량", format_memory_display(row_value(mem_rows[i], L"Capacity")));
    append_kv(out, L"    속도", row_value(mem_rows[i], L"Speed") + L" MHz");
  }

  append_section(out, L"그래픽");
  if (gpu_rows.empty()) {
    out << L"  그래픽 정보 없음\r\n";
  }
  for (size_t i = 0; i < gpu_rows.size(); ++i) {
    out << L"  그래픽 장치 " << (i + 1) << L"\r\n";
    append_kv(out, L"    이름", row_value(gpu_rows[i], L"Name"));
    append_kv(out, L"    VRAM", format_bytes_text(row_value(gpu_rows[i], L"AdapterRAM")));
    append_kv(out, L"    드라이버", row_value(gpu_rows[i], L"DriverVersion"));
  }

  append_section(out, L"저장장치");
  if (disk_rows.empty()) {
    out << L"  저장장치 정보 없음\r\n";
  }
  for (const auto& row : disk_rows) {
    out << L"  디스크 " << row_value(row, L"Index") << L"\r\n";
    append_kv(out, L"    제조사", row_value(row, L"Manufacturer"));
    append_kv(out, L"    모델명", row_value(row, L"Model"));
    append_kv(out, L"    인터페이스", row_value(row, L"InterfaceType"));
    append_kv(out, L"    종류", row_value(row, L"MediaType"));
    append_kv(out, L"    전체 용량", format_storage_display(row));
    append_kv(out, L"    시리얼", row_value(row, L"SerialNumber"));
  }

  append_section(out, L"네트워크");
  if (nic_rows.empty()) {
    out << L"  네트워크 장치 정보 없음\r\n";
  }
  for (const auto& row : nic_rows) {
    out << L"  " << row_value(row, L"Name") << L"\r\n";
    append_kv(out, L"    제조사", row_value(row, L"Manufacturer"));
    append_kv(out, L"    연결 이름", row_value(row, L"NetConnectionID"));
    append_kv(out, L"    MAC", row_value(row, L"MACAddress"));
  }

  const std::wstring program_files = env_or_default(L"ProgramFiles", L"C:\\Program Files");
  const std::wstring program_data = env_or_default(L"ProgramData", L"C:\\ProgramData");
  add_info_card(L"Recoverix 설치 상태",
                {
                    {L"StatusApp", yes_no(path_exists(program_files + L"\\Recoverix\\StatusApp\\RecoverixStatus.exe"))},
                    {L"Agent 폴더", yes_no(path_exists(program_files + L"\\Recoverix\\Agent"))},
                    {L"NVRAM Writer", yes_no(path_exists(program_files + L"\\Recoverix\\Agent\\recoverix-nvram-writer.exe"))},
                    {L"ProgramData", yes_no(path_exists(program_data + L"\\Recoverix"))},
                });
  append_section(out, L"Recoverix 설치 상태");
  append_kv(out, L"StatusApp", yes_no(path_exists(program_files + L"\\Recoverix\\StatusApp\\RecoverixStatus.exe")));
  append_kv(out, L"Agent 폴더", yes_no(path_exists(program_files + L"\\Recoverix\\Agent")));
  append_kv(out, L"NVRAM Writer", yes_no(path_exists(program_files + L"\\Recoverix\\Agent\\recoverix-nvram-writer.exe")));
  append_kv(out, L"ProgramData", yes_no(path_exists(program_data + L"\\Recoverix")));

  return out.str();
}

std::wstring quote_wql_instance(const std::wstring& class_name, const std::wstring& key, const std::wstring& value) {
  std::wstring escaped;
  for (wchar_t ch : value) {
    if (ch == L'\\') {
      escaped += L"\\\\";
    } else if (ch == L'\'') {
      escaped += L"\\'";
    } else {
      escaped += ch;
    }
  }
  return class_name + L"." + key + L"='" + escaped + L"'";
}

std::wstring get_system_drive() {
  std::wstring drive = env_or_default(L"SystemDrive", L"C:");
  if (drive.size() >= 2 && drive[1] == L':') {
    return drive.substr(0, 2);
  }
  return L"C:";
}

std::wstring classify_partition(const Row& partition, const std::wstring& logical_drive, const std::wstring& system_drive) {
  const std::wstring type = row_value(partition, L"Type");
  const bool is_system = !logical_drive.empty() && _wcsicmp(logical_drive.c_str(), system_drive.c_str()) == 0;
  if (is_system) {
    return L"Windows 파티션";
  }
  const std::wstring lower_type = [&]() {
    std::wstring tmp = type;
    std::transform(tmp.begin(), tmp.end(), tmp.begin(), [](wchar_t c) {
      return static_cast<wchar_t>(std::towlower(c));
    });
    return tmp;
  }();
  if (lower_type.find(L"system") != std::wstring::npos || lower_type.find(L"efi") != std::wstring::npos) {
    return L"EFI 시스템 파티션";
  }
  if (lower_type.find(L"reserved") != std::wstring::npos) {
    return L"Microsoft 예약 파티션";
  }
  if (logical_drive.empty()) {
    return L"보호/복구 영역 후보";
  }
  return L"일반 볼륨";
}

COLORREF role_fill_color(const std::wstring& role, bool gap) {
  if (gap) {
    return RGB(241, 245, 249);
  }
  if (role.find(L"Windows") != std::wstring::npos) {
    return RGB(31, 111, 235);
  }
  if (role.find(L"EFI") != std::wstring::npos || role.find(L"예약") != std::wstring::npos) {
    return RGB(15, 118, 110);
  }
  if (role.find(L"백업") != std::wstring::npos) {
    return RGB(180, 35, 24);
  }
  if (role.find(L"Recoverix") != std::wstring::npos || role.find(L"복구") != std::wstring::npos) {
    return RGB(99, 102, 241);
  }
  return RGB(203, 213, 225);
}

COLORREF role_border_color(const std::wstring& role, bool gap) {
  if (gap) {
    return RGB(148, 163, 184);
  }
  if (role.find(L"Windows") != std::wstring::npos) {
    return RGB(21, 88, 192);
  }
  if (role.find(L"EFI") != std::wstring::npos || role.find(L"예약") != std::wstring::npos) {
    return RGB(15, 95, 89);
  }
  if (role.find(L"백업") != std::wstring::npos) {
    return RGB(145, 32, 24);
  }
  if (role.find(L"Recoverix") != std::wstring::npos || role.find(L"복구") != std::wstring::npos) {
    return RGB(79, 70, 229);
  }
  return RGB(148, 163, 184);
}

std::wstring compact_segment_label(const std::wstring& role, const std::wstring& logical_drive) {
  if (role.find(L"Windows") != std::wstring::npos) {
    return L"Windows " + logical_drive;
  }
  if (role.find(L"EFI") != std::wstring::npos) {
    return L"EFI";
  }
  if (role.find(L"Microsoft 예약") != std::wstring::npos) {
    return L"MSR";
  }
  if (role.find(L"백업") != std::wstring::npos) {
    return L"Recovery Image";
  }
  if (role.find(L"Recoverix 복구환경") != std::wstring::npos) {
    return L"Recovery Linux";
  }
  if (role.find(L"여유") != std::wstring::npos) {
    return L"Free";
  }
  return role;
}

std::wstring build_disk_info() {
  std::wstringstream out;
  g_disk_model = DiskViewModel{};
  out << L"Recoverix 상태 확인 - 디스크 / 파티션 정보\r\n";
  out << L"현재 Windows가 부팅된 디스크를 기준으로 표시합니다.\r\n";
  out << L"Windows 탐색기에서 보이지 않는 영역은 EFI, 예약 파티션 또는 Recoverix 보호 영역일 수 있습니다.\r\n";
  out << L"저장장치 용량은 제조사 표기 기준과 Windows 표시 기준이 다를 수 있습니다.\r\n";
  g_disk_model.note =
      L"Windows가 부팅된 디스크 기준입니다. 제조사 GB 표기와 Windows 표시 기준, Recoverix 보호 영역 때문에 탐색기 용량이 작게 보일 수 있습니다.";

  WmiConnection wmi;
  std::wstring error;
  if (!wmi.connect(&error)) {
    out << L"\r\nWMI 연결에 실패했습니다.\r\n" << error << L"\r\n";
    g_disk_model.note = L"WMI 연결에 실패했습니다.";
    g_disk_model.disk_detail = error;
    return out.str();
  }

  const std::wstring system_drive = get_system_drive();
  append_section(out, L"부팅 정보");
  append_kv(out, L"현재 Windows 시스템 드라이브", system_drive);

  const std::wstring assoc_query =
      L"ASSOCIATORS OF {" + quote_wql_instance(L"Win32_LogicalDisk", L"DeviceID", system_drive) +
      L"} WHERE AssocClass=Win32_LogicalDiskToPartition";
  auto boot_partitions = query_or_note(wmi, assoc_query,
      {L"DeviceID", L"DiskIndex", L"Index", L"Name", L"Size", L"StartingOffset", L"Type"}, out);

  if (boot_partitions.empty()) {
    out << L"\r\n현재 Windows 파티션과 물리 디스크를 연결하지 못했습니다.\r\n";
    out << L"이 경우 Windows 권한 또는 WMI 정보 제공 상태를 확인해야 합니다.\r\n";
    return out.str();
  }

  const Row& boot_partition = boot_partitions.front();
  const std::wstring disk_index = row_value(boot_partition, L"DiskIndex");
  const uint64_t boot_start = parse_u64(row_value(boot_partition, L"StartingOffset"));
  append_kv(out, L"Windows 파티션", row_value(boot_partition, L"DeviceID"));
  append_kv(out, L"Windows 파티션 크기", format_bytes_text(row_value(boot_partition, L"Size")));
  append_kv(out, L"부팅 물리 디스크", L"Disk " + disk_index);

  auto disk_rows = query_or_note(wmi,
      L"SELECT Index, DeviceID, Model, Manufacturer, InterfaceType, MediaType, Size, SerialNumber FROM Win32_DiskDrive WHERE Index=" + disk_index,
      {L"Index", L"DeviceID", L"Model", L"Manufacturer", L"InterfaceType", L"MediaType", L"Size", L"SerialNumber"}, out);

  uint64_t disk_total = 0;
  if (!disk_rows.empty()) {
    append_section(out, L"현재 Windows 부팅 디스크");
    append_kv(out, L"디스크 번호", row_value(disk_rows[0], L"Index"));
    append_kv(out, L"장치", row_value(disk_rows[0], L"DeviceID"));
    append_kv(out, L"제조사", row_value(disk_rows[0], L"Manufacturer"));
    append_kv(out, L"모델명", row_value(disk_rows[0], L"Model"));
    append_kv(out, L"인터페이스", row_value(disk_rows[0], L"InterfaceType"));
    append_kv(out, L"종류", row_value(disk_rows[0], L"MediaType"));
    append_kv(out, L"전체 용량", format_storage_display(disk_rows[0]));
    append_kv(out, L"시리얼", row_value(disk_rows[0], L"SerialNumber"));
    disk_total = parse_u64(row_value(disk_rows[0], L"Size"));
    g_disk_model.total_size = disk_total;
    g_disk_model.disk_title = L"Disk " + row_value(disk_rows[0], L"Index") + L" - " + row_value(disk_rows[0], L"Model");
    g_disk_model.disk_detail =
        row_value(disk_rows[0], L"InterfaceType") + L" / " + row_value(disk_rows[0], L"MediaType");
    g_disk_model.capacity_text = format_storage_display(disk_rows[0]);
  }

  auto partitions = query_or_note(wmi,
      L"SELECT DeviceID, DiskIndex, Index, Name, Size, StartingOffset, Type, BootPartition, PrimaryPartition FROM Win32_DiskPartition WHERE DiskIndex=" + disk_index,
      {L"DeviceID", L"DiskIndex", L"Index", L"Name", L"Size", L"StartingOffset", L"Type", L"BootPartition", L"PrimaryPartition"}, out);

  std::sort(partitions.begin(), partitions.end(), [](const Row& a, const Row& b) {
    return parse_u64(row_value(a, L"StartingOffset")) < parse_u64(row_value(b, L"StartingOffset"));
  });

  uint64_t partition_sum = 0;
  uint64_t mounted_sum = 0;
  uint64_t cursor = 0;
  uint64_t windows_trailing_gap = 0;
  std::wstring previous_partition_id;
  const std::wstring boot_partition_id = row_value(boot_partition, L"DeviceID");
  int protected_partition_count = 0;
  append_section(out, L"파티션 목록");
  if (partitions.empty()) {
    out << L"  파티션 정보 없음\r\n";
  }

  for (const auto& partition : partitions) {
    const uint64_t part_start = parse_u64(row_value(partition, L"StartingOffset"));
    const uint64_t part_size = parse_u64(row_value(partition, L"Size"));
    if (part_start > cursor) {
      const uint64_t gap_size = part_start - cursor;
      const bool follows_windows = previous_partition_id == boot_partition_id;
      if (follows_windows) {
        windows_trailing_gap = gap_size;
      }
      DiskSegment gap{};
      gap.label = L"Free";
      gap.role = follows_windows ? L"Windows 파티션 뒤 여유 공간" : L"미할당/예약 여유 공간";
      gap.size_text = format_bytes(gap_size);
      gap.windows_text = format_windows_capacity(gap_size);
      gap.start = cursor;
      gap.size = gap_size;
      gap.gap = true;
      gap.fill = role_fill_color(gap.role, true);
      gap.border = role_border_color(gap.role, true);
      gap.tooltip =
          follows_windows
              ? L"Windows 파티션 바로 뒤의 작은 보호 여유 공간입니다.\r\n"
                L"디스크 복제 또는 저장장치 교체 후 생길 수 있는 미세한 파티션 경계 차이를 보정하는 용도입니다."
              : L"파티션 정렬 또는 장치 예약으로 생긴 여유 공간입니다.";
      g_disk_model.segments.push_back(gap);
      out << L"  미할당/예약 여유 공간\r\n";
      append_kv(out, L"    크기", format_bytes(gap_size));
      append_kv(out, L"    위치", follows_windows ? L"Windows 파티션 바로 뒤" : L"파티션 사이");
      append_kv(out, L"    설명",
                follows_windows ? L"복구 안정성 여유 공간 후보" : L"파티션 정렬 또는 예약 공간");
      out << L"\r\n";
    }

    std::wstring logical_drive;
    std::wstring logical_size;
    std::wstring logical_free;
    std::wstring file_system;
    std::wstring volume_name;

    const std::wstring logical_query =
        L"ASSOCIATORS OF {" +
        quote_wql_instance(L"Win32_DiskPartition", L"DeviceID", row_value(partition, L"DeviceID")) +
        L"} WHERE AssocClass=Win32_LogicalDiskToPartition";
    auto logical_rows = query_or_note(wmi, logical_query,
        {L"DeviceID", L"FileSystem", L"FreeSpace", L"Size", L"VolumeName"}, out);
    if (!logical_rows.empty()) {
      logical_drive = row_value(logical_rows[0], L"DeviceID");
      logical_size = row_value(logical_rows[0], L"Size");
      logical_free = row_value(logical_rows[0], L"FreeSpace");
      file_system = row_value(logical_rows[0], L"FileSystem");
      volume_name = row_value(logical_rows[0], L"VolumeName");
      mounted_sum += parse_u64(logical_size);
    }

    partition_sum += part_size;
    std::wstring role = classify_partition(partition, logical_drive, system_drive);
    if (logical_drive.empty() && part_start > boot_start &&
        role.find(L"보호/복구") != std::wstring::npos) {
      ++protected_partition_count;
      if (protected_partition_count == 1) {
        role = L"Recoverix 복구환경 영역";
      } else if (protected_partition_count == 2) {
        role = L"Recoverix 백업 이미지 저장 영역";
      } else {
        role = L"Recoverix 보호 영역";
      }
    }

    DiskSegment segment{};
    segment.label = compact_segment_label(role, logical_drive);
    segment.role = role;
    segment.size_text = format_bytes(part_size);
    segment.windows_text = format_windows_capacity(part_size);
    segment.start = part_start;
    segment.size = part_size;
    segment.fill = role_fill_color(role, false);
    segment.border = role_border_color(role, false);
    segment.tooltip = role + L"\r\n크기: " + segment.size_text;
    if (!logical_drive.empty()) {
      segment.tooltip += L"\r\n드라이브 문자: " + logical_drive;
    }
    if (!volume_name.empty() && volume_name != L"정보 없음") {
      segment.tooltip += L"\r\n볼륨 이름: " + volume_name;
    }
    if (role.find(L"Windows") != std::wstring::npos) {
      segment.tooltip += L"\r\nWindows가 설치되어 부팅되는 기본 파티션입니다.";
    } else if (role.find(L"EFI") != std::wstring::npos) {
      segment.tooltip += L"\r\nWindows와 Recoverix 부팅 항목을 시작하는 UEFI 시스템 영역입니다.";
    } else if (role.find(L"복구환경") != std::wstring::npos) {
      segment.tooltip += L"\r\nRecoverix 복구 런타임이 저장되는 보호 영역입니다.";
    } else if (role.find(L"백업 이미지") != std::wstring::npos) {
      segment.tooltip += L"\r\nWindows 복구 백업 이미지가 저장되는 보호 영역입니다.";
    } else if (logical_drive.empty()) {
      segment.tooltip += L"\r\nWindows 탐색기에서 일반 드라이브로 보이지 않는 보호 영역입니다.";
    }
    g_disk_model.segments.push_back(segment);

    out << L"  " << row_value(partition, L"DeviceID") << L"\r\n";
    append_kv(out, L"    분류", role);
    append_kv(out, L"    파티션 크기", format_bytes(part_size));
    append_kv(out, L"    시작 위치", format_bytes_text(row_value(partition, L"StartingOffset")));
    append_kv(out, L"    파티션 종류", row_value(partition, L"Type"));
    append_kv(out, L"    드라이브 문자", logical_drive.empty() ? L"없음" : logical_drive);
    append_kv(out, L"    파일시스템", file_system);
    append_kv(out, L"    볼륨 이름", volume_name);
    if (!logical_drive.empty()) {
      append_kv(out, L"    Windows 표시 용량", format_bytes_text(logical_size));
      append_kv(out, L"    여유 공간", format_bytes_text(logical_free));
    }
    out << L"\r\n";

    const uint64_t part_end = part_start + part_size;
    if (part_end > cursor) {
      cursor = part_end;
    }
    previous_partition_id = row_value(partition, L"DeviceID");
  }

  if (disk_total > cursor) {
    const uint64_t gap_size = disk_total - cursor;
    const bool follows_windows = previous_partition_id == boot_partition_id;
    if (follows_windows) {
      windows_trailing_gap = gap_size;
    }
    DiskSegment gap{};
    gap.label = L"Free";
    gap.role = follows_windows ? L"Windows 파티션 뒤 여유 공간" : L"디스크 끝 여유 공간";
    gap.size_text = format_bytes(gap_size);
    gap.windows_text = format_windows_capacity(gap_size);
    gap.start = cursor;
    gap.size = gap_size;
    gap.gap = true;
    gap.fill = role_fill_color(gap.role, true);
    gap.border = role_border_color(gap.role, true);
    gap.tooltip =
        follows_windows
            ? L"Windows 파티션 바로 뒤의 작은 보호 여유 공간입니다.\r\n"
              L"디스크 복제 또는 저장장치 교체 후 생길 수 있는 미세한 파티션 경계 차이를 보정하는 용도입니다."
            : L"디스크 끝에 남아 있는 미할당 또는 정렬 여유 공간입니다.";
    g_disk_model.segments.push_back(gap);
    out << L"  미할당/예약 여유 공간\r\n";
    append_kv(out, L"    크기", format_bytes(gap_size));
    append_kv(out, L"    위치", follows_windows ? L"Windows 파티션 바로 뒤" : L"디스크 끝");
    append_kv(out, L"    설명",
              follows_windows ? L"복구 안정성 여유 공간 후보" : L"파티션 정렬 또는 예약 공간");
    out << L"\r\n";
  }

  append_section(out, L"용량 설명");
  if (disk_total > 0) {
    append_kv(out, L"디스크 전체 용량", format_bytes(disk_total));
    append_kv(out, L"파티션 합계", format_bytes(partition_sum));
    append_kv(out, L"Windows에 문자로 표시되는 볼륨 합계", format_bytes(mounted_sum));
    if (disk_total > mounted_sum) {
      append_kv(out, L"Windows 탐색기에서 바로 보이지 않는 영역", format_bytes(disk_total - mounted_sum));
    }
    if (windows_trailing_gap > 0) {
      append_kv(out, L"Windows 파티션 뒤 여유 공간", format_bytes(windows_trailing_gap));
      g_disk_model.windows_gap_text = format_bytes(windows_trailing_gap);
    }
  }
  out << L"\r\n";
  out << L"전체 저장장치 용량 중 일부는 Recoverix 복구 기능, EFI 시스템 영역, Windows 예약 영역,\r\n";
  out << L"또는 백업 이미지 보관 영역으로 사용될 수 있습니다. 이 영역은 Windows 탐색기에서\r\n";
  out << L"일반 저장공간처럼 보이지 않을 수 있지만 복구 기능을 위해 정상적으로 예약된 공간입니다.\r\n";
  out << L"\r\n";
  out << L"저장장치 제조사는 보통 1GB를 1,000,000,000바이트 기준으로 표기합니다.\r\n";
  out << L"Windows는 1GiB = 1,073,741,824바이트에 가까운 기준으로 계산한 값을 표시하므로\r\n";
  out << L"예를 들어 512GB 저장장치는 약 476GB, 1TB 저장장치는 약 931GB,\r\n";
  out << L"2TB 저장장치는 약 1.81TB 정도로 Windows에서 표시될 수 있습니다.\r\n";
  out << L"이는 새제품 또는 공장 출하 상태의 저장장치에서도 동일하게 나타나는 정상적인 표기 차이입니다.\r\n";
  out << L"저장장치 제조사 또는 수입사에 문의해도 같은 기준 차이로 안내되는 일반적인 현상이며,\r\n";
  out << L"저장장치 불량이나 Recoverix 설치 오류가 아닙니다.\r\n";
  if (windows_trailing_gap > 0) {
    out << L"\r\n";
    out << L"Windows 파티션 뒤의 작은 여유 공간은 디스크 복제 또는 저장장치 교체 후 발생할 수 있는\r\n";
    out << L"미세한 파티션 경계 차이를 보정하기 위한 복구 안정성 여유 공간입니다. 일반 파일 저장공간이\r\n";
    out << L"아니며, 대용량 부족을 해결하기 위한 공간은 아닙니다.\r\n";
  }
  if (disk_total > mounted_sum) {
    g_disk_model.hidden_text = format_bytes(disk_total - mounted_sum);
  }

  return out.str();
}

std::wstring build_guide() {
  std::wstringstream out;
  g_page_model = PageViewModel{};
  add_note_card(
      L"Recoverix 설치 후 저장장치 일부 영역은 복구환경과 백업 이미지를 위해 보호 영역으로 사용됩니다. "
      L"이 프로그램은 고객이 현재 PC 상태와 복구솔루션 구성 목적을 확인하기 위한 읽기 전용 안내 프로그램입니다.");
  add_info_card(
      L"Recoverix란?",
      {},
      L"Windows 장애 발생 시 복구 환경으로 부팅하여 백업된 Windows 파티션을 복원할 수 있도록 구성된 복구솔루션입니다.");
  add_info_card(
      L"저장장치 용량이 작게 보이는 이유",
      {},
      L"Windows 탐색기에서 보이는 저장공간은 실제 저장장치 전체 용량보다 작게 보일 수 있습니다. "
      L"저장장치 제조사는 보통 1GB를 1,000,000,000바이트 기준으로 표기하지만, Windows는 1GiB = 1,073,741,824바이트에 가까운 기준으로 계산한 값을 표시합니다. "
      L"예를 들어 512GB 저장장치는 약 476GB, 1TB 저장장치는 약 931GB, 2TB 저장장치는 약 1.81TB 정도로 Windows에서 표시될 수 있습니다. "
      L"이는 새제품 또는 공장 출하 상태의 저장장치에서도 동일하게 나타나는 정상적인 표기 차이입니다. "
      L"저장장치 제조사 또는 수입사에 문의해도 같은 기준 차이로 안내되는 일반적인 현상입니다. "
      L"또한 일부 공간은 복구 런타임과 백업 이미지 저장을 위해 보호 영역으로 예약됩니다. "
      L"이는 저장장치 불량이나 Recoverix 설치 오류가 아닙니다.");
  add_info_card(
      L"디스크 복사 / 파티션 변경 주의",
      {
          {L"디스크 복사", L"디스크를 복사하거나 저장장치를 교체하면 백업 이미지와 현재 Windows 파티션 구조가 달라질 수 있습니다."},
          {L"파티션 변경", L"파티션 크기, 순서, 위치를 임의로 변경하면 복원 조건 검사가 실패할 수 있습니다."},
          {L"Windows 축소", L"Windows 파티션이 백업 당시보다 작아진 경우 시스템 복원이 차단될 수 있습니다."},
          {L"작업 전 확인", L"디스크 교체, 하드카피, 파티션 조정 전에는 중요한 데이터를 먼저 백업하고 작업 후 Recoverix 상태를 확인하십시오."},
      },
      L"",
      true);
  add_step_card(
      L"복구하는 방법",
      L"Recoverix 진입 키: q 또는 Q",
      {
          L"PC 전원을 켭니다.",
          L"제조사 로고가 보이면 q 또는 Q 키를 반복해서 누릅니다.",
          L"Recoverix 복구 화면에서 시스템 복원을 선택합니다.",
          L"화면의 경고 내용을 확인한 뒤 복원을 진행합니다.",
          L"복원이 완료되면 Windows로 재부팅합니다.",
      });
  add_info_card(
      L"주의",
      {
          {L"복원 범위", L"시스템 복원은 Windows 파티션을 백업 당시 상태로 되돌립니다."},
          {L"삭제 가능 항목", L"백업 이후 생성되거나 변경된 파일, 프로그램, 설정은 삭제될 수 있습니다."},
          {L"사전 백업", L"복원 전 중요한 개인 파일과 업무 자료는 반드시 외부 저장장치 또는 다른 안전한 위치에 백업하십시오."},
          {L"데이터 보호", L"Recoverix는 복원 전 데이터 백업 여부를 확인하거나 보장하지 않습니다."},
      },
      L"",
      true);
  add_info_card(
      L"백업 생성",
      {},
      L"Recoverix 백업은 Windows 파티션을 기준으로 생성됩니다. 백업 이미지가 이미 존재하는 경우 일반 사용자 모드에서는 중복 백업 생성을 막아 기존 복구 이미지를 보호합니다.");
  add_info_card(
      L"시스템 복원",
      {},
      L"시스템 복원은 Windows 파티션의 내용을 백업 당시 상태로 되돌립니다. 복원 대상 Windows 파티션의 크기가 백업 요구 크기보다 작은 경우 복원은 차단됩니다.");
  add_info_card(
      L"주의사항",
      {
          {L"전원", L"복원 작업 중 PC 전원을 끄지 마십시오."},
          {L"디스크 교체", L"저장장치 교체 또는 디스크 복제 후에는 부팅 메뉴가 달라질 수 있습니다."},
          {L"Windows 재설치", L"C: 드라이브만 포맷하거나 재설치하면 Windows Agent가 제거될 수 있습니다."},
          {L"보호 영역", L"Recoverix 보호 영역은 일반 저장공간으로 사용하지 않는 것이 정상입니다."},
      },
      L"",
      true);
  add_info_card(
      L"문의 전 확인할 내용",
      {
          {L"PC 정보", L"이 프로그램의 PC 정보 화면"},
          {L"디스크 정보", L"이 프로그램의 디스크/파티션 정보 화면"},
          {L"부팅 메뉴", L"Recoverix 부팅 메뉴 표시 여부"},
          {L"Windows", L"Windows 정상 부팅 여부"},
      });
  out << L"Recoverix 사용설명서\r\n";
  out << L"------------------------------------------------------------\r\n\r\n";

  out << L"1. Recoverix란?\r\n";
  out << L"Recoverix는 Windows 장애 발생 시 복구 환경으로 부팅하여 백업된 Windows 파티션을\r\n";
  out << L"복원할 수 있도록 구성된 복구솔루션입니다.\r\n\r\n";

  out << L"2. 저장장치 용량이 작게 보이는 이유\r\n";
  out << L"Recoverix 설치 후 Windows 탐색기에서 보이는 저장공간은 실제 저장장치 전체 용량보다\r\n";
  out << L"작게 보일 수 있습니다. 일부 공간은 복구 런타임과 백업 이미지 저장을 위해 보호 영역으로\r\n";
  out << L"예약되기 때문입니다.\r\n";
  out << L"저장장치 제조사는 보통 1GB를 1,000,000,000바이트 기준으로 표기하지만,\r\n";
  out << L"Windows는 1GiB = 1,073,741,824바이트에 가까운 기준으로 계산한 값을 표시합니다.\r\n";
  out << L"예를 들어 512GB 저장장치는 약 476GB, 1TB 저장장치는 약 931GB,\r\n";
  out << L"2TB 저장장치는 약 1.81TB 정도로 Windows에서 표시될 수 있습니다.\r\n";
  out << L"이는 새제품 또는 공장 출하 상태의 저장장치에서도 동일하게 나타나는 정상적인 표기 차이입니다.\r\n";
  out << L"저장장치 제조사 또는 수입사에 문의해도 같은 기준 차이로 안내되는 일반적인 현상이며,\r\n";
  out << L"저장장치 불량이나 Recoverix 설치 오류가 아닙니다.\r\n\r\n";

  out << L"3. 디스크 복사 / 파티션 변경 주의\r\n";
  out << L"  디스크 복사    : 디스크를 복사하거나 저장장치를 교체하면 백업 이미지와 현재 Windows 파티션 구조가 달라질 수 있습니다.\r\n";
  out << L"  파티션 변경    : 파티션 크기, 순서, 위치를 임의로 변경하면 복원 조건 검사가 실패할 수 있습니다.\r\n";
  out << L"  Windows 축소   : Windows 파티션이 백업 당시보다 작아진 경우 시스템 복원이 차단될 수 있습니다.\r\n";
  out << L"  작업 전 확인   : 디스크 교체, 하드카피, 파티션 조정 전에는 중요한 데이터를 먼저 백업하고 작업 후 Recoverix 상태를 확인하십시오.\r\n\r\n";

  out << L"4. 복구하는 방법\r\n";
  out << L"Recoverix 진입 키: q 또는 Q\r\n\r\n";
  out << L"  1. PC 전원을 켭니다.\r\n";
  out << L"  2. 제조사 로고가 보이면 q 또는 Q 키를 반복해서 누릅니다.\r\n";
  out << L"  3. Recoverix 복구 화면에서 시스템 복원을 선택합니다.\r\n";
  out << L"  4. 화면의 경고 내용을 확인한 뒤 복원을 진행합니다.\r\n";
  out << L"  5. 복원이 완료되면 Windows로 재부팅합니다.\r\n\r\n";

  out << L"5. 주의\r\n";
  out << L"  복원 범위      : 시스템 복원은 Windows 파티션을 백업 당시 상태로 되돌립니다.\r\n";
  out << L"  삭제 가능 항목 : 백업 이후 생성되거나 변경된 파일, 프로그램, 설정은 삭제될 수 있습니다.\r\n";
  out << L"  사전 백업      : 복원 전 중요한 개인 파일과 업무 자료는 반드시 외부 저장장치 또는 다른 안전한 위치에 백업하십시오.\r\n";
  out << L"  데이터 보호    : Recoverix는 복원 전 데이터 백업 여부를 확인하거나 보장하지 않습니다.\r\n\r\n";

  out << L"6. 백업 생성\r\n";
  out << L"Recoverix 백업은 Windows 파티션을 기준으로 생성됩니다. 백업 이미지가 이미 존재하는 경우\r\n";
  out << L"일반 사용자 모드에서는 중복 백업 생성을 막아 기존 복구 이미지를 보호합니다.\r\n\r\n";

  out << L"7. 시스템 복원\r\n";
  out << L"시스템 복원은 Windows 파티션의 내용을 백업 당시 상태로 되돌립니다.\r\n";
  out << L"복원 대상 Windows 파티션의 크기가 백업 요구 크기보다 작은 경우 복원은 차단됩니다.\r\n\r\n";

  out << L"8. 주의사항\r\n";
  out << L"- 복원 작업 중 PC 전원을 끄지 마십시오.\r\n";
  out << L"- 저장장치 교체 또는 디스크 복제 후에는 부팅 메뉴가 달라질 수 있습니다.\r\n";
  out << L"- Windows에서 C: 드라이브만 포맷하거나 재설치하면 Windows Agent가 제거될 수 있습니다.\r\n";
  out << L"- Recoverix 보호 영역은 일반 저장공간으로 사용하지 않는 것이 정상입니다.\r\n\r\n";

  out << L"9. 문의 전 확인할 내용\r\n";
  out << L"- 이 프로그램의 PC 정보 화면\r\n";
  out << L"- 이 프로그램의 디스크/파티션 정보 화면\r\n";
  out << L"- Recoverix 부팅 메뉴 표시 여부\r\n";
  out << L"- Windows가 정상 부팅되는지 여부\r\n";

  return out.str();
}

void set_font(HWND hwnd, HFONT font) {
  SendMessageW(hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
}

HWND create_button(HWND parent, int id, const wchar_t* text) {
  HWND hwnd = CreateWindowExW(
      0,
      WC_BUTTONW,
      text,
      WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
      0,
      0,
      0,
      0,
      parent,
      reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),
      g_instance,
      nullptr);
  set_font(hwnd, g_font);
  return hwnd;
}

void set_content_text(const std::wstring& text) {
  SetWindowTextW(g_content, text.c_str());
  SendMessageW(g_content, EM_SETSEL, 0, 0);
  SendMessageW(g_content, EM_SCROLLCARET, 0, 0);
  InvalidateRect(g_content, nullptr, TRUE);
  UpdateWindow(g_content);
}

void fill_rect(HDC hdc, const RECT& rc, COLORREF color) {
  HBRUSH brush = CreateSolidBrush(color);
  FillRect(hdc, &rc, brush);
  DeleteObject(brush);
}

void frame_rect(HDC hdc, const RECT& rc, COLORREF color) {
  HBRUSH brush = CreateSolidBrush(color);
  FrameRect(hdc, &rc, brush);
  DeleteObject(brush);
}

void draw_label(HDC hdc, const std::wstring& text, RECT rc, COLORREF color, UINT flags) {
  SetBkMode(hdc, TRANSPARENT);
  SetTextColor(hdc, color);
  DrawTextW(hdc, text.c_str(), -1, &rc, flags);
}

int measure_text_height(HDC hdc, const std::wstring& text, int width, UINT flags = DT_LEFT | DT_WORDBREAK) {
  if (trim(text).empty()) {
    return 0;
  }
  RECT rc{0, 0, std::max(20, width), 0};
  DrawTextW(hdc, text.c_str(), -1, &rc, flags | DT_CALCRECT);
  return std::max(18, static_cast<int>(rc.bottom - rc.top));
}

void draw_panel(HDC hdc, const RECT& rc, COLORREF fill, COLORREF border) {
  fill_rect(hdc, rc, fill);
  frame_rect(hdc, rc, border);
}

int client_height(HWND hwnd) {
  RECT rc{};
  GetClientRect(hwnd, &rc);
  return std::max(1, static_cast<int>(rc.bottom - rc.top));
}

int max_scroll_for(HWND hwnd, int content_height) {
  return std::max(0, content_height - client_height(hwnd));
}

void update_vertical_scrollbar(HWND hwnd, int content_height, int scroll_y) {
  SCROLLINFO si{};
  si.cbSize = sizeof(si);
  si.fMask = SIF_RANGE | SIF_PAGE | SIF_POS;
  si.nMin = 0;
  si.nMax = std::max(0, content_height - 1);
  si.nPage = static_cast<UINT>(client_height(hwnd));
  si.nPos = std::min(scroll_y, max_scroll_for(hwnd, content_height));
  SetScrollInfo(hwnd, SB_VERT, &si, TRUE);
}

void set_scroll_y(HWND hwnd, int& scroll_y, int content_height, int new_y) {
  const int next_y = std::max(0, std::min(new_y, max_scroll_for(hwnd, content_height)));
  if (next_y == scroll_y) {
    return;
  }
  scroll_y = next_y;
  update_vertical_scrollbar(hwnd, content_height, scroll_y);
  InvalidateRect(hwnd, nullptr, TRUE);
}

void handle_vertical_scroll(HWND hwnd, WPARAM wparam, int& scroll_y, int content_height) {
  SCROLLINFO si{};
  si.cbSize = sizeof(si);
  si.fMask = SIF_ALL;
  GetScrollInfo(hwnd, SB_VERT, &si);

  int next = scroll_y;
  switch (LOWORD(wparam)) {
    case SB_LINEUP:
      next -= 36;
      break;
    case SB_LINEDOWN:
      next += 36;
      break;
    case SB_PAGEUP:
      next -= static_cast<int>(si.nPage);
      break;
    case SB_PAGEDOWN:
      next += static_cast<int>(si.nPage);
      break;
    case SB_THUMBTRACK:
    case SB_THUMBPOSITION:
      next = si.nTrackPos;
      break;
    case SB_TOP:
      next = 0;
      break;
    case SB_BOTTOM:
      next = max_scroll_for(hwnd, content_height);
      break;
    default:
      break;
  }
  set_scroll_y(hwnd, scroll_y, content_height, next);
}

void handle_mouse_wheel(HWND hwnd, WPARAM wparam, int& scroll_y, int content_height) {
  const int delta = GET_WHEEL_DELTA_WPARAM(wparam);
  const int lines = std::max(1, std::abs(delta) / WHEEL_DELTA);
  const int step = 64 * lines;
  set_scroll_y(hwnd, scroll_y, content_height, scroll_y - (delta > 0 ? step : -step));
}

void hide_tooltip() {
  if (g_tooltip && g_tooltip_added) {
    TOOLINFOW ti{};
    ti.cbSize = sizeof(ti);
    ti.hwnd = g_disk_view;
    ti.uId = 1;
    SendMessageW(g_tooltip, TTM_TRACKACTIVATE, FALSE, reinterpret_cast<LPARAM>(&ti));
  }
}

void show_tooltip(HWND owner, POINT screen_point, const std::wstring& text) {
  if (!g_tooltip) {
    g_tooltip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, nullptr,
                                WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX,
                                CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT, CW_USEDEFAULT,
                                owner, nullptr, g_instance, nullptr);
    SendMessageW(g_tooltip, TTM_SETMAXTIPWIDTH, 0, 420);
  }

  g_tooltip_text = text;
  TOOLINFOW ti{};
  ti.cbSize = sizeof(ti);
  ti.uFlags = TTF_TRACK | TTF_ABSOLUTE;
  ti.hwnd = owner;
  ti.uId = 1;
  ti.lpszText = const_cast<LPWSTR>(g_tooltip_text.c_str());
  if (!g_tooltip_added) {
    SendMessageW(g_tooltip, TTM_ADDTOOLW, 0, reinterpret_cast<LPARAM>(&ti));
    g_tooltip_added = true;
  } else {
    SendMessageW(g_tooltip, TTM_UPDATETIPTEXTW, 0, reinterpret_cast<LPARAM>(&ti));
  }
  SendMessageW(g_tooltip, TTM_TRACKPOSITION, 0,
               MAKELPARAM(screen_point.x + 18, screen_point.y + 20));
  SendMessageW(g_tooltip, TTM_TRACKACTIVATE, TRUE, reinterpret_cast<LPARAM>(&ti));
}

uint64_t disk_segments_total() {
  if (g_disk_model.total_size > 0) {
    return g_disk_model.total_size;
  }
  uint64_t total = 0;
  for (const auto& segment : g_disk_model.segments) {
    total += segment.size;
  }
  return total;
}

std::vector<int> disk_segment_widths(int total_width) {
  std::vector<int> widths;
  const uint64_t total = disk_segments_total();
  if (total == 0 || total_width <= 0 || g_disk_model.segments.empty()) {
    return widths;
  }
  const int min_width = 34;
  int used = 0;
  for (const auto& segment : g_disk_model.segments) {
    int width = static_cast<int>(
        std::round((static_cast<double>(segment.size) / static_cast<double>(total)) * total_width));
    width = std::max(min_width, width);
    widths.push_back(width);
    used += width;
  }
  while (used > total_width) {
    bool changed = false;
    for (auto it = widths.rbegin(); it != widths.rend() && used > total_width; ++it) {
      if (*it > min_width) {
        --(*it);
        --used;
        changed = true;
      }
    }
    if (!changed) {
      break;
    }
  }
  while (used < total_width && !widths.empty()) {
    auto it = std::max_element(widths.begin(), widths.end());
    ++(*it);
    ++used;
  }
  return widths;
}

int card_height(HDC hdc, const InfoCard& card, int width) {
  const int padding = 16;
  const int key_width = 170;
  const int row_gap = 8;
  int height = padding + 26;
  const int content_width = std::max(80, width - padding * 2);
  if (!trim(card.body).empty()) {
    height += 10 + measure_text_height(hdc, card.body, content_width, DT_LEFT | DT_WORDBREAK);
  }
  if (!card.steps.empty()) {
    height += 6;
    const int step_text_width = std::max(80, content_width - 46);
    for (const auto& step : card.steps) {
      const int step_height =
          std::max(32, measure_text_height(hdc, step, step_text_width, DT_LEFT | DT_WORDBREAK));
      height += step_height + 10;
    }
  }
  for (const auto& row : card.rows) {
    const int value_width = std::max(80, content_width - key_width - 12);
    const int row_height =
        std::max(24, measure_text_height(hdc, nonempty_or_dash(row.value), value_width, DT_LEFT | DT_WORDBREAK));
    height += row_height + row_gap;
  }
  return height + padding;
}

int draw_info_card(HDC hdc, const InfoCard& card, int x, int y, int width) {
  const int height = card_height(hdc, card, width);
  RECT card_rc{x, y, x + width, y + height};
  const COLORREF card_fill =
      card.accent ? RGB(239, 246, 255) : (card.warning ? RGB(255, 251, 235) : RGB(255, 255, 255));
  const COLORREF card_border =
      card.accent ? RGB(31, 111, 235) : (card.warning ? RGB(245, 158, 11) : RGB(215, 221, 228));
  const COLORREF title_color =
      card.accent ? RGB(30, 64, 175) : (card.warning ? RGB(146, 64, 14) : RGB(23, 32, 42));
  draw_panel(hdc, card_rc, card_fill, card_border);
  if (card.accent) {
    RECT accent_bar{x, y, x + 6, y + height};
    fill_rect(hdc, accent_bar, RGB(31, 111, 235));
  } else if (card.warning) {
    RECT accent_bar{x, y, x + 6, y + height};
    fill_rect(hdc, accent_bar, RGB(245, 158, 11));
  }

  const int padding = 16;
  const int key_width = 170;
  RECT title_rc{x + padding, y + 12, x + width - padding, y + 40};
  SelectObject(hdc, g_header_font);
  draw_label(hdc, card.title, title_rc, title_color, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);

  SelectObject(hdc, g_font);
  int cursor = y + 46;
  const int content_width = std::max(80, width - padding * 2);
  if (!trim(card.body).empty()) {
    const int body_height = measure_text_height(hdc, card.body, content_width, DT_LEFT | DT_WORDBREAK);
    RECT body_rc{x + padding, cursor, x + width - padding, cursor + body_height};
    draw_label(hdc, card.body, body_rc, RGB(52, 64, 84), DT_LEFT | DT_WORDBREAK);
    cursor += body_height + 12;
  }

  if (!card.steps.empty()) {
    const int badge_size = 28;
    const int text_left = x + padding + 44;
    const int step_text_width = std::max(80, x + width - padding - text_left);
    for (size_t i = 0; i < card.steps.size(); ++i) {
      const int step_height =
          std::max(32, measure_text_height(hdc, card.steps[i], step_text_width, DT_LEFT | DT_WORDBREAK));
      RECT badge{x + padding, cursor + 2, x + padding + badge_size, cursor + 2 + badge_size};
      HBRUSH badge_brush = CreateSolidBrush(RGB(31, 111, 235));
      HBRUSH old_brush = static_cast<HBRUSH>(SelectObject(hdc, badge_brush));
      HPEN badge_pen = CreatePen(PS_SOLID, 1, RGB(29, 78, 216));
      HPEN old_pen = static_cast<HPEN>(SelectObject(hdc, badge_pen));
      Ellipse(hdc, badge.left, badge.top, badge.right, badge.bottom);
      SelectObject(hdc, old_pen);
      SelectObject(hdc, old_brush);
      DeleteObject(badge_pen);
      DeleteObject(badge_brush);

      RECT number_rc{badge.left, badge.top, badge.right, badge.bottom};
      draw_label(hdc, std::to_wstring(i + 1), number_rc, RGB(255, 255, 255),
                 DT_CENTER | DT_VCENTER | DT_SINGLELINE);

      RECT step_rc{text_left, cursor, x + width - padding, cursor + step_height};
      draw_label(hdc, card.steps[i], step_rc, RGB(23, 32, 42), DT_LEFT | DT_TOP | DT_WORDBREAK);
      cursor += step_height + 10;
    }
    cursor += 2;
  }

  for (const auto& row : card.rows) {
    const int value_width = std::max(80, content_width - key_width - 12);
    const int row_height =
        std::max(24, measure_text_height(hdc, nonempty_or_dash(row.value), value_width, DT_LEFT | DT_WORDBREAK));
    RECT key_rc{x + padding, cursor, x + padding + key_width, cursor + row_height};
    RECT value_rc{x + padding + key_width + 12, cursor, x + width - padding, cursor + row_height};
    draw_label(hdc, row.key, key_rc, RGB(86, 97, 111), DT_LEFT | DT_TOP | DT_SINGLELINE | DT_END_ELLIPSIS);
    draw_label(hdc, nonempty_or_dash(row.value), value_rc, RGB(23, 32, 42), DT_LEFT | DT_TOP | DT_WORDBREAK);
    cursor += row_height + 8;
  }

  return height;
}

void draw_page_view(HWND hwnd, HDC hdc) {
  RECT rc{};
  GetClientRect(hwnd, &rc);
  fill_rect(hdc, rc, RGB(244, 247, 251));

  const int margin = 24;
  const int width = std::max(
      120,
      static_cast<int>(rc.right - rc.left - margin * 2 - GetSystemMetrics(SM_CXVSCROLL)));
  int y = margin - g_page_scroll_y;

  SelectObject(hdc, g_font);
  if (!trim(g_page_model.note).empty()) {
    const int note_height = std::max(54, measure_text_height(hdc, g_page_model.note, width - 28) + 22);
    RECT note_rc{margin, y, margin + width, y + note_height};
    draw_panel(hdc, note_rc, RGB(248, 250, 252), RGB(215, 221, 228));
    RECT note_text{note_rc.left + 14, note_rc.top + 10, note_rc.right - 14, note_rc.bottom - 8};
    draw_label(hdc, g_page_model.note, note_text, RGB(52, 64, 84), DT_LEFT | DT_TOP | DT_WORDBREAK);
    y += note_height + 14;
  }

  for (const auto& card : g_page_model.cards) {
    const int height = card_height(hdc, card, width);
    if (y + height >= 0 && y <= rc.bottom) {
      draw_info_card(hdc, card, margin, y, width);
    }
    y += height + 14;
  }

  g_page_content_height = y + g_page_scroll_y + margin;
  update_vertical_scrollbar(hwnd, g_page_content_height, g_page_scroll_y);
}

LRESULT CALLBACK page_view_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
  switch (msg) {
    case WM_PAINT: {
      PAINTSTRUCT ps{};
      HDC hdc = BeginPaint(hwnd, &ps);
      draw_page_view(hwnd, hdc);
      EndPaint(hwnd, &ps);
      return 0;
    }
    case WM_VSCROLL:
      handle_vertical_scroll(hwnd, wparam, g_page_scroll_y, g_page_content_height);
      return 0;
    case WM_MOUSEWHEEL:
      handle_mouse_wheel(hwnd, wparam, g_page_scroll_y, g_page_content_height);
      return 0;
    case WM_SIZE:
      set_scroll_y(hwnd, g_page_scroll_y, g_page_content_height, g_page_scroll_y);
      InvalidateRect(hwnd, nullptr, TRUE);
      return 0;
  }
  return DefWindowProcW(hwnd, msg, wparam, lparam);
}

void draw_disk_view(HWND hwnd, HDC hdc) {
  RECT rc{};
  GetClientRect(hwnd, &rc);
  fill_rect(hdc, rc, RGB(255, 255, 255));

  const int margin = 24;
  const int scroll_bar = GetSystemMetrics(SM_CXVSCROLL);
  const int content_right = rc.right - margin - scroll_bar;
  const int offset = -g_disk_scroll_y;
  RECT note{margin, 18 + offset, content_right, 72 + offset};
  fill_rect(hdc, note, RGB(248, 250, 252));
  frame_rect(hdc, note, RGB(215, 221, 228));
  SelectObject(hdc, g_font);
  RECT note_text{note.left + 14, note.top + 10, note.right - 14, note.bottom - 8};
  draw_label(hdc, g_disk_model.note, note_text, RGB(52, 64, 84),
             DT_LEFT | DT_TOP | DT_WORDBREAK);

  SelectObject(hdc, g_header_font);
  RECT title{margin, 88 + offset, content_right, 118 + offset};
  draw_label(hdc,
             g_disk_model.disk_title.empty() ? L"부팅 디스크 정보" : g_disk_model.disk_title,
             title, RGB(23, 32, 42), DT_LEFT | DT_VCENTER | DT_SINGLELINE);

  SelectObject(hdc, g_font);
  RECT detail{margin, 122 + offset, content_right, 148 + offset};
  const std::wstring detail_text =
      g_disk_model.capacity_text.empty()
          ? g_disk_model.disk_detail
          : g_disk_model.capacity_text + L" / " + g_disk_model.disk_detail;
  draw_label(hdc, detail_text, detail, RGB(86, 97, 111),
             DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);

  RECT bar{margin, 168 + offset, content_right, 242 + offset};
  fill_rect(hdc, bar, RGB(241, 245, 249));
  frame_rect(hdc, bar, RGB(184, 195, 209));

  auto widths = disk_segment_widths(bar.right - bar.left);
  int x = bar.left;
  for (size_t i = 0; i < g_disk_model.segments.size() && i < widths.size(); ++i) {
    auto& segment = g_disk_model.segments[i];
    RECT part{x, bar.top, x + widths[i], bar.bottom};
    segment.rect = part;
    fill_rect(hdc, part, segment.fill);
    frame_rect(hdc, part, i == static_cast<size_t>(g_disk_hover_index) ? RGB(15, 23, 42) : segment.border);
    if (i == static_cast<size_t>(g_disk_hover_index)) {
      RECT inner{part.left + 2, part.top + 2, part.right - 2, part.bottom - 2};
      frame_rect(hdc, inner, RGB(15, 23, 42));
    }
    if (part.right - part.left >= 62) {
      RECT label{part.left + 4, part.top + 10, part.right - 4, part.top + 34};
      draw_label(hdc, segment.label, label,
                 segment.gap ? RGB(71, 85, 105) : RGB(255, 255, 255),
                 DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
      RECT size{part.left + 4, part.top + 36, part.right - 4, part.bottom - 6};
      draw_label(hdc, segment.size_text, size,
                 segment.gap ? RGB(71, 85, 105) : RGB(238, 242, 255),
                 DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    }
    x += widths[i];
  }

  RECT legend_title{margin, 266 + offset, content_right, 292 + offset};
  SelectObject(hdc, g_header_font);
  draw_label(hdc, L"파티션 역할", legend_title, RGB(23, 32, 42),
             DT_LEFT | DT_VCENTER | DT_SINGLELINE);

  SelectObject(hdc, g_font);
  int y = 306 + offset;
  int logical_y = 306;
  for (size_t i = 0; i < g_disk_model.segments.size(); ++i) {
    const auto& segment = g_disk_model.segments[i];
    const bool selected = static_cast<int>(i) == g_disk_hover_index;
    if (y + 54 >= 0 && y <= rc.bottom) {
      RECT row{margin, y, content_right, y + 46};
      fill_rect(hdc, row, selected ? RGB(219, 234, 254) : RGB(248, 250, 252));
      frame_rect(hdc, row, selected ? RGB(31, 111, 235) : RGB(226, 232, 240));
      if (selected) {
        RECT inner{row.left + 2, row.top + 2, row.right - 2, row.bottom - 2};
        frame_rect(hdc, inner, RGB(31, 111, 235));
      }
      RECT swatch{row.left + 12, row.top + 12, row.left + 34, row.top + 34};
      fill_rect(hdc, swatch, segment.fill);
      frame_rect(hdc, swatch, selected ? RGB(15, 23, 42) : segment.border);
      RECT name{row.left + 44, row.top + 6, row.left + 280, row.bottom - 6};
      draw_label(hdc, segment.label, name, selected ? RGB(15, 23, 42) : RGB(23, 32, 42),
                 DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
      RECT role{row.left + 280, row.top + 6, row.right - 150, row.bottom - 6};
      draw_label(hdc, segment.role, role, selected ? RGB(30, 64, 175) : RGB(52, 64, 84),
                 DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
      RECT size{row.right - 140, row.top + 6, row.right - 14, row.bottom - 6};
      draw_label(hdc, segment.size_text, size, selected ? RGB(30, 64, 175) : RGB(86, 97, 111),
                 DT_RIGHT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    }
    y += 54;
    logical_y += 54;
  }

  if (!g_disk_model.hidden_text.empty()) {
    RECT hidden{margin, y + 10, content_right, y + 40};
    draw_label(hdc, L"Windows 탐색기에서 바로 보이지 않는 영역: " + g_disk_model.hidden_text,
               hidden, RGB(86, 97, 111), DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    y += 34;
    logical_y += 34;
  }
  if (!g_disk_model.windows_gap_text.empty()) {
    RECT gap{margin, y + 4, content_right, y + 34};
    draw_label(hdc, L"Windows 파티션 뒤 여유 공간: " + g_disk_model.windows_gap_text +
                         L" (복구 안정성 경계 보정용)",
               gap, RGB(86, 97, 111), DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    y += 34;
    logical_y += 34;
  }
  g_disk_content_height = logical_y + 54;
  update_vertical_scrollbar(hwnd, g_disk_content_height, g_disk_scroll_y);
}

int disk_segment_at_point(POINT pt) {
  for (size_t i = 0; i < g_disk_model.segments.size(); ++i) {
    if (PtInRect(&g_disk_model.segments[i].rect, pt)) {
      return static_cast<int>(i);
    }
  }
  return -1;
}

LRESULT CALLBACK disk_view_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
  switch (msg) {
    case WM_PAINT: {
      PAINTSTRUCT ps{};
      HDC hdc = BeginPaint(hwnd, &ps);
      draw_disk_view(hwnd, hdc);
      EndPaint(hwnd, &ps);
      return 0;
    }
    case WM_MOUSEMOVE: {
      TRACKMOUSEEVENT tme{};
      tme.cbSize = sizeof(tme);
      tme.dwFlags = TME_LEAVE;
      tme.hwndTrack = hwnd;
      TrackMouseEvent(&tme);

      POINT pt{GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)};
      const int hit = disk_segment_at_point(pt);
      if (hit != g_disk_hover_index) {
        g_disk_hover_index = hit;
        InvalidateRect(hwnd, nullptr, FALSE);
      }
      if (hit >= 0 && hit < static_cast<int>(g_disk_model.segments.size())) {
        ClientToScreen(hwnd, &pt);
        show_tooltip(hwnd, pt, g_disk_model.segments[hit].tooltip);
      } else {
        hide_tooltip();
      }
      return 0;
    }
    case WM_VSCROLL:
      handle_vertical_scroll(hwnd, wparam, g_disk_scroll_y, g_disk_content_height);
      return 0;
    case WM_MOUSEWHEEL:
      handle_mouse_wheel(hwnd, wparam, g_disk_scroll_y, g_disk_content_height);
      return 0;
    case WM_SIZE:
      set_scroll_y(hwnd, g_disk_scroll_y, g_disk_content_height, g_disk_scroll_y);
      InvalidateRect(hwnd, nullptr, TRUE);
      return 0;
    case WM_MOUSELEAVE:
      g_disk_hover_index = -1;
      hide_tooltip();
      InvalidateRect(hwnd, nullptr, FALSE);
      return 0;
  }
  return DefWindowProcW(hwnd, msg, wparam, lparam);
}

void layout(HWND hwnd) {
  RECT rc{};
  GetClientRect(hwnd, &rc);
  const int width = rc.right - rc.left;
  const int height = rc.bottom - rc.top;
  const int margin = 20;
  const int sidebar = 250;
  const int button_w = sidebar - margin * 2;
  const int button_h = 42;
  const int gap = 10;

  MoveWindow(g_title, margin, 18, button_w, 32, TRUE);
  MoveWindow(g_subtitle, margin, 50, button_w, 46, TRUE);
  MoveWindow(g_btn_pc, margin, 118, button_w, button_h, TRUE);
  MoveWindow(g_btn_disk, margin, 118 + (button_h + gap), button_w, button_h, TRUE);
  MoveWindow(g_btn_guide, margin, 118 + (button_h + gap) * 2, button_w, button_h, TRUE);
  MoveWindow(g_btn_refresh, margin, height - 106, button_w, button_h, TRUE);
  MoveWindow(g_btn_exit, margin, height - 56, button_w, button_h, TRUE);

  const int content_x = sidebar + margin;
  const int content_w = std::max(100, width - content_x - margin);
  MoveWindow(g_header, content_x, 20, content_w, 34, TRUE);
  MoveWindow(g_content, content_x, 66, content_w, std::max(100, height - 86), TRUE);
  if (g_page_view) {
    MoveWindow(g_page_view, content_x, 66, content_w, std::max(100, height - 86), TRUE);
  }
  if (g_disk_view) {
    MoveWindow(g_disk_view, content_x, 66, content_w, std::max(100, height - 86), TRUE);
  }
}

void update_page(Page page) {
  g_current_page = page;
  hide_tooltip();
  if (g_page_view) {
    ShowWindow(g_page_view, SW_HIDE);
  }
  if (g_disk_view) {
    ShowWindow(g_disk_view, SW_HIDE);
  }
  ShowWindow(g_content, SW_SHOW);
  SetCursor(LoadCursorW(nullptr, IDC_WAIT));
  std::wstring header;
  std::wstring content;
  switch (page) {
    case Page::PcInfo:
      header = L"PC 정보";
      break;
    case Page::DiskInfo:
      header = L"디스크 / 파티션 정보";
      break;
    case Page::Guide:
      header = L"복구솔루션 사용설명서";
      break;
  }
  SetWindowTextW(g_header, header.c_str());
  set_content_text(L"정보를 다시 불러오는 중입니다...\r\n잠시만 기다려 주세요.");
  UpdateWindow(g_header);

  switch (page) {
    case Page::PcInfo:
      content = build_pc_info();
      break;
    case Page::DiskInfo:
      content = build_disk_info();
      break;
    case Page::Guide:
      content = build_guide();
      break;
  }
  if (page == Page::DiskInfo) {
    set_content_text(content);
    ShowWindow(g_content, SW_HIDE);
    g_disk_scroll_y = 0;
    if (g_disk_view) {
      ShowWindow(g_disk_view, SW_SHOW);
    }
    g_disk_hover_index = -1;
    if (g_disk_view) {
      InvalidateRect(g_disk_view, nullptr, TRUE);
      UpdateWindow(g_disk_view);
    }
  } else {
    set_content_text(content);
    ShowWindow(g_content, SW_HIDE);
    g_page_scroll_y = 0;
    if (g_page_view) {
      ShowWindow(g_page_view, SW_SHOW);
      InvalidateRect(g_page_view, nullptr, TRUE);
      UpdateWindow(g_page_view);
    }
  }
  SetCursor(LoadCursorW(nullptr, IDC_ARROW));
}

LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
  switch (msg) {
    case WM_CREATE: {
      g_background = CreateSolidBrush(RGB(244, 247, 251));
      g_sidebar_background = CreateSolidBrush(RGB(231, 238, 247));
      g_font = CreateFontW(-16, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                           OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                           DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
      g_title_font = CreateFontW(-24, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                 OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                 DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
      g_header_font = CreateFontW(-22, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                  OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                  DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");

      g_title = CreateWindowExW(0, WC_STATICW, L"Recoverix", WS_CHILD | WS_VISIBLE | SS_CENTER,
                                0, 0, 0, 0, hwnd, nullptr, g_instance, nullptr);
      g_subtitle = CreateWindowExW(0, WC_STATICW, L"상태 확인 및 안내", WS_CHILD | WS_VISIBLE | SS_CENTER,
                                   0, 0, 0, 0, hwnd, nullptr, g_instance, nullptr);
      g_header = CreateWindowExW(0, WC_STATICW, L"", WS_CHILD | WS_VISIBLE,
                                 0, 0, 0, 0, hwnd, nullptr, g_instance, nullptr);
      g_content = CreateWindowExW(
          WS_EX_CLIENTEDGE,
          WC_EDITW,
          L"",
          WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_VSCROLL | ES_MULTILINE | ES_AUTOVSCROLL |
              ES_READONLY | ES_LEFT,
          0,
          0,
          0,
          0,
          hwnd,
          nullptr,
          g_instance,
          nullptr);
      g_page_view = CreateWindowExW(
          WS_EX_CLIENTEDGE,
          L"RecoverixPageViewWindow",
          L"",
          WS_CHILD | WS_CLIPSIBLINGS | WS_VSCROLL,
          0,
          0,
          0,
          0,
          hwnd,
          nullptr,
          g_instance,
          nullptr);
      g_disk_view = CreateWindowExW(
          WS_EX_CLIENTEDGE,
          L"RecoverixDiskViewWindow",
          L"",
          WS_CHILD | WS_CLIPSIBLINGS | WS_VSCROLL,
          0,
          0,
          0,
          0,
          hwnd,
          nullptr,
          g_instance,
          nullptr);

      g_btn_pc = create_button(hwnd, kButtonPc, L"PC 정보");
      g_btn_disk = create_button(hwnd, kButtonDisk, L"디스크 / 파티션");
      g_btn_guide = create_button(hwnd, kButtonGuide, L"사용설명서");
      g_btn_refresh = create_button(hwnd, kButtonRefresh, L"새로고침");
      g_btn_exit = create_button(hwnd, kButtonExit, L"종료");

      set_font(g_title, g_title_font);
      set_font(g_subtitle, g_font);
      set_font(g_header, g_header_font);
      set_font(g_content, g_font);

      layout(hwnd);
      update_page(Page::PcInfo);
      return 0;
    }
    case WM_SIZE:
      layout(hwnd);
      InvalidateRect(hwnd, nullptr, TRUE);
      if (g_page_view) {
        InvalidateRect(g_page_view, nullptr, TRUE);
      }
      if (g_disk_view) {
        InvalidateRect(g_disk_view, nullptr, TRUE);
      }
      return 0;
    case WM_COMMAND:
      switch (LOWORD(wparam)) {
        case kButtonPc:
          update_page(Page::PcInfo);
          return 0;
        case kButtonDisk:
          update_page(Page::DiskInfo);
          return 0;
        case kButtonGuide:
          update_page(Page::Guide);
          return 0;
        case kButtonRefresh:
          update_page(g_current_page);
          return 0;
        case kButtonExit:
          DestroyWindow(hwnd);
          return 0;
      }
      break;
    case WM_CTLCOLORSTATIC: {
      HDC hdc = reinterpret_cast<HDC>(wparam);
      HWND child = reinterpret_cast<HWND>(lparam);
      if (child == g_content) {
        SetBkMode(hdc, OPAQUE);
        SetBkColor(hdc, RGB(255, 255, 255));
        SetTextColor(hdc, RGB(28, 35, 43));
        return reinterpret_cast<LRESULT>(GetStockObject(WHITE_BRUSH));
      }
      if (child == g_title || child == g_subtitle) {
        SetBkMode(hdc, TRANSPARENT);
        SetTextColor(hdc, RGB(33, 45, 58));
        return reinterpret_cast<LRESULT>(g_sidebar_background);
      }
      SetBkMode(hdc, TRANSPARENT);
      SetTextColor(hdc, RGB(33, 45, 58));
      return reinterpret_cast<LRESULT>(g_background);
    }
    case WM_CTLCOLOREDIT: {
      HDC hdc = reinterpret_cast<HDC>(wparam);
      SetBkColor(hdc, RGB(255, 255, 255));
      SetTextColor(hdc, RGB(28, 35, 43));
      return reinterpret_cast<LRESULT>(GetStockObject(WHITE_BRUSH));
    }
    case WM_ERASEBKGND: {
      HDC hdc = reinterpret_cast<HDC>(wparam);
      RECT rc{};
      GetClientRect(hwnd, &rc);
      FillRect(hdc, &rc, g_background);
      RECT sidebar{0, 0, 250, rc.bottom};
      FillRect(hdc, &sidebar, g_sidebar_background);
      return 1;
    }
    case WM_DESTROY:
      if (g_tooltip) {
        DestroyWindow(g_tooltip);
        g_tooltip = nullptr;
      }
      if (g_font) {
        DeleteObject(g_font);
      }
      if (g_title_font) {
        DeleteObject(g_title_font);
      }
      if (g_header_font) {
        DeleteObject(g_header_font);
      }
      if (g_background) {
        DeleteObject(g_background);
      }
      if (g_sidebar_background) {
        DeleteObject(g_sidebar_background);
      }
      PostQuitMessage(0);
      return 0;
  }
  return DefWindowProcW(hwnd, msg, wparam, lparam);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show_cmd) {
  g_instance = instance;

  INITCOMMONCONTROLSEX icc{};
  icc.dwSize = sizeof(icc);
  icc.dwICC = ICC_STANDARD_CLASSES;
  InitCommonControlsEx(&icc);

  WNDCLASSEXW wc{};
  wc.cbSize = sizeof(wc);
  wc.style = CS_HREDRAW | CS_VREDRAW;
  wc.lpfnWndProc = window_proc;
  wc.hInstance = instance;
  wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
  wc.hIcon = LoadIconW(instance, MAKEINTRESOURCEW(kAppIconResourceId));
  wc.hIconSm = LoadIconW(instance, MAKEINTRESOURCEW(kAppIconResourceId));
  wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
  wc.lpszClassName = L"RecoverixStatusWindow";

  if (!RegisterClassExW(&wc)) {
    MessageBoxW(nullptr, (L"창 클래스 등록 실패: " + last_error_message()).c_str(),
                L"Recoverix Status", MB_OK | MB_ICONERROR);
    return 1;
  }

  WNDCLASSEXW page_wc{};
  page_wc.cbSize = sizeof(page_wc);
  page_wc.style = CS_HREDRAW | CS_VREDRAW;
  page_wc.lpfnWndProc = page_view_proc;
  page_wc.hInstance = instance;
  page_wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
  page_wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
  page_wc.lpszClassName = L"RecoverixPageViewWindow";

  if (!RegisterClassExW(&page_wc)) {
    MessageBoxW(nullptr, (L"페이지 보기 클래스 등록 실패: " + last_error_message()).c_str(),
                L"Recoverix Status", MB_OK | MB_ICONERROR);
    return 1;
  }

  WNDCLASSEXW disk_wc{};
  disk_wc.cbSize = sizeof(disk_wc);
  disk_wc.style = CS_HREDRAW | CS_VREDRAW;
  disk_wc.lpfnWndProc = disk_view_proc;
  disk_wc.hInstance = instance;
  disk_wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
  disk_wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
  disk_wc.lpszClassName = L"RecoverixDiskViewWindow";

  if (!RegisterClassExW(&disk_wc)) {
    MessageBoxW(nullptr, (L"디스크 보기 클래스 등록 실패: " + last_error_message()).c_str(),
                L"Recoverix Status", MB_OK | MB_ICONERROR);
    return 1;
  }

  g_main = CreateWindowExW(
      0,
      wc.lpszClassName,
      L"Recoverix 상태 확인",
      WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
      CW_USEDEFAULT,
      CW_USEDEFAULT,
      1000,
      680,
      nullptr,
      nullptr,
      instance,
      nullptr);

  if (!g_main) {
    MessageBoxW(nullptr, (L"창 생성 실패: " + last_error_message()).c_str(),
                L"Recoverix Status", MB_OK | MB_ICONERROR);
    return 1;
  }

  ShowWindow(g_main, show_cmd);
  UpdateWindow(g_main);

  MSG msg{};
  while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
    TranslateMessage(&msg);
    DispatchMessageW(&msg);
  }
  return static_cast<int>(msg.wParam);
}
