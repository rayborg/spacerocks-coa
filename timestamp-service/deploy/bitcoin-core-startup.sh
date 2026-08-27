#!/bin/bash
set -euo pipefail

exec >>/var/log/bitcoin-core-bootstrap.log 2>&1

readonly VERSION="31.1"
readonly ARCHIVE="bitcoin-${VERSION}-x86_64-linux-gnu.tar.gz"
readonly ARCHIVE_SHA256="b80d9c3e04da78fb6f0569685673418cf686fadba9042d926d13fb87ff503f9e"
readonly SIGNER_FINGERPRINT="E777299FC265DD04793070EB944D35F9AC3DB76A"
readonly DATA_DEVICE="/dev/disk/by-id/google-bitcoin-core-data-prod"
readonly DATA_DIR="/var/lib/bitcoin"

metadata() {
  curl --fail --silent --show-error \
    -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --yes --no-install-recommends ca-certificates curl gnupg

if ! id bitcoin >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin bitcoin
fi

for _ in $(seq 1 60); do
  [[ -e "$DATA_DEVICE" ]] && break
  sleep 2
done
[[ -e "$DATA_DEVICE" ]]

if ! blkid "$DATA_DEVICE" >/dev/null 2>&1; then
  mkfs.ext4 -F -m 0 "$DATA_DEVICE"
fi
mkdir -p "$DATA_DIR"
if ! grep -qF "$DATA_DEVICE $DATA_DIR ext4" /etc/fstab; then
  printf '%s %s ext4 defaults,nofail 0 2\n' "$DATA_DEVICE" "$DATA_DIR" >>/etc/fstab
fi
mountpoint -q "$DATA_DIR" || mount "$DATA_DIR"
chown bitcoin:bitcoin "$DATA_DIR"
chmod 750 "$DATA_DIR"

DOWNLOAD_DIR="$(mktemp -d)"
readonly DOWNLOAD_DIR
trap 'rm -rf "$DOWNLOAD_DIR"' EXIT
cd "$DOWNLOAD_DIR"
curl --fail --silent --show-error --location --remote-name \
  "https://bitcoincore.org/bin/bitcoin-core-${VERSION}/${ARCHIVE}"
printf '%s  %s\n' "$ARCHIVE_SHA256" "$ARCHIVE" | sha256sum --check --strict -
curl --fail --silent --show-error --location --remote-name \
  "https://bitcoincore.org/bin/bitcoin-core-${VERSION}/SHA256SUMS"
curl --fail --silent --show-error --location --remote-name \
  "https://bitcoincore.org/bin/bitcoin-core-${VERSION}/SHA256SUMS.asc"
curl --fail --silent --show-error --location --output fanquake.gpg \
  "https://raw.githubusercontent.com/bitcoin-core/guix.sigs/main/builder-keys/fanquake.gpg"
gpg --batch --import fanquake.gpg
gpg --batch --with-colons --fingerprint "$SIGNER_FINGERPRINT" | \
  grep -q "^fpr:::::::::${SIGNER_FINGERPRINT}:$"
set +o pipefail
gpg --batch --status-fd 1 --verify SHA256SUMS.asc SHA256SUMS | \
  grep -Eq "^\[GNUPG:\] VALIDSIG [A-F0-9]{40} .* ${SIGNER_FINGERPRINT}$"
signature_valid=$?
set -o pipefail
[[ "$signature_valid" -eq 0 ]]

tar --extract --gzip --file "$ARCHIVE"
install -m 0755 "bitcoin-${VERSION}/bin/bitcoind" /usr/local/bin/bitcoind
install -m 0755 "bitcoin-${VERSION}/bin/bitcoin-cli" /usr/local/bin/bitcoin-cli

install -d -m 0750 -o root -g bitcoin /etc/bitcoin
cat >/etc/bitcoin/bitcoin.conf <<EOF
server=1
disablewallet=1
prune=20000
dbcache=1024
maxconnections=40
maxmempool=100
mempoolexpiry=24
listen=1
listenonion=0
onlynet=ipv4
rpcbind=0.0.0.0
rpcallowip=10.128.0.0/20
rpcauth=$(metadata bitcoin-rpcauth)
EOF
chown root:bitcoin /etc/bitcoin/bitcoin.conf
chmod 0640 /etc/bitcoin/bitcoin.conf

cat >/etc/systemd/system/bitcoind.service <<EOF
[Unit]
Description=Bitcoin Core daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=bitcoin
Group=bitcoin
ExecStart=/usr/local/bin/bitcoind -conf=/etc/bitcoin/bitcoin.conf -datadir=${DATA_DIR}
Restart=on-failure
RestartSec=10
TimeoutStopSec=300
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=${DATA_DIR}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now bitcoind
