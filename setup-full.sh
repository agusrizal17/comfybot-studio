#!/usr/bin/env bash
# =============================================================================
# ComfyBot Studio — installer SEMUA-SEKALIGUS untuk VPS CloudRift baru
# (Ubuntu 24.04, GPU NVIDIA — teruji di Tesla V100 SXM3 32GB / Volta sm_70).
#
# Gabungan setup.sh + install-hunyuan15.sh + install-qwen-edit.sh:
# SATU kali tempel, semua model kepasang.
#
# Cara pakai (di VPS baru, sebagai user biasa):
#   curl -fsSL https://raw.githubusercontent.com/agusrizal17/comfybot-studio/main/setup-full.sh | bash
# atau unduh dulu lalu:  bash setup-full.sh
#
# Yang dipasang (±171 GB unduhan model — siapkan disk kosong ≥ 200 GB):
#   - ComfyUI + torch 2.8.0/cu128 (pin Volta) + node ComfyUI-GGUF
#   - Qwen-Image 2.1 GGUF (gambar T2I + edit turbo)          ±17 GB
#   - Wan 2.2 A14B GGUF (video T2V + I2V)                    ±61 GB
#   - HunyuanVideo 1.5 GGUF (mesin video kedua, 480p+720p)   ±65 GB
#   - Qwen-Image-Edit-2511 GGUF (edit presisi, mode Akurat)  ±28 GB
#   - Bot Telegram ComfyBot Studio (tombol + gate channel + antrian + kuota)
#   - Service systemd (auto-start & auto-restart)
#
# Di tengah jalan script menanyakan 3 hal: BOT_TOKEN, ADMIN_ID,
# CHANNEL_USERNAME — ketik langsung di terminal; tersimpan hanya di
# .env bot di VPS ini (chmod 600).
# Script aman dijalankan ulang: file yang ukurannya sudah pas dilewati,
# unduhan yang putus dilanjutkan (wget -c).
# =============================================================================
set -euo pipefail
REPO_URL="https://github.com/agusrizal17/comfybot-studio.git"
SRC_DIR="$HOME/comfybot-studio-src"
BOT_DIR="$HOME/comfy-tele-bot"
M="$HOME/ComfyUI/models"

step() { echo ""; echo "=== $* ==="; }

dl() { # $1=url  $2=dest — verifikasi ukuran via content-length; skip bila lengkap
  local url="$1" dest="$2" want got
  want=$(curl -sIL "$url" | grep -i '^content-length' | tail -1 | tr -dc '0-9' || true)
  if [ -f "$dest" ]; then
    got=$(stat -c%s "$dest")
    if [ -n "$want" ] && [ "$got" = "$want" ]; then
      echo "SKIP (sudah lengkap): $(basename "$dest")"
      return
    fi
    echo "Lanjut unduh ($(basename "$dest") — ada $got dari $want byte)"
  else
    echo ">> $(basename "$dest")"
  fi
  mkdir -p "$(dirname "$dest")"
  wget -c --progress=dot:giga -O "$dest" "$url" || { echo "GAGAL unduh: $url"; exit 1; }
}

# --- Cek ruang disk (peringatan saja) ---------------------------------------
FREE_GB=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
if [ -n "$FREE_GB" ] && [ "$FREE_GB" -lt 200 ]; then
  echo "PERINGATAN: ruang kosong tinggal ${FREE_GB}GB — total unduhan ±171GB."
  echo "Kalau disk di bawah ±200GB, install bisa gagal di tengah jalan."
  sleep 5
fi

# --- 1. Paket dasar ---------------------------------------------------------
step "1/9 Paket dasar (git, python, ffmpeg)"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3-venv python3-dev build-essential wget ffmpeg

# --- 2. ComfyUI + torch pin Volta -------------------------------------------
step "2/9 ComfyUI + PyTorch 2.8.0/cu128 (pin wajib untuk V100/Volta)"
[ -d "$HOME/ComfyUI" ] || git clone https://github.com/comfyanonymous/ComfyUI.git "$HOME/ComfyUI"
cd "$HOME/ComfyUI"
[ -d venv ] || python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -U pip
# JANGAN ganti versi ini: cu124 mentok torch 2.6 (crash), torch 2.9+/cu130
# membuang dukungan Volta. 2.8.0+cu128 = kombinasi terakhir yang teruji.
pip install -q torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 \
  --index-url https://download.pytorch.org/whl/cu128
pip install -q -r requirements.txt

# --- 3. Node GGUF -------------------------------------------------------------
step "3/9 Node ComfyUI-GGUF"
cd "$HOME/ComfyUI/custom_nodes"
[ -d ComfyUI-GGUF ] || git clone https://github.com/leejet/ComfyUI-GGUF
pip install -q -r ComfyUI-GGUF/requirements.txt
deactivate

mkdir -p "$M/diffusion_models" "$M/text_encoders" "$M/vae" "$M/loras" "$M/clip_vision"

