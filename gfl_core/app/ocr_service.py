import re

from vietocr.tool.config import Cfg
from vietocr.tool.predictor import Predictor

from gfl_core.models.layout import (
    AcceleratorOptions,
    PPDocLayoutV3Options,
    PPDocLayoutV3TransformersPredictor,
)
from gfl_core.utils.image_utils import crop_image_region

layout_model = PPDocLayoutV3TransformersPredictor(
    layout_options=PPDocLayoutV3Options(),
    accelerator_options=AcceleratorOptions(),
    artifacts_path="~/.cache/huggingface/hub/models--PaddlePaddle--PP-DocLayoutV3",
)
layout_model.initialize()

_ocr_config = Cfg.load_config_from_name("vgg_transformer")
_ocr_config["device"] = "cpu"
ocr_model = Predictor(_ocr_config)

def extract_cccd(image_path: str) -> dict:
    results = layout_model.predict(image=image_path)
    image_crops = crop_image_region(image_path, results)

    image_list, text_image_list = [], []
    for crop in image_crops:
        label = crop["label"]
        if label == "image":
            image_list.append(crop["image"])
        elif label in ("text", "vertical_text"):
            text_image_list.append(crop["image"])

    full_text = "\n".join(ocr_model.predict_batch(text_image_list))

    result = {
        "image": image_list,
        "id":    None,
        "name":  None,
        "birth": None,
        "sex":   None,
        "place": None,
    }

    m = re.search(r"\b(\d{12})\b", full_text)
    if m:
        result["id"] = m.group(1)

    m = re.search(r"(?:Họ\s*và\s*tên|Ful+\s*name|Full\s*name)[^\n]*\n([^\n]+)", full_text, re.I)
    if m:
        result["name"] = m.group(1).strip()

    m = re.search(r"(\d{2}/\d{2}/\d{4})", full_text)
    if m:
        result["birth"] = m.group(1)

    m = re.search(r"\bSex\s+(Nữ|Nam|Female|Male)\b", full_text, re.I)
    if m:
        result["sex"] = m.group(1)

    m = re.search(r"(?:Quê\s+qu\S*|Place\s+of\s+or?\w*)[^\n]*\n([^\n]+)", full_text, re.I)
    if m:
        result["place"] = m.group(1).strip()

    return result