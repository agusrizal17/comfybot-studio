#!/usr/bin/env python3
"""
Bot Telegram PUBLIK untuk ComfyUI lokal (ComfyBot Studio v2).

Akses: semua user Telegram, WAJIB join channel dulu (gate getChatMember).
Navigasi full tombol (inline keyboard) — perintah teks tetap jalan:
- /start atau /menu            -> menu utama (tombol)
- Teks biasa / /gambar <prompt>-> text-to-image
- Foto + caption perintah      -> edit foto (album 2 foto = gabung)
- /video <prompt>              -> text-to-video; foto + caption /video = I2V

Fitur publik: antrian global 1 GPU (satu job jalan dalam satu waktu),
generate UNLIMITED tanpa kuota harian (sejak v2.5 — perintah owner),
progress bar live via websocket ComfyUI. ADMIN_ID bebas gate + akses video.
Sejak v2.6: registry user (users.json, buat broadcast) + blokir user per ID
(blocked.json) + perintah admin /broadcast, /blokir, /unblokir, /blokirlist.

Jalan di VM yang sama dengan ComfyUI (API localhost:8188).
Konfigurasi via .env: BOT_TOKEN, ADMIN_ID, CHANNEL_USERNAME (@channel).
"""
import asyncio
import io
import json
import logging
import os
import random
import time
import uuid

import requests
import websocket
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ChatMemberStatus
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.request import HTTPXRequest

COMFY = "http://127.0.0.1:8188"
COMFY_WS = "ws://127.0.0.1:8188/ws"
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ.get("CHANNEL_USERNAME", "").strip()  # contoh: @namachannel
IMG_QUOTA = int(os.environ.get("IMG_QUOTA", "10"))
VID_QUOTA = int(os.environ.get("VID_QUOTA", "3"))
QUOTA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quota.json")

# Aturan tetap untuk semua edit foto: dari foto referensi hanya wajah yang
# diambil — ekspresi dan pakaian selalu dibuat baru mengikuti caption,
# kecuali user menulis sebaliknya di caption-nya.
EDIT_PREFIX = (
    "ATURAN WAJIB: dari foto referensi ambil HANYA wajah dan identitas "
    "orangnya (bentuk wajah, rahang, hidung, telinga, warna dan tekstur "
    "kulit, proporsi kepala). DILARANG menyalin ekspresi foto referensi: "
    "buat ekspresi baru yaitu kedua mata terbuka menatap kamera dan "
    "tersenyum natural, kecuali instruksi adegan meminta ekspresi lain. "
    "DILARANG memakai pakaian yang sama dengan foto referensi: ganti "
    "dengan pakaian baru sesuai instruksi adegan; bila adegan tidak "
    "menyebut pakaian, gunakan pakaian kasual rapi yang berbeda dari "
    "foto referensi.\n\nInstruksi adegan berikut WAJIB dilaksanakan "
    "SEPENUHNYA — termasuk mengganti latar, pose, dan komposisi, serta "
    "menambah orang atau objek sesuai permintaan:\n"
)

COMBINE_PREFIX = (
    "Gabungkan semua orang dari foto-foto referensi menjadi SATU foto "
    "bersama dalam satu frame, semua wajah terlihat jelas. "
)

UNET = "qwen-image-2.1-turbo-Q8_0.gguf"
CLIP = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"

# --- Model edit KHUSUS: Qwen-Image-Edit-2511 GGUF (unduh install-qwen-edit.sh) ---
# Model edit beneran (bukan turbo): jauh lebih patuh instruksi adegan,
# tapi lambat. Resep diringankan 7 Nov setelah uji lapangan 20 step
# tembus timeout 30 menit di V100 (berbagi miner host): 12 step CFG 3,
# skala edit 0,7MP. Encoder & VAE-nya beda dari stack turbo (2.5-VL),
# jadi graph edit punya loader sendiri.
EDIT_UNET = "qwen-image-edit-2511-Q6_K.gguf"
EDIT_CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
EDIT_VAE = "qwen_image_vae.safetensors"
EDIT_STEPS = 12
EDIT_CFG = 3.0

# --- Wan 2.2 A14B GGUF (video) ---
WAN_VAE = "Wan2.1_VAE.safetensors"
WAN_T5 = "umt5-xxl-encoder-Q8_0.gguf"
WAN_I2V_HIGH = "Wan2.2-I2V-A14B-HighNoise-Q6_K.gguf"
WAN_I2V_LOW = "Wan2.2-I2V-A14B-LowNoise-Q6_K.gguf"
WAN_T2V_HIGH = "Wan2.2-T2V-A14B-HighNoise-Q6_K.gguf"
WAN_T2V_LOW = "Wan2.2-T2V-A14B-LowNoise-Q6_K.gguf"
WAN_I2V_LORA_HIGH = "wan22_i2v_lightning_high.safetensors"
WAN_I2V_LORA_LOW = "wan22_i2v_lightning_low.safetensors"
WAN_T2V_LORA_HIGH = "wan22_t2v_lightning_high.safetensors"
WAN_T2V_LORA_LOW = "wan22_t2v_lightning_low.safetensors"
# Negative prompt resmi template Wan 2.2 (versi I2V — tanpa tambahan anti-NSFW)
WAN_NEG = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，"
    "静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，"
    "多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，"
    "形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，"
    "背景人很多，倒着走"
)
VIDEO_FPS = 16
VIDEO_LENGTH = 81  # frame @16fps ≈ 5 detik
DEFAULT_MOTION = (
    "the subject moves naturally, looking at the camera, "
    "gentle slow camera movement, cinematic"
)

# --- HunyuanVideo 1.5 GGUF (video kedua; terpasang via install-hunyuan15.sh) ---
# PENTING: checkpoint 720p dan 480p itu fine-tune TERPISAH — model 720p yang
# dipaksa jalan di latent 480p hasilnya rusak. Bot memakai varian 480p
# CFG-distilled (8 step CFG 1) untuk T2V maupun I2V. I2V WAJIB pakai
# CLIP Vision (sigclip) sebagai input HunyuanVideo15ImageToVideo — tanpa
# itu model kehilangan sinyal identitas dari foto sumber.
HY_T2V_FILE = "hunyuanvideo1.5_480p_t2v_cfg_distilled-Q8_0.gguf"
HY_I2V_FILE = "hunyuanvideo1.5_480p_i2v_cfg_distilled-Q8_0.gguf"
HY_VAE = "hunyuanvideo15_vae_fp16.safetensors"
HY_CLIP1 = "qwen_2.5_vl_7b.safetensors"
HY_CLIP2 = "byt5_small_glyphxl_fp16.safetensors"
HY_CLIP_VISION = "sigclip_vision_patch14_384.safetensors"
HY_FPS = 24

STATE_TTL = 600  # detik, state/foto yang menunggu input berikutnya

pending_photos: dict[int, dict] = {}   # user_id -> {"names": [...], "ts": float}
album_buffers: dict[str, dict] = {}    # media_group_id -> {...}
user_state: dict[int, dict] = {}       # user_id -> {"mode": str, "names": [...], "ts": float}
i2v_engine: dict[int, str] = {}        # user_id -> "wan" | "hy" (dibawa dari tombol ke alur foto)
gate_cache: dict[int, tuple[float, bool]] = {}  # user_id -> (ts, joined)

