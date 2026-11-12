CC ?= cc
PYTHON ?= python3
CFLAGS ?= -O2 -std=c99 -Wall -Wextra -Werror
CORE = src/gauge_core.c
HEADERS = src/gauge_core.h src/drive_frame.h src/diagnostic_lease.h
.PHONY: assets test

assets:
	$(PYTHON) tools/make_bars.py
	$(PYTHON) tools/make_drive_apps.py

build:
	mkdir -p build

build/boostdeck-collector: src/collector.c $(CORE) $(HEADERS) | build
	$(CC) $(CFLAGS) -Isrc $(filter %.c,$^) -lm -o $@

SHIFT_FAST_RPM ?= 0

build/shift-deck: src/collector.c $(CORE) $(HEADERS) | build
	$(CC) $(CFLAGS) -Isrc -DSHIFT_DECK_ONLY -DSHIFT_FAST_RPM=$(SHIFT_FAST_RPM) $(filter %.c,$^) -lm -o $@

build/mhd-maps: src/collector.c $(CORE) $(HEADERS) | build
	$(CC) $(CFLAGS) -Isrc -DMHD_MAPS_ONLY $(filter %.c,$^) -lm -o $@

build/test-core: tests/test_core.c $(CORE) $(HEADERS) | build
	$(CC) $(CFLAGS) -Isrc $(filter %.c,$^) -lm -o $@

test: build/boostdeck-collector build/shift-deck build/mhd-maps build/test-core
	./build/test-core
	$(PYTHON) -B tests/test_transport.py
	$(PYTHON) -B tests/test_drive_apps.py
