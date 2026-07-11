#!/usr/bin/env bash

# restart-dev-container.sh - Restart the hass-engie-be development container
# Usage: ./scripts/restart-dev-container.sh [container_name]
#
# Based on Home Assistant local development best practices.
# Uses the official HA container with the project mounted as a volume.

set -e

CONTAINER_NAME="${1:-engie_be_dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

HA_VERSION="2026.7.1"
HA_IMAGE="ghcr.io/home-assistant/home-assistant:${HA_VERSION}"

echo "=========================================="
echo "Restarting HA dev container: $CONTAINER_NAME"
echo "Home Assistant: $HA_VERSION"
echo "Project: $PROJECT_DIR"
echo "=========================================="

# Stop and remove existing container
echo "Stopping existing container..."
podman stop "$CONTAINER_NAME" 2>/dev/null || true
podman rm "$CONTAINER_NAME" 2>/dev/null || true

# Pull the official HA image
echo "Pulling Home Assistant image..."
podman pull "$HA_IMAGE" 2>&1 | tail -1

# Start container - the official HA image runs HA by default
# We mount our project at /config so HA can find the custom_components
# The entrypoint of the HA image will start HA automatically
echo "Starting container..."
podman run -d \
  --name "$CONTAINER_NAME" \
  -p 8123:8123 \
  -v "$PROJECT_DIR:/config:Z" \
  -e TZ=Europe/Brussels \
  "$HA_IMAGE"

# Wait for HA to start up
echo "Waiting for Home Assistant to start..."
sleep 45

# Verify it's running
echo "Checking container status..."
podman ps --filter "name=$CONTAINER_NAME" --format "{{.Names}} {{.Status}}"

echo ""
echo "=========================================="
echo "Home Assistant should be running!"
echo "Access at: http://localhost:8123"
echo "=========================================="
echo ""
echo "Container: $CONTAINER_NAME"
echo "To view logs: podman logs -f $CONTAINER_NAME"
echo "To stop: podman stop $CONTAINER_NAME"
echo ""
echo "The integration is automatically loaded from:"
echo "  /config/custom_components/engie_be"
echo ""
