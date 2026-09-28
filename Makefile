# agentids — build targets

BINARY_COLLECTOR := agentids-collector
BINARY_CENTRAL   := agentids-central
GO              ?= go

.PHONY: all build build-collector build-central test lint clean fmt vet

all: build

build: build-collector build-central

build-collector:
	$(GO) build -o bin/$(BINARY_COLLECTOR) ./cmd/collector

build-central:
	$(GO) build -o bin/$(BINARY_CENTRAL) ./cmd/central

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
