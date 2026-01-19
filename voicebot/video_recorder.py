import os
import cv2
from collections import deque
from streamlit_webrtc import VideoProcessorBase
import av

class VideoRecorder(VideoProcessorBase):
    def __init__(self):
        self.frames = deque(maxlen=3000)  # ~2–3 min buffer

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        self.frames.append(img)
        return av.VideoFrame.from_ndarray(img, format="bgr24")

    def save_video(self, output_path):
        if not self.frames:
            print("No frames to save.")
            return

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        height, width, _ = self.frames[0].shape
        out = cv2.VideoWriter(output_path, cv2.VideoWriter_fourcc(*'vp80'), 15.0, (width, height))

        for frame in self.frames:
            out.write(frame)

        out.release()
        print(f"Video saved: {output_path}")
                                     