JOB_QUEUE: asyncio.Queue = asyncio.Queue()
WORKER_BUSY = {"busy": False}
# Job edit yang menunggu pilihan model dari user (per user_id).
PREJOBS: dict[int, dict] = {}

STAGE_LABELS = {
    "UnetLoaderGGUF": "Muat model",
    "CLIPLoader": "Muat encoder teks",
    "VAELoader": "Muat VAE",
    "LoadImage": "Baca foto",
    "ImageScaleToTotalPixels": "Sesuaikan ukuran foto",
    "VAEEncode": "Encode foto",
    "CLIPTextEncode": "Baca prompt",
    "TextEncodeQwenImageEditPlus": "Baca perintah edit",
    "ConditioningZeroOut": "Siapkan conditioning",
    "ModelSamplingAuraFlow": "Siapkan sampling",
    "EmptySD3LatentImage": "Siapkan kanvas",
    "KSampler": "Lagi digambar",
    "KSamplerAdvanced": "Lagi digambar",
    "CLIPLoaderGGUF": "Muat encoder teks",
    "LoraLoaderModelOnly": "Muat LoRA",
    "WanImageToVideo": "Siapkan video",
    "EmptyHunyuanLatentVideo": "Siapkan kanvas video",
    "GetImageSize": "Baca ukuran foto",
    "VAEDecode": "Render akhir",
    "SaveImage": "Simpan gambar",
}


def base_nodes():
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": UNET}},
        "2": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": 3.1}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
    }


def t2i_graph(prompt: str, seed: int):
    g = base_nodes()
    g.update({
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["3", 0], "text": prompt}},
        "5": {"class_type": "ConditioningZeroOut",
              "inputs": {"conditioning": ["4", 0]}},
        "7": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "positive": ["4", 0],
                         "negative": ["5", 0], "latent_image": ["7", 0],
                         "seed": seed, "steps": 8, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["6", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "telebot"}},
    })
    return g


def _edit_stack_nodes():
    """Loader stack model edit 2511: unet GGUF + encoder 2.5-VL + VAE."""
    return {
        "1": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": EDIT_UNET}},
        "2": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": 3.1}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": EDIT_CLIP, "type": "qwen_image",
                         "device": "default"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": EDIT_VAE}},
    }


def edit_graph(prompt: str, image_names: list[str], seed: int):
    # Model edit KHUSUS 2511 (akurat, lambat).
    g = _edit_stack_nodes()
    g.update({
        "11": {"class_type": "LoadImage", "inputs": {"image": image_names[0]}},
        "12": {"class_type": "ImageScaleToTotalPixels",
               "inputs": {"image": ["11", 0], "upscale_method": "lanczos",
                          "megapixels": 0.7, "resolution_steps": 1}},
        "15": {"class_type": "VAEEncode",
               "inputs": {"pixels": ["12", 0], "vae": ["6", 0]}},
    })
    encode_inputs = {"clip": ["3", 0], "vae": ["6", 0],
                     "image1": ["12", 0], "prompt": EDIT_PREFIX + prompt}
    if len(image_names) > 1:
        g["19"] = {"class_type": "LoadImage", "inputs": {"image": image_names[1]}}
        g["20"] = {"class_type": "ImageScaleToTotalPixels",
                   "inputs": {"image": ["19", 0], "upscale_method": "lanczos",
                              "megapixels": 0.7, "resolution_steps": 1}}
        encode_inputs["image2"] = ["20", 0]
        encode_inputs["prompt"] = COMBINE_PREFIX + encode_inputs["prompt"]
    g["13"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": encode_inputs}
    g.update({
        "14": {"class_type": "ConditioningZeroOut",
               "inputs": {"conditioning": ["13", 0]}},
        "21": {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["13", 0],
                          "reference_latents_method": "index_timestep_zero"}},
        "16": {"class_type": "KSampler",
               "inputs": {"model": ["2", 0], "positive": ["21", 0],
                          "negative": ["14", 0], "latent_image": ["15", 0],
                          "seed": seed, "steps": EDIT_STEPS, "cfg": EDIT_CFG,
                          "sampler_name": "euler", "scheduler": "simple",
                          "denoise": 1.0}},
        "17": {"class_type": "VAEDecode",
               "inputs": {"samples": ["16", 0], "vae": ["6", 0]}},
        "18": {"class_type": "SaveImage",
               "inputs": {"images": ["17", 0], "filename_prefix": "teleedit"}},
    })
    return g


def edit_graph_turbo(prompt: str, image_names: list[str], seed: int):
    """Edit pakai stack TURBO (cepat; nurut adegan seadanya)."""
    g = base_nodes()
    g.update({
        "11": {"class_type": "LoadImage", "inputs": {"image": image_names[0]}},
        "12": {"class_type": "ImageScaleToTotalPixels",
               "inputs": {"image": ["11", 0], "upscale_method": "lanczos",
                          "megapixels": 1.0, "resolution_steps": 1}},
        "15": {"class_type": "VAEEncode",
               "inputs": {"pixels": ["12", 0], "vae": ["6", 0]}},
    })
    encode_inputs = {"clip": ["3", 0], "vae": ["6", 0],
                     "image1": ["12", 0], "prompt": EDIT_PREFIX + prompt}
    if len(image_names) > 1:
        g["19"] = {"class_type": "LoadImage", "inputs": {"image": image_names[1]}}
        g["20"] = {"class_type": "ImageScaleToTotalPixels",
                   "inputs": {"image": ["19", 0], "upscale_method": "lanczos",
                              "megapixels": 1.0, "resolution_steps": 1}}
        encode_inputs["image2"] = ["20", 0]
        encode_inputs["prompt"] = COMBINE_PREFIX + encode_inputs["prompt"]
    g["13"] = {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": encode_inputs}
    g.update({
        "14": {"class_type": "ConditioningZeroOut",
               "inputs": {"conditioning": ["13", 0]}},
        "16": {"class_type": "KSampler",
               "inputs": {"model": ["2", 0], "positive": ["13", 0],
                          "negative": ["14", 0], "latent_image": ["15", 0],
                          "seed": seed, "steps": 8, "cfg": 1.0,
                          "sampler_name": "euler", "scheduler": "simple",
                          "denoise": 1.0}},
        "17": {"class_type": "VAEDecode",
               "inputs": {"samples": ["16", 0], "vae": ["6", 0]}},
        "18": {"class_type": "SaveImage",
               "inputs": {"images": ["17", 0], "filename_prefix": "teleedit"}},
    })
    return g


def t2i_accurate_graph(prompt: str, seed: int):
    """Teks -> gambar pakai model 2511 tanpa foto referensi (akurat, lambat)."""
    g = _edit_stack_nodes()
    g.update({
        "13": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["3", 0], "vae": ["6", 0],
                          "prompt": prompt}},
        "14": {"class_type": "ConditioningZeroOut",
               "inputs": {"conditioning": ["13", 0]}},
        "7": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": 896, "height": 896, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "positive": ["13", 0],
                         "negative": ["14", 0], "latent_image": ["7", 0],
                         "seed": seed, "steps": EDIT_STEPS, "cfg": EDIT_CFG,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["6", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "telebot"}},
    })
    return g


