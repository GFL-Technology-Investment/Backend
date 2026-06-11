from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union, Dict

import torch
import numpy as np
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForObjectDetection

from gfl_core.models.layout.processor import LayoutProcessor
from gfl_core.models.layout.options import PPDocLayoutV3Options, AcceleratorOptions
from gfl_core.models.layout.label_mapping import PP_DOCLAYOUT_V3_LABELS, THRESHOLD_BY_CLASS
from gfl_core.utils.color_map import get_colormap, font_colormap
from gfl_core.utils.image_utils import load_image_to_pil


class PPDocLayoutV3TransformersPredictor:

    def __init__(
        self,
        *,
        layout_options: PPDocLayoutV3Options,
        accelerator_options: AcceleratorOptions,
        artifacts_path: Optional[Union[Path, str]] = None,
    ):
        
        self._options = layout_options
        self._accelerator_options = accelerator_options
        self._artifacts_path = Path(artifacts_path)

        self._model, self._image_processor = None, None

        device = getattr(accelerator_options, "device", None)
        device_id = getattr(accelerator_options, "device_id", None)
        if (
            device and device.startswith("cuda") 
            and device_id is not None
        ):
            self._device = f"cuda:{str(device_id)}"
        else:
            self._device = "cpu"

    def _resolve_model_path(self) -> Path:
        from huggingface_hub import snapshot_download

        if (
            self._artifacts_path is None or
            not self._artifacts_path.exists()
        ):

            download_models = snapshot_download(
                repo_id="PaddlePaddle/PP-DocLayoutV3_safetensors",
                revision="main",
                local_dir=None,
            )

            model_folder = Path(download_models)
        
        return model_folder

    def _get_infer_device(self, model=None):
        import torch

        infer_model = model or getattr(self, "infer", None)
        if infer_model is None:
            return torch.device("cpu")
        if hasattr(infer_model, "device"):
            return infer_model.device
        try:
            return next(infer_model.parameters()).device
        except StopIteration:
            return torch.device("cpu")
        
    def _move_to_infer_device(self, model_inputs, model=None):
        infer_model = model or getattr(self, "infer", None)
        device = self._get_infer_device(model=infer_model)

        if hasattr(model_inputs, "to") and callable(getattr(model_inputs, "to")):
            model_inputs = model_inputs.to(device)
        elif isinstance(model_inputs, dict):
            model_inputs = {
                k: v.to(device) if torch.is_tensor(v) else v
                for k, v in model_inputs.items()
            }
        else:
            raise TypeError(
                f"Unsupported model_inputs type: {type(model_inputs)!r}; "
                "expected a Hugging Face BatchFeature/BatchEncoding or a dict of tensors."
            )

        target_dtype = None
        if infer_model is not None:
            try:
                target_dtype = next(infer_model.parameters()).dtype
            except StopIteration:
                pass

        if target_dtype is not None and target_dtype in (
            torch.float16,
            torch.bfloat16,
            torch.float32,
        ):
            for key in list(model_inputs.keys()):
                value = model_inputs[key]
                if torch.is_tensor(value) and value.is_floating_point():
                    if value.dtype != target_dtype:
                        model_inputs[key] = value.to(dtype=target_dtype)

        return model_inputs

    def _format_layout_transformers_output(self, prediction: List[Dict]) -> Dict:
        boxes = prediction["boxes"].detach().cpu().numpy()
        scores = prediction["scores"].detach().cpu().numpy()
        labels = prediction["labels"].detach().cpu().numpy()
        if len(boxes) == 0:
            return np.empty((0, 6), dtype=np.float32)
        
        formatted = np.concatenate(
            [
                labels[:, None].astype(np.float32, copy=False),
                scores[:, None].astype(np.float32, copy=False),
                boxes.astype(np.float32, copy=False),
            ],
            axis=1,
        )

        if "order_seq" in prediction:
            order_seq = (
                prediction["order_seq"]
                .detach()
                .cpu()
                .numpy()
                .astype(np.float32, copy=False)
            )
            if len(formatted) == len(order_seq):
                formatted = np.concatenate([formatted, order_seq[:, None]], axis=1)

        output = {"boxes": formatted}
        if "polygon_points" in prediction:
            output["polygon_points"] = [
                np.asarray(points) for points in prediction["polygon_points"]
            ]
        return output
    
    def _get_hf_threshold(self, threshold: Optional[float | Dict]):
        effective_threshold = threshold if threshold is not None else self.threshold
        if effective_threshold is None:
            effective_threshold = 0.5
        if isinstance(effective_threshold, dict):
            return effective_threshold, 0.0
        return effective_threshold, float(effective_threshold)        

    def initialize(self) -> None:
        model_folder = self._resolve_model_path()

        self._image_processor = AutoImageProcessor.from_pretrained(str(model_folder))
        self._model = AutoModelForObjectDetection.from_pretrained(
            str(model_folder)
        ).to(self._device)
        self._model.eval()

        # ID2LABEL
        self._id2label = getattr(self._model.config, "id2label", None)
        if self._id2label is None:
            self._id2label = PP_DOCLAYOUT_V3_LABELS

        labels = list(self._id2label.values())

        self.layout_postprocess = LayoutProcessor(labels)

        # Threshold
        threshold = self._options.threshold
        if threshold is None:
            self.threshold = THRESHOLD_BY_CLASS
        else:
            self.threshold = threshold

    def predict_batch(self, images: List[Image.Image]) -> List[List[Dict]]:

        if len(images) == 0:
            return []
        
        if self._model is None or self._image_processor is None:
            raise RuntimeError("Not initialized. Call initialize() first.")

        images = [load_image_to_pil(image) for image in images]
        effective_threshold, hf_threshold = self._get_hf_threshold(self.threshold)

        # Preprocess images using HF processor
        inputs = self._image_processor(images=images, return_tensors="pt")
        inputs = self._move_to_infer_device(inputs, self._model)

        with torch.inference_mode():
            outputs = self._model(**inputs)

        target_sizes = torch.tensor(
            [image.size[::-1] for image in images], device=self._device, dtype=torch.int64
        )

        predictions = self._image_processor.post_process_object_detection(
            outputs, threshold=hf_threshold, target_sizes=target_sizes
        )

        batch_outputs = [
            self._format_layout_transformers_output(prediction)
            for prediction in predictions
        ]

        boxes = self.layout_postprocess(
            images,
            batch_outputs,
            threshold=effective_threshold,
            layout_nms=self._options.layout_nms,
            layout_unclip_ratio=self._options.layout_unclip_ratio,
            layout_merge_bboxes_mode=self._options.layout_merge_bboxes_mode,
            layout_shape_mode=self._options.layout_shape_mode,
            filter_overlap_boxes=self._options.filter_overlap_boxes,
            skip_order_labels=self._options.skip_order_labels,
        )

        return boxes
    
    def predict(self, image: Image.Image) -> List[Dict]:
        self._options.batch_size = 1
        return self.predict_batch(images=[image])[0]
    
    def visualize(self, image, boxes: List[Dict]) -> Image.Image:
        import PIL
        from PIL import ImageFont, ImageDraw

        font_size = int(0.018 * int(image.width)) + 2
        # font = ImageFont.truetype()
        font = ImageFont.load_default()

        draw_thickness = int(max(image.size) * 0.002)
        draw = ImageDraw.Draw(image)
        label2color = {}
        catid2fontcolor = {}
        color_list = get_colormap(rgb=True)

        for i, dt in enumerate(boxes):
            label, bbox, score = dt["label"], dt["coordinate"], dt["score"]
            if label not in label2color:
                color_index = i % len(color_list)
                label2color[label] = color_list[color_index]
                catid2fontcolor[label] = font_colormap(color_index)
            color = tuple(label2color[label])
            font_color = tuple(catid2fontcolor[label])

            if len(bbox) == 4:
                # draw bbox of normal object detection
                xmin, ymin, xmax, ymax = bbox
                rectangle = [
                    (xmin, ymin),
                    (xmin, ymax),
                    (xmax, ymax),
                    (xmax, ymin),
                    (xmin, ymin),
                ]
            else:
                raise ValueError(
                    f"Only support bbox format of [xmin,ymin,xmax,ymax] or [x1,y1,x2,y2,x3,y3,x4,y4], got bbox of shape {len(bbox)}."
                )

            # draw bbox
            draw.line(
                rectangle,
                width=draw_thickness,
                fill=color,
            )

            # draw label
            text = "{} {:.2f}".format(dt["label"], score)
            if tuple(map(int, PIL.__version__.split("."))) <= (10, 0, 0):
                tw, th = draw.textsize(text, font=font)
            else:
                left, top, right, bottom = draw.textbbox((0, 0), text, font)
                tw, th = right - left, bottom - top + 4
            if ymin < th:
                draw.rectangle([(xmin, ymin), (xmin + tw + 4, ymin + th + 1)], fill=color)
                draw.text((xmin + 2, ymin - 2), text, fill=font_color, font=font)
            else:
                draw.rectangle([(xmin, ymin - th), (xmin + tw + 4, ymin + 1)], fill=color)
                draw.text((xmin + 2, ymin - th - 2), text, fill=font_color, font=font)

            text_position = (bbox[2] + 2, bbox[1] - font_size // 2)
            if int(image.width) - bbox[2] < font_size:
                text_position = (
                    int(bbox[2] - font_size * 1.1),
                    bbox[1] - font_size // 2,
                )
            draw.text(text_position, str(i + 1), font=font, fill="red")

        return image
    
if __name__ == "__main__":
    image_path = "/mnt/data1/home/staging/workspace/zplus/data/id.png"
    pil_image = Image.open(image_path).convert("RGB")

    model = PPDocLayoutV3TransformersPredictor(
        layout_options=PPDocLayoutV3Options(
            threshold=THRESHOLD_BY_CLASS
        ),
        accelerator_options=AcceleratorOptions(),
        artifacts_path="~/.cache/huggingface/hub/models--PaddlePaddle--PP-DocLayoutV3"
    )
    model.initialize()

    results = model.predict_batch(images=[pil_image])
    print(results)
    
    img = model.visualize(pil_image, results[0])
    img.save("viz.png")