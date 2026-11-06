#!/usr/bin/env python3
"""
Bot Telegram untuk ComfyUI lokal (Qwen-Image 2.1 Turbo GGUF).
- Kirim teks / perintah /gambar <prompt>  -> text-to-image
- Kirim foto + caption perintah          -> edit foto itu
- Kirim 2 foto (album) + caption          -> gabung dua foto jadi satu gambar edit
  (foto tanpa caption juga bisa: foto disimpan dulu, perintah editnya
   tinggal dikirim sebagai pesan teks berikutnya)
Ada progress bar live (websocket ComfyUI) yang di-edit ke pesan status.
Hanya merespons ADMIN_ID. Jalan di VM yang sama dengan ComfyUI (API localhost:8188).
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
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

COMFY = "http://127.0.0.1:8188"
COMFY_WS = "ws://127.0.0.1:8188/ws"
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
BOT_TOKEN = os.environ["BOT_TOKEN"]

# Aturan tetap untuk semua edit foto: dari foto referensi hanya wajah yang
# diambil — ekspresi dan pakaian selalu dibuat baru mengikuti caption,
# kecuali user menulis sebaliknya di caption-nya.
EDIT_PREFIX = (
    "ATURAN WAJIB, laksanakan sebelum membaca instruksi adegan: dari "
    "foto referensi ambil HANYA wajah dan identitas orangnya (bentuk "
    "wajah, struktur rahang, hidung, telinga, warna dan tekstur kulit, "
    "proporsi kepala). DILARANG menyalin ekspresi dari foto referensi: "
    "buat ekspresi baru yaitu kedua mata terbuka menatap kamera dan "
    "tersenyum natural, kecuali instruksi adegan meminta ekspresi lain. "
    "DILARANG memakai pakaian yang sama dengan foto referensi: ganti "
    "dengan pakaian baru sesuai instruksi adegan; bila adegan tidak "
    "menyebut pakaian, gunakan pakaian kasual rapi yang serasi dan "
    "berbeda dari foto referensi.\n\nInstruksi adegan: "
)

COMBINE_PREFIX = (
    "Gabungkan semua orang dari foto-foto referensi menjadi SATU foto "
    "bersama dalam satu frame, semua wajah terlihat jelas. "
)

UNET = "qwen-image-2.1-turbo-Q8_0.gguf"
CLIP = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"

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

PENDING_TTL = 600  # detik, foto yang menunggu perintah edit

job_lock = asyncio.Lock()
pending_photos: dict[int, dict] = {}   # user_id -> {"names": [...], "ts": float}
album_buffers: dict[str, dict] = {}    # media_group_id -> {"items": [...], "caption": str, "gen": int}

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


def edit_graph(prompt: str, image_names: list[str], seed: int):
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
    g["13"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": encode_inputs}
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


def _stitch_mp4(frames: list[bytes]) -> bytes:
    """Rakit frame PNG jadi mp4 (H.264) pakai ffmpeg di VM ini."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for i, fr in enumerate(frames):
            with open(os.path.join(td, f"f{i:04d}.png"), "wb") as fh:
                fh.write(fr)
        out = os.path.join(td, "out.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-framerate", str(VIDEO_FPS),
             "-i", os.path.join(td, "f%04d.png"),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out],
            check=True, capture_output=True, timeout=600,
        )
        with open(out, "rb") as fh:
            return fh.read()


def _result_for(pid: str, kind: str):
    if kind == "video":
        frames = _fetch_frames(pid)
        if frames is None:
            return None
        return _stitch_mp4(frames)
    return _fetch_result(pid)


def comfy_run(graph, state: dict, kind: str = "image",
              deadline_s: int = 900) -> bytes:
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
                img = _result_for(pid, kind)
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
                    img = _result_for(pid, kind)
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