def _wan_nodes(high_unet, low_unet, high_lora, low_lora, prompt):
    """Rangkaian dasar Wan 2.2 A14B: 2 expert GGUF + LoRA Lightning + T5 GGUF."""
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": high_unet}},
        "2": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": high_lora,
                         "strength_model": 1.0}},
        "3": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["2", 0], "shift": 5.0}},
        "4": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": low_unet}},
        "5": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["4", 0], "lora_name": low_lora,
                         "strength_model": 1.0}},
        "6": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["5", 0], "shift": 5.0}},
        "7": {"class_type": "CLIPLoaderGGUF",
              "inputs": {"clip_name": WAN_T5, "type": "wan"}},
        "8": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["7", 0], "text": prompt}},
        "9": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["7", 0], "text": WAN_NEG}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": WAN_VAE}},
    }


def _wan_samplers(seed, positive, negative, latent):
    """2 sampler Lightning: high-noise step 0-2, low-noise step 2-4 (cfg 1)."""
    return {
        "15": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["3", 0], "positive": positive,
                          "negative": negative, "latent_image": latent,
                          "add_noise": "enable", "noise_seed": seed,
                          "steps": 4, "cfg": 1.0, "sampler_name": "euler",
                          "scheduler": "simple", "start_at_step": 0,
                          "end_at_step": 2,
                          "return_with_leftover_noise": "enable"}},
        "16": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["6", 0], "positive": positive,
                          "negative": negative, "latent_image": ["15", 0],
                          "add_noise": "disable", "noise_seed": 0,
                          "steps": 4, "cfg": 1.0, "sampler_name": "euler",
                          "scheduler": "simple", "start_at_step": 2,
                          "end_at_step": 4,
                          "return_with_leftover_noise": "disable"}},
        "17": {"class_type": "VAEDecode",
               "inputs": {"samples": ["16", 0], "vae": ["10", 0]}},
        "18": {"class_type": "SaveImage",
               "inputs": {"images": ["17", 0], "filename_prefix": "televideo"}},
    }


def wan_i2v_graph(image_name: str, prompt: str, seed: int):
    """Foto -> video: foto diskalakan ke ±480p (kelipatan 16), jadi frame awal."""
    g = _wan_nodes(WAN_I2V_HIGH, WAN_I2V_LOW,
                   WAN_I2V_LORA_HIGH, WAN_I2V_LORA_LOW, prompt)
    g.update({
        "11": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "12": {"class_type": "ImageScaleToTotalPixels",
               "inputs": {"image": ["11", 0], "upscale_method": "lanczos",
                          "megapixels": 0.4, "resolution_steps": 16}},
        "13": {"class_type": "GetImageSize", "inputs": {"image": ["12", 0]}},
        "14": {"class_type": "WanImageToVideo",
               "inputs": {"positive": ["8", 0], "negative": ["9", 0],
                          "vae": ["10", 0], "width": ["13", 0],
                          "height": ["13", 1], "length": VIDEO_LENGTH,
                          "batch_size": 1, "start_image": ["12", 0]}},
    })
    g.update(_wan_samplers(seed, ["14", 0], ["14", 1], ["14", 2]))
    return g


def wan_t2v_graph(prompt: str, seed: int):
    """Teks -> video 832x480."""
    g = _wan_nodes(WAN_T2V_HIGH, WAN_T2V_LOW,
                   WAN_T2V_LORA_HIGH, WAN_T2V_LORA_LOW, prompt)
    g.update({
        "14": {"class_type": "EmptyHunyuanLatentVideo",
               "inputs": {"width": 832, "height": 480,
                          "length": VIDEO_LENGTH, "batch_size": 1}},
    })
    g.update(_wan_samplers(seed, ["8", 0], ["9", 0], ["14", 0]))
    return g


def _hy_nodes(unet_file, prompt):
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": unet_file}},
        "2": {"class_type": "DualCLIPLoader",
              "inputs": {"clip_name1": HY_CLIP1, "clip_name2": HY_CLIP2,
                         "type": "hunyuan_video_15", "device": "default"}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": prompt}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 0], "text": ""}},
        "5": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["1", 0], "shift": 7.0}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": HY_VAE}},
    }


def hy_t2v_graph(prompt: str, seed: int):
    """Teks -> video Hunyuan 1.5 (480p distilled) 832x480, 121 frame @24fps."""
    g = _hy_nodes(HY_T2V_FILE, prompt)
    g.update({
        "6": {"class_type": "EmptyHunyuanVideo15Latent",
              "inputs": {"width": 832, "height": 480, "length": 121,
                         "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["5", 0], "positive": ["3", 0],
                         "negative": ["4", 0], "latent_image": ["6", 0],
                         "seed": seed, "steps": 8, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["7", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "comfybot"}},
    })
    return g


def hy_i2v_graph(image_name: str, prompt: str, seed: int):
    """Foto -> video Hunyuan 1.5 (distilled): foto diskalakan ke ±480p."""
    g = _hy_nodes(HY_I2V_FILE, prompt)
    g.update({
        "11": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "12": {"class_type": "ImageScaleToTotalPixels",
               "inputs": {"image": ["11", 0], "upscale_method": "lanczos",
                          "megapixels": 0.4, "resolution_steps": 16}},
        "13": {"class_type": "GetImageSize", "inputs": {"image": ["12", 0]}},
        "15": {"class_type": "CLIPVisionLoader",
               "inputs": {"clip_name": HY_CLIP_VISION}},
        "16": {"class_type": "CLIPVisionEncode",
               "inputs": {"clip_vision": ["15", 0], "image": ["12", 0],
                          "crop": "center"}},
        "14": {"class_type": "HunyuanVideo15ImageToVideo",
               "inputs": {"positive": ["3", 0], "negative": ["4", 0],
                          "vae": ["7", 0], "width": ["13", 0],
                          "height": ["13", 1], "length": 121,
                          "batch_size": 1, "start_image": ["12", 0],
                          "clip_vision_output": ["16", 0]}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["5", 0], "positive": ["14", 0],
                         "negative": ["14", 1], "latent_image": ["14", 2],
                         "seed": seed, "steps": 8, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["7", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "comfybot"}},
    })
    return g


# ---------------------------------------------------------------------------
# ComfyUI I/O
# ---------------------------------------------------------------------------

def _fetch_result(pid: str) -> bytes | None:
    """Ambil bytes gambar dari /history bila prompt sudah selesai."""
    h = requests.get(f"{COMFY}/history/{pid}", timeout=30)
    h.raise_for_status()
    data = h.json()
    if pid not in data:
        return None
    status = data[pid].get("status", {})
    if status.get("status_str") == "error":
        raise RuntimeError(f"ComfyUI error: {status}")
    for node_out in data[pid].get("outputs", {}).values():
        for img in node_out.get("images", []):
            v = requests.get(
                f"{COMFY}/view",
                params={"filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output")},
                timeout=60,
            )
            v.raise_for_status()
            return v.content
    return None


