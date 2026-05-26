#!/bin/bash
# create_container.sh — Run on remote host to create a dev container.
# Required environment variables:
#   CONTAINER_NAME  — e.g. dev_container_zhangsan
#   IMAGE           — e.g. openeuler/openeuler:24.03-lts-sp3
#   CPUSET_CPUS     — e.g. 48-63
#   MEMORY          — e.g. 32g
#   HOST_PORT       — e.g. 2222
#   USERNAME        — username for naming committed image
#   PUBKEY          — SSH public key string (ed25519)
#   INSTALL_TOOLS   — if "true", install dev tools (gcc, g++, python3, git, vim, etc.)
# Usage:
#   ssh <host> 'VAR1=val1 VAR2=val2 ... bash -s' < create_container.sh

set -e

# Validate required env vars
for var in CONTAINER_NAME IMAGE CPUSET_CPUS MEMORY HOST_PORT USERNAME PUBKEY; do
    if [ -z "${!var}" ]; then
        echo "ERROR: $var is not set"
        exit 1
    fi
done

echo "=== Step 1: Check image: $IMAGE ==="
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Image $IMAGE already exists locally, skip pull."
else
    echo "Image not found locally, pulling..."
    docker pull "$IMAGE"
fi

# Clean up any previous failed attempt
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

echo "=== Step 2: Create container ==="
docker create \
    --name "$CONTAINER_NAME" \
    --cpuset-cpus "$CPUSET_CPUS" \
    --memory="$MEMORY" \
    --memory-swap="$MEMORY" \
    --cpus "$(echo "$CPUSET_CPUS" | awk -F'-' '{print $2-$1+1}')" \
    --hostname "$CONTAINER_NAME" \
    --restart=unless-stopped \
    -p "$HOST_PORT":22 \
    "$IMAGE" \
    sh -c 'while true; do sleep 3600; done'

echo "=== Step 3: Start container ==="
docker start "$CONTAINER_NAME"
sleep 3

# Verify it's running
if ! docker ps --filter "name=$CONTAINER_NAME" --format '{{.Names}}' | grep -q "$CONTAINER_NAME"; then
    echo "ERROR: Container failed to start"
    docker logs "$CONTAINER_NAME" 2>&1 || true
    exit 1
fi

echo "=== Step 4: Check and install required packages ==="
OS_TYPE=$(docker exec "$CONTAINER_NAME" sh -c 'cat /etc/os-release 2>/dev/null | grep "^ID=" | cut -d= -f2 | tr -d "\""' 2>/dev/null || echo "unknown")
echo "Detected OS: $OS_TYPE"

# Check if sshd already exists in the container
HAS_SSHD=$(docker exec "$CONTAINER_NAME" sh -c 'command -v sshd 2>/dev/null || echo ""')
if [ -n "$HAS_SSHD" ]; then
    echo "sshd already exists ($HAS_SSHD), skip openssh-server install."
else
    echo "sshd not found, installing openssh-server..."
    case "$OS_TYPE" in
        openeuler|openEuler|centos|fedora|rhel|anolis)
            docker exec "$CONTAINER_NAME" dnf install -y openssh-server 2>&1 | tail -3 ;;
        ubuntu|debian)
            docker exec "$CONTAINER_NAME" apt-get update 2>&1 | tail -3
            docker exec "$CONTAINER_NAME" apt-get install -y openssh-server 2>&1 | tail -3 ;;
        *)
            docker exec "$CONTAINER_NAME" sh -c 'command -v dnf && dnf install -y openssh-server || (apt-get update && apt-get install -y openssh-server)' 2>&1 | tail -5 ;;
    esac
fi

