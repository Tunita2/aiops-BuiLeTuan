#!/usr/bin/env bash
# start_stack.sh — Start the W3-D2 chaos engineering stack

set -e

echo "=== W3-D2 Chaos Engineering Stack ==="
echo "Starting Docker Compose..."

# Build and start
docker compose up -d --build

# Wait for health checks
echo ""
echo "Waiting for services to be healthy..."
sleep 30

# Check status
echo ""
echo "Service status:"
docker compose ps

echo ""
echo "Stack is ready!"
echo "  Frontend:   http://localhost:3000"
echo "  API Gateway: http://localhost:8080"
echo "  Pipeline:   http://localhost:8000"
echo "  Prometheus: http://localhost:9090"
echo "  Grafana:    http://localhost:3001"
