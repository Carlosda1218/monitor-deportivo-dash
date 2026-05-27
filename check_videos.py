import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
from app import _resolve_uploaded_video

videos = [
    "20260503_005430_07048cf5.mp4",
    "videoplayback.mp4",
    "videoplayback_871497d6.mp4",
    "20230325_213445.mp4",
]

for rel in videos:
    path = _resolve_uploaded_video(rel)
    if not path or not os.path.exists(path):
        print(f"No existe: {rel}")
        continue
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print(f"No se puede abrir: {rel}")
        continue
    fps    = cap.get(cv2.CAP_PROP_FPS)
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    dur    = frames / fps if fps > 0 else 0
    size   = os.path.getsize(path) / (1024*1024)
    cap.release()
    mins = int(dur // 60)
    secs = dur % 60
    print(f"{os.path.basename(rel):40s}  {dur:7.1f}s  ({mins}:{secs:04.1f})  fps={fps:.0f}  frames={frames:.0f}  {size:.1f}MB")
