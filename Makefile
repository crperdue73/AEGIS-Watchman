# agentids — build targets

BINARY_COLLECTOR := agentids-collector
BINARY_CENTRAL   := agentids-central
BINARY_MANIFEST  := agentids-manifest
GO              ?= go

.PHONY: all build build-collector build-central build-manifest test lint clean fmt vet

all: build

build: build-collector build-central build-manifest

build-collector:
	$(GO) build -o bin/$(BINARY_COLLECTOR) ./cmd/collector

build-central:
	$(GO) build -o bin/$(BINARY_CENTRAL) ./cmd/central

build-manifest:
	$(GO) build -o bin/$(BINARY_MANIFEST) ./cmd/manifest

test:
	$(GO) test ./...

vet:
	$(GO) vet ./...

lint: vet
	@echo "add golangci-lint when the tree has code"

fmt:
	$(GO) fmt ./...

clean:
	rm -rf bin/