def _fetch_frames(pid: str) -> list[bytes] | None:
    """Ambil SEMUA frame gambar dari /history (buat video), urut nama file."""
    h = requests.get(f"{COMFY}/history/{pid}", timeout=30)
    h.raise_for_status()
    data = h.json()
    if pid not in data:
        return None
    status = data[pid].get("status", {})
    if status.get("status_str") == "error":
        raise RuntimeError(f"ComfyUI error: {status}")
    files = []
    for node_out in data[pid].get("outputs", {}).values():
        for img in node_out.get("images", []):
            if img.get("type") != "temp":
                files.append(img)
    if not files:
        return None
    files.sort(key=lambda im: im["filename"])
    frames = []
    for img in files:
        v = requests.get(
            f"{COMFY}/view",
            params={"filename": img["filename"],
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output")},
            timeout=60,
        )
        v.raise_for_status()
        frames.append(v.content)
    return frames


def _stitch_mp4(frames: list[bytes], fps: int = VIDEO_FPS) -> bytes:
    """Rakit frame PNG jadi mp4 (H.264) pakai ffmpeg di VM ini."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for i, fr in enumerate(frames):
            with open(os.path.join(td, f"f{i:04d}.png"), "wb") as fh:
                fh.write(fr)
        out = os.path.join(td, "out.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-framerate", str(fps),
             "-i", os.path.join(td, "f%04d.png"),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out],
            check=True, capture_output=True, timeout=600,
        )
        with open(out, "rb") as fh:
            return fh.read()


def _result_for(pid: str, kind: str, fps: int = VIDEO_FPS):
    if kind == "video":
        frames = _fetch_frames(pid)
        if frames is None:
            return None
        return _stitch_mp4(frames, fps)
    return _fetch_result(pid)


def comfy_run(graph, state: dict, kind: str = "image",
              deadline_s: int = 900, fps: int = VIDEO_FPS) -> bytes:
    """Antri workflow, pantau progress via websocket, kembalikan bytes hasil.

    state diisi dari thread ini: {"stage": str, "pct": int | None}
    """
    client_id = uuid.uuid4().hex
    r = requests.post(
        f"{COMFY}/prompt",
        json={"prompt": graph, "client_id": client_id},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"ComfyUI menolak workflow ({r.status_code}): {r.text[:600]}"
        )
    pid = r.json()["prompt_id"]
    deadline = time.time() + deadline_s
    ws = websocket.create_connection(f"{COMFY_WS}?clientId={client_id}", timeout=30)
    try:
        while time.time() < deadline:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                img = _result_for(pid, kind, fps)
                if img:
                    return img
                continue
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            data = msg.get("data", {})
            if data.get("prompt_id") not in (None, pid):
                continue
            mtype = msg.get("type")
            if mtype == "executing":
                node = data.get("node")
                if node is None:
                    img = _result_for(pid, kind, fps)
                    if img:
                        return img
                else:
                    cls = graph.get(str(node), {}).get("class_type", "")
                    state["stage"] = STAGE_LABELS.get(cls, cls or "Proses")
                    if cls not in ("KSampler", "KSamplerAdvanced"):
                        state["pct"] = None
            elif mtype == "progress":
                mx = data.get("max") or 0
                val = data.get("value") or 0
                if mx:
                    state["pct"] = int(val * 100 / mx)
                    node = data.get("node")
                    cls = graph.get(str(node), {}).get("class_type", "")
                    if cls:
                        state["stage"] = STAGE_LABELS.get(cls, state["stage"])
            elif mtype == "execution_error":
                raise RuntimeError(f"ComfyUI error: {data}")
        raise TimeoutError(f"Kelamaan (>{deadline_s // 60} menit), dibatalkan.")
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def comfy_upload(image_bytes: bytes, name: str) -> str:
    r = requests.post(
        f"{COMFY}/upload/image",
        files={"image": (name, io.BytesIO(image_bytes), "image/jpeg")},
        data={"overwrite": "true"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["name"]


def bar_text(state: dict, label: str, emoji: str = "🎨") -> str:
    stage = state.get("stage") or "Siapkan"
    pct = state.get("pct")
    t0 = state.get("t0")
    elapsed = ""
    if t0:
        s = int(time.time() - t0)
        elapsed = f" ⏱ {s // 60}:{s % 60:02d}"
    if pct is None:
        return f"{emoji} {label} — {stage}…{elapsed}"
    filled = max(0, min(10, round(pct / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    return f"{emoji} {label} — {stage}…{elapsed}\n[{bar}] {pct}%"


# ---------------------------------------------------------------------------
# User, gate channel, kuota
# ---------------------------------------------------------------------------

def is_admin_id(user_id: int) -> bool:
    return user_id == ADMIN_ID


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _quota_load() -> dict:
    try:
        with open(QUOTA_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def _quota_save(data: dict):
    try:
        with open(QUOTA_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:  # noqa: BLE001
        pass


def quota_left(user_id: int, kind: str) -> int:
    limit = IMG_QUOTA if kind == "image" else VID_QUOTA
    if is_admin_id(user_id):
        return limit
    used = _quota_load().get(_today(), {}).get(str(user_id), {})
    return max(0, limit - int(used.get(kind, 0)))


def quota_charge(user_id: int, kind: str) -> bool:
    """v2.5: generate UNLIMITED untuk semua user — selalu True, tanpa catat kuota."""
    return True


def quota_refund(user_id: int, kind: str):
    if is_admin_id(user_id):
        return
    data = _quota_load()
    rec = data.get(_today(), {}).get(str(user_id))
    if rec and int(rec.get(kind, 0)) > 0:
        rec[kind] = int(rec[kind]) - 1
        _quota_save(data)


# ---------------------------------------------------------------------------
# Registry user & blokir (v2.6)
# users.json  : semua user yang pernah berinteraksi (target broadcast)
# blocked.json: ID yang diblokir admin — semua interaksinya diabaikan total
# ---------------------------------------------------------------------------

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_PATH = os.path.join(_BASE_DIR, "users.json")
BLOCKED_PATH = os.path.join(_BASE_DIR, "blocked.json")


def _load_id_set(path: str) -> set:
    try:
        with open(path, encoding="utf-8") as fh:
            return {int(x) for x in json.load(fh)}
    except Exception:  # noqa: BLE001
        return set()


def _save_id_set(path: str, ids: set):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(sorted(ids), fh)
    except Exception:  # noqa: BLE001
        pass


USERS: set = _load_id_set(USERS_PATH)
BLOCKED: set = _load_id_set(BLOCKED_PATH)

# Pesan yang diterima user terblokir tiap mencoba memakai bot
# (dibatasi maks 1x per jam per user biar tidak jadi banjir balasan).
BLOCK_NOTICE = (
    "Anda diblokir sementara oleh admin dengan alasan terlalu spam "
    "melakukan generate gambar/foto, jika ada kendala pada bot harap "
    "hubungi admin bot: @yaelahzal17"
)
NOTICE_INTERVAL = 3600
_last_notice: dict = {}

# Seed registry dari quota.json lama (user yang pernah generate sebelum v2.6)
try:
    for _day in _quota_load().values():
        if isinstance(_day, dict):
            for _uid in _day:
                try:
                    USERS.add(int(_uid))
                except (TypeError, ValueError):
                    pass
except Exception:  # noqa: BLE001
    pass


def register_user(user_id: int):
    if user_id and user_id not in USERS:
        USERS.add(user_id)
        _save_id_set(USERS_PATH, USERS)


def is_blocked(user_id: int) -> bool:
    return user_id != ADMIN_ID and user_id in BLOCKED


def block_user(user_id: int):
    BLOCKED.add(user_id)
    _save_id_set(BLOCKED_PATH, BLOCKED)


def unblock_user(user_id: int):
    BLOCKED.discard(user_id)
    _save_id_set(BLOCKED_PATH, BLOCKED)


async def guard_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handler group -1: catat user ke registry; user terblokir dikirimi
    pemberitahuan blokir (maks 1x/jam) lalu interaksinya diabaikan total."""
    user = update.effective_user
    if user is None:
        return
    register_user(user.id)
    if is_blocked(user.id):
        now = time.time()
        if now - _last_notice.get(user.id, 0) >= NOTICE_INTERVAL:
            _last_notice[user.id] = now
            chat = update.effective_chat
            if chat is not None:
                try:
                    await ctx.bot.send_message(chat_id=chat.id, text=BLOCK_NOTICE)
                except Exception:  # noqa: BLE001
                    pass
        raise ApplicationHandlerStop


async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not is_admin_id(update.effective_user.id):
        return
    reply = msg.reply_to_message
    text = " ".join(ctx.args).strip()
    if reply is None and not text:
        await msg.reply_text(
            "📣 Cara broadcast:\n"
            "• /broadcast <teks> — kirim teks ke semua user terdaftar\n"
            "• Reply pesan/foto apa pun dengan /broadcast — pesan itu yang "
            "diteruskan ke semua user\n\n"
            f"User terdaftar saat ini: {len(USERS - {ADMIN_ID})} "
            f"(diblokir: {len(BLOCKED)})"
        )
        return
    targets = sorted(USERS - {ADMIN_ID})
    if not targets:
        await msg.reply_text("Belum ada user terdaftar buat di-broadcast.")
        return
    await msg.reply_text(f"📣 Broadcast ke {len(targets)} user, mulai…")

    async def deliver(uid: int) -> str:
        try:
            if reply is not None:
                await ctx.bot.copy_message(
                    chat_id=uid, from_chat_id=msg.chat_id,
                    message_id=reply.message_id,
                )
            else:
                try:
                    await ctx.bot.send_message(
                        chat_id=uid, text=text, parse_mode="Markdown")
                except TelegramError:
                    await ctx.bot.send_message(chat_id=uid, text=text)
            return "ok"
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 1)
            try:
                if reply is not None:
                    await ctx.bot.copy_message(
                        chat_id=uid, from_chat_id=msg.chat_id,
                        message_id=reply.message_id,
                    )
                else:
                    await ctx.bot.send_message(chat_id=uid, text=text)
                return "ok"
            except Exception:  # noqa: BLE001
                return "fail"
        except Forbidden:
            return "gone"
        except Exception:  # noqa: BLE001
            return "fail"

    ok = fail = gone = 0
    for uid in targets:
        res = await deliver(uid)
        if res == "ok":
            ok += 1
        elif res == "gone":
            gone += 1
            USERS.discard(uid)
        else:
            fail += 1
        await asyncio.sleep(0.07)
    if gone:
        _save_id_set(USERS_PATH, USERS)
    await msg.reply_text(
        "📣 Broadcast selesai.\n"
        f"✅ Terkirim: {ok}\n"
        f"❌ Gagal: {fail}\n"
        f"🗑 Dihapus dari daftar (nge-block bot): {gone}"
    )


