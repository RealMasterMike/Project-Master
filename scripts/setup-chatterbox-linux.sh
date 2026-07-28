#!/usr/bin/env bash
set -euo pipefail

ENGINE_SOURCE_REVISION="5de7a54aa4e5e2baadb0182dde554908b48b85c2"
TORCH_VERSION="2.9.1"
TORCH_INDEX="${PROJECT_MASTER_TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
ENGINE_ROOT="${PROJECT_MASTER_CHATTERBOX_ROOT:-${DATA_HOME}/com.master.desktop/voice-engines/chatterbox}"
SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_ROOT}/.." && pwd)"
WORKER="${PROJECT_MASTER_CHATTERBOX_WORKER:-${REPO_ROOT}/ProjectMaster-v0.1.0/src/project_master/integrations/voice/chatterbox_worker.py}"
PYTHON="${PROJECT_MASTER_PYTHON311:-$(command -v python3.11 || true)}"

if [[ -z "${PYTHON}" || ! -x "${PYTHON}" ]]; then
  echo "Python 3.11 is required. Install it, then rerun this script." >&2
  exit 1
fi
for program in git ffmpeg; do
  if ! command -v "${program}" >/dev/null 2>&1; then
    echo "${program} is required. Install it, then rerun this script." >&2
    exit 1
  fi
done
if [[ ! -f "${WORKER}" ]]; then
  echo "Chatterbox worker not found at ${WORKER}." >&2
  exit 1
fi

mkdir -p "${ENGINE_ROOT}"
if [[ ! -x "${ENGINE_ROOT}/venv/bin/python" ]]; then
  "${PYTHON}" -m venv "${ENGINE_ROOT}/venv"
fi
ENGINE_PYTHON="${ENGINE_ROOT}/venv/bin/python"

"${ENGINE_PYTHON}" -m pip install --disable-pip-version-check --upgrade pip
"${ENGINE_PYTHON}" -m pip install --disable-pip-version-check "chatterbox-tts==0.1.7"
"${ENGINE_PYTHON}" -m pip install \
  --disable-pip-version-check \
  --no-deps \
  --force-reinstall \
  "git+https://github.com/resemble-ai/chatterbox.git@${ENGINE_SOURCE_REVISION}"
"${ENGINE_PYTHON}" -m pip install \
  --disable-pip-version-check \
  --upgrade \
  "torch==${TORCH_VERSION}" \
  "torchaudio==${TORCH_VERSION}" \
  --index-url "${TORCH_INDEX}"

export PROJECT_MASTER_VOICE_ENGINE_ROOT="${ENGINE_ROOT}"
export HF_HOME="${ENGINE_ROOT}/models"
export HUGGINGFACE_HUB_CACHE="${ENGINE_ROOT}/models/hub"
export PKUSEG_HOME="${ENGINE_ROOT}/pkuseg"
"${ENGINE_PYTHON}" "${WORKER}" --prefetch

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
"${ENGINE_PYTHON}" "${WORKER}" --health

echo "Chatterbox is installed, pinned, inventoried, and ready at ${ENGINE_ROOT}."