# --- 4. Model gambar: Qwen-Image 2.1 GGUF (±17 GB) ----------------------------
step "4/9 Model gambar Qwen-Image 2.1 GGUF"
BASE="https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF/resolve/main"
dl "$BASE/qwen-image-2.1-turbo-Q8_0.gguf"                       "$M/diffusion_models/qwen-image-2.1-turbo-Q8_0.gguf"
dl "$BASE/text_encoders/qwen3vl_8b_int8_convrot.safetensors"    "$M/text_encoders/qwen3vl_8b_int8_convrot.safetensors"
dl "$BASE/vae/qwen_image_2.1_vae_bf16.safetensors"              "$M/vae/qwen_image_2.1_vae_bf16.safetensors"

# --- 5. Model video: Wan 2.2 A14B GGUF (±61 GB) -------------------------------
step "5/9 Model video Wan 2.2 A14B GGUF (I2V + T2V)"
Q="https://huggingface.co/QuantStack"
L="https://huggingface.co/lightx2v/Wan2.2-Lightning/resolve/main"
dl "$Q/Wan2.2-I2V-A14B-GGUF/resolve/main/VAE/Wan2.1_VAE.safetensors" \
   "$M/vae/Wan2.1_VAE.safetensors"
dl "https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q8_0.gguf" \
   "$M/text_encoders/umt5-xxl-encoder-Q8_0.gguf"
dl "$L/Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/high_noise_model.safetensors" \
   "$M/loras/wan22_i2v_lightning_high.safetensors"
dl "$L/Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/low_noise_model.safetensors" \
   "$M/loras/wan22_i2v_lightning_low.safetensors"
dl "$L/Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V2.0/high_noise_model.safetensors" \
   "$M/loras/wan22_t2v_lightning_high.safetensors"
dl "$L/Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V2.0/low_noise_model.safetensors" \
   "$M/loras/wan22_t2v_lightning_low.safetensors"
dl "$Q/Wan2.2-I2V-A14B-GGUF/resolve/main/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q6_K.gguf" \
   "$M/diffusion_models/Wan2.2-I2V-A14B-HighNoise-Q6_K.gguf"
dl "$Q/Wan2.2-I2V-A14B-GGUF/resolve/main/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q6_K.gguf" \
   "$M/diffusion_models/Wan2.2-I2V-A14B-LowNoise-Q6_K.gguf"
dl "$Q/Wan2.2-T2V-A14B-GGUF/resolve/main/HighNoise/Wan2.2-T2V-A14B-HighNoise-Q6_K.gguf" \
   "$M/diffusion_models/Wan2.2-T2V-A14B-HighNoise-Q6_K.gguf"
dl "$Q/Wan2.2-T2V-A14B-GGUF/resolve/main/LowNoise/Wan2.2-T2V-A14B-LowNoise-Q6_K.gguf" \
   "$M/diffusion_models/Wan2.2-T2V-A14B-LowNoise-Q6_K.gguf"

# --- 6. Model video kedua: HunyuanVideo 1.5 GGUF (±65 GB) ---------------------
step "6/9 Model video HunyuanVideo 1.5 GGUF (480p + 720p)"
# Update node GGUF dulu (dukungan arsitektur hunyuanvideo15 ada di versi baru;
# di clone baru langkah ini no-op, di install ulang memastikan versi terbaru).
if [ -d "$HOME/ComfyUI/custom_nodes/ComfyUI-GGUF/.git" ]; then
  git -C "$HOME/ComfyUI/custom_nodes/ComfyUI-GGUF" pull --ff-only || echo "(pull GGUF gagal — lanjut pakai versi yang ada)"
fi
J7="https://huggingface.co/jayn7"
COH="https://huggingface.co/Comfy-Org/Hunyuan-Video-1.5/resolve/main/split_files"
dl "$J7/HunyuanVideo-1.5_T2V_720p-GGUF/resolve/main/720p/hunyuanvideo1.5_720p_t2v-Q8_0.gguf" \
   "$M/diffusion_models/hunyuanvideo1.5_720p_t2v-Q8_0.gguf"
dl "$J7/HunyuanVideo-1.5_I2V_720p-GGUF/resolve/main/720p/hunyuanvideo1.5_720p_i2v-Q8_0.gguf" \
   "$M/diffusion_models/hunyuanvideo1.5_720p_i2v-Q8_0.gguf"
dl "$J7/HunyuanVideo-1.5_I2V_720p-GGUF/resolve/main/720p_distilled/hunyuanvideo1.5_720p_i2v_cfg_distilled-Q8_0.gguf" \
   "$M/diffusion_models/hunyuanvideo1.5_720p_i2v_cfg_distilled-Q8_0.gguf"