def _target_id(ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return None
    try:
        return int(ctx.args[0])
    except (TypeError, ValueError):
        return None


async def cmd_blokir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin_id(update.effective_user.id):
        return
    uid = _target_id(ctx)
    if uid is None:
        await update.message.reply_text("Cara pakai: /blokir <id_user>")
        return
    if uid == ADMIN_ID:
        await update.message.reply_text("Nggak bisa blokir admin 😄")
        return
    block_user(uid)
    await update.message.reply_text(
        f"🚫 User {uid} diblokir — semua pesannya bakal diabaikan bot.\n"
        f"Total diblokir: {len(BLOCKED)}"
    )


async def cmd_unblokir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin_id(update.effective_user.id):
        return
    uid = _target_id(ctx)
    if uid is None:
        await update.message.reply_text("Cara pakai: /unblokir <id_user>")
        return
    unblock_user(uid)
    await update.message.reply_text(
        f"✅ Blokir user {uid} dibuka. Total diblokir: {len(BLOCKED)}"
    )


async def cmd_blokirlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin_id(update.effective_user.id):
        return
    if not BLOCKED:
        await update.message.reply_text("Daftar blokir kosong.")
        return
    ids = "\n".join(f"• {uid}" for uid in sorted(BLOCKED))
    await update.message.reply_text(f"🚫 User diblokir ({len(BLOCKED)}):\n{ids}")


async def check_joined(bot, user_id: int, force: bool = False) -> bool:
    """True bila user sudah join channel (atau gate nonaktif / admin)."""
    if is_admin_id(user_id) or not CHANNEL:
        return True
    now = time.time()
    cached = gate_cache.get(user_id)
    if cached and not force and now - cached[0] < 300:
        return cached[1]
    joined = False
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        status = member.status
        if status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                      ChatMemberStatus.OWNER):
            joined = True
        elif status == ChatMemberStatus.RESTRICTED:
            joined = bool(getattr(member, "is_member", False))
    except Exception as e:  # noqa: BLE001
        logging.warning("get_chat_member gagal untuk %s: %s", user_id, e)
        joined = False
    gate_cache[user_id] = (now, joined)
    return joined


# ---------------------------------------------------------------------------
# UI: keyboard & teks
# ---------------------------------------------------------------------------

def gate_kb() -> InlineKeyboardMarkup:
    url = "https://t.me/" + CHANNEL.lstrip("@")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel Dulu", url=url)],
        [InlineKeyboardButton("✅ Sudah Join", callback_data="gate_check")],
    ])


def menu_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🖼 Buat Gambar", callback_data="act_img"),
         InlineKeyboardButton("✏️ Edit Foto", callback_data="act_edit")],
    ]
    if is_admin_id(user_id):
        rows.append(
            [InlineKeyboardButton("🎬 Buat Video", callback_data="act_vid"),
             InlineKeyboardButton("📹 Gerakkan Foto", callback_data="act_i2v")]
        )
    rows.append([InlineKeyboardButton("ℹ️ Bantuan", callback_data="help")])
    return InlineKeyboardMarkup(rows)


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_state")],
    ])


def motion_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Pakai Gerakan Bawaan", callback_data="default_motion")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_state")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("« Menu Utama", callback_data="menu")],
    ])


GATE_TEXT = (
    "🔒 Bot ini khusus member channel.\n\n"
    "Join dulu channel kami, habis itu pencet tombol ✅ Sudah Join ya."
)

HELP_TEXT = (
    "ℹ️ *Cara pakai*\n\n"
    "🖼 *Buat Gambar* — tulis prompt, gambar dibuat dari teks (±1 menit).\n"
    "✏️ *Edit Foto* — kirim foto lalu tulis perintah editnya. Kirim 2 foto "
    "sekaligus (album) buat menggabungkan dua orang jadi satu foto. Dari foto "
    "referensi hanya wajah yang dipakai — ekspresi & pakaian selalu dibuat "
    "baru mengikuti perintahmu.\n\n"
    "Semua request masuk ANTRIAN (GPU cuma bisa 1 job dalam satu waktu). "
    "Ada kuota harian per user yang reset tiap hari.\n\n"
    "Perintah teks juga bisa: /gambar <prompt>, atau kirim "
    "foto + caption perintah langsung."
)

HELP_SUFFIX_ADMIN = (
    "\n\n🎬 *Buat Video* & 📹 *Gerakkan Foto* — aktif buat kamu (admin). "
    "Pas bikin video kamu bisa milih mesin: Wan 2.2 (detail, lambat) atau "
    "Hunyuan 1.5 (lebih ringan)."
    "\n\n👑 Perintah admin: /broadcast (kirim pengumuman ke semua user), "
    "/blokir <id>, /unblokir <id>, /blokirlist."
)

