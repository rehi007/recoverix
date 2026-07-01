CXX := x86_64-w64-mingw32-g++
OUT := recoverix-nvram-writer.exe
SRC := recoverix_nvram_writer.cpp

CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -municode -finput-charset=UTF-8
LDFLAGS := -static -ladvapi32

.PHONY: all clean

all: $(OUT)

$(OUT): $(SRC)
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(OUT)
