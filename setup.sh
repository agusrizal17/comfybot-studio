#!/usr/bin/env bash
# =============================================================================
# ComfyBot Studio — installer SEKALI TEMPEL untuk VPS CloudRift baru
# (Ubuntu 24.04, GPU NVIDIA — teruji di Tesla V100 SXM3 32GB / Volta sm_70).
#
# Cara pakai (di VPS baru, sebagai user biasa):
#   curl -fsSL https://raw.githubusercontent.com/agusrizal17/comfybot-studio/main/setup.sh | bash
# atau unduh dulu lalu:  bash setup.sh
#
# Yang dipasang: ComfyUI + torch 2.8.0/cu128 (pin Volta) + node GGUF +
# model Qwen-Image 2.1 GGUF + Wan 2.2 A14B GGUF (±79 GB total unduhan,
# semua bisa dilanjut kalau putus) + bot Telegram publik (tombol + gate
# channel + antrian + kuota) + service systemd (auto-start & auto-restart).
#
# Di tengah jalan script akan menanyakan 3 hal: BOT_TOKEN, ADMIN_ID,
# CHANNEL_USERNAME — ketik langsung di terminal, tidak disimpan di mana pun
# selain .env bot di VPS ini (chmod 600).
# Script aman dijalankan ulang: file yang sudah ada dilewati/dilanjutkan.
# =============================================================================
set -u
REPO_URL="https://github.com/agusrizal17/comfybot-studio.git"
SRC_DIR="$HOME/comfybot-studio-src"
BOT_DIR="$HOME/comfy-tele-bot"
M="$HOME/ComfyUI/models"

step() { echo ""; echo "=== $* ==="; }

dl() { # $1=url  $2=dest — lewati bila ukuran file sudah >0 dan wget -c mengonfirmasi selesai
  echo ">> $(basename "$2")"
  wget -c --progress=dot:giga -O "$2" "$1" || { echo "GAGAL unduh: $1"; exit 1; }
}

# --- 1. Paket dasar ---------------------------------------------------------
step "1/7 Paket dasar (git, python, ffmpeg)"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3-venv python3-dev build-essential wget ffmpeg

# --- 2. ComfyUI + torch pin Volta -------------------------------------------
step "2/7 ComfyUI + PyTorch 2.8.0/cu128 (pin wajib untuk V100/Volta)"
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
step "3/7 Node ComfyUI-GGUF"
cd "$HOME/ComfyUI/custom_nodes"
[ -d ComfyUI-GGUF ] || git clone https://github.com/leejet/ComfyUI-GGUF
pip install -q -r ComfyUI-GGUF/requirements.txt
deactivate

mkdir -p "$M/diffusion_models" "$M/text_encoders" "$M/vae" "$M/loras"

# --- 4. Model gambar: Qwen-Image 2.1 GGUF (±17 GB) ----------------------------
step "4/7 Model gambar Qwen-Image 2.1 GGUF"
BASE="https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF/resolve/main"
dl "$BASE/qwen-image-2.1-turbo-Q8_0.gguf"                       "$M/diffusion_models/qwen-image-2.1-turbo-Q8_0.gguf"
dl "$BASE/text_encoders/qwen3vl_8b_int8_convrot.safetensors"    "$M/text_encoders/qwen3vl_8b_int8_convrot.safetensors"
dl "$BASE/vae/qwen_image_2.1_vae_bf16.safetensors"              "$M/vae/qwen_image_2.1_vae_bf16.safetensors"

# --- 5. Model video: Wan 2.2 A14B GGUF (±61 GB) -------------------------------
step "5/7 Model video Wan 2.2 A14B GGUF (I2V + T2V)"
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

# --- 6. Bot Telegram ----------------------------------------------------------
step "6/7 Bot Telegram (ambil dari repo GitHub)"
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

# --- 7. systemd ----------------------------------------------------------------
step "7/7 Service systemd (auto-start + auto-restart)"
for unit in comfyui comfy-tele-bot; do
  sed -e "s/YOUR_USER/$USER/g" -e "s|/home/$USER|/home/$USER|g" \
      "$SRC_DIR/systemd/$unit.service" | sudo tee "/etc/systemd/system/$unit.service" > /dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now comfyui.service comfy-tele-bot.service
sleep 5
sudo systemctl --no-pager --lines=0 status comfyui.service comfy-tele-bot.service || true

echo ""
echo "=== INSTALL SELESAI ==="
echo "Cek ComfyUI : curl -s http://127.0.0.1:8188/system_stats | head -c 200; echo"
echo "Log ComfyUI : tail -f ~/comfyui.log     |  Log bot: tail -f ~/bot.log"
echo ""
echo "PENTING sebelum membagikan bot ke publik:"
echo "1. Jadikan bot ini ADMIN di channel Telegram kamu (syarat gate join bisa jalan)."
echo "2. Buka bot-nya, /start — menu tombol harus muncul."
echo "3. Jangan buka UI web ComfyUI selagi bot lagi render (interrupt membatalkan job)."
