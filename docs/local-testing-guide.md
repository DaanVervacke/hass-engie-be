# Local Testing Guide

**Documentation for running tests when Home Assistant versions are not yet on PyPI**

---

## Problem Statement

Home Assistant releases (e.g., 2026.7.1) may not be immediately available on PyPI after release. This prevents local test execution using `requirements.txt` since pip cannot resolve the dependency.

**Symptom:**
```bash
$ pip install -r requirements.txt
ERROR: Could not find a version that satisfies the requirement homeassistant==2026.7.1
```

**Impact:**
- Cannot run tests locally using the standard `.venv` approach
- CI works because it uses GitHub Actions with pre-built containers
- Local development is blocked until HA publishes to PyPI

---

## Solution: Use the Dev Container

The project uses a **podman-based development container** that has Home Assistant pre-installed. This container matches the CI environment and allows tests to run locally regardless of PyPI availability.

---

## Prerequisites

1. **Podman** (or Docker) installed on the host machine
   ```bash
   # macOS
   brew install podman
   
   # Linux
   sudo apt-get install podman
   ```

2. **Container is running** with the correct HA version
   ```bash
   podman ps -a | grep engie_be_dev
   ```
   If not running, start it:
   ```bash
   ./scripts/restart-dev-container.sh
   ```

---

## Quick Start

### 1. Ensure dev container is running
```bash
# Check if container exists and is running
podman ps -a | grep engie_be_dev

# If not, start it (uses HA 2026.7.1 from Ghcr)
./scripts/restart-dev-container.sh
```

### 2. Install test dependencies in container (one-time setup)
```bash
podman exec engie_be_dev pip install \
  "pytest-homeassistant-custom-component @ git+https://github.com/MatthewFlamm/pytest-homeassistant-custom-component@0.13.345"
```

### 3. Run tests inside container
```bash
# Full test suite with coverage
podman exec engie_be_dev python -m pytest tests/ \
  -v --tb=short \
  --cov=custom_components.engie_be \
  --cov-report=term \
  --cov-fail-under=95

# Quick run (less verbose)
podman exec engie_be_dev python -m pytest tests/ \
  --tb=short \
  --cov=custom_components.engie_be \
  --cov-report=term \
  --cov-fail-under=95 \
  -q

# Run specific test file
podman exec engie_be_dev python -m pytest tests/test_trigger.py -v

# Run tests matching a pattern
podman exec engie_be_dev python -m pytest tests/ -k "quarter" -v
```

---

## Understanding the Setup

### Container Configuration

The dev container is defined by:
- **Image:** `ghcr.io/home-assistant/home-assistant:2026.7.1`
- **Name:** `engie_be_dev` (default)
- **Port:** 8123 (HA web UI)
- **Volume:** Project directory mounted at `/config`

See `scripts/restart-dev-container.sh` for full configuration.

### Why This Works

1. **HA pre-installed:** The container has Home Assistant 2026.7.1 already installed
2. **Python 3.14:** Matches the CI environment (Python 3.14.6)
3. **Isolated environment:** No dependency conflicts with host Python
4. **Persistent:** Once set up, the container persists across sessions

---

## Common Commands

### Container Management

| Command | Description |
|---------|-------------|
| `podman ps -a` | List all containers (running and stopped) |
| `podman start engie_be_dev` | Start existing container |
| `podman stop engie_be_dev` | Stop running container |
| `podman rm engie_be_dev` | Remove container (loses installed packages) |
| `podman exec engie_be_dev bash` | Open shell in container |
| `./scripts/restart-dev-container.sh` | Full restart (stop, remove, recreate) |

### Test Commands

| Command | Description |
|---------|-------------|
| `podman exec engie_be_dev python -m pytest tests/ -v` | Run all tests verbose |
| `podman exec engie_be_dev python -m pytest tests/ -q` | Run all tests quiet |
| `podman exec engie_be_dev python -m pytest tests/test_trigger.py -v` | Run trigger tests |
| `podman exec engie_be_dev python -m pytest tests/ -k "epex" -v` | Run EPEX tests |
| `podman exec engie_be_dev python -m pytest tests/ -k "quarter" -v` | Run quarter-hourly tests |

### Package Management in Container

| Command | Description |
|---------|-------------|
| `podman exec engie_be_dev pip list` | List installed packages |
| `podman exec engie_be_dev pip install <package>` | Install package |
| `podman exec engie_be_dev pip install -r requirements.txt` | Install all requirements |
| `podman exec engie_be_dev pip show homeassistant` | Check HA version |

---

## Troubleshooting

### Container not found
```bash
$ podman ps -a | grep engie
# No output

# Solution: Start the container
./scripts/restart-dev-container.sh
```

### Container stopped
```bash
$ podman ps | grep engie
# No output (container exists but is stopped)

# Solution: Start it
podman start engie_be_dev
```

### pytest-homeassistant-custom-component not installed
```bash
$ podman exec engie_be_dev python -m pytest tests/
ImportError: No module named 'pytest_homeassistant_custom_component'

# Solution: Install it
podman exec engie_be_dev pip install \
  "pytest-homeassistant-custom-component @ git+https://github.com/MatthewFlamm/pytest-homeassistant-custom-component@0.13.345"
```

