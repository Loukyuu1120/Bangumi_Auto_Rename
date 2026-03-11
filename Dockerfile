# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build
# ─────────────────────────────────────────────────────────────────────────────
FROM golang:1.22-alpine AS builder

# git is needed by `go mod download` for VCS-based dependencies
RUN apk add --no-cache git

WORKDIR /build

# Download dependencies first so Docker can cache this layer independently
# from source-code changes.
COPY go_app/go.mod go_app/go.sum ./
RUN go mod download

# Copy the full Go application source
COPY go_app/ .

# Build a fully static binary:
#   CGO_ENABLED=0  – no C runtime dependency
#   -s -w          – strip debug info (reduces binary size ~30 %)
RUN CGO_ENABLED=0 GOOS=linux go build \
        -ldflags="-s -w" \
        -o /bangumi_auto_rename \
        .

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM alpine:3.19

# ca-certificates – required for outbound HTTPS calls (TMDB, OpenAI, Gemini …)
# tzdata          – required so the TZ env var is honoured
RUN apk add --no-cache ca-certificates tzdata

ENV TZ=Asia/Shanghai

# Persistent data directory (config, task records, logs).
# Mount a host volume here so settings survive container re-creates.
VOLUME ["/Bangumi_Auto_Rename/data"]

WORKDIR /app

# Copy the compiled binary from the builder stage
COPY --from=builder /bangumi_auto_rename ./bangumi_auto_rename

# Copy the static web UI (index.html, app.js, …)
COPY go_app/static ./static

EXPOSE 5999

ENTRYPOINT ["/app/bangumi_auto_rename"]
CMD ["--data", "/Bangumi_Auto_Rename/data", "--static", "/app/static"]
