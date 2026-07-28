#!/usr/bin/env bash

# restart-dev-container.sh - Restart the hass-engie-be development container
# Usage: ./scripts/restart-dev-container.sh [container_name]
#
# Starts the official Home Assistant container, at the version pinned in
# requirements.txt, with the integration and blueprints mounted read-write.
# HA state persists across restarts in the gitignored dev-config/ directory.

set -e

CONTAINER_NAME="${1:-engie_be_dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if ! command -v podman >/dev/null 2>&1; then
    echo "Error: podman is not installed."
    echo "Install it first, see https://podman.io/docs/installation"
    exit 1
fi

REQUIREMENTS_FILE="$PROJECT_DIR/requirements.txt"
HA_VERSION="$(grep -E '^homeassistant==' "$REQUIREMENTS_FILE" | sed -E 's/^homeassistant==([0-9.]+).*/\1/')"

if [[ -z "$HA_VERSION" ]]; then
    echo "Error: could not parse a homeassistant== pin from $REQUIREMENTS_FILE."
    echo "Expected a line like homeassistant==2026.7.4"
    exit 1
fi

HA_IMAGE="ghcr.io/home-assistant/home-assistant:${HA_VERSION}"

DEV_CONFIG_DIR="$PROJECT_DIR/dev-config"
INTEGRATION_DIR="$PROJECT_DIR/custom_components/engie_be"
BLUEPRINTS_DIR="$PROJECT_DIR/blueprints/automation/DaanVervacke"

echo "=========================================="
echo "Restarting HA dev container: $CONTAINER_NAME"
echo "Home Assistant: $HA_VERSION"
echo "Project: $PROJECT_DIR"
echo "=========================================="

if [[ ! -d "$DEV_CONFIG_DIR" ]]; then
    echo "Creating $DEV_CONFIG_DIR..."
    mkdir -p "$DEV_CONFIG_DIR"
fi

CONFIGURATION_FILE="$DEV_CONFIG_DIR/configuration.yaml"
if [[ ! -f "$CONFIGURATION_FILE" ]]; then
    echo "Seeding $CONFIGURATION_FILE..."
    cat > "$CONFIGURATION_FILE" <<'EOF'
default_config:

logger:
  default: info
  logs:
    custom_components.engie_be: debug
EOF
fi

# Stop and remove any existing container so this script is safe to re-run.
echo "Stopping existing container..."
podman stop "$CONTAINER_NAME" 2>/dev/null || true
podman rm "$CONTAINER_NAME" 2>/dev/null || true

echo "Pulling Home Assistant image..."
podman pull "$HA_IMAGE" 2>&1 | tail -1

# dev-config/ becomes /config, the integration and our blueprints are
# mounted on top of it so HA state stays out of the working tree.
echo "Starting container..."
podman run -d \
  --name "$CONTAINER_NAME" \
  -p 8123:8123 \
  -v "$DEV_CONFIG_DIR:/config:Z" \
  -v "$INTEGRATION_DIR:/config/custom_components/engie_be:Z" \
  -v "$BLUEPRINTS_DIR:/config/blueprints/automation/DaanVervacke:Z" \
  -e TZ=Europe/Brussels \
  "$HA_IMAGE"

echo "Waiting for Home Assistant to become ready..."
READY_TIMEOUT=120
ELAPSED=0
until curl --silent --output /dev/null --fail "http://localhost:8123"; do
    if [[ "$ELAPSED" -ge "$READY_TIMEOUT" ]]; then
        echo ""
        echo "Error: Home Assistant did not respond on http://localhost:8123 within ${READY_TIMEOUT}s."
        echo "Check the container logs with: podman logs $CONTAINER_NAME"
        exit 1
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

echo "Home Assistant is ready."
echo ""
echo "=========================================="
echo "Home Assistant is running!"
echo "Access at: http://localhost:8123"
echo "=========================================="
echo ""
echo "Container: $CONTAINER_NAME"
echo "To view logs: podman logs -f $CONTAINER_NAME"
echo "To stop: podman stop $CONTAINER_NAME"
echo ""
echo "The integration is loaded from:"
echo "  $INTEGRATION_DIR"
echo "Blueprints are loaded from:"
echo "  $BLUEPRINTS_DIR"
echo "Home Assistant state persists in:"
echo "  $DEV_CONFIG_DIR"
echo ""
