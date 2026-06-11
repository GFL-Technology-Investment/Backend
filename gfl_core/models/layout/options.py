from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import (
    Annotated, 
    ClassVar, 
    Literal, 
    Dict, 
    List, 
    Tuple
)


class PPDocLayoutV3Options(BaseModel):

    kind: ClassVar[Literal["pp_doclayout_v3"]] = "pp_doclayout_v3"
    
    threshold: Annotated[
        float | Dict[int, float] | None,
        Field(
            default=None,
            description=(
                "Confidence threshold used to filter detections. "
                "Can be a single float applied to all classes or "
                "a mapping of class_id -> threshold."
            ),
        ),
    ]

    batch_size: Annotated[
        int,
        Field(
            default=8,
            gt=0,
            description="Batch size used during layout inference.",
        ),
    ]

    layout_nms: Annotated[
        bool,
        Field(
            default=False,
            description="Enable layout-aware NMS post-processing.",
        ),
    ]

    layout_unclip_ratio: Annotated[
        float | Tuple[float, float],
        Field(
            default=(1.0, 1.0),
            description=(
                "Expand detected layout boxes before post-processing. "
                "A single float applies to both axes. "
                "Tuple format: (width_ratio, height_ratio)."
            ),
        ),
    ]

    layout_merge_bboxes_mode: Annotated[
        str | Dict | None,
        Field(
            default=None,
            description=(
                "Bounding box merge strategy. "
                "None disables merging."
            ),
        ),
    ]

    layout_shape_mode: Annotated[
        Literal["rect", "quad", "poly", "auto"],
        Field(
            default="auto",
            description=(
                "Output geometry type. "
                "'rect' -> xyxy box, "
                "'quad' -> 4-point quadrilateral, "
                "'poly' -> polygon, "
                "'auto' -> backend decides."
            ),
        ),
    ]

    filter_overlap_boxes: Annotated[
        bool,
        Field(
            default=True,
            description="Remove heavily overlapping layout regions.",
        ),
    ]

    skip_order_labels: Annotated[
        List[str] | None,
        Field(
            default=None,
            description=(
                "Labels excluded from reading-order computation."
            ),
        ),
    ]

    model_config = ConfigDict(
        extra="forbid",
    )


class AcceleratorOptions(BaseModel):

    device: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Execution device. Examples: "
                "'cpu', 'cuda', 'cuda:0'."
            ),
        ),
    ]

    device_id: Annotated[
        str | int,
        Field(
            default="0",
            description=""
        )
    ]