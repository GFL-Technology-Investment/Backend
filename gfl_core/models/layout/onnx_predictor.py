import os
import io
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
 
import cv2
import numpy as np
import onnxruntime as ort
import yaml
from PIL import Image
 
from utils.layout_postprocess_utils import LayoutPostProcess
 
logger = logging.getLogger(__name__)

def load_image_to_pil(image: Union[Image.Image, str, bytes, np.ndarray]) -> Image.Image:
    """
    Normalize any accepted image type to a PIL RGB image. If your project
    already has this helper in `utils`, drop this and import that one instead
    -- duplicated here only so this file is self-contained.
    """
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, str):
        return Image.open(image).convert("RGB")
    if isinstance(image, bytes):
        return Image.open(io.BytesIO(image)).convert("RGB")
    if isinstance(image, np.ndarray):
        # np.ndarray is assumed BGR (cv2 convention), matching what predict()
        # expects elsewhere in this file.
        return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    raise TypeError(f"Unsupported image type: {type(image)}")

def _to_bgr_ndarray(image: Union[Image.Image, str, bytes, np.ndarray]) -> np.ndarray:
    """Normalize any accepted image type to a BGR np.ndarray for `predict()`."""
    if isinstance(image, np.ndarray):
        return image
    pil_image = load_image_to_pil(image)
    return cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)


