#!/usr/bin/env bash
# VOICEVOX ENGINE をローカルに立てる（無料・日本語特化の音声合成）
set -euo pipefail
IMAGE="voicevox/voicevox_engine:cpu-ubuntu20.04-latest"
echo "起動中: $IMAGE  (http://127.0.0.1:50021)"
echo "止めるときは Ctrl-C"
docker run --rm -p 50021:50021 "$IMAGE"