HELP_SUFFIX_USER = (
    "\n\n🎬 Fitur video saat ini khusus admin."
)


def menu_text(user_id: int, first_name: str = "") -> str:
    if is_admin_id(user_id):
        quota = "Kuota: ∞ (admin)"
    else:
        quota = "Kuota: ∞ — unlimited, generate sepuasnya 🎉"
    hello = f"Halo {first_name} 👋" if first_name else "Halo 👋"
    return (
        f"{hello}\n\n"
        "Selamat datang di *ComfyBot Studio* — studio gambar & video AI gratis.\n"
        f"{quota}\n\n"
        "Mau bikin apa? Pencet tombolnya aja 👇"
    )


# ---------------------------------------------------------------------------
# Antrian & worker
# ---------------------------------------------------------------------------

async def submit_job(msg, user_id: int, kind: str, graph, label: str,
                     prompt_text: str = "", fps: int = VIDEO_FPS,
                     deadline: int | None = None):
    """Masukkan job ke antrian global. kind: 'image' | 'video' (buat kuota)."""
    if is_blocked(user_id):
        return
    if not quota_charge(user_id, kind):
        await msg.reply_text(
            "⚠️ Kuota harianmu habis buat hari ini. Coba lagi besok ya 🙏"
        )
        return
    fu = msg.from_user
    if fu is not None:
        uname = f" (@{fu.username})" if fu.username else ""
        user_label = f"{fu.full_name}{uname} — id {fu.id}"
    else:
        user_label = f"id {user_id}"
    await JOB_QUEUE.put({
        "msg": msg, "user_id": user_id, "kind": kind,
        "graph": graph, "label": label, "prompt": prompt_text,
        "user_label": user_label, "fps": fps, "deadline": deadline,
    })
    pos = JOB_QUEUE.qsize() + (1 if WORKER_BUSY["busy"] else 0)
    if pos <= 1:
        await msg.reply_text(f"📥 {label} — masuk antrian, langsung diproses…")
    else:
        await msg.reply_text(
            f"📥 {label} — masuk antrian posisi ke-{pos}. "
            "Ditunggu aja, hasilnya dikirim ke sini."
        )


async def job_worker():
    """Satu-satunya yang menjalankan ComfyUI — job diproses satu per satu."""
    while True:
        job = await JOB_QUEUE.get()
        if is_blocked(job["user_id"]):
            continue
        WORKER_BUSY["busy"] = True
        msg = job["msg"]
        kind = job["kind"]
        label = job["label"]
        emoji = "🎬" if kind == "video" else "🎨"
        note = None
        edit_task = None
        try:
            note = await msg.reply_text(f"{emoji} {label} — mulai…")
            await msg.chat.send_action(
                ChatAction.UPLOAD_VIDEO if kind == "video" else ChatAction.UPLOAD_PHOTO
            )
            state: dict = {"stage": "Antri", "pct": None, "t0": time.time()}

            async def editor():
                last = ""
                while True:
                    await asyncio.sleep(3)
                    text = bar_text(state, label, emoji)
                    if text != last:
                        last = text
                        try:
                            await note.edit_text(text)
                        except Exception:  # noqa: BLE001
                            pass

            edit_task = asyncio.create_task(editor())
            data = await asyncio.to_thread(
                comfy_run, job["graph"], state, kind,
                job.get("deadline") or (1800 if kind == "video" else 900),
                job.get("fps", VIDEO_FPS),
            )
            edit_task.cancel()
            if kind == "video":
                await msg.reply_video(video=io.BytesIO(data), supports_streaming=True)
            else:
                await msg.reply_photo(photo=io.BytesIO(data))
                # Pemantauan admin: hasil generate gambar user lain +
                # prompt-nya diteruskan ke admin (model tanpa filter —
                # owner harus bisa mengawasi penyalahgunaan).
                if ADMIN_ID and not is_admin_id(job["user_id"]):
                    caption = (
                        "🔎 Hasil generate user\n"
                        f"👤 {job['user_label']}\n"
                        f"🏷 {label}\n"
                        f"💬 Prompt: {job['prompt'][:600]}"
                    )
                    try:
                        await msg.get_bot().send_photo(
                            chat_id=ADMIN_ID, photo=io.BytesIO(data),
                            caption=caption,
                        )
                        logging.info(
                            "[PANTAU] hasil user %s terkirim ke admin",
                            job["user_id"])
                    except Exception:  # noqa: BLE001
                        logging.exception("[PANTAU] gagal kirim foto ke admin")
                        try:
                            await msg.get_bot().send_message(
                                chat_id=ADMIN_ID, text=caption)
                        except Exception:  # noqa: BLE001
                            logging.exception(
                                "[PANTAU] fallback teks juga gagal")
            try:
                await note.delete()
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            quota_refund(job["user_id"], kind)
            if edit_task:
                edit_task.cancel()
            if note is not None:
                try:
                    await note.edit_text(f"❌ Gagal: {e}")
                except Exception:  # noqa: BLE001
                    pass
            else:
                try:
                    await msg.reply_text(f"❌ Gagal: {e}")
                except Exception:  # noqa: BLE001
                    pass
        finally:
            WORKER_BUSY["busy"] = False
            JOB_QUEUE.task_done()


def seed() -> int:
    return random.randint(0, 2**31 - 1)


async def submit_t2i(msg, user_id: int, prompt: str, engine: str = "turbo"):
    if engine == "acc":
        await submit_job(msg, user_id, "image",
                         t2i_accurate_graph(prompt, seed()),
                         "Bikin gambar", prompt, deadline=1800)
    else:
        await submit_job(msg, user_id, "image", t2i_graph(prompt, seed()),
                         "Bikin gambar", prompt)


async def submit_edit(msg, user_id: int, names: list[str], instruction: str):
    """Edit TIDAK langsung jalan: user pilih modelnya dulu (Turbo/Akurat)."""
    label = "Edit foto" if len(names) == 1 else "Gabung foto"
    PREJOBS[user_id] = {"names": names, "instruction": instruction,
                        "msg": msg, "label": label}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Turbo — cepat",
                              callback_data="model:edit:turbo")],
        [InlineKeyboardButton("🎯 Akurat 2511 — nurut prompt, lambat",
                              callback_data="model:edit:acc")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_state")],
    ])
    await msg.reply_text(f"✏️ {label} — pilih modelnya:", reply_markup=kb)


async def submit_t2v(msg, user_id: int, prompt: str, engine: str = "wan"):
    if engine == "hy":
        await submit_job(msg, user_id, "video", hy_t2v_graph(prompt, seed()),
                         "Bikin video (Hunyuan 1.5)", prompt, fps=HY_FPS,
                         deadline=2700)
    else:
        await submit_job(msg, user_id, "video", wan_t2v_graph(prompt, seed()),
                         "Bikin video", prompt)


async def submit_i2v(msg, user_id: int, name: str, motion: str,
                     engine: str = "wan"):
    if engine == "hy":
        await submit_job(msg, user_id, "video", hy_i2v_graph(name, motion, seed()),
                         "Gerakin foto jadi video (Hunyuan 1.5)", motion,
                         fps=HY_FPS, deadline=2700)
    else:
        await submit_job(msg, user_id, "video", wan_i2v_graph(name, motion, seed()),
                         "Gerakin foto jadi video", motion)


