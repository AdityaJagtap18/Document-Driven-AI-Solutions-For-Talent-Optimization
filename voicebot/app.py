import streamlit as st
from streamlit_webrtc import webrtc_streamer, WebRtcMode, RTCConfiguration
from voice_bot import run_voice_interview
from video_recorder import VideoRecorder
import threading
import time
import os

RTC_CONFIG = RTCConfiguration({
    "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]
})

st.set_page_config(page_title="AI Interview Bot with Video", layout="centered")
st.title("🧠 AI Interview Bot with Video")

# ---- SESSION STATE INIT ----
if "interview_started" not in st.session_state:
    st.session_state.interview_started = False
if "interview_done" not in st.session_state:
    st.session_state.interview_done = False
if "current_msg" not in st.session_state:
    st.session_state.current_msg = ""
if "video_saved" not in st.session_state:
    st.session_state.video_saved = False
if "candidate_name" not in st.session_state:
    st.session_state.candidate_name = "Unknown_User"

# ---- Run Bot in Background ----
def run_bot_and_stop_video():
    generator = run_voice_interview()
    for message in generator:
        if message.startswith("🎤 Bot:"):
            st.session_state.current_msg = message.replace("🎤 Bot: ", "")
        elif message.startswith("🗣️ You:"):
            pass

    # Get candidate name at end
    try:
        candidate_name = generator.send(None)
    except StopIteration as e:
        candidate_name = e.value or "Unknown_User"

    st.session_state["candidate_name"] = candidate_name
    st.session_state["interview_done"] = True

# ---- UI ----
if not st.session_state.interview_started:
    if st.button("🟣 Start Interview"):
        st.session_state.interview_started = True
        st.rerun()

elif st.session_state.interview_started and not st.session_state.interview_done:
    # Live Stream + Bot + Recorder
    candidate = st.session_state["candidate_name"]
    candidate_safe = candidate.replace(" ", "_")
    save_dir = f"InterviewData/{candidate_safe}/InterviewVideo"
    os.makedirs(save_dir, exist_ok=True)
    video_path = f"{save_dir}/recording.webm"
    st.session_state["video_path"] = video_path

    # Show Video Feed
    st.markdown("### 🔴 Live Video")
    webrtc_ctx = webrtc_streamer(
        key="interview",
        mode=WebRtcMode.SENDRECV,
        rtc_configuration=RTC_CONFIG,
        media_stream_constraints={"video": True, "audio": True},
        video_processor_factory=VideoRecorder,
        async_processing=True,
    )
    st.session_state["webrtc_ctx"] = webrtc_ctx

    # Start bot after stream is ready
    if webrtc_ctx and webrtc_ctx.state.playing and "bot_thread" not in st.session_state:
        thread = threading.Thread(target=run_bot_and_stop_video, daemon=True)
        thread.start()
        st.session_state["bot_thread"] = thread

    st.markdown("### 🤖 Bot Says:")
    st.info(st.session_state.current_msg or "Waiting for bot...")

# ---- Interview Done ----
elif st.session_state.interview_done and not st.session_state.video_saved:
    st.success("🎉 Interview Completed!")
  
    webrtc_ctx = st.session_state.get("webrtc_ctx")
    video_path = st.session_state.get("video_path")

    if webrtc_ctx and hasattr(webrtc_ctx, "video_processor") and webrtc_ctx.video_processor:
        try:
            webrtc_ctx.video_processor.save_video(video_path)
            st.success(f"🎥 Video saved successfully to `{video_path}`")
            st.session_state["video_saved"] = True
        except Exception as e:
            st.error(f"❌ Failed to save video: {e}")
    else:
        st.warning("⚠️ Video processor not available for saving.")
 
    if st.button("🔁 Restart"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