dl "$J7/HunyuanVideo-1.5_T2V_480p-GGUF/resolve/main/480p_distilled/hunyuanvideo1.5_480p_t2v_cfg_distilled-Q8_0.gguf" \
   "$M/diffusion_models/hunyuanvideo1.5_480p_t2v_cfg_distilled-Q8_0.gguf"
dl "$J7/HunyuanVideo-1.5_I2V_480p-GGUF/resolve/main/480p_distilled/hunyuanvideo1.5_480p_i2v_cfg_distilled-Q8_0.gguf" \
   "$M/diffusion_models/hunyuanvideo1.5_480p_i2v_cfg_distilled-Q8_0.gguf"
dl "$COH/text_encoders/qwen_2.5_vl_7b.safetensors" \
   "$M/text_encoders/qwen_2.5_vl_7b.safetensors"
dl "$COH/text_encoders/byt5_small_glyphxl_fp16.safetensors" \
   "$M/text_encoders/byt5_small_glyphxl_fp16.safetensors"
dl "$COH/vae/hunyuanvideo15_vae_fp16.safetensors" \
   "$M/vae/hunyuanvideo15_vae_fp16.safetensors"
dl "$COH/clip_vision/sigclip_vision_patch14_384.safetensors" \
   "$M/clip_vision/sigclip_vision_patch14_384.safetensors"

# --- 7. Model edit presisi: Qwen-Image-Edit-2511 GGUF (±28 GB) ----------------
step "7/9 Model edit Qwen-Image-Edit-2511 GGUF (mode Akurat bot)"
UE="https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF/resolve/main"
COQ="https://huggingface.co/Comfy-Org/Qwen-Image/resolve/main/split_files"
dl "$UE/qwen-image-edit-2511-Q6_K.gguf" \
   "$M/diffusion_models/qwen-image-edit-2511-Q6_K.gguf"
dl "$COQ/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors" \
   "$M/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
dl "$COQ/vae/qwen_image_vae.safetensors" \
   "$M/vae/qwen_image_vae.safetensors"

# --- 8. Bot Telegram ----------------------------------------------------------
step "8/9 Bot Telegram (ambil dari repo GitHub)"
[ -d "$SRC_DIR" ] || git clone "$REPO_URL" "$SRC_DIR"
mkdir -p "$BOT_DIR"
cp "$SRC_DIR/bot/bot.py" "$SRC_DIR/bot/requirements.txt" "$BOT_DIR/"
cd "$BOT_DIR"
[ -d venv ] || python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -U pip
pip install -q -r requirements.txt
deactivate

if [ -f "$BOT_DIR/.env" ]; then
  echo ".env sudah ada — dilewati (edit manual bila mau ganti): $BOT_DIR/.env"
else
  echo ""
  echo "Isi konfigurasi bot (dari @BotFather & data kamu sendiri):"
  read -r -p "BOT_TOKEN       : " IN_TOKEN < /dev/tty
  read -r -p "ADMIN_ID (ID Telegram kamu, angka): " IN_ADMIN < /dev/tty
  read -r -p "CHANNEL_USERNAME (contoh @namachannel): " IN_CHAN < /dev/tty
  {
    echo "BOT_TOKEN=$IN_TOKEN"
    echo "ADMIN_ID=$IN_ADMIN"
    echo "CHANNEL_USERNAME=$IN_CHAN"
    echo "IMG_QUOTA=10"
    echo "VID_QUOTA=3"
  } > "$BOT_DIR/.env"
  chmod 600 "$BOT_DIR/.env"
  echo ".env ditulis ke $BOT_DIR/.env"
fi

# --- 9. systemd ----------------------------------------------------------------
step "9/9 Service systemd (auto-start + auto-restart)"
for unit in comfyui comfy-tele-bot; do
  sed -e "s/YOUR_USER/$USER/g" -e "s|/home/$USER|/home/$USER|g" \
      "$SRC_DIR/systemd/$unit.service" | sudo tee "/etc/systemd/system/$unit.service" > /dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now comfyui.service comfy-tele-bot.service
sleep 5
sudo systemctl --no-pager --lines=0 status comfyui.service comfy-tele-bot.service || true

echo ""
echo "=== INSTALL SELESAI — SEMUA MODEL KEPASANG ==="
echo "Ringkasan model:"
du -sh "$M/diffusion_models" "$M/text_encoders" "$M/vae" "$M/loras" "$M/clip_vision" 2>/dev/null || true
echo "Cek ComfyUI : curl -s http://127.0.0.1:8188/system_stats | head -c 200; echo"
echo "Log ComfyUI : tail -f ~/comfyui.log     |  Log bot: tail -f ~/bot.log"
echo ""
echo "PENTING sebelum membagikan bot ke publik:"
echo "1. Jadikan bot ini ADMIN di channel Telegram kamu (syarat gate join bisa jalan)."
echo "2. Buka bot-nya, /start — menu tombol harus muncul."
echo "3. Jangan buka UI web ComfyUI selagi bot lagi render (interrupt membatalkan job)."
