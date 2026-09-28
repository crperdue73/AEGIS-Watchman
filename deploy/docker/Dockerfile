# Build + run the collector and central service. Phase 1 work in progress.
# The collector needs access to the agent workspaces it hashes.

FROM golang:1.22 AS build
WORKDIR /src
COPY . .
RUN make build

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /src/bin/agentids-collector /agentids-collector
COPY --from=build /src/bin/agentids-central /agentids-central
# Collector by default; override the entrypoint to run central.
ENTRYPOINT ["/agentids-collector"]
