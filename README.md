# ComfyBot Studio

**A phone-first AI image & video studio that runs on affordable, older-generation GPUs — controlled entirely from Telegram.**

No desktop, no canvas-dragging on a phone screen. You chat with a bot: send a prompt and get an image, send a photo with a caption and get an edit, send two photos and get a merged portrait, or ask for a video and get an MP4 back — with a live progress bar in the chat while it renders.

The whole stack is self-hosted on a single rented GPU instance and built from open-weight models in GGUF format.

## What it does

- **Text → image** — send any prompt as a chat message
- **Photo + caption → edited photo** — background swaps, restyles, scene changes
- **Two-photo album → merged portrait** — combines two people into one frame, preserving each face's identity while generating fresh expressions and outfits (the reference photos contribute *who* the people are, not their original expressions or clothes)
- **Text → video** — `/video <prompt>` renders a ~5 second 480p clip
- **Photo + `/video <motion>` → image-to-video** — animates a still photo
- **Live progress** — a status message in Telegram updates per sampling step via ComfyUI's websocket API
- **Single-operator lock** — the bot only responds to one configured Telegram user ID
- **Runs unattended** — ComfyUI and the bot are systemd services with auto-restart

### Public mode (v2)

The bot now ships in a public configuration built for sharing with a community:

- **Open to all Telegram users**, gated by **mandatory channel membership** — users must join a configured Telegram channel first (checked live via `getChatMember`; the bot must be an admin in that channel)
- **Button-first UI** — an inline-keyboard main menu (Make Image / Edit Photo / Make Video / Animate Photo / Help); no commands to memorize, text commands still work
- **Global queue** — one GPU means one job at a time; everyone sees their queue position and a live progress bar
- **Per-user daily quotas** for images and videos (configurable in `.env`, admin exempt, failed jobs refunded)
- **Video is admin-only** in public mode — regular users get image generation and editing; the expensive video features stay with the operator

## Why GGUF, and why a V100?

This project targets the hardware that is actually affordable for indie builders in Southeast Asia: previous-generation datacenter cards like the Tesla V100 (Volta, `sm_70`). Two hard constraints shaped the design:

1. **Volta has no FP8 support.** The official ComfyUI templates for newer models download FP8 weights that simply do not run on a V100. The working path is **GGUF quantization** (via [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)) — the same flagship models, quantized to fit and run on 32 GB of VRAM.
2. **The torch build matters.** Current torch/CUDA combinations have dropped Volta support or crash in quantized ops. The install scripts pin `torch==2.8.0` with `cu128`, the last combination verified working end-to-end for this stack.

Current models in use:

| Task | Model | Format |
| --- | --- | --- |
| Image generate / edit | Qwen-Image-2.1 | GGUF Q8_0 + Qwen3-VL 8B encoder (INT8) |
| Video (T2V + I2V) | Wan 2.2 A14B | GGUF Q6_K, dual expert (high/low noise) |
| Video acceleration | LightX2V "Lightning" LoRAs (Seko) | 4-step, CFG 1.0 |

## Hardware & cost

Developed and running on a **Tesla V100 SXM3 32GB** instance from [CloudRift](https://www.cloudrift.ai) at **$0.28/hour** — roughly 3,500 GPU hours per $1,000. A V100 render of a 5-second 480p video takes on the order of 10–20 minutes; images take under a minute. RTX 4090 benchmarks of the identical workflows are on the roadmap so the guide can show honest speed/quality trade-offs per tier.

## Repository layout

```
setup.sh     One-paste installer for a fresh VPS (everything below, unattended)
install/     One-shot install scripts (Qwen-Image stack, Wan 2.2 stack)
workflows/   ComfyUI API-format workflow JSON (GGUF image generate + edit)
bot/         The Telegram bot (python-telegram-bot) + .env.example
systemd/     Service units for ComfyUI and the bot (edit user/paths first)
```

## Quick start

**One-paste install** on a fresh Ubuntu 24.04 GPU instance — installs ComfyUI, both model stacks (~79 GB), the bot and the systemd services, then asks for your bot token, admin ID and channel:

```bash
curl -fsSL https://raw.githubusercontent.com/agusrizal17/comfybot-studio/main/setup.sh | bash
```

Manual route, assuming ComfyUI is already cloned to `~/ComfyUI`:

```bash
# 1. Image stack (Qwen-Image GGUF + pinned torch)
bash install/install-qwen.sh

# 2. Video stack (Wan 2.2 A14B GGUF, ~61 GB of downloads, resumable)
bash install/install-wan.sh

# 3. Telegram bot
cd bot
cp .env.example .env   # fill in BOT_TOKEN (from @BotFather) and your ADMIN_ID
pip install -r requirements.txt
python bot.py
```

For unattended operation, adapt the units in `systemd/` (replace `YOUR_USER` and paths) and `systemctl enable --now comfyui comfy-tele-bot`.

## Pitfalls this project documents (so you don't have to rediscover them)

- **FP8 templates ≠ Volta.** If a template offers to download a 15 GB FP8 safetensors for a V100, stop — use the GGUF build of the same model.
- **`ImageScaleToTotalPixels` needs `resolution_steps`** in API-format workflows, or ComfyUI rejects the prompt with a 400.
- **ComfyUI behind a tunnel returns 403** unless started with `--enable-cors-header <tunnel-origin>`.
- **One driver at a time.** While the bot is rendering, don't queue or interrupt jobs from the web UI — an interrupt kills the running render mid-sampler. The bot talks to `localhost:8188` directly; the web UI (and any tunnel) is optional.
- **Slow Telegram networks** need generous HTTP timeouts in the bot (the defaults time out on `getMe`); see `HTTPXRequest` in `bot/bot.py`.

## Status & roadmap

Working end-to-end since November 2026 on a single V100 instance. Next:

- [ ] RTX 4090 benchmark pass (same workflows, both tiers documented)
- [ ] Public release of the bot (multi-user mode with per-user quotas)
- [ ] Written tutorial series (Indonesian + English summary) covering setup and the Volta pitfalls above
- [ ] Short demo videos of generated results

## Credits

- **GPU infrastructure:** [CloudRift](https://www.cloudrift.ai) — this project is developed and run on CloudRift GPU instances.
- **Engine:** [ComfyUI](https://github.com/comfyanonymous/ComfyUI) and [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)
- **Models:** Qwen-Image (Qwen team), Wan 2.2 (Wan team), LightX2V Lightning LoRAs — each model keeps its own license; check the model card before any commercial use. The code in this repository is MIT-licensed; the models are not.

## License

MIT — see `LICENSE`.
