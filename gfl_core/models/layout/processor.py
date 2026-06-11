from typing import Tuple, Union, Dict, Optional, List

import cv2
import numpy as np
from PIL import Image
from loguru import logger as _log


class LayoutProcessor:

    def __init__(
        self,
        labels: List[str],
        scale_size: Tuple[int, int] = (640, 640),
    ):
        self.labels = labels
        self.scale_size = scale_size

    @staticmethod
    def _iou(box1, box2):
        x1, y1, x2, y2 = box1
        x1_p, y1_p, x2_p, y2_p = box2

        x1_i = max(x1, x1_p)
        y1_i = max(y1, y1_p)
        x2_i = min(x2, x2_p)
        y2_i = min(y2, y2_p)

        inter_area = max(0, x2_i - x1_i) * max(0, y2_i - y1_i)

        box1_area = (x2 - x1) * (y2 - y1)
        box2_area = (x2_p - x1_p) * (y2_p - y1_p)

        iou_value = inter_area / float(box1_area + box2_area - inter_area)

        return iou_value

    @staticmethod
    def _nms(boxes, iou_same=0.6, iou_diff=0.95):
        scores = boxes[:, 1]
        indices = np.argsort(scores)[::-1]
        selected_boxes = []

        while len(indices) > 0:
            current = indices[0]
            current_box = boxes[current]
            current_class = current_box[0]
            current_coords = current_box[2:]

            selected_boxes.append(current)
            indices = indices[1:]

            filtered_indices = []
            for i in indices:
                box = boxes[i]
                box_class = box[0]
                box_coords = box[2:]
                iou_value = LayoutProcessor._iou(current_coords, box_coords)
                threshold = iou_same if current_class == box_class else iou_diff

                if iou_value < threshold:
                    filtered_indices.append(i)
            indices = filtered_indices
        return selected_boxes

    @staticmethod
    def _is_contained(box1, box2):
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
    def _check_containment(boxes, formula_index=None, category_index=None, mode=None):
        n = len(boxes)
        contains_other = np.zeros(n, dtype=int)
        contained_by_other = np.zeros(n, dtype=int)

        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                if formula_index is not None:
                    if boxes[i][0] == formula_index and boxes[j][0] != formula_index:
                        continue
                if category_index is not None and mode is not None:
                    if mode == "large" and boxes[j][0] == category_index:
                        if LayoutProcessor._is_contained(boxes[i], boxes[j]):
                            contained_by_other[i] = 1
                            contains_other[j] = 1
                    if mode == "small" and boxes[i][0] == category_index:
                        if LayoutProcessor._is_contained(boxes[i], boxes[j]):
                            contained_by_other[i] = 1
                            contains_other[j] = 1
                else:
                    if LayoutProcessor._is_contained(boxes[i], boxes[j]):
                        contained_by_other[i] = 1
                        contains_other[j] = 1
        return contains_other, contained_by_other

    @staticmethod
    def _rect_from_box(box):
        x_min, y_min, x_max, y_max = np.asarray(box).astype(np.int32)
        return np.array(
            [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]],
            dtype=np.float32,
        )

    @staticmethod
    def _normalize_layout_polygon(
        box,
        polygon,
        layout_shape_mode,
        previous_polygon=None,
    ):
        rect = LayoutProcessor._rect_from_box(box)

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

        quad = LayoutProcessor._convert_polygon_to_quad(polygon)
        if layout_shape_mode == "quad":
            return quad if quad is not None else rect

        if layout_shape_mode == "auto":
            rect_list = rect.tolist()
            if quad is not None:
                quad_list = quad.tolist()
                iou_rect_quad = LayoutProcessor._calculate_polygon_overlap_ratio(
                    rect_list, quad_list, mode="union"
                )
                if iou_rect_quad >= 0.95:
                    return rect

                poly_list = polygon.tolist()
                iou_poly_quad = LayoutProcessor._calculate_polygon_overlap_ratio(
                    poly_list, quad_list, mode="union"
                )

                iou_pre = 0
                if previous_polygon is not None:
                    iou_pre = LayoutProcessor._calculate_polygon_overlap_ratio(
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

    @staticmethod
    def _convert_polygon_to_quad(polygon):
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
    def _is_convex(p_prev, p_curr, p_next):
        v1 = p_curr - p_prev
        v2 = p_next - p_curr
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        return cross < 0

    @staticmethod
    def _angle_between_vectors(v1, v2):
        unit_v1 = v1 / np.linalg.norm(v1)
        unit_v2 = v2 / np.linalg.norm(v2)
        dot_prod = np.clip(np.dot(unit_v1, unit_v2), -1.0, 1.0)
        angle_rad = np.arccos(dot_prod)
        return np.degrees(angle_rad)

    @staticmethod
    def _calc_new_point(p_curr, v1, v2, distance=20):
        dir_vec = v1 / np.linalg.norm(v1) + v2 / np.linalg.norm(v2)
        dir_vec = dir_vec / np.linalg.norm(dir_vec)
        p_new = p_curr + dir_vec * distance
        return p_new

    @staticmethod
    def _extract_custom_vertices(
        polygon, max_allowed_dist, sharp_angle_thresh=45, max_dist_ratio=0.3
    ):
        poly = np.array(polygon)
        n = len(poly)
        max_allowed_dist *= max_dist_ratio

        point_info = []
        for i in range(n):
            p_prev, p_curr, p_next = poly[(i - 1) % n], poly[i], poly[(i + 1) % n]
            v1, v2 = p_prev - p_curr, p_next - p_curr
            is_convex_point = LayoutProcessor._is_convex(p_prev, p_curr, p_next)
            angle = LayoutProcessor._angle_between_vectors(v1, v2)
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

    @staticmethod
    def _mask2polygon(mask, max_allowed_dist, epsilon_ratio=0.004, extract_custom=True):
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not cnts:
            return None

        cnt = max(cnts, key=cv2.contourArea)
        epsilon = epsilon_ratio * cv2.arcLength(cnt, True)
        approx_cnt = cv2.approxPolyDP(cnt, epsilon, True)
        polygon_points = approx_cnt.squeeze()
        polygon_points = np.atleast_2d(polygon_points)
        if extract_custom:
            polygon_points = LayoutProcessor._extract_custom_vertices(polygon_points, max_allowed_dist)

        return polygon_points

    @staticmethod
    def _extract_polygon_points_by_masks(boxes, masks, scale_ratio, layout_shape_mode):
        scale_w, scale_h = scale_ratio[0] / 4, scale_ratio[1] / 4
        h_m, w_m = masks.shape[1:]
        polygon_points = []

        max_box_w = max(boxes[:, 4] - boxes[:, 3])

        for i in range(len(boxes)):
            x_min, y_min, x_max, y_max = boxes[i, 2:6].astype(np.int32)
            box_w, box_h = x_max - x_min, y_max - y_min
            rect = LayoutProcessor._rect_from_box(boxes[i, 2:6])

            if box_w <= 0 or box_h <= 0:
                polygon_points.append(rect)
                continue

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

            resized_mask = cv2.resize(
                cropped.astype(np.uint8), (box_w, box_h), interpolation=cv2.INTER_NEAREST
            )

            if box_w > max_box_w * 0.6:
                max_allowed_dist = box_w
            else:
                max_allowed_dist = max_box_w

            polygon = LayoutProcessor._mask2polygon(resized_mask, max_allowed_dist)
            if polygon is not None and len(polygon) > 0:
                polygon = polygon + np.array([x_min, y_min])
            polygon_points.append(
                LayoutProcessor._normalize_layout_polygon(
                    box=boxes[i, 2:6],
                    polygon=polygon,
                    layout_shape_mode=layout_shape_mode,
                    previous_polygon=(
                        polygon_points[-1] if len(polygon_points) > 0 else None
                    ),
                )
            )

        return polygon_points

    @staticmethod
    def _make_valid(poly):
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly

    @staticmethod
    def _calculate_polygon_overlap_ratio(
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
        poly1 = LayoutProcessor._make_valid(poly1)
        poly2 = LayoutProcessor._make_valid(poly2)
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

    @staticmethod
    def _calculate_bbox_area(bbox):
        x1, y1, x2, y2 = map(float, bbox)
        area = abs((x2 - x1) * (y2 - y1))
        return area

    @staticmethod
    def _calculate_overlap_ratio(
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

        bbox1_area = LayoutProcessor._calculate_bbox_area(bbox1)
        bbox2_area = LayoutProcessor._calculate_bbox_area(bbox2)

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
    def _unclip_boxes(boxes, unclip_ratio=None):
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
    def _filter_boxes(
        src_boxes: List[Dict], layout_shape_mode: str
    ) -> List[Dict]:
        boxes = [box for box in src_boxes if box["label"] != "reference"]
        dropped_indexes = set()

        for i in range(len(boxes)):
            x1, y1, x2, y2 = boxes[i]["coordinate"]
            w, h = x2 - x1, y2 - y1
            if w < 6 or h < 6:
                dropped_indexes.add(i)
            for j in range(i + 1, len(boxes)):
                if i in dropped_indexes or j in dropped_indexes:
                    continue
                overlap_ratio = LayoutProcessor._calculate_overlap_ratio(
                    boxes[i]["coordinate"], boxes[j]["coordinate"], "small"
                )
                if (
                    boxes[i]["label"] == "inline_formula"
                    or boxes[j]["label"] == "inline_formula"
                ):
                    if overlap_ratio > 0.5:
                        if boxes[i]["label"] == "inline_formula":
                            dropped_indexes.add(i)
                        if boxes[j]["label"] == "inline_formula":
                            dropped_indexes.add(j)
                        continue
                if overlap_ratio > 0.7:
                    if layout_shape_mode != "rect" and "polygon_points" in boxes[i]:
                        poly_overlap_ratio = LayoutProcessor._calculate_polygon_overlap_ratio(
                            boxes[i]["polygon_points"], boxes[j]["polygon_points"], "small"
                        )
                        if poly_overlap_ratio < 0.7:
                            continue
                    box_area_i = LayoutProcessor._calculate_bbox_area(boxes[i]["coordinate"])
                    box_area_j = LayoutProcessor._calculate_bbox_area(boxes[j]["coordinate"])
                    labels = {boxes[i]["label"], boxes[j]["label"]}
                    if labels & {"image", "table", "seal", "chart"} and len(labels) > 1:
                        if "table" not in labels or labels <= {
                            "table",
                            "image",
                            "seal",
                            "chart",
                        }:
                            continue
                    if box_area_i >= box_area_j:
                        dropped_indexes.add(j)
                    else:
                        dropped_indexes.add(i)
        out_boxes = [box for idx, box in enumerate(boxes) if idx not in dropped_indexes]
        return out_boxes

    @staticmethod
    def _restructured_boxes(
        boxes: np.ndarray,
        labels: List[str],
        img_size: Tuple[int, int],
        polygon_points: np.ndarray = None,
    ):
        box_list = []
        w, h = img_size

        for idx, box in enumerate(boxes):
            xmin, ymin, xmax, ymax = box[2:]
            xmin = int(max(0, xmin))
            ymin = int(max(0, ymin))
            xmax = int(min(w, xmax))
            ymax = int(min(h, ymax))
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
    def _update_order_index(boxes: List[Dict], skip_order_labels: List[str]):
        order_index = 1
        for box in boxes:
            label = box["label"]
            if label not in skip_order_labels:
                box["order"] = order_index
                order_index += 1
            else:
                box["order"] = None
        return boxes

    @staticmethod
    def _normalize_polygon_points_by_boxes(boxes, polygon_points, layout_shape_mode):
        normalized_points = []

        for polygon, box in zip(polygon_points, boxes):
            normalized_points.append(
                LayoutProcessor._normalize_layout_polygon(
                    box=box[2:6],
                    polygon=polygon,
                    layout_shape_mode=layout_shape_mode,
                    previous_polygon=(
                        normalized_points[-1] if len(normalized_points) > 0 else None
                    ),
                )
            )

        return normalized_points

    def apply(
        self,
        boxes: np.ndarray,
        image_size: Tuple[int, int],
        threshold: Union[float, Dict],
        layout_nms: Optional[bool],
        layout_unclip_ratio: Optional[Union[float, Tuple[float, float], dict]],
        layout_merge_bboxes_mode: Optional[Union[str, dict]],
        masks: Optional[np.ndarray] = None,
        layout_shape_mode: Optional[str] = "auto",
        polygon_points: Optional[List[np.ndarray]] = None,
    ):
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

        if layout_nms:
            selected_indices = self._nms(boxes[:, :6], iou_same=0.6, iou_diff=0.98)
            boxes = np.array(boxes[selected_indices])
            if masks is not None:
                masks = [masks[i] for i in selected_indices]
            if polygon_points is not None:
                polygon_points = [polygon_points[i] for i in selected_indices]

        filter_large_image = True
        if filter_large_image and len(boxes) > 1 and boxes.shape[1] in [6, 7, 8]:
            if image_size[0] > image_size[1]:
                area_thres = 0.82
            else:
                area_thres = 0.93
            image_index = self.labels.index("image") if "image" in self.labels else None
            img_area = image_size[0] * image_size[1]
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
                    xmax = min(image_size[0], xmax)
                    ymax = min(image_size[1], ymax)
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
                    contains_other, contained_by_other = self._check_containment(
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
                            contains_other, contained_by_other = self._check_containment(
                                boxes[:, :6],
                                formula_index,
                                category_index,
                                mode=layout_mode,
                            )
                            keep_mask &= contained_by_other == 0
                        elif layout_mode == "small":
                            contains_other, contained_by_other = self._check_containment(
                                boxes[:, :6],
                                formula_index,
                                category_index,
                                mode=layout_mode,
                            )
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
            sorted_idx = np.lexsort((-boxes[:, 7], boxes[:, 6]))
            sorted_boxes = boxes[sorted_idx]
            boxes = sorted_boxes[:, :6]
            if masks is not None:
                sorted_masks = [masks[i] for i in sorted_idx]
                masks = sorted_masks
            if polygon_points is not None:
                polygon_points = [polygon_points[i] for i in sorted_idx]

        if boxes.shape[1] == 7:
            sorted_idx = np.argsort(boxes[:, 6])
            sorted_boxes = boxes[sorted_idx]
            boxes = sorted_boxes[:, :6]
            if masks is not None:
                sorted_masks = [masks[i] for i in sorted_idx]
                masks = sorted_masks
            if polygon_points is not None:
                polygon_points = [polygon_points[i] for i in sorted_idx]

        if polygon_points is None and masks is not None:
            scale_ratio = [h / s for h, s in zip(self.scale_size, image_size)]
            polygon_points = self._extract_polygon_points_by_masks(
                boxes, np.array(masks), scale_ratio, layout_shape_mode
            )
        elif polygon_points is not None:
            polygon_points = self._normalize_polygon_points_by_boxes(
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
            boxes = self._unclip_boxes(boxes, layout_unclip_ratio)

        if boxes.shape[1] == 6:
            boxes = self._restructured_boxes(boxes, self.labels, image_size, polygon_points)
        else:
            raise ValueError(
                f"The shape of boxes should be 6 or 10, instead of {boxes.shape[1]}"
            )
        return boxes

    def __call__(
        self,
        images: List[Image.Image],
        batch_outputs: List[Dict],
        threshold: Union[float, Dict],
        layout_nms: Optional[bool],
        layout_unclip_ratio: Optional[Union[float, Tuple[float, float], dict]],
        layout_merge_bboxes_mode: Optional[Union[str, dict]],
        layout_shape_mode: Optional[str] = None,
        filter_overlap_boxes: Optional[bool] = None,
        skip_order_labels: Optional[List[str]] = None,
    ):
        outputs = []
        for idx, (image, output) in enumerate(zip(images, batch_outputs)):
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
                    _log.warning(
                        f"The model you are using does not support polygon output, but the layout_shape_mode is specified as {layout_shape_mode}, which will be set to 'rect'"
                    )
                masks = None
                polygon_points = None

            boxes = self.apply(
                output["boxes"],
                (image.width, image.height),
                threshold,
                layout_nms,
                layout_unclip_ratio,
                layout_merge_bboxes_mode,
                masks,
                current_layout_shape_mode,
                polygon_points=polygon_points,
            )

            if filter_overlap_boxes:
                boxes = self._filter_boxes(boxes, current_layout_shape_mode)

            if skip_order_labels is not None:
                boxes = self._update_order_index(boxes, skip_order_labels)

            outputs.append(boxes)

        return outputs