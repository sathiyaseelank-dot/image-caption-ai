# Image Caption Generator AI

A CPU-friendly **Image Caption Generator** built with Hugging Face's BLIP model
and Gradio. Runs on any laptop — **no GPU required**.

## Quick start (Windows)

1. Download **`install.bat`** from the [Releases](../../releases) page
   (or copy it from this repo).
2. Double-click it.
3. Wait 10–20 minutes.
4. Double-click the **"Image Caption Generator"** shortcut on your Desktop.
5. Click **Download Model** in the app (~1 GB, one time only).
6. Upload an image → **Generate Caption**.

`install.bat` downloads this project automatically — you only need that one file.

## Quick start (any platform, manual)

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:7860

## How it works

The app checks for the AI model on startup:

- **Model present** → loads it and is ready immediately.
- **Model missing** → shows a **Download Model** button instead of failing.

The model is stored in `model_cache/` inside the project folder, so the whole
project is self-contained and portable.

## Project structure

```
image-caption-ai/
├── app.py              # The application (Gradio web UI)
├── run.bat             # Windows launcher
├── requirements.txt    # Python dependencies
├── model_cache/        # AI model lives here (created on first download)
└── README.md
```

## Requirements

| Item | Minimum |
|------|---------|
| OS | Windows 10/11 (64-bit), or Linux/macOS |
| Python | 3.10 – 3.12 |
| RAM | 8 GB recommended |
| Disk | ~4 GB free |
| GPU | **Not required** |

## Model details

| Property | Value |
|----------|-------|
| Model | `Salesforce/blip-image-captioning-base` |
| Size | ~945 MB |
| License | MIT |
| Speed | ~5 seconds per image on CPU |

## Architecture

```
Image → ViT vision encoder → Q-Former → text decoder → caption
```

**BLIP** = Bootstrapped Language-Image Pre-training (Salesforce Research).

## Troubleshooting

| Problem | Solution |
|---------|----------|
| App says "not installed" | Run `install.bat` first |
| Model download fails | Check internet, click **Download Model** again |
| Slow first start | Normal — model takes ~30s to load into memory |
| Port 7860 in use | Edit `app.py`, change `server_port=7860` to `7861` |
| Out of memory | Close other apps; 8 GB RAM recommended |

## License

MIT — free to use for college projects.
