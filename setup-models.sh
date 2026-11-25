#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
model_dir="${PICASSO_DATA:-.local}/models"
mkdir -p "$model_dir"
download_model() {
  local model_path="$1" checksum="$2" target temporary
  target="$model_dir/${model_path##*/}"
  if [[ -f "$target" ]] && printf '%s  %s\n' "$checksum" "$target" | sha256sum -c --status; then
    return
  fi
  temporary="$(mktemp "$model_dir/.download.XXXXXX")"
  if curl --fail --silent --show-error --location --max-time 120 -o "$temporary" \
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/$model_path" &&
    printf '%s  %s\n' "$checksum" "$temporary" | sha256sum -c; then
    mv "$temporary" "$target"
  else
    rm -f "$temporary"
    return 1
  fi
}
download_model face_detection_yunet/face_detection_yunet_2023mar.onnx 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
download_model face_recognition_sface/face_recognition_sface_2021dec.onnx 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79
