#!/usr/bin/env bash
# Install ComfyUI + Qwen-Image-2.1-Turbo (GGUF Q8_0) di CloudRift V100-SXM3-32GB
# Ubuntu 24.04 - disiapkan 4 Nov 2026 untuk user (VM Ubuntu 24.04 VM)
set -e
cd ~

sudo apt-get update -qq
sudo apt-get install -y -qq git python3-venv python3-dev build-essential wget

# ComfyUI
[ -d ~/ComfyUI ] || git clone https://github.com/comfyanonymous/ComfyUI.git ~/ComfyUI
cd ~/ComfyUI
[ -d venv ] || python3 -m venv venv
source venv/bin/activate
pip install -q -U pip
# Torch 2.8.0 CUDA 12.8: versi minimum yang cocok dengan comfy-kitchen ComfyUI terbaru
# dan masih mendukung V100 (Volta sm_70). JANGAN pakai cu124 (mentok torch 2.6 -> crash
# infer_schema list[int]) dan JANGAN torch 2.9+/cu130 (CUDA 13 membuang dukungan Volta).
pip install -q torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
pip install -q -r requirements.txt

# Node GGUF (fork leejet sesuai panduan repo model)
cd ~/ComfyUI/custom_nodes
[ -d ComfyUI-GGUF ] || git clone https://github.com/leejet/ComfyUI-GGUF
pip install -q -r ComfyUI-GGUF/requirements.txt

# Model files (total ~17.6 GB)
BASE=https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF/resolve/main
cd ~/ComfyUI
wget -q --show-progress -O models/diffusion_models/qwen-image-2.1-turbo-Q8_0.gguf "$BASE/qwen-image-2.1-turbo-Q8_0.gguf"
wget -q --show-progress -O models/text_encoders/qwen3vl_8b_int8_convrot.safetensors "$BASE/text_encoders/qwen3vl_8b_int8_convrot.safetensors"
wget -q --show-progress -O models/vae/qwen_image_2.1_vae_bf16.safetensors "$BASE/vae/qwen_image_2.1_vae_bf16.safetensors"

echo "=== INSTALL SELESAI ==="
