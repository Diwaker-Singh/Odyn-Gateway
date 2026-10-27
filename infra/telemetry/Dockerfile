# Proposed build; execution requires explicit local Go-builder approval.
FROM --platform=$BUILDPLATFORM golang:1.26.10-alpine@sha256:a2012b4a1d1f9f7e9f44e0d39b805a901e856b1dd80b4e6ee4338db547050308 AS build
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
