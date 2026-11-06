#!/usr/bin/env bash
# ============================================================================
# install-hunyuan15.sh — HunyuanVideo 1.5 (GGUF) untuk ComfyUI
# Ditulis untuk: Tesla V100 SXM3 32GB (Volta, sm_70) — jalur GGUF, BUKAN FP8
# (V100 tidak mendukung FP8; file fp8_scaled dari template resmi tidak dipakai
# untuk diffusion maupun text encoder di script ini).
#
# Yang dipasang (±65 GB total):
#   diffusion_models/  hunyuanvideo1.5_720p_t2v-Q8_0.gguf            (9,0 GB)
#                      hunyuanvideo1.5_720p_i2v-Q8_0.gguf            (9,0 GB)
#                      hunyuanvideo1.5_720p_i2v_cfg_distilled-Q8_0   (9,0 GB)
#                      hunyuanvideo1.5_480p_t2v_cfg_distilled-Q8_0   (9,0 GB)
#                      hunyuanvideo1.5_480p_i2v_cfg_distilled-Q8_0   (9,0 GB)
#   text_encoders/     qwen_2.5_vl_7b.safetensors                    (16,6 GB)
#                      byt5_small_glyphxl_fp16.safetensors           (0,44 GB)
#   vae/               hunyuanvideo15_vae_fp16.safetensors           (2,5 GB)
#   clip_vision/       sigclip_vision_patch14_384.safetensors        (0,86 GB)
#
# CATATAN: checkpoint 720p dan 480p adalah fine-tune TERPISAH. Bot
# ComfyBot Studio memakai varian 480p distilled (di V100, 720p jalan
# ±490 detik/step — tidak viable; dan model 720p dipaksa di latent 480p
# hasilnya rusak). File 720p tetap dipasang buat eksperimen manual.
# I2V juga WAJIB ada file clip_vision di atas (sinyal identitas foto).
#
# Resep resmi (template ComfyUI): 1280x720, 121 frame @24fps (±5 detik),
# euler 20 step, CFG 6, ModelSamplingSD3 shift 7. Varian cfg_distilled =
# versi cepat I2V (CFG 1, ±8 step). Stage Super-Resolution 1080p dari
# template resmi SENGAJA tidak dipasang (model ekstra 16,6 GB, berat di V100).
#
# Script aman diulang (skip file yang sudah ada, wget -c resume).
# Jalankan di VM, dari home directory:   bash install-hunyuan15.sh
# ============================================================================
set -euo pipefail

COMFY="${COMFY:-$HOME/ComfyUI}"

dl() { # url dest
  local url="$1" dest="$2"
  if [ -s "$dest" ]; then echo "ADA (skip): $dest"; return; fi
  mkdir -p "$(dirname "$dest")"
  echo "Download: $url"
  wget -c -O "$dest" "$url"
}

if [ ! -d "$COMFY/models" ]; then
  echo "ERROR: folder ComfyUI tidak ditemukan di $COMFY" >&2
  echo "Set COMFY=/path/ke/ComfyUI kalau lokasinya beda." >&2
  exit 1
fi
echo "ComfyUI: $COMFY"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true

# --- 0) Update node GGUF (dukungan arsitektur hunyuanvideo15 ada di versi
#        terbaru; kalau foldernya bukan git repo, langkah ini dilewati) ---
GGUF_DIR="$COMFY/custom_nodes/ComfyUI-GGUF"
if [ -d "$GGUF_DIR/.git" ]; then
  echo "Update ComfyUI-GGUF..."
  git -C "$GGUF_DIR" pull --ff-only || echo "(pull gagal — lanjut pakai versi yang ada)"
fi

# --- 1) Diffusion models (GGUF Q8_0, repo jayn7) ---
J7="https://huggingface.co/jayn7"
dl "$J7/HunyuanVideo-1.5_T2V_720p-GGUF/resolve/main/720p/hunyuanvideo1.5_720p_t2v-Q8_0.gguf" \
   "$COMFY/models/diffusion_models/hunyuanvideo1.5_720p_t2v-Q8_0.gguf"
dl "$J7/HunyuanVideo-1.5_I2V_720p-GGUF/resolve/main/720p/hunyuanvideo1.5_720p_i2v-Q8_0.gguf" \
   "$COMFY/models/diffusion_models/hunyuanvideo1.5_720p_i2v-Q8_0.gguf"
dl "$J7/HunyuanVideo-1.5_I2V_720p-GGUF/resolve/main/720p_distilled/hunyuanvideo1.5_720p_i2v_cfg_distilled-Q8_0.gguf" \
   "$COMFY/models/diffusion_models/hunyuanvideo1.5_720p_i2v_cfg_distilled-Q8_0.gguf"

# --- 2) Text encoders (Comfy-Org, fp16 polos — aman di Volta) ---
CO="https://huggingface.co/Comfy-Org/Hunyuan-Video-1.5/resolve/main/split_files"
dl "$CO/text_encoders/qwen_2.5_vl_7b.safetensors" \
   "$COMFY/models/text_encoders/qwen_2.5_vl_7b.safetensors"
dl "$CO/text_encoders/byt5_small_glyphxl_fp16.safetensors" \
   "$COMFY/models/text_encoders/byt5_small_glyphxl_fp16.safetensors"

# --- 3) VAE ---
dl "$CO/vae/hunyuanvideo15_vae_fp16.safetensors" \
   "$COMFY/models/vae/hunyuanvideo15_vae_fp16.safetensors"

# --- 4) Varian 480p CFG-distilled (yang dipakai bot) + CLIP Vision I2V ---
dl "$J7/HunyuanVideo-1.5_T2V_480p-GGUF/resolve/main/480p_distilled/hunyuanvideo1.5_480p_t2v_cfg_distilled-Q8_0.gguf" \
   "$COMFY/models/diffusion_models/hunyuanvideo1.5_480p_t2v_cfg_distilled-Q8_0.gguf"
dl "$J7/HunyuanVideo-1.5_I2V_480p-GGUF/resolve/main/480p_distilled/hunyuanvideo1.5_480p_i2v_cfg_distilled-Q8_0.gguf" \
   "$COMFY/models/diffusion_models/hunyuanvideo1.5_480p_i2v_cfg_distilled-Q8_0.gguf"
dl "$CO/clip_vision/sigclip_vision_patch14_384.safetensors" \
   "$COMFY/models/clip_vision/sigclip_vision_patch14_384.safetensors"

echo
echo "=== SELESAI — file terpasang: ==="
ls -lh "$COMFY/models/diffusion_models/" | grep -i hunyuan || true
ls -lh "$COMFY/models/text_encoders/" | grep -iE "qwen_2.5_vl|byt5" || true
ls -lh "$COMFY/models/vae/" | grep -i hunyuan || true
echo
echo "Restart ComfyUI biar model kebaca:"
echo "  sudo systemctl restart comfyui.service"
