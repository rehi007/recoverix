CXX := x86_64-w64-mingw32-g++
WINDRES := x86_64-w64-mingw32-windres
POWERSHELL ?= pwsh

APP_NAME := RecoverixStatus
OUT_DIR := build
SRC := src/recoverix_status.cpp
RES := build/recoverix_status.res
OUT := $(OUT_DIR)/$(APP_NAME).exe
PACKAGE_DIR := $(OUT_DIR)/package
SIGNED_OUT := $(OUT_DIR)/$(APP_NAME).signed.exe
SIGN_STATUS_APP := packaging/sign_status_app.ps1
VERIFY_SIGNATURE := packaging/verify_signature.ps1

CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -municode -DUNICODE -D_UNICODE -finput-charset=UTF-8
LDFLAGS := -static -mwindows -lcomctl32 -lole32 -loleaut32 -lwbemuuid -luuid -ladvapi32 -lshell32 -lshlwapi

.PHONY: all clean package package-signed sign sign-required verify-signature verify-signature-required

all: $(OUT)

$(OUT_DIR):
	mkdir -p $(OUT_DIR)

$(RES): resources/recoverix_status.rc resources/app.manifest resources/recoverix_status.ico | $(OUT_DIR)
	$(WINDRES) -O coff -i resources/recoverix_status.rc -o $(RES)

$(OUT): $(SRC) $(RES) | $(OUT_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $(SRC) $(RES) $(LDFLAGS)

sign: all
	@if command -v $(POWERSHELL) >/dev/null 2>&1; then \
		$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(SIGN_STATUS_APP) -InputPath "$(OUT)" -OutputPath "$(SIGNED_OUT)" -Optional; \
	else \
		echo "[codesign] $(POWERSHELL) not found; copying unsigned development build."; \
		cp "$(OUT)" "$(SIGNED_OUT)"; \
	fi

sign-required: all
	@if ! command -v $(POWERSHELL) >/dev/null 2>&1; then \
		echo "[codesign] ERROR: $(POWERSHELL) not found. Production package requires PowerShell and signtool.exe."; \
		exit 1; \
	fi
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(SIGN_STATUS_APP) -InputPath "$(OUT)" -OutputPath "$(SIGNED_OUT)"

verify-signature: sign
	@if command -v $(POWERSHELL) >/dev/null 2>&1; then \
		$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(VERIFY_SIGNATURE) -Path "$(SIGNED_OUT)" -AllowUnsigned; \
	else \
		echo "[codesign] $(POWERSHELL) not found; signature verification skipped for development build."; \
	fi

verify-signature-required: sign-required
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(VERIFY_SIGNATURE) -Path "$(SIGNED_OUT)"

package: verify-signature
	mkdir -p $(PACKAGE_DIR)
	cp $(SIGNED_OUT) $(PACKAGE_DIR)/$(APP_NAME).exe
	cp packaging/install_status_app.cmd $(PACKAGE_DIR)/
	cp packaging/create_desktop_shortcut.ps1 $(PACKAGE_DIR)/
	cp $(SIGN_STATUS_APP) $(PACKAGE_DIR)/
	cp $(VERIFY_SIGNATURE) $(PACKAGE_DIR)/

package-signed: verify-signature-required
	mkdir -p $(PACKAGE_DIR)
	cp $(SIGNED_OUT) $(PACKAGE_DIR)/$(APP_NAME).exe
	cp packaging/install_status_app.cmd $(PACKAGE_DIR)/
	cp packaging/create_desktop_shortcut.ps1 $(PACKAGE_DIR)/
	cp $(SIGN_STATUS_APP) $(PACKAGE_DIR)/
	cp $(VERIFY_SIGNATURE) $(PACKAGE_DIR)/

clean:
	rm -rf $(OUT_DIR)