# ---------------------------------------------------------------------------
# Alur foto
# ---------------------------------------------------------------------------

async def accept_photos(msg, user_id: int, raw_list: list[bytes],
                        caption: str, mode_hint: str | None):
    """Upload foto ke ComfyUI lalu rutekan sesuai mode/caption."""
    names = []
    try:
        for i, raw in enumerate(raw_list):
            name = await asyncio.to_thread(
                comfy_upload, raw, f"tele_{user_id}_{int(time.time())}_{i}.jpg"
            )
            names.append(name)
    except Exception as e:  # noqa: BLE001
        await msg.reply_text(f"❌ Gagal upload foto ke ComfyUI: {e}")
        return
    cap = caption.strip()
    if mode_hint == "i2v":
        if not is_admin_id(user_id):
            await msg.reply_text("🔒 Fitur video khusus admin.")
            return
        engine = i2v_engine.pop(user_id, "wan")
        motion = cap[len("/video"):].strip() if cap.lower().startswith("/video") else cap
        if motion:
            await submit_i2v(msg, user_id, names[0], motion, engine)
        else:
            user_state[user_id] = {"mode": "i2v_motion", "names": names,
                                   "engine": engine, "ts": time.time()}
            await msg.reply_text(
                "📹 Foto diterima. Sekarang tulis gerakannya.\n"
                "Contoh: tersenyum dan melambai ke kamera",
                reply_markup=motion_kb(),
            )
        return
    if mode_hint == "edit":
        if cap:
            await submit_edit(msg, user_id, names, cap)
        else:
            user_state[user_id] = {"mode": "edit_caption", "names": names, "ts": time.time()}
            await msg.reply_text(
                "✏️ Foto diterima. Sekarang kirim perintah edit-nya sebagai pesan teks.",
                reply_markup=cancel_kb(),
            )
        return
    # Tanpa mode tombol: perilaku klasik — caption /video = I2V, caption lain
    # = edit, tanpa caption = simpan dulu & tunggu teks berikutnya.
    if cap.lower().startswith("/video"):
        if not is_admin_id(user_id):
            await msg.reply_text("🔒 Fitur video khusus admin.")
            return
        motion = cap[len("/video"):].strip() or DEFAULT_MOTION
        await submit_i2v(msg, user_id, names[0], motion)
    elif cap:
        await submit_edit(msg, user_id, names, cap)
    else:
        pending_photos[user_id] = {"names": names, "ts": time.time()}
        user_state[user_id] = {"mode": "edit_caption", "names": names, "ts": time.time()}
        hint = ("\nAtau /video <gerakan> buat digerakin jadi video."
                if is_admin_id(user_id) else "")
        await msg.reply_text(
            f"📸 {len(names)} foto diterima. Sekarang kirim perintah edit-nya sebagai pesan teks.\n"
            "Contoh: gabungkan kedua orang ini jadi foto pengantin adat Jawa serba putih"
            + hint
        )


async def finalize_album(media_group_id: str, gen: int):
    await asyncio.sleep(3)
    buf = album_buffers.get(media_group_id)
    if not buf or buf["gen"] != gen:
        return  # masih ada foto susulan; pemanggil terakhir yang mengeksekusi
    album_buffers.pop(media_group_id, None)
    await accept_photos(buf["msg"], buf["user_id"], buf["items"],
                        buf["caption"], buf["mode"])


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not await check_joined(ctx.bot, user.id):
        return
    msg = update.message
    photo = msg.photo[-1]
    tg_file = await photo.get_file()
    raw = bytes(await tg_file.download_as_bytearray())
    caption = (msg.caption or "").strip()
    st = user_state.get(user.id)
    mode_hint = None
    if st and time.time() - st["ts"] < STATE_TTL:
        if st["mode"] == "photo_edit":
            mode_hint = "edit"
            user_state.pop(user.id, None)
        elif st["mode"] == "photo_i2v":
            mode_hint = "i2v"
            i2v_engine[user.id] = st.get("engine", "wan")
            user_state.pop(user.id, None)
    group_id = msg.media_group_id
    if group_id:
        buf = album_buffers.setdefault(group_id, {
            "items": [], "caption": "", "gen": 0,
            "msg": msg, "user_id": user.id, "mode": mode_hint,
        })
        buf["items"].append(raw)
        if caption:
            buf["caption"] = caption
        buf["gen"] += 1
        asyncio.create_task(finalize_album(group_id, buf["gen"]))
    else:
        await accept_photos(msg, user.id, [raw], caption, mode_hint)


# ---------------------------------------------------------------------------
# Handler teks, perintah, tombol
# ---------------------------------------------------------------------------

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not await check_joined(ctx.bot, user.id):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    msg = update.message
    st = user_state.get(user.id)
    if st and time.time() - st["ts"] < STATE_TTL:
        mode = st["mode"]
        user_state.pop(user.id, None)
        if mode == "prompt_img":
            await submit_t2i(msg, user.id, text, st.get("engine", "turbo"))
            return
        if mode == "prompt_vid":
            await submit_t2v(msg, user.id, text, st.get("engine", "wan"))
            return
        if mode == "edit_caption":
            pending_photos.pop(user.id, None)
            await submit_edit(msg, user.id, st["names"], text)
            return
        if mode == "i2v_motion":
            await submit_i2v(msg, user.id, st["names"][0], text,
                             st.get("engine", "wan"))
            return
    pend = pending_photos.get(user.id)
    if pend and time.time() - pend["ts"] < STATE_TTL:
        pending_photos.pop(user.id, None)
        await submit_edit(msg, user.id, pend["names"], text)
        return
    await submit_t2i(msg, user.id, text)


async def send_gate(update: Update):
    await update.message.reply_text(GATE_TEXT, reply_markup=gate_kb())