def is_admin(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == ADMIN_ID


def bar_text(state: dict, label: str, emoji: str = "🎨") -> str:
    stage = state.get("stage") or "Siapkan"
    pct = state.get("pct")
    if pct is None:
        return f"{emoji} {label} — {stage}…"
    filled = max(0, min(10, round(pct / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    return f"{emoji} {label} — {stage}…\n[{bar}] {pct}%"


async def run_job(update: Update, graph, label: str, kind: str = "image"):
    if job_lock.locked():
        await update.message.reply_text("⏳ Masih ada proses yang jalan, tunggu selesai dulu ya.")
        return
    emoji = "🎬" if kind == "video" else "🎨"
    async with job_lock:
        note = await update.message.reply_text(f"{emoji} {label} — mulai…")
        await update.message.chat.send_action(
            ChatAction.UPLOAD_VIDEO if kind == "video" else ChatAction.UPLOAD_PHOTO
        )
        state: dict = {"stage": "Antri", "pct": None}

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
        try:
            data = await asyncio.to_thread(
                comfy_run, graph, state, kind,
                1800 if kind == "video" else 900,
            )
            edit_task.cancel()
            if kind == "video":
                await update.message.reply_video(
                    video=io.BytesIO(data), supports_streaming=True
                )
            else:
                await update.message.reply_photo(photo=io.BytesIO(data))
            await note.delete()
        except Exception as e:  # noqa: BLE001
            edit_task.cancel()
            try:
                await note.edit_text(f"❌ Gagal: {e}")
            except Exception:  # noqa: BLE001
                pass


async def start_edit(update: Update, names: list[str], instruction: str):
    label = "Edit foto" if len(names) == 1 else "Gabung foto"
    await run_job(
        update,
        edit_graph(instruction, names, random.randint(0, 2**31 - 1)),
        label,
    )


async def start_video_t2v(update: Update, prompt: str):
    await run_job(
        update,
        wan_t2v_graph(prompt, random.randint(0, 2**31 - 1)),
        "Bikin video",
        kind="video",
    )


async def start_video_i2v(update: Update, name: str, motion: str):
    await run_job(
        update,
        wan_i2v_graph(name, motion, random.randint(0, 2**31 - 1)),
        "Gerakin foto jadi video",
        kind="video",
    )


async def accept_photos(update: Update, raw_list: list[bytes], caption: str):
    """Upload foto ke ComfyUI, lalu edit langsung (ada caption) atau simpan dulu."""
    user_id = update.effective_user.id
    names = []
    try:
        for i, raw in enumerate(raw_list):
            name = await asyncio.to_thread(
                comfy_upload, raw, f"tele_{user_id}_{int(time.time())}_{i}.jpg"
            )
            names.append(name)
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"❌ Gagal upload foto ke ComfyUI: {e}")
        return
    cap = caption.strip()
    if cap.lower().startswith("/video"):
        motion = cap[len("/video"):].strip() or DEFAULT_MOTION
        await start_video_i2v(update, names[0], motion)
    elif cap:
        await start_edit(update, names, cap)
    else:
        pending_photos[user_id] = {"names": names, "ts": time.time()}
        await update.message.reply_text(
            f"📸 {len(names)} foto diterima. Sekarang kirim perintah edit-nya sebagai pesan teks.\n"
            "Contoh: gabungkan kedua orang ini jadi foto pengantin adat Jawa serba putih"
        )


async def finalize_album(media_group_id: str, gen: int, first_update: Update):
    await asyncio.sleep(3)
    buf = album_buffers.get(media_group_id)
    if not buf or buf["gen"] != gen:
        return  # masih ada foto susulan; pemanggil terakhir yang mengeksekusi
    album_buffers.pop(media_group_id, None)
    await accept_photos(first_update, buf["items"], buf["caption"])


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    raw = bytes(await tg_file.download_as_bytearray())
    group_id = update.message.media_group_id
    caption = (update.message.caption or "").strip()
    if group_id:
        buf = album_buffers.setdefault(
            group_id, {"items": [], "caption": "", "gen": 0}
        )
        buf["items"].append(raw)
        if caption:
            buf["caption"] = caption
        buf["gen"] += 1
        asyncio.create_task(finalize_album(group_id, buf["gen"], update))
    else:
        await accept_photos(update, [raw], caption)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "Halo bro 👋\n\n"
        "• Kirim teks biasa atau /gambar <prompt> → bikin gambar dari teks.\n"
        "• Kirim foto + caption perintah → edit foto itu.\n"
        "• Kirim 2 foto sekaligus (album) + caption → gabung dua foto jadi satu gambar.\n"
        "• Foto tanpa caption juga bisa — perintah editnya kirim sebagai teks setelahnya.\n"
        "• Aturan bawaan edit: dari foto cuma wajahnya yang dipakai — ekspresi & pakaian selalu dibikin baru ngikutin caption (kecuali lu tulis sebaliknya).\n"
        "• /video <prompt> → bikin video dari teks (±5 detik, render makan menit).\n"
        "• Kirim foto + caption diawali /video → foto itu digerakin jadi video. Contoh caption: /video tersenyum dan melambai ke kamera\n\n"
        "Contoh: kirim foto kamu + foto pasangan sebagai album, caption-nya: "
        "combine both people into a Javanese wedding portrait, white traditional attire"
    )


async def cmd_gambar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    prompt = " ".join(ctx.args).strip()
    if not prompt:
        await update.message.reply_text("Isi prompt-nya bro, contoh: /gambar a cat wearing sunglasses")
        return
    await run_job(update, t2i_graph(prompt, random.randint(0, 2**31 - 1)), "Bikin gambar")


async def cmd_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    prompt = " ".join(ctx.args).strip()
    user_id = update.effective_user.id
    pend = pending_photos.get(user_id)
    if pend and time.time() - pend["ts"] < PENDING_TTL:
        pending_photos.pop(user_id, None)
        await start_video_i2v(update, pend["names"][0], prompt or DEFAULT_MOTION)
        return
    if not prompt:
        await update.message.reply_text(
            "Isi prompt-nya bro, contoh: /video a cat walking on the beach at sunset\n"
            "Atau kirim foto dulu, terus /video <gerakan> buat gerakin foto itu."
        )
        return
    await start_video_t2v(update, prompt)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    user_id = update.effective_user.id
    pend = pending_photos.get(user_id)
    if pend and time.time() - pend["ts"] < PENDING_TTL:
        pending_photos.pop(user_id, None)
        await start_edit(update, pend["names"], text)
        return
    await run_job(update, t2i_graph(text, random.randint(0, 2**31 - 1)), "Bikin gambar")


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
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("gambar", cmd_gambar))
    app.add_handler(CommandHandler("video", cmd_video))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    print("Bot jalan. Ctrl+C untuk berhenti.")
    app.run_polling()


if __name__ == "__main__":
    main()