class PPDocLayoutV3ONNX:

    _DEFAULT_TARGET_SIZE = (800, 800)
    _DEFAULT_DRAW_THRESHOLD = 0.5

    def __init__(
        self,
        model_dir: str = None,
        threshold: Optional[Union[float, dict]] = None,
        layout_nms: bool = False,
        layout_unclip_ratio: Optional[Union[float, Tuple[float, float], dict]] = None,
        layout_merge_bboxes_mode: Optional[Union[str, dict]] = None,
        layout_shape_mode: Optional[str] = "auto",
        filter_overlap_boxes: Optional[bool] = True,
        skip_order_labels: Optional[List[str]] = None,
        providers: Optional[List[str]] = None,
        model_ocr_dir: Optional[str] = None,
        device: Optional[str] = None,
    ):

        if model_dir is None:
            from huggingface_hub import snapshot_download

            model_dir = snapshot_download("PaddlePaddle/PP-DocLayoutV3_onnx")
        self.model_dir = model_dir

        # inference.yml is the single source of truth for both the label
        # list and the exact preprocessing recipe used at export time.
        self._config = self._load_config()
        self.labels = self._load_label_mapping()
 
        preprocess_steps = self._config.get("Preprocess", [])
        resize_cfg = next((s for s in preprocess_steps if s.get("type") == "Resize"), {})
        self.target_size: Tuple[int, int] = tuple(
            resize_cfg.get("target_size", self._DEFAULT_TARGET_SIZE)
        )
        self.draw_threshold = self._config.get("draw_threshold", self._DEFAULT_DRAW_THRESHOLD)
 
        # An explicit `threshold` argument always wins; otherwise fall back
        # to the model's own draw_threshold from inference.yml.
        self.default_threshold = threshold if threshold is not None else self.draw_threshold
        self.layout_nms = layout_nms
        self.layout_unclip_ratio = layout_unclip_ratio
        self.layout_merge_bboxes_mode = layout_merge_bboxes_mode
        self.layout_shape_mode = layout_shape_mode
        self.filter_overlap_boxes = filter_overlap_boxes
        self.skip_order_labels = skip_order_labels
 
        self.layout_postprocessor = LayoutPostProcess(
            labels=self.labels, scale_size=[self.target_size[1], self.target_size[0]],
        )
 
        self._providers = providers
        self._model: Optional[ort.InferenceSession] = None
        self._input_names: List[str] = []
        self._output_names: List[str] = []
 
        self.model_ocr_dir = model_ocr_dir
        self._device = device or (
            "cuda" if "CUDAExecutionProvider" in ort.get_available_providers() else "cpu"
        )
        self._ocr_detector = None
 
    def load_model(self) -> None:
        """Build the onnxruntime session if it hasn't been built yet (no-op otherwise)."""
        if self._model is not None:
            return
        self._model = self._creat_session(self._providers)
        # Cache I/O names once instead of re-querying onnxruntime every call.
        self._input_names = [i.name for i in self._model.get_inputs()]
        self._output_names = [o.name for o in self._model.get_outputs()]
 
    def _load_ocr_detector(self):
        """Lazily build and cache the VietOCR Predictor (loads weights once, not per-call)."""
        if self._ocr_detector is not None:
            return self._ocr_detector
 
        from vietocr.tool.predictor import Predictor
        from vietocr.tool.config import Cfg
 
        config = Cfg.load_config_from_name("vgg_transformer")
        if self.model_ocr_dir is not None:
            config["weights"] = self.model_ocr_dir
        config["device"] = self._device
 
        self._ocr_detector = Predictor(config)
        return self._ocr_detector

    def _load_config(self) -> dict:
        """Parse inference.yml shipped next to inference.onnx in model_dir."""
        yml_path = os.path.join(self.model_dir, "inference.yml")
        with open(yml_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
 
    def _load_label_mapping(self) -> List[str]:
        """`label_list` order == model class id order, used to map cls_id -> name."""
        labels = self._config.get("label_list")
        if not labels:
            raise ValueError(f"`label_list` not found in {self.model_dir}/inference.yml")
        return labels
 
    def _creat_session(self, providers: Optional[List[str]] = None) -> ort.InferenceSession:
        """Build the onnxruntime session, preferring CUDA when available."""
        onnx_path = os.path.join(self.model_dir, "inference.onnx")
        if providers is None:
            available = ort.get_available_providers()
            providers = (
                ["CUDAExecutionProvider", "CPUExecutionProvider"]
                if "CUDAExecutionProvider" in available
                else ["CPUExecutionProvider"]
            )
 
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(onnx_path, sess_options=sess_options, providers=providers)
 
        # Log actual I/O names: I can't open this 131MB graph from here to
        # confirm them ahead of time, so verify against this log line (or
        # `session.get_inputs()/get_outputs()`) before trusting _run_single().
        logger.info(
            "Loaded %s | providers=%s | inputs=%s | outputs=%s",
            onnx_path,
            session.get_providers(),
            [i.name for i in session.get_inputs()],
            [o.name for o in session.get_outputs()],
        )
        return session

    def _preprocess(self, image: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int], np.ndarray]:
        """
        Resize(800x800, keep_ratio=False, bilinear) -> /255 (mean=0,std=1 is
        a no-op beyond the rescale) -> Permute HWC->CHW. Returns the input
        blob, (orig_w, orig_h), and the scale_factor most Paddle2ONNX
        detection graphs expect as a second input to restore box coordinates
        to the original image size internally.
        """
        ori_h, ori_w = image.shape[:2]
        target_h, target_w = self.target_size
 
        resized = cv2.resize(image, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        blob = resized.astype(np.float32) / 255.0
        blob = blob.transpose(2, 0, 1)[np.newaxis, ...]  # NCHW
 
        scale_factor = np.array([[target_h / ori_h, target_w / ori_w]], dtype=np.float32)
        return blob, (ori_w, ori_h), scale_factor

    def _build_feed_dict(
        self, image_blob: np.ndarray, scale_factor: np.ndarray
    ) -> Dict[str, np.ndarray]:
        """
        Standard Paddle2ONNX detection exports declare up to 3 inputs:
        `image`, `im_shape`, `scale_factor`. Only feed whichever of these
        the graph actually declares (some exports only need `image`).
        """
        target_h, target_w = self.target_size
        candidates = {
            "image": image_blob,
            "im_shape": np.array([[target_h, target_w]], dtype=np.float32),
            "scale_factor": scale_factor,
        }
        feed = {name: candidates[name] for name in self._input_names if name in candidates}
        if not feed:
            # Non-standard input name -- feed the image under whatever single
            # input the graph declares and let onnxruntime raise if it's wrong.
            feed = {self._input_names[0]: image_blob}
        return feed

    def _run_single(self, image: np.ndarray) -> Tuple[dict, dict]:
        image_blob, ori_size, scale_factor = self._preprocess(image)
        feed = self._build_feed_dict(image_blob, scale_factor)
        outputs = self._model.run(self._output_names, feed)
        output_map = dict(zip(self._output_names, outputs))

        # `boxes`: [N, 6] (cls_id, score, x1, y1, x2, y2) already rescaled to
        # the original image size -- the standard Paddle2ONNX detection
        # output name. If your export names it differently, fix this lookup
        # (check the names logged in `_creat_session`).
        boxes, masks = None, None
        for out in outputs:
            if not isinstance(out, np.ndarray):
                continue
            if out.ndim == 2 and out.shape[1] in (6, 7, 8):
                boxes = out
            elif out.ndim == 3:
                masks = out
 
        if boxes is None:
            raise KeyError(
                f"Could not find a 2D [N, 6|7|8] boxes output among "
                f"{self._output_names} with shapes {[o.shape for o in outputs]}. "
                "Update _run_single()."
            )
 
        output: Dict[str, Any] = {"boxes": boxes}
        # PP-DocLayoutV3 also predicts per-instance masks (confirmed 200x200,
        # int32) -- attach them only if this export actually produced one;
        # LayoutPostProcess.__call__ already branches on "masks" being present.
        if masks is not None:
            output["masks"] = masks
 
        data = {"ori_img_size": ori_size}
        return output, data
 
    def predict(self, batch_data: List[np.ndarray]) -> List[List[Dict]]:
        """
        Run layout detection on a batch of images (each an HxWx3 BGR
        np.ndarray, e.g. from cv2.imread). Images are sent through the
        session one at a time: PaddleDetection/PP-DocLayoutV3 exports
        commonly hardcode batch_size=1 in the decoder, so a Python-level
        loop is the safe default. Switch to a single batched session.run()
        only if you've confirmed your export supports batch > 1.
        """
        batch_outputs, datas = [], []

        if self._model is None:
            self.load_model()

        for image in batch_data:
            output, data = self._run_single(image)
            batch_outputs.append(output)
            datas.append(data)
 
        return self.layout_postprocessor(
            batch_outputs,
            datas,
            threshold=self.default_threshold,
            layout_nms=self.layout_nms,
            layout_unclip_ratio=self.layout_unclip_ratio,
            layout_merge_bboxes_mode=self.layout_merge_bboxes_mode,
            layout_shape_mode=self.layout_shape_mode,
            filter_overlap_boxes=self.filter_overlap_boxes,
            skip_order_labels=self.skip_order_labels,
        )
 
    def __call__(self, batch_data: List[np.ndarray]) -> List[List[Dict]]:
        return self.predict(batch_data)