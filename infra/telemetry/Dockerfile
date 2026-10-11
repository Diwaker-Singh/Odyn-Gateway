# Proposed build; execution requires explicit local Go-builder approval.
FROM --platform=$BUILDPLATFORM golang:1.26.9-alpine@sha256:cdfd4fe2da6b225d8b40c6b7a105736e548e83ff56d5d8f9394446eeb5eb84e0 AS build
ARG TARGETARCH
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
WORKDIR /build
COPY builder.yaml /build/builder.yaml
COPY buildinfo/go.mod buildinfo/go.sum /build/locked/
RUN go install go.opentelemetry.io/collector/cmd/builder@v0.162.0 \
    && /go/bin/builder --config=/build/builder.yaml --skip-get-modules --skip-compilation \
    && cp /build/locked/go.mod /build/locked/go.sum /build/collector/ \
    && cd /build/collector \
    && GOOS=linux GOARCH=$TARGETARCH go build -mod=readonly -trimpath -ldflags="-s -w" -o odyn-otelcol .

FROM scratch
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /build/collector/odyn-otelcol /odyn-otelcol
COPY --from=build /build/collector/go.mod /build/collector/go.sum /buildinfo/
USER 10001:10001
ENTRYPOINT ["/odyn-otelcol"]
