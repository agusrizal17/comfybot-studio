#!/usr/bin/env bash
# ============================================================
# ComfyBot Studio — installer model EDIT KHUSUS Qwen-Image-Edit-2511
# ------------------------------------------------------------
# Model edit beneran (bukan turbo): jauh lebih patuh instruksi
# adegan, tapi lebih lambat. Bot ComfyBot Studio v2.3+ memakai
# model ini khusus untuk job "Edit foto" / "Gabung foto";
# generate gambar (T2I) tetap pakai stack turbo yang cepat.
#
# Yang dipasang (±27,6 GB total):
#   diffusion_models/  qwen-image-edit-2511-Q6_K.gguf        (17,9 GB)
#                      repo unsloth/Qwen-Image-Edit-2511-GGUF
#   text_encoders/     qwen_2.5_vl_7b_fp8_scaled.safetensors (9,4 GB)
#                      repo Comfy-Org/Qwen-Image (split_files)
#   vae/               qwen_image_vae.safetensors            (0,25 GB)
#
# Resep (template resmi ComfyUI image_qwen_image_edit_2511):
#   ModelSamplingAuraFlow shift 3.1, KSampler 20 step CFG 3,
#   euler/simple, TextEncodeQwenImageEditPlus +
#   FluxKontextMultiReferenceLatentMethod (index_timestep_zero).
#   Q6_K dipilih (bukan Q8_0 22,4 GB) supaya muat di V100 32 GB
#   bareng encoder 9,4 GB.
#
# Script ini AMAN dijalankan ulang: file yang ukurannya sudah
# pas dilewati; file kepotong dilanjutkan (wget -c).
# Setelah selesai: restart ComfyUI (sudo systemctl restart
# comfyui.service) lalu redeploy bot-nya.
#
# Cara pakai:
#   curl -fsSL https://raw.githubusercontent.com/agusrizal17/comfybot-studio/main/install/install-qwen-edit.sh | bash
# ============================================================
set -euo pipefail

COMFY="${COMFY_DIR:-$HOME/ComfyUI}"
UE="https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF/resolve/main"
CO="https://huggingface.co/Comfy-Org/Qwen-Image/resolve/main/split_files"

dl() {  # dl <url> <tujuan>
  local url="$1" dest="$2" want got
  want=$(curl -sIL "$url" | grep -i '^content-length' | tail -1 | tr -dc '0-9')
  if [ -f "$dest" ]; then
    got=$(stat -c%s "$dest")
    if [ -n "$want" ] && [ "$got" = "$want" ]; then
      echo "SKIP (sudah lengkap): $dest"
      return
    fi
    echo "Lanjut unduh (ada $got dari $want byte): $dest"
  fi
  mkdir -p "$(dirname "$dest")"
  wget -c --progress=bar:force -O "$dest" "$url"
}

echo "=== Model edit Qwen-Image-Edit-2511 -> $COMFY/models ==="

dl "$UE/qwen-image-edit-2511-Q6_K.gguf" \
   "$COMFY/models/diffusion_models/qwen-image-edit-2511-Q6_K.gguf"
dl "$CO/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors" \
   "$COMFY/models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
dl "$CO/vae/qwen_image_vae.safetensors" \
   "$COMFY/models/vae/qwen_image_vae.safetensors"

echo
echo "=== SELESAI. Isi folder model sekarang: ==="
ls -la "$COMFY/models/diffusion_models" | grep -i edit || true
ls -la "$COMFY/models/text_encoders" | grep -i 2.5_vl || true
ls -la "$COMFY/models/vae" | grep -i qwen || true
echo
echo "Langkah berikutnya:"
echo "  sudo systemctl restart comfyui.service"
echo "  (lalu redeploy bot ComfyBot Studio v2.3+)"
