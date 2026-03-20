#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
SHIM_DIR=${SHIM_DIR:-"$HOME/.local/bin"}
SKILLCTL_REPO_URL=${SKILLCTL_REPO_URL:-""}
SKILLCTL_REPO_REF=${SKILLCTL_REPO_REF:-main}
SKILLCTL_INSTALL_DIR=${SKILLCTL_INSTALL_DIR:-"$HOME/.local/share/skillctl"}

ensure_repo() {
  if [ -f "${SCRIPT_DIR}/pyproject.toml" ] && [ -d "${SCRIPT_DIR}/skillctl" ]; then
    printf '%s\n' "${SCRIPT_DIR}"
    return
  fi

  if [ -z "${SKILLCTL_REPO_URL}" ]; then
    echo "[skillctl] Not running from repo root, and SKILLCTL_REPO_URL is not set." >&2
    echo "[skillctl] Set SKILLCTL_REPO_URL=https://github.com/<you>/<repo>.git and rerun." >&2
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "[skillctl] git is required for remote install mode." >&2
    exit 1
  fi

  if [ -d "${SKILLCTL_INSTALL_DIR}/.git" ]; then
    echo "[skillctl] Updating existing clone in ${SKILLCTL_INSTALL_DIR}"
    git -C "${SKILLCTL_INSTALL_DIR}" fetch --depth 1 origin "${SKILLCTL_REPO_REF}"
    git -C "${SKILLCTL_INSTALL_DIR}" checkout -f FETCH_HEAD
  else
    echo "[skillctl] Cloning ${SKILLCTL_REPO_URL} into ${SKILLCTL_INSTALL_DIR}"
    rm -rf "${SKILLCTL_INSTALL_DIR}"
    mkdir -p "$(dirname "${SKILLCTL_INSTALL_DIR}")"
    git clone --depth 1 --branch "${SKILLCTL_REPO_REF}" "${SKILLCTL_REPO_URL}" "${SKILLCTL_INSTALL_DIR}"
  fi

  printf '%s\n' "${SKILLCTL_INSTALL_DIR}"
}

REPO_DIR=$(ensure_repo)
PYTHONPATH_PREFIX="${REPO_DIR}${PYTHONPATH+:$PYTHONPATH}"

echo "[skillctl] Validating CLI binaries for shim installation"
PYTHONPATH="${PYTHONPATH_PREFIX}" "${PYTHON_BIN}" -m skillctl shim check --dir "${SHIM_DIR}"
echo "[skillctl] Note: if a real CLI binary already occupies ${SHIM_DIR}/<name>, that provider will be skipped."

echo "[skillctl] Installing package with ${PYTHON_BIN} -m pip install -e ."
"${PYTHON_BIN}" -m pip install -e "${REPO_DIR}"

echo "[skillctl] Rebuilding skill index"
PYTHONPATH="${PYTHONPATH_PREFIX}" "${PYTHON_BIN}" -m skillctl index rebuild

echo "[skillctl] Installing shims into ${SHIM_DIR}"
PYTHONPATH="${PYTHONPATH_PREFIX}" "${PYTHON_BIN}" -m skillctl shim install --dir "${SHIM_DIR}"

echo "[skillctl] Installation complete"
echo "[skillctl] If ${SHIM_DIR} is not on PATH, add:"
echo "export PATH=\"${SHIM_DIR}:\$PATH\""
