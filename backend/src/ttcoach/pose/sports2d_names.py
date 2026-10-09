"""Sports2D (HALPE_26 'Body_with_feet') TRC marker names -> canonical joints (DESIGN §8)."""

from __future__ import annotations

SPORTS2D_TO_CANONICAL: dict[str, str] = {
    "Hip": "mid_hip",
    "RHip": "r_hip",
    "RKnee": "r_knee",
    "RAnkle": "r_ankle",
    "RBigToe": "r_big_toe",
    "RSmallToe": "r_small_toe",
    "RHeel": "r_heel",
    "LHip": "l_hip",
    "LKnee": "l_knee",
    "LAnkle": "l_ankle",
    "LBigToe": "l_big_toe",
    "LSmallToe": "l_small_toe",
    "LHeel": "l_heel",
    "Neck": "neck",
    "Head": "head",
    "Nose": "nose",
    "RShoulder": "r_shoulder",
    "RElbow": "r_elbow",
    "RWrist": "r_wrist",
    "LShoulder": "l_shoulder",
    "LElbow": "l_elbow",
    "LWrist": "l_wrist",
    # Present in HALPE_26 but not written by Sports2D 0.8.34; mapped in case a later version adds them.
    "REye": "r_eye",
    "LEye": "l_eye",
    "REar": "r_ear",
    "LEar": "l_ear",
}
