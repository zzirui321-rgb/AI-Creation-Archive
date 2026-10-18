"""Optional local Grounding DINO + SAM 2 service; installs no packages."""

import argparse
import base64
import hashlib
import io
import os
import secrets
import threading

DETECTOR = "IDEA-Research/grounding-dino-tiny"
DETECTOR_REVISION = "a2bb814dd30d776dcf7e30523b00659f4f141c71"
SEGMENTER = "facebook/sam2.1-hiera-tiny"
SEGMENTER_REVISION = "de431c4043854a71d8101e17995dfe596bf101a5"


def download_models(cache):
    from huggingface_hub import snapshot_download

    return [
        snapshot_download(
            identifier,
            revision=revision,
            cache_dir=cache,
            allow_patterns=["*.json", "*.safetensors", "vocab.txt", "tokenizer*"],
        )
        for identifier, revision in ((DETECTOR, DETECTOR_REVISION), (SEGMENTER, SEGMENTER_REVISION))
    ]


class GroundedSAM:
    def __init__(self, paths, device="cpu"):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model, Sam2Processor

        self.torch, self.device = torch, device
        torch.set_num_threads(min(8, os.cpu_count() or 1))
        self.detector_processor = AutoProcessor.from_pretrained(paths[0], local_files_only=True)
        self.detector = (
            AutoModelForZeroShotObjectDetection.from_pretrained(paths[0], local_files_only=True, use_safetensors=True)
            .eval()
            .to(device)
        )
        self.mask_processor = Sam2Processor.from_pretrained(paths[1], local_files_only=True)
        self.mask_model = (
            Sam2Model.from_pretrained(paths[1], local_files_only=True, use_safetensors=True).eval().to(device)
        )
        self.lock = threading.Lock()

    def segment(self, data, targets):
        from PIL import Image, ImageOps
        from torchvision.ops import nms

        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 16_777_216:
                raise ValueError("Image exceeds pixel limit")
            image = ImageOps.exif_transpose(source).convert("RGB")
        with self.lock, self.torch.inference_mode():
            inputs = self.detector_processor(
                images=[image] * len(targets),
                text=[target.strip().rstrip(".") + "." for target in targets],
                padding=True,
                return_tensors="pt",
            ).to(self.device)
            outputs = self.detector(**inputs)
            detections = self.detector_processor.post_process_grounded_object_detection(
                outputs,
                inputs.input_ids,
                threshold=0.3,
                text_threshold=0.25,
                target_sizes=[(image.height, image.width)] * len(targets),
            )
            candidates = []
            for target, detection in zip(targets, detections):
                boxes, scores = detection["boxes"], detection["scores"]
                indices = nms(boxes, scores, 0.6)[:16].tolist()
                if not indices:
                    continue
                selected = boxes[indices].cpu().tolist()
                mask_inputs = self.mask_processor(images=image, input_boxes=[selected], return_tensors="pt").to(
                    self.device
                )
                masks = self.mask_model(**mask_inputs, multimask_output=False)
                restored = self.mask_processor.post_process_masks(
                    masks.pred_masks.cpu(), mask_inputs["original_sizes"].cpu()
                )[0]
                labels = detection.get("text_labels", detection.get("labels", []))
                for position, index in enumerate(indices):
                    mask = Image.fromarray((restored[position, 0].numpy() * 255).astype("uint8"))
                    buffer = io.BytesIO()
                    mask.save(buffer, "PNG")
                    candidates.append(
                        {
                            "label": str(labels[index]),
                            "target": target,
                            "score": float(scores[index]),
                            "mask_base64": base64.b64encode(buffer.getvalue()).decode(),
                        }
                    )
        return {
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "model": {
                "detector": DETECTOR,
                "detector_revision": DETECTOR_REVISION,
                "segmenter": SEGMENTER,
                "segmenter_revision": SEGMENTER_REVISION,
            },
            "candidates": candidates,
        }


def create_app(model, token=""):
    from fastapi import FastAPI, Header, HTTPException, Request
    from starlette.concurrency import run_in_threadpool

    app = FastAPI(title="AiCA local semantic segmentation")

    @app.get("/health")
    def health():
        return {"status": "ready", "device": model.device, "detector": DETECTOR, "segmenter": SEGMENTER}

    @app.post("/segment")
    async def segment(request: Request, authorization: str = Header(default="")):
        if token and not secrets.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Segmentation authentication required")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 28 * 1024 * 1024:
                raise HTTPException(413, "Image request exceeds byte limit")
            chunks.append(chunk)
        import json

        try:
            body = json.loads(b"".join(chunks))
            encoded, targets = body["image_base64"], body["targets"]
            if (
                not isinstance(targets, list)
                or not 1 <= len(targets) <= 8
                or any(not isinstance(item, str) or not 0 < len(item) <= 200 for item in targets)
            ):
                raise ValueError("Invalid target descriptions")
            data = base64.b64decode(encoded, validate=True)
            return await run_in_threadpool(model.segment, data, targets)
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise HTTPException(422, "Invalid segmentation input") from error

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--port", type=int, default=8101)
    parser.add_argument(
        "--user-site-last", action="store_true", help="Use environment packages before existing user-site dependencies"
    )
    args = parser.parse_args()
    if args.user_site_last:
        import site
        import sys

        shared = site.getusersitepackages()
        if shared not in sys.path:
            sys.path.append(shared)
    paths = download_models(args.cache_dir)
    if args.download_only:
        print("Pinned detector and segmenter snapshots downloaded.")
    else:
        import uvicorn

        model = GroundedSAM(paths, args.device)
        uvicorn.run(create_app(model, os.environ.get("AICA_SEGMENT_API_KEY", "")), host="127.0.0.1", port=args.port)
