git 
from typing import (
    Optional,
    List,
    Tuple,
    Dict,
    Union,
)

import cv2
import numpy as np

SKIP_ORDER_LABELS = [
    "figure_title",
    "vision_footnote",
    "image",
    "chart",
    "table",
    "header",
    "header_image",
    "footer",
    "footer_image",
    "footnote",
    "aside_text",
]


class DetPostProcess:

    def __init__(
        self,
        labels: Optional[List[str]] = None,
    ) -> None:
        super().__init__()
        self.labels = labels

    @staticmethod
    def iou(box1, box2):
        """Compute the Intersection over Union (IoU) of two bounding boxes."""
        x1, y1, x2, y2 = box1
        x1_p, y1_p, x2_p, y2_p = box2

        # Compute the intersection coordinates
        x1_i = max(x1, x1_p)
        y1_i = max(y1, y1_p)
        x2_i = min(x2, x2_p)
        y2_i = min(y2, y2_p)

        # Compute the area of intersection
        inter_area = max(0, x2_i - x1_i + 1) * max(0, y2_i - y1_i + 1)

        # Compute the area of both bounding boxes
        box1_area = (x2 - x1 + 1) * (y2 - y1 + 1)
        box2_area = (x2_p - x1_p + 1) * (y2_p - y1_p + 1)

        # Compute the IoU
        iou_value = inter_area / float(box1_area + box2_area - inter_area)

        return iou_value

    @staticmethod
    def iou_batch(box, boxes: np.ndarray) -> np.ndarray:

        x1, y1, x2, y2 = box
        x1s, y1s, x2s, y2s = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
 
        xi1 = np.maximum(x1, x1s)
        yi1 = np.maximum(y1, y1s)
        xi2 = np.minimum(x2, x2s)
        yi2 = np.minimum(y2, y2s)
 
        inter = np.maximum(0, xi2 - xi1 + 1) * np.maximum(0, yi2 - yi1 + 1)
        area1 = (x2 - x1 + 1) * (y2 - y1 + 1)
        areas = (x2s - x1s + 1) * (y2s - y1s + 1)
        union = area1 + areas - inter
 
        return np.divide(inter, union, out=np.zeros_like(union, dtype=float), where=union > 0)

    @staticmethod
    def is_contained(box1, box2):
        """Check if box1 is contained within box2."""
        _, _, x1, y1, x2, y2 = box1
        _, _, x1_p, y1_p, x2_p, y2_p = box2
        box1_area = (x2 - x1) * (y2 - y1)
        xi1 = max(x1, x1_p)
        yi1 = max(y1, y1_p)
        xi2 = min(x2, x2_p)
        yi2 = min(y2, y2_p)
        inter_width = max(0, xi2 - xi1)
        inter_height = max(0, yi2 - yi1)
        intersect_area = inter_width * inter_height
        iou = intersect_area / box1_area if box1_area > 0 else 0
        return iou >= 0.9

    @staticmethod
    def unclip_boxes(boxes, unclip_ratio=None):

        if unclip_ratio is None:
            return boxes

        if isinstance(unclip_ratio, dict):
            expanded_boxes = []
            for box in boxes:
                class_id, score, x1, y1, x2, y2 = box
                if class_id in unclip_ratio:
                    width_ratio, height_ratio = unclip_ratio[class_id]

                    width = x2 - x1
                    height = y2 - y1

                    new_w = width * width_ratio
                    new_h = height * height_ratio
                    center_x = x1 + width / 2
                    center_y = y1 + height / 2

                    new_x1 = center_x - new_w / 2
                    new_y1 = center_y - new_h / 2
                    new_x2 = center_x + new_w / 2
                    new_y2 = center_y + new_h / 2

                    expanded_boxes.append([class_id, score, new_x1, new_y1, new_x2, new_y2])
                else:
                    expanded_boxes.append(box)
            return np.array(expanded_boxes)

        else:
            widths = boxes[:, 4] - boxes[:, 2]
            heights = boxes[:, 5] - boxes[:, 3]

            new_w = widths * unclip_ratio[0]
            new_h = heights * unclip_ratio[1]
            center_x = boxes[:, 2] + widths / 2
            center_y = boxes[:, 3] + heights / 2

            new_x1 = center_x - new_w / 2
            new_y1 = center_y - new_h / 2
            new_x2 = center_x + new_w / 2
            new_y2 = center_y + new_h / 2
            expanded_boxes = np.column_stack(
                (boxes[:, 0], boxes[:, 1], new_x1, new_y1, new_x2, new_y2)
            )
            return expanded_boxes

    @staticmethod
    def restructured_boxes(
        boxes: np.ndarray, labels: List[str], img_size: Tuple[int, int], polygon_points: np.ndarray = None,
    ):

        box_list = []
        w, h = img_size

        for idx, box in enumerate(boxes):
            xmin, ymin, xmax, ymax = box[2:]
            xmin = max(0, xmin)
            ymin = max(0, ymin)
            xmax = min(w, xmax)
            ymax = min(h, ymax)
            if xmax <= xmin or ymax <= ymin:
                continue
            res = {
                "cls_id": int(box[0]),
                "label": labels[int(box[0])],
                "score": float(box[1]),
                "coordinate": [xmin, ymin, xmax, ymax],
                "order": idx + 1,
            }
            if polygon_points is not None:
                polygon_point = polygon_points[idx]
                if polygon_point is None:
                    continue
                res["polygon_points"] = polygon_point
            box_list.append(res)

        return box_list

    @staticmethod
    def restructured_rotated_boxes(
        boxes: np.ndarray, labels: List[str], img_size: Tuple[int, int]
    ):

        box_list = []
        w, h = img_size

        assert boxes.shape[1] == 10, "The shape of rotated boxes should be [N, 10]"
        for box in boxes:
            x1, y1, x2, y2, x3, y3, x4, y4 = box[2:]
            x1 = min(max(0, x1), w)
            y1 = min(max(0, y1), h)
            x2 = min(max(0, x2), w)
            y2 = min(max(0, y2), h)
            x3 = min(max(0, x3), w)
            y3 = min(max(0, y3), h)
            x4 = min(max(0, x4), w)
            y4 = min(max(0, y4), h)
            box_list.append(
                {
                    "cls_id": int(box[0]),
                    "label": labels[int(box[0])],
                    "score": float(box[1]),
                    "coordinate": [x1, y1, x2, y2, x3, y3, x4, y4],
                }
            )

        return box_list

    @classmethod
    def nms(cls, boxes, iou_same=0.6, iou_diff=0.95):
        """Perform Non-Maximum Suppression (NMS) with different IoU thresholds for same and different classes."""
        scores = boxes[:, 1]
        classes = boxes[:, 0]
        coords = boxes[:, 2:6]

        # Sort indices by scores in descending order
        order = np.argsort(scores)[::-1]
        selected_boxes = []
 
        while len(order) > 0:
            current = order[0]
            selected_boxes.append(current)
 
            remaining = order[1:]
            if len(remaining) == 0:
                break
 
            iou_values = cls.iou_batch(coords[current], coords[remaining])
            same_class = classes[remaining] == classes[current]
            threshold = np.where(same_class, iou_same, iou_diff)
 
            order = remaining[iou_values < threshold]
 
        return selected_boxes

    @classmethod
    def check_containment(cls, boxes, formula_index=None, category_index=None, mode=None):
        """Check containment relationships among boxes."""
        n = len(boxes)
        if n == 0:
            return np.zeros(0, dtype=int), np.zeros(0, dtype=int)

        classes = boxes[:, 0]
        x1, y1, x2, y2 = boxes[:, 2], boxes[:, 3], boxes[:, 4], boxes[:, 5]
 
        # Pairwise intersection box(es): rows = "container" candidate i, cols = j
        xi1 = np.maximum(x1[:, None], x1[None, :])
        yi1 = np.maximum(y1[:, None], y1[None, :])
        xi2 = np.minimum(x2[:, None], x2[None, :])
        yi2 = np.minimum(y2[:, None], y2[None, :])
        inter_area = np.maximum(0, xi2 - xi1) * np.maximum(0, yi2 - yi1)
 
        box_area = (x2 - x1) * (y2 - y1)
        box_area_safe = np.where(box_area > 0, box_area, 1)
        # ratio_ij: fraction of box i's area covered by box j -> "i contained in j"
        ratio_ij = inter_area / box_area_safe[:, None]
        ratio_ij = np.where(box_area[:, None] > 0, ratio_ij, 0)
 
        valid = ~np.eye(n, dtype=bool)  # exclude i == j
 
        if formula_index is not None:
            # Original rule: skip pair (i, j) when i is a formula box and j is not.
            is_formula_i = (classes == formula_index)[:, None]
            is_formula_j = (classes == formula_index)[None, :]
            valid &= ~(is_formula_i & ~is_formula_j)
 
        if category_index is not None and mode is not None:
            # When mode is set, only the mode-restricted pairs count (matches the
            # original if/else: the unconditional branch is NOT also evaluated).
            if mode == "large":
                valid &= (classes == category_index)[None, :]
            elif mode == "small":
                valid &= (classes == category_index)[:, None]
 
        contained_mask = (ratio_ij >= 0.9) & valid
        contained_by_other = contained_mask.any(axis=1).astype(int)
        contains_other = contained_mask.any(axis=0).astype(int)
        return contains_other, contained_by_other

    def apply(
        self,
        boxes: np.ndarray,
        img_size: Tuple[int, int],
        threshold: Union[float, Dict],
        layout_nms: Optional[bool],
        layout_unclip_ratio: Optional[Union[float, Tuple[float, float], Dict]],
        layout_merge_bboxes_mode: Optional[Union[str, Dict]],
    ):
        # Threshold
        if isinstance(threshold, float):
            expect_boxes = (boxes[:, 1] > threshold) & (boxes[:, 0] > -1)
            boxes = boxes[expect_boxes, :]
        elif isinstance(threshold, dict):
            category_filtered_boxes = []
            for cat_id in np.unique(boxes[:, 0]):
                category_boxes = boxes[boxes[:, 0] == cat_id]
                category_threshold = threshold.get(int(cat_id), 0.5)
                selected_indices = (category_boxes[:, 1] > category_threshold) & (
                    category_boxes[:, 0] > -1
                )
                category_filtered_boxes.append(category_boxes[selected_indices])
            boxes = (
                np.vstack(category_filtered_boxes)
                if category_filtered_boxes
                else np.array([])
            )
 
        # Layout NMS
        if layout_nms:
            selected_indices = self.nms(boxes[:, :6], iou_same=0.6, iou_diff=0.98)
            boxes = np.array(boxes[selected_indices])
 
        filter_large_image = True
        # boxes.shape[1] == 6 is object detection, 7 is new ordered object detection, 8 is ordered object detection
        if filter_large_image and len(boxes) > 1 and boxes.shape[1] in [6, 7, 8]:
            if img_size[0] > img_size[1]:
                area_thres = 0.82
            else:
                area_thres = 0.93
            image_index = self.labels.index("image") if "image" in self.labels else None
            img_area = img_size[0] * img_size[1]
            filtered_boxes = []
            for box in boxes:
                (
                    label_index,
                    score,
                    xmin,
                    ymin,
                    xmax,
                    ymax,
                ) = box[:6]
                if label_index == image_index:
                    xmin = max(0, xmin)
                    ymin = max(0, ymin)
                    xmax = min(img_size[0], xmax)
                    ymax = min(img_size[1], ymax)
                    box_area = (xmax - xmin) * (ymax - ymin)
                    if box_area <= area_thres * img_area:
                        filtered_boxes.append(box)
                else:
                    filtered_boxes.append(box)
            if len(filtered_boxes) == 0:
                filtered_boxes = boxes
            boxes = np.array(filtered_boxes)
 
        if layout_merge_bboxes_mode:
            formula_index = (
                self.labels.index("formula") if "formula" in self.labels else None
            )
            if isinstance(layout_merge_bboxes_mode, str):
                assert layout_merge_bboxes_mode in [
                    "union",
                    "large",
                    "small",
                ], f"The value of `layout_merge_bboxes_mode` must be one of ['union', 'large', 'small'], but got {layout_merge_bboxes_mode}"
 
                if layout_merge_bboxes_mode == "union":
                    pass
                else:
                    contains_other, contained_by_other = self.check_containment(
                        boxes[:, :6], formula_index
                    )
                    if layout_merge_bboxes_mode == "large":
                        boxes = boxes[contained_by_other == 0]
                    elif layout_merge_bboxes_mode == "small":
                        boxes = boxes[(contains_other == 0) | (contained_by_other == 1)]
            elif isinstance(layout_merge_bboxes_mode, dict):
                keep_mask = np.ones(len(boxes), dtype=bool)
                for category_index, layout_mode in layout_merge_bboxes_mode.items():
                    assert layout_mode in [
                        "union",
                        "large",
                        "small",
                    ], f"The value of `layout_merge_bboxes_mode` must be one of ['union', 'large', 'small'], but got {layout_mode}"
                    if layout_mode == "union":
                        pass
                    else:
                        if layout_mode == "large":
                            contains_other, contained_by_other = self.check_containment(
                                boxes[:, :6],
                                formula_index,
                                category_index,
                                mode=layout_mode,
                            )
                            # Remove boxes that are contained by other boxes
                            keep_mask &= contained_by_other == 0
                        elif layout_mode == "small":
                            contains_other, contained_by_other = self.check_containment(
                                boxes[:, :6],
                                formula_index,
                                category_index,
                                mode=layout_mode,
                            )
                            # Keep boxes that do not contain others or are contained by others
                            keep_mask &= (contains_other == 0) | (
                                contained_by_other == 1
                            )
                boxes = boxes[keep_mask]
 
        if boxes.size == 0:
            return []
 
        if boxes.shape[1] == 8:
            # Sort boxes by their order
            sorted_idx = np.lexsort((-boxes[:, 7], boxes[:, 6]))
            sorted_boxes = boxes[sorted_idx]
            boxes = sorted_boxes[:, :6]
 
        if boxes.shape[1] == 7:
            # Sort boxes by their order
            sorted_idx = np.argsort(boxes[:, 6])
            sorted_boxes = boxes[sorted_idx]
            boxes = sorted_boxes[:, :6]
 
        # Unclip
        if layout_unclip_ratio:
            if isinstance(layout_unclip_ratio, float):
                layout_unclip_ratio = (layout_unclip_ratio, layout_unclip_ratio)
            elif isinstance(layout_unclip_ratio, (tuple, list)):
                assert (
                    len(layout_unclip_ratio) == 2
                ), f"The length of `layout_unclip_ratio` should be 2."
            elif isinstance(layout_unclip_ratio, dict):
                pass
            else:
                raise ValueError(
                    f"The type of `layout_unclip_ratio` must be float, Tuple[float, float] or  Dict[int, Tuple[float, float]], but got {type(layout_unclip_ratio)}."
                )
            boxes = self.unclip_boxes(boxes, layout_unclip_ratio)
 
        if boxes.shape[1] == 6:
            """For Normal Object Detection"""
            boxes = self.restructured_boxes(boxes, self.labels, img_size)
        elif boxes.shape[1] == 10:
            """Adapt For Rotated Object Detection"""
            boxes = self.restructured_rotated_boxes(boxes, self.labels, img_size)
        else:
            """Unexpected Input Box Shape"""
            raise ValueError(
                f"The shape of boxes should be 6 or 10, instead of {boxes.shape[1]}"
            )
        return boxes
 
    def __call__(
        self,
        batch_outputs: List[dict],
        datas: List[dict],
        threshold: Optional[Union[float, dict]] = None,
        layout_nms: Optional[bool] = None,
        layout_unclip_ratio: Optional[Union[float, Tuple[float, float]]] = None,
        layout_merge_bboxes_mode: Optional[str] = None,
    ):
 
        outputs = []
        for data, output in zip(datas, batch_outputs):
            boxes = self.apply(
                output["boxes"],
                data["ori_img_size"],
                threshold,
                layout_nms,
                layout_unclip_ratio,
                layout_merge_bboxes_mode,
            )
            outputs.append(boxes)
        return outputs


