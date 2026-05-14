#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

: "${DB_NAME:=CCCS105}"
: "${DB_USER:=cccs105_user}"
: "${DB_PASSWORD:=cccs105_pass}"

if [[ "${DB_ENGINE:-mysql}" != "mysql" ]]; then
  echo "DB_ENGINE is not mysql in .env; skipping MariaDB setup." >&2
  exit 1
fi

echo "Installing MariaDB packages if needed..."
sudo pacman -S --needed mariadb mariadb-libs base-devel

if [[ ! -d /var/lib/mysql/mysql ]]; then
  echo "Initializing MariaDB data directory..."
  sudo mariadb-install-db --user=mysql --basedir=/usr --datadir=/var/lib/mysql
fi

echo "Starting MariaDB service..."
sudo systemctl enable --now mariadb

escaped_password=${DB_PASSWORD//\'/\'\'}

echo "Creating database..."
sudo mariadb <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
SQL

if [[ "${DB_USER}" != "root" ]]; then
  echo "Creating application database user..."
  sudo mariadb <<SQL
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${escaped_password}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${escaped_password}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${escaped_password}';
ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${escaped_password}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
else
  echo "Using existing root database account; skipping user creation."
fi

echo "Testing application database login..."
mariadb --host="${DB_HOST:-127.0.0.1}" --port="${DB_PORT:-3306}" --user="${DB_USER}" --password="${DB_PASSWORD}" "${DB_NAME}" -e "SELECT DATABASE() AS database_name;"

echo "MariaDB is installed, running, and configured for ${DB_NAME}."