### Coverage missing
```bash
$ podman exec engie_be_dev python -m pytest tests/ --cov=custom_components.engie_be
ImportError: No module named 'coverage'

# Solution: Install coverage
podman exec engie_be_dev pip install coverage pytest-cov
```

### Port already in use
```bash
$ ./scripts/restart-dev-container.sh
Error: port 8123 already in use

# Solution: Free the port or use a different one
podman stop engie_be_dev 2>/dev/null || true
./scripts/restart-dev-container.sh my_engie_dev 8124
```

---

## Linting

Linting does **not** require Home Assistant, so it can run directly in the host `.venv`:

```bash
# Standard linting
./scripts/lint

# Or directly
.venv/bin/ruff check custom_components/engie_be/ tests/
```

---

## Best Practices

### 1. Prefer container for tests
Always use the dev container for running tests to ensure consistency with CI.

### 2. Keep container updated
Periodically rebuild the container to pick up new HA versions:
```bash
./scripts/restart-dev-container.sh
```

### 3. Install dependencies once
After creating a new container, install test dependencies:
```bash
podman exec engie_be_dev pip install \
  "pytest-homeassistant-custom-component @ git+https://github.com/MatthewFlamm/pytest-homeassistant-custom-component@0.13.345"
```

### 4. Use container for HA-specific tasks
Any task requiring Home Assistant (tests, running HA, etc.) should use the container.

### 5. Use host venv for non-HA tasks
Tasks not requiring HA (linting, type checking with mypy, etc.) can use the host `.venv`.

---

## CI vs Local Comparison

| Environment | Home Assistant | Python | pytest-hacc |
|-------------|---------------|--------|-------------|
| CI (GitHub Actions) | 2026.7.1 (from PyPI) | 3.14 | 0.13.345 |
| Local (container) | 2026.7.1 (from Ghcr) | 3.14 | 0.13.345 |
| Local (.venv) | Not installed | 3.9 | Not installed |

**Key insight:** The container matches CI, so test results are consistent.

---

## Version Mapping

When HA version in `requirements.txt` doesn't match container version:

| requirements.txt | Container HA | pytest-hacc | Action |
|----------------|--------------|--------------|--------|
| 2026.7.1 | 2026.7.1 | 0.13.345 | ✅ Works |
| 2026.7.1 | 2026.6.1 | 0.13.345 | ❌ May fail |
| 2026.7.1 | 2026.7.1 | 0.13.337 | ⚠️ Works but outdated |

**Solution:** Update the container to match requirements.txt:
```bash
# Update HA_VERSION in restart script
sed -i 's/HA_VERSION=.*/HA_VERSION="2026.7.1"/' scripts/restart-dev-container.sh
./scripts/restart-dev-container.sh
```

---

## Automating the Workflow

### Script: Run tests in container
Create `scripts/test-container`:
```bash
#!/usr/bin/env bash
set -e

echo "Running tests in dev container..."
podman exec engie_be_dev python -m pytest tests/ \
  --tb=short \
  --cov=custom_components.engie_be \
  --cov-report=term \
  --cov-fail-under=95 \
  "$@"
```

Make it executable:
```bash
chmod +x scripts/test-container
```

Usage:
```bash
./scripts/test-container          # Run all tests
./scripts/test-container -v      # Verbose
./scripts/test-container -k epex  # Filter by pattern
```

### Script: Setup container for testing
Create `scripts/setup-test-container`:
```bash
#!/usr/bin/env bash
set -e

echo "Setting up dev container for testing..."

# Ensure container is running
if ! podman ps | grep -q engie_be_dev; then
    echo "Container not running. Starting..."
    ./scripts/restart-dev-container.sh
fi

# Install test dependencies
echo "Installing test dependencies..."
podman exec engie_be_dev pip install \
  "pytest-homeassistant-custom-component @ git+https://github.com/MatthewFlamm/pytest-homeassistant-custom-component@0.13.345"

echo "Setup complete!"
```

---

## Reference: Dev Container Script

The `scripts/restart-dev-container.sh` script:

```bash
#!/usr/bin/env bash

CONTAINER_NAME="${1:-engie_be_dev}"
HA_VERSION="2026.7.1"
HA_IMAGE="ghcr.io/home-assistant/home-assistant:${HA_VERSION}"

# Stop and remove existing container
podman stop "$CONTAINER_NAME" 2>/dev/null || true
podman rm "$CONTAINER_NAME" 2>/dev/null || true

# Pull the official HA image
podman pull "$HA_IMAGE"

# Run container with project mounted
podman run -d \
  --name "$CONTAINER_NAME" \
  -p 8123:8123 \
  -v "$(pwd):/config" \
  -e TZ=Europe/Brussels \
  "$HA_IMAGE"

echo "Container $CONTAINER_NAME started with HA $HA_VERSION"
```

---

## Summary

| Task | Command |
|------|---------|
| Start container | `./scripts/restart-dev-container.sh` |
| Setup tests | `podman exec engie_be_dev pip install pytest-homeassistant-custom-component...` |
| Run tests | `podman exec engie_be_dev python -m pytest tests/ --cov...` |
| Run linting | `./scripts/lint` (uses host .venv) |
| Open shell | `podman exec -it engie_be_dev bash` |

**Golden rule:** Use the dev container for anything requiring Home Assistant. Use the host venv for everything else.

---

*Last updated: 2026-07-11*
*Documentation created after successfully running 1023 tests with 96.98% coverage using podman dev container*