class LayoutPostProcess(DetPostProcess):

    def __init__(
        self,
        labels: Optional[List[str]] = None,
        scale_size: Optional[List[int]] = None
    ):
        super().__init__(labels)
        self.scale_size = scale_size

    @staticmethod
    def _rect_from_box(box):
        x_min, y_min, x_max, y_max = np.asarray(box).astype(np.int32)
        return np.array(
            [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]],
            dtype=np.float32,
        )

    @staticmethod
    def is_convex(p_prev, p_curr, p_next):
        """
        Calculate if the polygon is convex.
        """
        v1 = p_curr - p_prev
        v2 = p_next - p_curr
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        return cross < 0

    @staticmethod
    def angle_between_vectors(v1, v2):
        """
        Calculate the angle between two vectors.
        """

        unit_v1 = v1 / np.linalg.norm(v1)
        unit_v2 = v2 / np.linalg.norm(v2)
        dot_prod = np.clip(np.dot(unit_v1, unit_v2), -1.0, 1.0)
        angle_rad = np.arccos(dot_prod)
        return np.degrees(angle_rad)

    @staticmethod
    def calc_new_point(p_curr, v1, v2, distance=20):
        """
        Calculate the new point based on the direction of two vectors.
        """
        dir_vec = v1 / np.linalg.norm(v1) + v2 / np.linalg.norm(v2)
        dir_vec = dir_vec / np.linalg.norm(dir_vec)
        p_new = p_curr + dir_vec * distance
        return p_new

    @staticmethod
    def convert_polygon_to_quad(polygon):

        if polygon is None or len(polygon) < 3:
            return None

        points = np.array(polygon, dtype=np.float32)
        if len(points.shape) == 1:
            points = points.reshape(-1, 2)

        min_rect = cv2.minAreaRect(points)
        quad = cv2.boxPoints(min_rect)

        center = quad.mean(axis=0)
        angles = np.arctan2(quad[:, 1] - center[1], quad[:, 0] - center[0])
        sorted_indices = np.argsort(angles)
        quad = quad[sorted_indices]
        sums = quad[:, 0] + quad[:, 1]
        top_left_idx = np.argmin(sums)
        quad = np.roll(quad, -top_left_idx, axis=0)

        return quad

    @staticmethod
    def calculate_bbox_area(bbox):
        """Calculate bounding box area"""
        x1, y1, x2, y2 = map(float, bbox)
        area = abs((x2 - x1) * (y2 - y1))
        return area

    @classmethod
    def calculate_overlap_ratio(
        cls,
        bbox1: Union[np.ndarray, list, tuple],
        bbox2: Union[np.ndarray, list, tuple],
        mode="union",
    ) -> float:

        bbox1 = np.array(bbox1)
        bbox2 = np.array(bbox2)

        x_min_inter = np.maximum(bbox1[0], bbox2[0])
        y_min_inter = np.maximum(bbox1[1], bbox2[1])
        x_max_inter = np.minimum(bbox1[2], bbox2[2])
        y_max_inter = np.minimum(bbox1[3], bbox2[3])

        inter_width = np.maximum(0, x_max_inter - x_min_inter)
        inter_height = np.maximum(0, y_max_inter - y_min_inter)

        inter_area = inter_width * inter_height

        bbox1_area = cls.calculate_bbox_area(bbox1)
        bbox2_area = cls.calculate_bbox_area(bbox2)

        if mode == "union":
            ref_area = bbox1_area + bbox2_area - inter_area
        elif mode == "small":
            ref_area = np.minimum(bbox1_area, bbox2_area)
        elif mode == "large":
            ref_area = np.maximum(bbox1_area, bbox2_area)
        else:
            raise ValueError(
                f"Invalid mode {mode}, must be one of ['union', 'small', 'large']."
            )

        if ref_area == 0:
            return 0.0

        return inter_area / ref_area

    @staticmethod
    def calculate_overlap_ratio_matrix(coords: np.ndarray, mode: str = "small") -> np.ndarray:
        x1, y1, x2, y2 = coords[:, 0], coords[:, 1], coords[:, 2], coords[:, 3]
 
        xi1 = np.maximum(x1[:, None], x1[None, :])
        yi1 = np.maximum(y1[:, None], y1[None, :])
        xi2 = np.minimum(x2[:, None], x2[None, :])
        yi2 = np.minimum(y2[:, None], y2[None, :])
        inter_area = np.maximum(0, xi2 - xi1) * np.maximum(0, yi2 - yi1)
 
        area = (x2 - x1) * (y2 - y1)
        if mode == "union":
            ref = area[:, None] + area[None, :] - inter_area
        elif mode == "small":
            ref = np.minimum(area[:, None], area[None, :])
        elif mode == "large":
            ref = np.maximum(area[:, None], area[None, :])
        else:
            raise ValueError(f"Invalid mode {mode}, must be one of ['union', 'small', 'large'].")
 
        ref_safe = np.where(ref > 0, ref, 1)
        ratio = np.where(ref > 0, inter_area / ref_safe, 0.0)
        np.fill_diagonal(ratio, 0.0)
        return ratio

    @staticmethod
    def make_valid(poly):
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly

    @staticmethod
    def update_order_index(boxes: List[Dict], skip_order_labels: List[str]):

        order_index = 1
        for box in boxes:
            label = box["label"]
            if label not in skip_order_labels:
                box["order"] = order_index
                order_index += 1
            else:
                box["order"] = None
        return boxes

    @classmethod
    def calculate_polygon_overlap_ratio(
        cls,
        polygon1: List[Tuple[int, int]],
        polygon2: List[Tuple[int, int]],
        mode: str = "union",
    ) -> float:

        try:
            from shapely.geometry import Polygon
        except ImportError:
            raise ImportError("Please install Shapely library.")
        poly1 = Polygon(polygon1)
        poly2 = Polygon(polygon2)
        poly1 = cls.make_valid(poly1)
        poly2 = cls.make_valid(poly2)
        intersection = poly1.intersection(poly2).area
        union = poly1.union(poly2).area
        if mode == "union":
            return intersection / union
        elif mode == "small":
            small_area = min(poly1.area, poly2.area)
            return intersection / small_area
        elif mode == "large":
            large_area = max(poly1.area, poly2.area)
            return intersection / large_area
        else:
            raise ValueError(f"Unknown mode: {mode}")

    @classmethod
    def _normalize_layout_polygon(
        cls,
        box,
        polygon,
        layout_shape_mode,
        previous_polygon=None,
    ):
        rect = cls._rect_from_box(box)

        if polygon is None:
            return rect

        polygon = np.asarray(polygon, dtype=np.float32)
        if polygon.ndim == 1:
            polygon = polygon.reshape(-1, 2)

        if len(polygon) < 4:
            return rect

        if layout_shape_mode == "rect":
            return rect

        if layout_shape_mode == "poly":
            return polygon

        quad = cls.convert_polygon_to_quad(polygon)
        if layout_shape_mode == "quad":
            return quad if quad is not None else rect

        if layout_shape_mode == "auto":
            rect_list = rect.tolist()
            if quad is not None:
                quad_list = quad.tolist()
                iou_rect_quad = cls.calculate_polygon_overlap_ratio(
                    rect_list, quad_list, mode="union"
                )
                if iou_rect_quad >= 0.95:
                    return rect

                poly_list = polygon.tolist()
                iou_poly_quad = cls.calculate_polygon_overlap_ratio(
                    poly_list, quad_list, mode="union"
                )

                iou_pre = 0
                if previous_polygon is not None:
                    iou_pre = cls.calculate_polygon_overlap_ratio(
                        previous_polygon.tolist(),
                        rect_list,
                        mode="small",
                    )

                if iou_poly_quad >= 0.8 and iou_pre < 0.01:
                    return quad

            return polygon

        raise ValueError(
            "layout_shape_mode must be one of ['rect', 'poly', 'quad', 'auto']"
        )

    @classmethod
    def normalize_polygon_points_by_boxes(cls, boxes, polygon_points, layout_shape_mode):
        normalized_points = []

        for polygon, box in zip(polygon_points, boxes):
            normalized_points.append(
                cls._normalize_layout_polygon(
                    box=box[2:6],
                    polygon=polygon,
                    layout_shape_mode=layout_shape_mode,
                    previous_polygon=(
                        normalized_points[-1] if len(normalized_points) > 0 else None
                    ),
                )
            )

        return normalized_points

    @classmethod
    def extract_custom_vertices(
        cls, polygon, max_allowed_dist, sharp_angle_thresh=45, max_dist_ratio=0.3
    ):
        poly = np.array(polygon)
        n = len(poly)
        max_allowed_dist *= max_dist_ratio
 
        point_info = []
        for i in range(n):
            p_prev, p_curr, p_next = poly[(i - 1) % n], poly[i], poly[(i + 1) % n]
            v1, v2 = p_prev - p_curr, p_next - p_curr
            is_convex_point = cls.is_convex(p_prev, p_curr, p_next)
            angle = cls.angle_between_vectors(v1, v2)
            point_info.append(
                {
                    "index": i,
                    "is_convex": is_convex_point,
                    "angle": angle,
                    "v1": v1,
                    "v2": v2,
                }
            )
 
        concave_indices = [i for i, info in enumerate(point_info) if not info["is_convex"]]
        preserve_concave = set()
 
        if concave_indices:
            groups = []
            current_group = [concave_indices[0]]
 
            for i in range(1, len(concave_indices)):
                if concave_indices[i] - concave_indices[i - 1] == 1 or (
                    concave_indices[i - 1] == n - 1 and concave_indices[i] == 0
                ):
                    current_group.append(concave_indices[i])
                else:
                    if len(current_group) >= 2:
                        groups.extend(current_group)
                    current_group = [concave_indices[i]]
 
            if len(current_group) >= 2:
                groups.extend(current_group)
 
            if (
                len(concave_indices) >= 2
                and concave_indices[0] == 0
                and concave_indices[-1] == n - 1
            ):
                if 0 in groups and n - 1 in groups:
                    preserve_concave.update(groups)
            else:
                preserve_concave.update(groups)
 
        kept_points = [
            i
            for i, info in enumerate(point_info)
            if info["is_convex"] or (i in preserve_concave and info["angle"] >= 120)
        ]
 
        final_points = []
        for idx in range(len(kept_points)):
            current_idx = kept_points[idx]
            next_idx = kept_points[(idx + 1) % len(kept_points)]
            final_points.append(current_idx)
 
            dist = np.linalg.norm(poly[current_idx] - poly[next_idx])
            if dist > max_allowed_dist:
                intermediate = (
                    list(range(current_idx + 1, next_idx))
                    if next_idx > current_idx
                    else list(range(current_idx + 1, n)) + list(range(0, next_idx))
                )
 
                if intermediate:
                    num_needed = int(np.ceil(dist / max_allowed_dist)) - 1
                    if len(intermediate) <= num_needed:
                        final_points.extend(intermediate)
                    else:
                        step = len(intermediate) / num_needed
                        final_points.extend(
                            [intermediate[int(i * step)] for i in range(num_needed)]
                        )
 
        final_points = sorted(set(final_points))
        res = []
 
        for i in final_points:
            info = point_info[i]
            p_curr = poly[i]
 
            if info["is_convex"] and abs(info["angle"] - sharp_angle_thresh) < 1:
                v1_norm = info["v1"] / np.linalg.norm(info["v1"])
                v2_norm = info["v2"] / np.linalg.norm(info["v2"])
                dir_vec = v1_norm + v2_norm
                dir_vec /= np.linalg.norm(dir_vec)
                d = (np.linalg.norm(info["v1"]) + np.linalg.norm(info["v2"])) / 2
                res.append(tuple(p_curr + dir_vec * d))
            else:
                res.append(tuple(p_curr))
 
        return res
 
    @classmethod
    def mask2polygon(cls, mask, max_allowed_dist, epsilon_ratio=0.004, extract_custom=True):
 
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
 
        if not cnts:
            return None
 
        cnt = max(cnts, key=cv2.contourArea)
        epsilon = epsilon_ratio * cv2.arcLength(cnt, True)
        approx_cnt = cv2.approxPolyDP(cnt, epsilon, True)
        polygon_points = approx_cnt.squeeze()
        polygon_points = np.atleast_2d(polygon_points)
        if extract_custom:
            polygon_points = cls.extract_custom_vertices(polygon_points, max_allowed_dist)
 
        return polygon_points
 
    @classmethod
    def extract_polygon_points_by_masks(cls, boxes, masks, scale_ratio, layout_shape_mode):
 
        scale_w, scale_h = scale_ratio[0] / 4, scale_ratio[1] / 4
        h_m, w_m = masks.shape[1:]
        polygon_points = []
 
        max_box_w = max(boxes[:, 4] - boxes[:, 3])
 
        for i in range(len(boxes)):
            x_min, y_min, x_max, y_max = boxes[i, 2:6].astype(np.int32)
            box_w, box_h = x_max - x_min, y_max - y_min
            rect = cls._rect_from_box(boxes[i, 2:6])
 
            if box_w <= 0 or box_h <= 0:
                polygon_points.append(rect)
                continue
 
            # crop mask
            x_s = np.clip(
                [int(round(x_min * scale_w)), int(round(x_max * scale_w))], 0, w_m
            )
            y_s = np.clip(
                [int(round(y_min * scale_h)), int(round(y_max * scale_h))], 0, h_m
            )
 
            cropped = masks[i, y_s[0] : y_s[1], x_s[0] : x_s[1]]
            if cropped.size == 0 or np.sum(cropped) == 0:
                polygon_points.append(rect)
                continue
 
            # resize mask to match box size
            resized_mask = cv2.resize(
                cropped.astype(np.uint8), (box_w, box_h), interpolation=cv2.INTER_NEAREST
            )
 
            if box_w > max_box_w * 0.6:
                max_allowed_dist = box_w
            else:
                max_allowed_dist = max_box_w
 
            polygon = cls.mask2polygon(resized_mask, max_allowed_dist)
            if polygon is not None and len(polygon) > 0:
                polygon = polygon + np.array([x_min, y_min])
            polygon_points.append(
                cls._normalize_layout_polygon(
                    box=boxes[i, 2:6],
                    polygon=polygon,
                    layout_shape_mode=layout_shape_mode,
                    previous_polygon=(
                        polygon_points[-1] if len(polygon_points) > 0 else None
                    ),
                )
            )
 
        return polygon_points
 
    @classmethod
    def filter_boxes(
        cls, src_boxes: List[Dict], layout_shape_mode: str
    ) -> List[Dict]:
        """
        Remove overlapping boxes from layout detection results.
 
        Complexity note: deciding which of N boxes overlap requires checking pairs,
        so this is O(N^2) in the worst case no matter what (same reasoning as
        check_containment above). What changes here is *how* the O(N^2) pairwise
        check is done: the rect-overlap ratio for ALL pairs is computed once via
        vectorized numpy (calculate_overlap_ratio_matrix), and the expensive,
        non-vectorizable parts (shapely polygon overlap, label-set tie-break logic)
        only run for the small subset of pairs that actually clear the 0.5/0.7
        rect-overlap threshold, instead of recomputing area/intersection per pair
        in a Python loop.
        """
        boxes = [box for box in src_boxes if box["label"] != "reference"]
        n = len(boxes)
        if n == 0:
            return []
 
        coords = np.array([box["coordinate"] for box in boxes], dtype=float)
        widths = coords[:, 2] - coords[:, 0]
        heights = coords[:, 3] - coords[:, 1]
 
        dropped_indexes = set(np.where((widths < 6) | (heights < 6))[0].tolist())
 
        # One vectorized pass over all pairs instead of a nested Python loop.
        overlap_matrix = cls.calculate_overlap_ratio_matrix(coords, mode="small")
        areas = widths * heights
 
        for i in range(n):
            if i in dropped_indexes:
                continue
            for j in range(i + 1, n):
                if j in dropped_indexes:
                    continue
 
                overlap_ratio = overlap_matrix[i, j]
                label_i, label_j = boxes[i]["label"], boxes[j]["label"]
 
                if label_i == "inline_formula" or label_j == "inline_formula":
                    if overlap_ratio > 0.5:
                        if label_i == "inline_formula":
                            dropped_indexes.add(i)
                        if label_j == "inline_formula":
                            dropped_indexes.add(j)
                        continue
 
                if overlap_ratio > 0.7:
                    if layout_shape_mode != "rect" and "polygon_points" in boxes[i]:
                        poly_overlap_ratio = cls.calculate_polygon_overlap_ratio(
                            boxes[i]["polygon_points"], boxes[j]["polygon_points"], "small"
                        )
                        if poly_overlap_ratio < 0.7:
                            continue
                    labels = {label_i, label_j}
                    if labels & {"image", "table", "seal", "chart"} and len(labels) > 1:
                        if "table" not in labels or labels <= {
                            "table",
                            "image",
                            "seal",
                            "chart",
                        }:
                            continue
                    if areas[i] >= areas[j]:
                        dropped_indexes.add(j)
                    else:
                        dropped_indexes.add(i)
 
        out_boxes = [box for idx, box in enumerate(boxes) if idx not in dropped_indexes]
        return out_boxes
 
    def apply(
        self,
        boxes: np.ndarray,
        img_size: Tuple[int, int],
        threshold: Union[float, dict],
        layout_nms: Optional[bool],
        layout_unclip_ratio: Optional[Union[float, Tuple[float, float], dict]],
        layout_merge_bboxes_mode: Optional[Union[str, dict]],
        masks: Optional[np.ndarray] = None,
        layout_shape_mode: Optional[str] = "auto",
        polygon_points: Optional[List[np.ndarray]] = None,
    ):
        # Layout shape mode
        if layout_shape_mode == "rect":
            masks = None
            polygon_points = None
        boxes[:, 2:6] = np.round(boxes[:, 2:6]).astype(int)
        if isinstance(threshold, float):
            expect_boxes = (boxes[:, 1] > threshold) & (boxes[:, 0] > -1)
            boxes = boxes[expect_boxes, :]
            if masks is not None:
                masks = masks[expect_boxes, ...]
            if polygon_points is not None:
                polygon_points = [
                    polygon_points[i] for i, keep in enumerate(expect_boxes) if keep
                ]
        elif isinstance(threshold, dict):
            category_filtered_boxes = []
            if masks is not None:
                category_filtered_masks = []
            if polygon_points is not None:
                category_filtered_polygon_points = []
            for cat_id in np.unique(boxes[:, 0]):
                category_boxes = boxes[boxes[:, 0] == cat_id]
                if masks is not None:
                    category_masks = masks[boxes[:, 0] == cat_id]
                if polygon_points is not None:
                    category_polygon_points = [
                        polygon_points[i] for i in np.where(boxes[:, 0] == cat_id)[0]
                    ]
                category_threshold = threshold.get(int(cat_id), 0.5)
                selected_indices = (category_boxes[:, 1] > category_threshold) & (
                    category_boxes[:, 0] > -1
                )
                if masks is not None:
                    category_masks = category_masks[selected_indices]
                    category_filtered_masks.append(category_masks)
                if polygon_points is not None:
                    category_filtered_polygon_points.extend(
                        [
                            poly
                            for poly, keep in zip(
                                category_polygon_points, selected_indices
                            )
                            if keep
                        ]
                    )
                category_filtered_boxes.append(category_boxes[selected_indices])
            boxes = (
                np.vstack(category_filtered_boxes)
                if category_filtered_boxes
                else np.array([])
            )
            if masks is not None:
                masks = (
                    np.concatenate(category_filtered_masks)
                    if category_filtered_masks
                    else np.array([])
                )
            if polygon_points is not None:
                polygon_points = category_filtered_polygon_points
 
        # Layout NMS
        if layout_nms:
            selected_indices = self.nms(boxes[:, :6], iou_same=0.6, iou_diff=0.98)
            boxes = np.array(boxes[selected_indices])
            if masks is not None:
                masks = [masks[i] for i in selected_indices]
            if polygon_points is not None:
                polygon_points = [polygon_points[i] for i in selected_indices]
 
        filter_large_image = True
        # boxes.shape[1] == 6 is object detection, 7 is new ordered object detection, 8 is ordered object detection
        if filter_large_image and len(boxes) > 1 and boxes.shape[1] in [6, 7, 8]:
            if img_size[0] > img_size[1]:
                area_thres = 0.82
            else:
                area_thres = 0.93
            image_index = self.labels.index("image") if "image" in self.labels else None
            img_area = img_size[0] * img_size[1]
            filtered_boxes = []
            filtered_masks = []
            filtered_polygon_points = []
            for idx, box in enumerate(boxes):
                (
                    label_index,
                    score,
                    xmin,
                    ymin,
                    xmax,
                    ymax,
                ) = box[:6]
                if label_index == image_index:
                    xmin = max(0, xmin)
                    ymin = max(0, ymin)
                    xmax = min(img_size[0], xmax)
                    ymax = min(img_size[1], ymax)
                    box_area = (xmax - xmin) * (ymax - ymin)
                    if box_area <= area_thres * img_area:
                        filtered_boxes.append(box)
                        if masks is not None:
                            filtered_masks.append(masks[idx])
                        if polygon_points is not None:
                            filtered_polygon_points.append(polygon_points[idx])
                else:
                    filtered_boxes.append(box)
                    if masks is not None:
                        filtered_masks.append(masks[idx])
                    if polygon_points is not None:
                        filtered_polygon_points.append(polygon_points[idx])
            if len(filtered_boxes) == 0:
                filtered_boxes = boxes
                if masks is not None:
                    filtered_masks = masks
                if polygon_points is not None:
                    filtered_polygon_points = polygon_points
            boxes = np.array(filtered_boxes)
            if masks is not None:
                masks = filtered_masks
            if polygon_points is not None:
                polygon_points = filtered_polygon_points
 
        if layout_merge_bboxes_mode:
            formula_index = (
                self.labels.index("formula") if "formula" in self.labels else None
            )
            if isinstance(layout_merge_bboxes_mode, str):
                assert layout_merge_bboxes_mode in [
                    "union",
                    "large",
                    "small",
                ], f"The value of `layout_merge_bboxes_mode` must be one of ['union', 'large', 'small'], but got {layout_merge_bboxes_mode}"
 
                if layout_merge_bboxes_mode == "union":
                    pass
                else:
                    contains_other, contained_by_other = self.check_containment(
                        boxes[:, :6], formula_index
                    )
                    if layout_merge_bboxes_mode == "large":
                        boxes = boxes[contained_by_other == 0]
                        if masks is not None:
                            masks = [
                                mask
                                for i, mask in enumerate(masks)
                                if contained_by_other[i] == 0
                            ]
                    elif layout_merge_bboxes_mode == "small":
                        boxes = boxes[(contains_other == 0) | (contained_by_other == 1)]
                        if masks is not None:
                            masks = [
                                mask
                                for i, mask in enumerate(masks)
                                if (contains_other[i] == 0)
                                | (contained_by_other[i] == 1)
                            ]
            elif isinstance(layout_merge_bboxes_mode, dict):
                keep_mask = np.ones(len(boxes), dtype=bool)
                for category_index, layout_mode in layout_merge_bboxes_mode.items():
                    assert layout_mode in [
                        "union",
                        "large",
                        "small",
                    ], f"The value of `layout_merge_bboxes_mode` must be one of ['union', 'large', 'small'], but got {layout_mode}"
                    if layout_mode == "union":
                        pass
                    else:
                        if layout_mode == "large":
                            contains_other, contained_by_other = self.check_containment(
                                boxes[:, :6],
                                formula_index,
                                category_index,
                                mode=layout_mode,
                            )
                            # Remove boxes that are contained by other boxes
                            keep_mask &= contained_by_other == 0
                        elif layout_mode == "small":
                            contains_other, contained_by_other = self.check_containment(
                                boxes[:, :6],
                                formula_index,
                                category_index,
                                mode=layout_mode,
                            )
                            # Keep boxes that do not contain others or are contained by others
                            keep_mask &= (contains_other == 0) | (
                                contained_by_other == 1
                            )
                boxes = boxes[keep_mask]
                if masks is not None:
                    masks = [mask for i, mask in enumerate(masks) if keep_mask[i]]
                if polygon_points is not None:
                    polygon_points = [
                        poly for i, poly in enumerate(polygon_points) if keep_mask[i]
                    ]
 
        if boxes.size == 0:
            return np.array([])
 
        if boxes.shape[1] == 8:
            # Sort boxes by their order
            sorted_idx = np.lexsort((-boxes[:, 7], boxes[:, 6]))
            sorted_boxes = boxes[sorted_idx]
            boxes = sorted_boxes[:, :6]
            if masks is not None:
                sorted_masks = [masks[i] for i in sorted_idx]
                masks = sorted_masks
            if polygon_points is not None:
                polygon_points = [polygon_points[i] for i in sorted_idx]
 
        if boxes.shape[1] == 7:
            # Sort boxes by their order
            sorted_idx = np.argsort(boxes[:, 6])
            sorted_boxes = boxes[sorted_idx]
            boxes = sorted_boxes[:, :6]
            if masks is not None:
                sorted_masks = [masks[i] for i in sorted_idx]
                masks = sorted_masks
            if polygon_points is not None:
                polygon_points = [polygon_points[i] for i in sorted_idx]
 
        if polygon_points is None and masks is not None:
            scale_ratio = [h / s for h, s in zip(self.scale_size, img_size)]
            polygon_points = self.extract_polygon_points_by_masks(
                boxes, np.array(masks), scale_ratio, layout_shape_mode
            )
        elif polygon_points is not None:
            polygon_points = self.normalize_polygon_points_by_boxes(
                boxes, polygon_points, layout_shape_mode
            )
 
        if layout_unclip_ratio:
            if isinstance(layout_unclip_ratio, float):
                layout_unclip_ratio = (layout_unclip_ratio, layout_unclip_ratio)
            elif isinstance(layout_unclip_ratio, (tuple, list)):
                assert (
                    len(layout_unclip_ratio) == 2
                ), f"The length of `layout_unclip_ratio` should be 2."
            elif isinstance(layout_unclip_ratio, dict):
                pass
            else:
                raise ValueError(
                    f"The type of `layout_unclip_ratio` must be float, Tuple[float, float] or  Dict[int, Tuple[float, float]], but got {type(layout_unclip_ratio)}."
                )
            boxes = self.unclip_boxes(boxes, layout_unclip_ratio)
 
        if boxes.shape[1] == 6:
            """For Normal Object Detection"""
            boxes = self.restructured_boxes(boxes, self.labels, img_size, polygon_points)
        else:
            """Unexpected Input Box Shape"""
            raise ValueError(
                f"The shape of boxes should be 6 or 10, instead of {boxes.shape[1]}"
            )
        return boxes
 
    def __call__(
        self,
        batch_outputs: List[dict],
        datas: List[dict],
        threshold: Optional[Union[float, dict]] = None,
        layout_nms: Optional[bool] = None,
        layout_unclip_ratio: Optional[Union[float, Tuple[float, float]]] = None,
        layout_merge_bboxes_mode: Optional[str] = None,
        layout_shape_mode: Optional[str] = None,
        filter_overlap_boxes: Optional[bool] = None,
        skip_order_labels: Optional[List[str]] = None,
    ):
 
        outputs = []
        for idx, (data, output) in enumerate(zip(datas, batch_outputs)):
            current_layout_shape_mode = layout_shape_mode
            if "masks" in output:
                masks = output["masks"]
                polygon_points = None
            elif "polygon_points" in output:
                masks = None
                polygon_points = output["polygon_points"]
            else:
                current_layout_shape_mode = "rect"
                if idx == 0 and layout_shape_mode not in ["rect", "auto"]:
                    logging.warning(
                        f"The model you are using does not support polygon output, but the layout_shape_mode is specified as {layout_shape_mode}, which will be set to 'rect'"
                    )
                masks = None
                polygon_points = None
            boxes = self.apply(
                output["boxes"],
                data["ori_img_size"],
                threshold,
                layout_nms,
                layout_unclip_ratio,
                layout_merge_bboxes_mode,
                masks,
                current_layout_shape_mode,
                polygon_points=polygon_points,
            )
            if filter_overlap_boxes:
                boxes = self.filter_boxes(boxes, current_layout_shape_mode)
            skip_order_labels = (
                skip_order_labels
                if skip_order_labels is not None
                else SKIP_ORDER_LABELS
            )
            boxes = self.update_order_index(boxes, skip_order_labels)
            outputs.append(boxes)
        return outputs