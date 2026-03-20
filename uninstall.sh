#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
SHIM_DIR=${SHIM_DIR:-"$HOME/.local/bin"}
REMOVE_CACHE=${REMOVE_CACHE:-0}
SKILLCTL_REPO_URL=${SKILLCTL_REPO_URL:-""}
SKILLCTL_REPO_REF=${SKILLCTL_REPO_REF:-main}
SKILLCTL_INSTALL_DIR=${SKILLCTL_INSTALL_DIR:-"$HOME/.local/share/skillctl"}

ensure_repo() {
  if [ -f "${SCRIPT_DIR}/pyproject.toml" ] && [ -d "${SCRIPT_DIR}/skillctl" ]; then
    printf '%s\n' "${SCRIPT_DIR}"
    return
  fi

  if [ -d "${SKILLCTL_INSTALL_DIR}/.git" ]; then
    printf '%s\n' "${SKILLCTL_INSTALL_DIR}"
    return
  fi

  if [ -z "${SKILLCTL_REPO_URL}" ]; then
    echo "[skillctl] Local repo not found, and SKILLCTL_REPO_URL is not set." >&2
    echo "[skillctl] Set SKILLCTL_REPO_URL=https://github.com/<you>/<repo>.git and rerun." >&2
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "[skillctl] git is required for remote uninstall mode." >&2
    exit 1
  fi

  echo "[skillctl] Cloning ${SKILLCTL_REPO_URL} into ${SKILLCTL_INSTALL_DIR} for uninstall"
  rm -rf "${SKILLCTL_INSTALL_DIR}"
  mkdir -p "$(dirname "${SKILLCTL_INSTALL_DIR}")"
  git clone --depth 1 --branch "${SKILLCTL_REPO_REF}" "${SKILLCTL_REPO_URL}" "${SKILLCTL_INSTALL_DIR}"
  printf '%s\n' "${SKILLCTL_INSTALL_DIR}"
}

REPO_DIR=$(ensure_repo)
PYTHONPATH_PREFIX="${REPO_DIR}${PYTHONPATH+:$PYTHONPATH}"

echo "[skillctl] Removing shims from ${SHIM_DIR}"
if PYTHONPATH="${PYTHONPATH_PREFIX}" "${PYTHON_BIN}" -m skillctl shim remove --dir "${SHIM_DIR}" 2>/dev/null; then
  :
else
  # Standalone fallback: remove shim files that match the skillctl pattern
  # without requiring the Python package to be importable.
  echo "[skillctl] Python module unavailable, removing shims manually"
  for cli in claude codex gemini; do
    shim="${SHIM_DIR}/${cli}"
    if [ -f "${shim}" ] && grep -q "SKILLCTL_REAL_" "${shim}" 2>/dev/null && grep -q "\-m skillctl " "${shim}" 2>/dev/null; then
      rm -f "${shim}"
      echo "[skillctl] Removed ${shim}"
    fi
  done
fi

echo "[skillctl] Uninstalling Python package"
"${PYTHON_BIN}" -m pip uninstall -y skillctl || true

if [ "${REMOVE_CACHE}" = "1" ]; then
  CACHE_DIR="${REPO_DIR}/.skillctl"
  echo "[skillctl] Removing install-repo cache directory ${CACHE_DIR}"
  echo "[skillctl] Note: project-local .skillctl caches created in other workspaces are not removed automatically."
  rm -rf "${CACHE_DIR}"
fi

echo "[skillctl] Uninstall complete"
