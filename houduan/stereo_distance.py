"""Calibrated left/right stereo distance for YOLO-OBB detections.

Camera input format: a single 1280x480 frame containing 640x480 left and
640x480 right images, with the calibration in camera_config.py.
"""

import math
import os

import cv2
import numpy as np

try:
    import camera_config
    CALIBRATION_ERROR = None
except Exception as exc:
    # Damaged or missing calibration must never prevent monocular operation.
    camera_config = None
    CALIBRATION_ERROR = exc


def distance_weight(distance_m):
    """Near damage contributes more; far damage contributes less.

    Invalid / monocular measurements contribute unchanged area (weight 1).
    Boundaries are deliberately continuous to avoid grade flicker.
    """
    if distance_m is None or not math.isfinite(float(distance_m)) or distance_m <= 0:
        return 1.0
    if distance_m <= 0.5:
        return 1.5
    if distance_m < 2.0:
        return 1.5 - (distance_m - 0.5) * (0.5 / 1.5)
    if distance_m < 4.0:
        return 1.0 - (distance_m - 2.0) * (0.25 / 2.0)
    return 0.75


def weighted_damage_ratio(detections, frame_shape):
    """Preserve original box-area rule, scaling only valid stereo boxes."""
    h, w = frame_shape[:2]
    total = max(1, h * w)
    base = sum(float(d['area']) for d in detections) / total
    adjusted = sum(
        float(d['area']) * distance_weight(d.get('distance_m'))
        for d in detections
    ) / total
    return min(1.0, max(0.0, base)), min(1.0, max(0.0, adjusted))


class StereoDistanceEstimator:
    """StereoBM matcher with robust valid-pixel mean within each rotated box."""

    EYE_WIDTH = 640
    HEIGHT = 480

    def __init__(self):
        if camera_config is None:
            raise ValueError(f'无法加载双目标定：{CALIBRATION_ERROR}')
        if tuple(camera_config.size) != (self.EYE_WIDTH, self.HEIGHT):
            raise ValueError('双目标定分辨率不是 640x480，拒绝使用不匹配的标定')
        self.min_distance_m = max(0.05, float(os.environ.get('STEREO_MIN_DISTANCE_M', '0.25')))
        self.max_distance_m = max(self.min_distance_m, float(
            os.environ.get('STEREO_MAX_DISTANCE_M', '12.0')))
        self.matcher = cv2.StereoBM_create(numDisparities=80, blockSize=15)
        self.matcher.setROI1(camera_config.validPixROI1)
        self.matcher.setROI2(camera_config.validPixROI2)
        self.matcher.setPreFilterCap(31)
        self.matcher.setMinDisparity(4)
        self.matcher.setTextureThreshold(15)
        self.matcher.setUniquenessRatio(10)
        self.matcher.setSpeckleWindowSize(100)
        self.matcher.setSpeckleRange(32)
        self.matcher.setDisp12MaxDiff(1)

    def rectify_pair(self, image):
        """Return rectified LEFT BGR for YOLO and rectified RIGHT gray for BM.

        YOLO must run on the rectified left image: otherwise its box polygon
        would not align with the rectified depth image.
        """
        if image is None or image.shape[:2] != (self.HEIGHT, self.EYE_WIDTH * 2):
            raise ValueError('需要 1280x480 左右拼接双目帧')
        left = image[:, :self.EYE_WIDTH]
        right = image[:, self.EYE_WIDTH:]
        left_rectified = cv2.remap(
            left, camera_config.left_map1, camera_config.left_map2, cv2.INTER_LINEAR)
        right_gray = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
        right_rectified = cv2.remap(
            right_gray, camera_config.right_map1, camera_config.right_map2, cv2.INTER_LINEAR)
        return left_rectified, right_rectified

    def measure_detections(self, left_rectified, right_rectified, detections):
        """Set distance_m on detections; None means insufficient reliable data.

        Only points inside each *rotated* YOLO polygon are sampled. Outliers,
        invalid BM disparities, non-finite points and unreasonable ranges are
        ignored. The final measurement is the mean of trimmed valid pixels.
        """
        if not detections:
            return
        left_gray = cv2.cvtColor(left_rectified, cv2.COLOR_BGR2GRAY)
        if left_gray.shape != (self.HEIGHT, self.EYE_WIDTH) or right_rectified.shape != left_gray.shape:
            raise ValueError('双目图像尺寸与标定不一致')

        raw = self.matcher.compute(left_gray, right_rectified)
        disparity = raw.astype(np.float32) / 16.0
        # The BM invalid marker is (minDisparity - 1), and 0 is not a
        # finite-depth disparity. Never turn invalid points into 10000mm.
        valid = (disparity > max(0.0, float(self.matcher.getMinDisparity())))
        points_mm = cv2.reprojectImageTo3D(
            disparity, camera_config.Q, handleMissingValues=False)
        forward_m = points_mm[:, :, 2] / 1000.0
        ranges_m = np.linalg.norm(points_mm, axis=2) / 1000.0
        valid &= np.isfinite(forward_m) & (forward_m > 0)
        valid &= np.isfinite(ranges_m)
        valid &= (ranges_m >= self.min_distance_m) & (ranges_m <= self.max_distance_m)

        for detection in detections:
            detection['distance_m'] = None
            polygon = np.asarray(detection['corners'], dtype=np.int32).reshape(4, 2)
            mask = np.zeros(left_gray.shape, dtype=np.uint8)
            cv2.fillPoly(mask, [polygon], 255)
            pixel_count = int(np.count_nonzero(mask))
            if pixel_count == 0:
                continue
            values = ranges_m[(mask != 0) & valid]
            # Do not trust a box that contains only a few isolated valid dots.
            required = max(8, min(40, math.ceil(pixel_count * 0.03)))
            if values.size < required:
                continue
            if values.size >= 10:
                p10, p90 = np.percentile(values, [10, 90])
                values = values[(values >= p10) & (values <= p90)]
            if values.size == 0:
                continue
            distance = float(np.mean(values))
            if math.isfinite(distance):
                detection['distance_m'] = distance
                detection['distance_valid_pixels'] = int(values.size)
