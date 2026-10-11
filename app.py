"""
Image Caption Generator AI
==========================
A CPU-friendly image captioning app using the BLIP model.

The app checks for the AI model on startup:
  - If the model is already downloaded, it loads and is ready to use.
  - If not, it shows a "Download Model" button instead of failing.

The model is kept inside the project folder (model_cache/) so the whole
project is self-contained and portable.

Usage:
    Double-click run.bat  (Windows)
    or: python app.py     (any platform)

Then open http://127.0.0.1:7860 in your browser.
"""

import os
import time

# Keep the model inside the project folder so the whole thing is portable.
# This MUST be set before transformers/huggingface_hub are imported, so the
# cache path is picked up.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_HOME", os.path.join(PROJECT_DIR, "model_cache"))

import gradio as gr
import torch
from PIL import Image

# ============================================================
# Configuration
# ============================================================
MODEL_NAME = "Salesforce/blip-image-captioning-base"
MAX_CAPTION_LENGTH = 50
DEVICE = "cpu"  # No GPU required

# Filled in once the model is loaded.
_processor = None
_model = None


# ============================================================
# Model presence check
# ============================================================
def model_is_downloaded() -> bool:
    """Check whether the model weights are already in the local cache."""
    try:
        from huggingface_hub import try_to_load_from_cache

        result = try_to_load_from_cache(MODEL_NAME, "pytorch_model.bin")
        return isinstance(result, str) and os.path.exists(result)
    except Exception:
        # If the check itself fails, report "not downloaded" so the user
        # gets the download button rather than a crash.
        return False


def load_model() -> tuple[bool, str]:
    """Load the model into memory. Returns (ok, message)."""
    global _processor, _model

    if _model is not None:
        return True, "Model already loaded."

    try:
        from transformers import BlipProcessor, BlipForConditionalGeneration

        _processor = BlipProcessor.from_pretrained(MODEL_NAME)
        _model = BlipForConditionalGeneration.from_pretrained(MODEL_NAME)
        _model.to(DEVICE)
        _model.eval()
        return True, "Model loaded successfully."

    except Exception as exc:
        _processor = None
        _model = None
        return False, f"Could not load the model: {exc}"


def download_model(progress=gr.Progress()):
    """Download the model, reporting progress. Yields status messages."""
    global _processor, _model

    try:
        from transformers import BlipProcessor, BlipForConditionalGeneration

        progress(0.05, desc="Starting download...")
        yield "Downloading the AI model (~1 GB). This can take 5-15 minutes."

        progress(0.15, desc="Downloading processor...")
        _processor = BlipProcessor.from_pretrained(MODEL_NAME)

        progress(0.35, desc="Downloading model weights (the big part)...")
        yield "Downloading model weights (~945 MB). Please wait..."

        _model = BlipForConditionalGeneration.from_pretrained(MODEL_NAME)
        _model.to(DEVICE)
        _model.eval()

        progress(1.0, desc="Done")
        yield "**Model downloaded and ready.** Upload an image below."

    except Exception as exc:
        _processor = None
        _model = None
        yield (
            f"**Download failed:** {exc}\n\n"
            "Check your internet connection, then click **Download Model** again."
        )


# ============================================================
# Core function
# ============================================================
def generate_caption(image: Image.Image) -> str:
    """Take a PIL Image and return a text caption."""
    if image is None:
        return "Please upload an image first."

    if _model is None or _processor is None:
        return "The model is not loaded yet. Click 'Download Model' first."

    if image.mode != "RGB":
        image = image.convert("RGB")

    inputs = _processor(images=image, return_tensors="pt")
    inputs = {k: v.to(DEVICE) for k, v in inputs.items()}

    with torch.no_grad():
        output = _model.generate(
            **inputs,
            max_new_tokens=MAX_CAPTION_LENGTH,
            num_beams=5,
            early_stopping=True,
        )

    return _processor.decode(output[0], skip_special_tokens=True)


# ============================================================
# Interface
# ============================================================
def build_interface():
    already_have_model = model_is_downloaded()

    if already_have_model:
        print("Model found in cache - loading...")
        ok, message = load_model()
        print(message)
    else:
        print("Model not downloaded yet - the app will offer a Download button.")
        ok = False

    with gr.Blocks(title="Image Caption Generator AI") as demo:
        gr.Markdown("""
        # Image Caption Generator AI
        Upload any image and get an AI-generated description.
        """)

        # ---- Model status + download button ----
        status = gr.Markdown(
            value=(
                "**Model ready.** Upload an image below."
                if ok
                else "**Model not downloaded yet.** Click **Download Model** to get it (~1 GB, one time only)."
            )
        )

        download_btn = gr.Button(
            "Download Model",
            variant="secondary",
            visible=not ok,
        )

        # ---- Main app ----
        with gr.Row():
            with gr.Column():
                image_input = gr.Image(label="Upload Image", type="pil", height=400)
                submit_btn = gr.Button("Generate Caption", variant="primary", size="lg")

            with gr.Column():
                caption_output = gr.Textbox(
                    label="Generated Caption",
                    placeholder="Your caption will appear here...",
                    lines=3,
                    interactive=False,
                )
                time_output = gr.Textbox(label="Processing Time", interactive=False)

        def on_generate(image):
            start = time.time()
            caption = generate_caption(image)
            return caption, f"{time.time() - start:.2f} seconds"

        def on_download():
            """Stream download progress into the status line."""
            for message in download_model():
                yield message

        submit_btn.click(
            fn=on_generate,
            inputs=[image_input],
            outputs=[caption_output, time_output],
        )
        image_input.change(
            fn=on_generate,
            inputs=[image_input],
            outputs=[caption_output, time_output],
        )
        download_btn.click(fn=on_download, inputs=[], outputs=[status])

    return demo


if __name__ == "__main__":
    demo = build_interface()
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True,
        inbrowser=False,
    )