async def send_menu(update: Update):
    user = update.effective_user
    await update.message.reply_text(
        menu_text(user.id, user.first_name or ""),
        reply_markup=menu_kb(user.id), parse_mode="Markdown",
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_joined(ctx.bot, update.effective_user.id):
        await send_gate(update)
        return
    await send_menu(update)


async def cmd_gambar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_joined(ctx.bot, update.effective_user.id):
        await send_gate(update)
        return
    prompt = " ".join(ctx.args).strip()
    if not prompt:
        await update.message.reply_text(
            "Isi prompt-nya, contoh: /gambar a cat wearing sunglasses"
        )
        return
    await submit_t2i(update.message, update.effective_user.id, prompt)


async def cmd_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_joined(ctx.bot, user_id):
        await send_gate(update)
        return
    if not is_admin_id(user_id):
        await update.message.reply_text("🔒 Fitur video khusus admin.")
        return
    prompt = " ".join(ctx.args).strip()
    pend = pending_photos.get(user_id)
    if pend and time.time() - pend["ts"] < STATE_TTL:
        pending_photos.pop(user_id, None)
        user_state.pop(user_id, None)
        await submit_i2v(update.message, user_id, pend["names"][0],
                         prompt or DEFAULT_MOTION)
        return
    if not prompt:
        await update.message.reply_text(
            "Isi prompt-nya, contoh: /video a cat walking on the beach at sunset\n"
            "Atau kirim foto dulu, terus /video <gerakan> buat gerakin foto itu."
        )
        return
    await submit_t2v(update.message, user_id, prompt)


ASK_TEXTS = {
    "act_img": ("prompt_img",
                "🖼 Kirim prompt gambarnya sekarang.\n"
                "Contoh: a majestic waterfall in a lush jungle, golden hour"),
    "act_edit": ("photo_edit",
                 "✏️ Kirim foto yang mau diedit (boleh 1 foto, atau album 2 foto "
                 "buat gabung dua orang). Habis itu tulis perintah edit-nya — "
                 "atau langsung tulis perintahnya sebagai caption foto."),
    "act_vid": ("prompt_vid",
                "🎬 Tulis adegan videonya sekarang.\n"
                "Contoh: a cat walking on the beach at sunset"),
    "act_i2v": ("photo_i2v",
                "📹 Kirim fotonya sekarang. Habis itu tulis gerakannya — "
                "atau tulis gerakannya langsung sebagai caption foto."),
}


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data or ""
    if data == "gate_check":
        if await check_joined(ctx.bot, user.id, force=True):
            await query.answer("Mantap, keanggotaan terverifikasi ✅")
            try:
                await query.edit_message_text(
                    menu_text(user.id, user.first_name or ""),
                    reply_markup=menu_kb(user.id), parse_mode="Markdown",
                )
            except Exception:  # noqa: BLE001
                pass
        else:
            await query.answer(
                "Kamu belum tercatat join channel. Join dulu ya, baru pencet lagi.",
                show_alert=True,
            )
        return
    if not await check_joined(ctx.bot, user.id):
        await query.answer("Join channel dulu ya 🙏", show_alert=True)
        return
    if data == "menu":
        user_state.pop(user.id, None)
        await query.answer()
        try:
            await query.edit_message_text(
                menu_text(user.id, user.first_name or ""),
                reply_markup=menu_kb(user.id), parse_mode="Markdown",
            )
        except Exception:  # noqa: BLE001
            pass
        return
    if data == "help":
        await query.answer()
        suffix = HELP_SUFFIX_ADMIN if is_admin_id(user.id) else HELP_SUFFIX_USER
        try:
            await query.edit_message_text(
                HELP_TEXT + suffix, reply_markup=back_kb(), parse_mode="Markdown",
            )
        except Exception:  # noqa: BLE001
            pass
        return
    if data in ("act_vid", "act_i2v"):
        if not is_admin_id(user.id):
            await query.answer("🔒 Fitur video khusus admin.", show_alert=True)
            return
        kind = "vid" if data == "act_vid" else "i2v"
        title = "🎬 Buat Video" if kind == "vid" else "📹 Gerakkan Foto"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Wan 2.2 — detail, lebih lambat",
                                  callback_data=f"eng:wan:{kind}")],
            [InlineKeyboardButton("Hunyuan 1.5 — lebih ringan",
                                  callback_data=f"eng:hy:{kind}")],
            [InlineKeyboardButton("⬅️ Menu", callback_data="menu")],
        ])
        await query.answer()
        try:
            await query.edit_message_text(
                f"{title}\nPilih mesin videonya:", reply_markup=kb,
            )
        except Exception:  # noqa: BLE001
            pass
        return
    if data.startswith("eng:"):
        _, eng, ekind = data.split(":")
        mode, text = ASK_TEXTS["act_vid" if ekind == "vid" else "act_i2v"]
        user_state[user.id] = {"mode": mode, "names": [], "engine": eng,
                               "ts": time.time()}
        await query.answer()
        try:
            await query.edit_message_text(text, reply_markup=cancel_kb())
        except Exception:  # noqa: BLE001
            pass
        return
    if data == "act_img":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Turbo — cepat",
                                  callback_data="model:t2i:turbo")],
            [InlineKeyboardButton("🎯 Akurat 2511 — nurut prompt, lambat",
                                  callback_data="model:t2i:acc")],
            [InlineKeyboardButton("⬅️ Menu", callback_data="menu")],
        ])
        await query.answer()
        try:
            await query.edit_message_text(
                "🖼 Buat Gambar\nPilih modelnya:", reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass
        return
    if data.startswith("model:"):
        _, scope, choice = data.split(":")
        await query.answer()
        if scope == "t2i":
            mode, text = ASK_TEXTS["act_img"]
            user_state[user.id] = {"mode": mode, "names": [],
                                   "engine": choice, "ts": time.time()}
            try:
                await query.edit_message_text(text, reply_markup=cancel_kb())
            except Exception:  # noqa: BLE001
                pass
            return
        # scope == "edit": jalankan job yang menunggu pilihan model.
        pj = PREJOBS.pop(user.id, None)
        if not pj:
            try:
                await query.edit_message_text(
                    "Sesi editnya udah lewat — kirim ulang fotonya ya.")
            except Exception:  # noqa: BLE001
                pass
            return
        if choice == "acc":
            graph = edit_graph(pj["instruction"], pj["names"], seed())
            dl = 1800
        else:
            graph = edit_graph_turbo(pj["instruction"], pj["names"], seed())
            dl = 900
        try:
            await query.edit_message_text(
                f"✏️ {pj['label']} — model dipilih, masuk antrian…")
        except Exception:  # noqa: BLE001
            pass
        await submit_job(pj["msg"], user.id, "image", graph, pj["label"],
                         pj["instruction"], deadline=dl)
        return
    if data in ASK_TEXTS:
        mode, text = ASK_TEXTS[data]
        user_state[user.id] = {"mode": mode, "names": [], "ts": time.time()}
        await query.answer()
        try:
            await query.edit_message_text(text, reply_markup=cancel_kb())
        except Exception:  # noqa: BLE001
            pass
        return
    if data == "cancel_state":
        user_state.pop(user.id, None)
        await query.answer("Dibatalkan.")
        try:
            await query.edit_message_text(
                menu_text(user.id, user.first_name or ""),
                reply_markup=menu_kb(user.id), parse_mode="Markdown",
            )
        except Exception:  # noqa: BLE001
            pass
        return
    if data == "default_motion":
        st = user_state.get(user.id)
        await query.answer()
        if st and st["mode"] == "i2v_motion":
            user_state.pop(user.id, None)
            await submit_i2v(query.message, user.id, st["names"][0],
                             DEFAULT_MOTION, st.get("engine", "wan"))
        return
    await query.answer()


async def _post_init(app: Application):
    asyncio.create_task(job_worker())
    if CHANNEL:
        logging.info("Gate channel aktif: %s", CHANNEL)
    else:
        logging.warning("CHANNEL_USERNAME kosong — gate join channel NONAKTIF.")


def main():
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO
    )
    # Jaringan VM ini lambat ke Telegram; timeout bawaan PTB (5 dtk) bikin
    # getMe saat start timeout terus. Longgarkan semua timeout.
    req = HTTPXRequest(
        connect_timeout=30.0, read_timeout=60.0,
        write_timeout=60.0, pool_timeout=30.0,
    )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(req)
        .get_updates_request(req)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(TypeHandler(Update, guard_update), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("gambar", cmd_gambar))
    app.add_handler(CommandHandler("video", cmd_video))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("blokir", cmd_blokir))
    app.add_handler(CommandHandler("unblokir", cmd_unblokir))
    app.add_handler(CommandHandler("blokirlist", cmd_blokirlist))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    print("Bot jalan. Ctrl+C untuk berhenti.")
    app.run_polling()


if __name__ == "__main__":
    main()