if [ "${INSTALL_TOOLS:-false}" = "true" ]; then
    echo "=== Step 4b: Check and install dev tools ==="
    TOOLS_MISSING=""
    for tool in gcc make cmake python3 git vim; do
        if ! docker exec "$CONTAINER_NAME" sh -c "command -v $tool 2>/dev/null" >/dev/null 2>&1; then
            TOOLS_MISSING="$TOOLS_MISSING $tool"
        fi
    done
    if [ -z "$TOOLS_MISSING" ]; then
        echo "All dev tools already present, skip install."
    else
        echo "Missing tools:$TOOLS_MISSING, installing..."
        case "$OS_TYPE" in
            openeuler|openEuler|centos|fedora|rhel|anolis)
                docker exec "$CONTAINER_NAME" dnf install -y hostname vim git curl wget gcc gcc-c++ make cmake python3 python3-devel 2>&1 | tail -5
                docker exec "$CONTAINER_NAME" dnf install -y golang 2>&1 | tail -3 || echo "golang not available in repo, skipping" ;;
            ubuntu|debian)
                docker exec "$CONTAINER_NAME" apt-get install -y hostname vim git curl wget gcc g++ make cmake python3 python3-dev golang 2>&1 | tail -5 ;;
            *)
                docker exec "$CONTAINER_NAME" sh -c 'command -v dnf && dnf install -y hostname vim git curl wget gcc gcc-c++ make cmake python3 python3-devel || (apt-get update && apt-get install -y hostname vim git curl wget gcc g++ make cmake python3 python3-dev)' 2>&1 | tail -10 ;;
        esac
    fi
fi

echo "=== Step 5: Generate SSH host keys ==="
docker exec "$CONTAINER_NAME" ssh-keygen -A 2>&1

echo "=== Step 6: Configure SSH ==="
# Inject public key
docker exec "$CONTAINER_NAME" mkdir -p /root/.ssh
echo "$PUBKEY" | docker exec -i "$CONTAINER_NAME" sh -c 'cat > /root/.ssh/authorized_keys'
docker exec "$CONTAINER_NAME" chmod 700 /root/.ssh
docker exec "$CONTAINER_NAME" chmod 600 /root/.ssh/authorized_keys

# Configure sshd
docker exec "$CONTAINER_NAME" mkdir -p /etc/ssh/sshd_config.d
docker exec "$CONTAINER_NAME" bash -c "cat > /etc/ssh/sshd_config.d/dev.conf" << 'SSHCONF'
Port 22
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
UsePAM no
SSHCONF

# Restart sshd to pick up config
docker exec "$CONTAINER_NAME" pkill sshd 2>/dev/null || true
sleep 1
docker exec -d "$CONTAINER_NAME" /usr/sbin/sshd

echo "=== Step 7: Commit container to preserve state ==="
docker commit "$CONTAINER_NAME" "dev-${USERNAME}:latest"

echo "=== Step 8: Recreate with auto-start entrypoint ==="

# Create entrypoint script path inside the committed image
ENTRYPOINT_SCRIPT="/entrypoint.sh"

# Stop and remove old container
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

# Recreate from committed image with entrypoint that starts sshd
docker create \
    --name "$CONTAINER_NAME" \
    --cpuset-cpus "$CPUSET_CPUS" \
    --memory="$MEMORY" \
    --memory-swap="$MEMORY" \
    --cpus "$(echo "$CPUSET_CPUS" | awk -F'-' '{print $2-$1+1}')" \
    --hostname "$CONTAINER_NAME" \
    --restart=unless-stopped \
    -p "$HOST_PORT":22 \
    "dev-${USERNAME}:latest" \
    sh -c '/usr/sbin/sshd; while true; do sleep 3600; done'

echo "=== Step 9: Final start ==="
docker start "$CONTAINER_NAME"
sleep 3

# Final verification
if docker ps --filter "name=$CONTAINER_NAME" --format '{{.Names}}' | grep -q "$CONTAINER_NAME"; then
    echo "=== SUCCESS: Container $CONTAINER_NAME is running ==="
    echo "SSH accessible at port $HOST_PORT"
else
    echo "=== FAILED: Container did not start ==="
    docker logs "$CONTAINER_NAME" 2>&1 || true
    exit 1
fi
