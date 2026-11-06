#!/usr/bin/env bash
# Install Wan 2.2 A14B GGUF (I2V + T2V) untuk ComfyUI di VM CloudRift (V100).
# Jalur GGUF — aman untuk Volta (FP8 bawaan template ComfyUI TIDAK jalan di V100).
# Urutan unduh: VAE + encoder + LoRA dulu, lalu expert I2V (bisa langsung dites),
# terakhir expert T2V. Semua wget -c (bisa dilanjut kalau putus).
set -u
M="$HOME/ComfyUI/models"
mkdir -p "$M/diffusion_models" "$M/text_encoders" "$M/vae" "$M/loras"

dl() { # $1=url  $2=dest
  echo ""
  echo ">> $(basename "$2")"
  wget -c --progress=dot:giga -O "$2" "$1" || { echo "GAGAL: $1"; exit 1; }
}

# ffmpeg: dipakai bot Telegram buat merakit frame video jadi mp4
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo ">> install ffmpeg"
  sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg
fi

Q="https://huggingface.co/QuantStack"
L="https://huggingface.co/lightx2v/Wan2.2-Lightning/resolve/main"

echo "=== 1/4 VAE + text encoder ==="
dl "$Q/Wan2.2-I2V-A14B-GGUF/resolve/main/VAE/Wan2.1_VAE.safetensors" \
   "$M/vae/Wan2.1_VAE.safetensors"
dl "https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q8_0.gguf" \
   "$M/text_encoders/umt5-xxl-encoder-Q8_0.gguf"

echo "=== 2/4 LoRA Lightning 4 steps (I2V + T2V) ==="
dl "$L/Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/high_noise_model.safetensors" \
   "$M/loras/wan22_i2v_lightning_high.safetensors"
dl "$L/Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/low_noise_model.safetensors" \
   "$M/loras/wan22_i2v_lightning_low.safetensors"
dl "$L/Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V2.0/high_noise_model.safetensors" \
   "$M/loras/wan22_t2v_lightning_high.safetensors"
dl "$L/Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V2.0/low_noise_model.safetensors" \
   "$M/loras/wan22_t2v_lightning_low.safetensors"

echo "=== 3/4 Expert I2V A14B Q6_K (foto -> video) ==="
dl "$Q/Wan2.2-I2V-A14B-GGUF/resolve/main/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q6_K.gguf" \
   "$M/diffusion_models/Wan2.2-I2V-A14B-HighNoise-Q6_K.gguf"
dl "$Q/Wan2.2-I2V-A14B-GGUF/resolve/main/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q6_K.gguf" \
   "$M/diffusion_models/Wan2.2-I2V-A14B-LowNoise-Q6_K.gguf"
echo ""
echo "=== I2V SIAP — bagian T2V menyusul di bawah ==="

echo "=== 4/4 Expert T2V A14B Q6_K (teks -> video) ==="
dl "$Q/Wan2.2-T2V-A14B-GGUF/resolve/main/HighNoise/Wan2.2-T2V-A14B-HighNoise-Q6_K.gguf" \
   "$M/diffusion_models/Wan2.2-T2V-A14B-HighNoise-Q6_K.gguf"
dl "$Q/Wan2.2-T2V-A14B-GGUF/resolve/main/LowNoise/Wan2.2-T2V-A14B-LowNoise-Q6_K.gguf" \
   "$M/diffusion_models/Wan2.2-T2V-A14B-LowNoise-Q6_K.gguf"

echo ""
echo "=== INSTALL WAN SELESAI ==="
ls -lh "$M/diffusion_models/"Wan2.2* "$M/text_encoders/umt5-xxl-encoder-Q8_0.gguf" \
       "$M/vae/Wan2.1_VAE.safetensors" "$M/loras/"wan22_*
