import ctypes
import io
import os
import sys
import threading
import time
import wave
from ctypes import wintypes

import keyboard
import numpy as np
import requests
import sounddevice as sd
from dotenv import load_dotenv
from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
)


GROQ_TRANSCRIPT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

SAMPLE_RATE = 16000
CHANNELS = 1

load_dotenv()

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.windll.kernel32
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_RETURN = 0x0D


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def raise_for_status_with_details(response: requests.Response) -> None:
    if response.ok:
        return

    try:
        payload = response.json()
        detail = payload.get("error") or payload.get("message") or str(payload)
    except Exception:
        detail = response.text.strip() or response.reason

    raise requests.HTTPError(
        f"{response.status_code} {response.reason}: {detail}",
        response=response,
    )


def get_groq_api_key() -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if api_key:
        return api_key

    raise RuntimeError(
        "GROQ_API_KEY is not set. Create a .env file or set the environment variable."
    )


def build_wav(audio_data: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio_data.tobytes())
    return buffer.getvalue()


def transcribe_with_groq(audio_bytes: bytes) -> str:
    response = requests.post(
        GROQ_TRANSCRIPT_URL,
        headers={"Authorization": f"Bearer {get_groq_api_key()}"},
        files={"file": ("segment.wav", audio_bytes, "audio/wav")},
        data={
            "model": GROQ_MODEL,
            "temperature": "0",
            "response_format": "json",
            "language": "ru",
        },
        timeout=180,
    )
    raise_for_status_with_details(response)
    return (response.json().get("text") or "").strip()


def transcribe_audio(audio_data: np.ndarray) -> str:
    wav_bytes = build_wav(audio_data)
    return transcribe_with_groq(wav_bytes)


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow())


def focus_window(hwnd: int) -> None:
    if not hwnd:
        return

    foreground = user32.GetForegroundWindow()
    current_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    foreground_thread = (
        user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    )

    try:
        if foreground_thread:
            user32.AttachThreadInput(foreground_thread, current_thread, True)
        if target_thread:
            user32.AttachThreadInput(target_thread, current_thread, True)

        user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if foreground_thread:
            user32.AttachThreadInput(foreground_thread, current_thread, False)
        if target_thread:
            user32.AttachThreadInput(target_thread, current_thread, False)


def release_modifier_keys() -> None:
    for vk_code in (VK_MENU, VK_CONTROL, VK_SHIFT):
        user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


def send_virtual_key(vk_code: int, *, key_up: bool = False) -> None:
    extra = ctypes.pointer(wintypes.ULONG(0))
    key_input = KEYBDINPUT(
        wVk=vk_code,
        wScan=0,
        dwFlags=KEYEVENTF_KEYUP if key_up else 0,
        time=0,
        dwExtraInfo=extra,
    )
    input_record = INPUT(type=INPUT_KEYBOARD, ki=key_input)
    result = user32.SendInput(1, ctypes.byref(input_record), ctypes.sizeof(INPUT))
    if result != 1:
        raise OSError(ctypes.get_last_error(), "SendInput failed for virtual key.")


def send_unicode_char(char: str) -> None:
    extra = ctypes.pointer(wintypes.ULONG(0))
    code_point = ord(char)

    key_down = KEYBDINPUT(
        wVk=0,
        wScan=code_point,
        dwFlags=KEYEVENTF_UNICODE,
        time=0,
        dwExtraInfo=extra,
    )
    key_up = KEYBDINPUT(
        wVk=0,
        wScan=code_point,
        dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
        time=0,
        dwExtraInfo=extra,
    )

    down_record = INPUT(type=INPUT_KEYBOARD, ki=key_down)
    up_record = INPUT(type=INPUT_KEYBOARD, ki=key_up)

    result = user32.SendInput(1, ctypes.byref(down_record), ctypes.sizeof(INPUT))
    if result != 1:
        raise OSError(ctypes.get_last_error(), "SendInput failed for key down.")

    result = user32.SendInput(1, ctypes.byref(up_record), ctypes.sizeof(INPUT))
    if result != 1:
        raise OSError(ctypes.get_last_error(), "SendInput failed for key up.")


def send_unicode_text(text: str) -> None:
    if not text:
        return

    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    for char in normalized_text:
        if char == "\n":
            send_virtual_key(VK_RETURN, key_up=False)
            send_virtual_key(VK_RETURN, key_up=True)
            continue
        send_unicode_char(char)


class OverlayWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()

        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        if hasattr(Qt.WindowType, "WindowDoesNotAcceptFocus"):
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        if hasattr(Qt.WindowType, "WindowTransparentForInput"):
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.current_state = "idle"
        self.current_level = 0.0

        self.setFixedSize(96, 96)
        self.hide()

    def position_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        x = geometry.x() + (geometry.width() - self.width()) // 2
        y = geometry.y() + geometry.height() - self.height() - 36
        self.move(QPoint(x, y))

    def set_state(self, state: str) -> None:
        self.current_state = state
        self.update()

    def set_level(self, level: float) -> None:
        self.current_level = max(0.0, min(level, 1.0))
        self.update()

    def set_transcript(self, value: str) -> None:
        _ = value

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center_x = self.width() / 2
        center_y = self.height() / 2

        if self.current_state == "error":
            base_color = QColor(255, 92, 92)
            level = 0.45
        elif self.current_state == "finalizing":
            base_color = QColor(86, 210, 255)
            level = 0.22
        elif self.current_state == "pasting":
            base_color = QColor(94, 239, 168)
            level = 0.28
        else:
            base_color = QColor(52, 211, 123)
            level = self.current_level

        glow_radius = 30 + (18 * level)
        glow_color = QColor(base_color)
        glow_color.setAlpha(40 + int(level * 90))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow_color)
        painter.drawEllipse(
            QPoint(int(center_x), int(center_y)),
            int(glow_radius),
            int(glow_radius),
        )

        ring_color = QColor(base_color)
        ring_color.setAlpha(110 + int(level * 120))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ring_color, 3))
        painter.drawEllipse(
            QPoint(int(center_x), int(center_y)),
            int(25 + level * 8),
            int(25 + level * 8),
        )

        disc_color = QColor(15, 23, 42, 225)
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
        painter.setBrush(disc_color)
        painter.drawEllipse(QPoint(int(center_x), int(center_y)), 26, 26)

        mic_color = QColor(base_color)
        mic_color = mic_color.lighter(135)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mic_color)
        painter.drawRoundedRect(int(center_x - 8), int(center_y - 16), 16, 24, 8, 8)

        painter.setBrush(disc_color)
        painter.drawRoundedRect(int(center_x - 5), int(center_y - 13), 10, 18, 5, 5)

        stem_pen = QPen(mic_color, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(stem_pen)
        painter.drawLine(
            QPoint(int(center_x), int(center_y + 8)),
            QPoint(int(center_x), int(center_y + 16)),
        )
        painter.drawLine(
            QPoint(int(center_x - 8), int(center_y + 16)),
            QPoint(int(center_x + 8), int(center_y + 16)),
        )

        arc_path = QPainterPath()
        arc_path.moveTo(center_x - 14, center_y - 3)
        arc_path.cubicTo(
            center_x - 14,
            center_y + 12,
            center_x + 14,
            center_y + 12,
            center_x + 14,
            center_y - 3,
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(mic_color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        painter.drawPath(arc_path)

    def show_overlay(self) -> None:
        self.position_on_screen()
        self.show()
        self.raise_()

    def hide_overlay(self) -> None:
        self.hide()


class DictationController(QObject):
    hotkey_pressed = Signal()
    hotkey_released = Signal()
    state_changed = Signal(str)
    transcript_changed = Signal(str)
    level_changed = Signal(float)
    job_finished = Signal(object)
    paste_finished = Signal()
    error_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.stream: sd.InputStream | None = None
        self.active = False
        self.awaiting_transcription = False
        self.target_hwnd = 0
        self.current_buffers: list[np.ndarray] = []

        self.hotkey_pressed.connect(self.start_session)
        self.hotkey_released.connect(self.stop_session)
        self.job_finished.connect(self.handle_transcription_finished)
        self.paste_finished.connect(self.reset_after_paste)

        keyboard.on_press_key(
            "f1", lambda _event: self.hotkey_pressed.emit(), suppress=True
        )
        keyboard.on_release_key(
            "f1", lambda _event: self.hotkey_released.emit(), suppress=True
        )

    def shutdown(self) -> None:
        keyboard.unhook_all()
        self.stop_stream()

    def start_session(self) -> None:
        if self.active or self.awaiting_transcription:
            return

        self.target_hwnd = get_foreground_window()
        self.active = True
        self.awaiting_transcription = False
        self.current_buffers = []
        self.transcript_changed.emit("")
        self.state_changed.emit("listening")

        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                callback=self.audio_callback,
            )
            self.stream.start()
        except Exception as exc:
            self.active = False
            self.state_changed.emit("error")
            self.error_changed.emit(str(exc))
            QTimer.singleShot(1200, lambda: self.state_changed.emit("idle"))

    def stop_session(self) -> None:
        if not self.active:
            return

        self.active = False
        self.awaiting_transcription = True
        self.state_changed.emit("finalizing")
        self.stop_stream()
        self.level_changed.emit(0.0)

        audio_data = self.consume_recording()
        if audio_data is None:
            self.paste_finished.emit()
            return

        threading.Thread(
            target=self.transcribe_session_worker,
            args=(audio_data,),
            daemon=True,
        ).start()

    def stop_stream(self) -> None:
        if self.stream is None:
            return
        try:
            self.stream.stop()
            self.stream.close()
        finally:
            self.stream = None

    def audio_callback(self, indata, frames, _time_info, status) -> None:
        if status:
            print(status)

        normalized = indata.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(np.square(normalized))))
        level = min(rms * 9.0, 1.0)
        self.level_changed.emit(level)

        if not self.active:
            return

        self.current_buffers.append(indata.copy())

    def consume_recording(self) -> np.ndarray | None:
        if not self.current_buffers:
            return None

        audio_data = np.concatenate(self.current_buffers, axis=0)
        self.current_buffers = []
        return audio_data

    def transcribe_session_worker(self, audio_data: np.ndarray) -> None:
        try:
            text = transcribe_audio(audio_data)
            payload = {"text": text, "error": None}
        except Exception as exc:
            payload = {"text": "", "error": str(exc)}

        self.job_finished.emit(payload)

    def handle_transcription_finished(self, payload: object) -> None:
        data = (
            payload
            if isinstance(payload, dict)
            else {"text": "", "error": "Unknown error"}
        )

        error = str(data.get("error") or "").strip()
        text = str(data.get("text") or "").strip()

        if error:
            self.awaiting_transcription = False
            self.error_changed.emit(error)
            self.state_changed.emit("error")
            QTimer.singleShot(1200, self.paste_finished.emit)
            return

        self.finish_session(text)

    def finish_session(self, final_text: str) -> None:
        self.awaiting_transcription = False
        self.transcript_changed.emit(final_text)

        if not final_text:
            self.paste_finished.emit()
            return

        self.state_changed.emit("pasting")
        threading.Thread(
            target=self.paste_text_worker,
            args=(final_text, self.target_hwnd),
            daemon=True,
        ).start()

    def paste_text_worker(self, text: str, hwnd: int) -> None:
        if text:
            try:
                print(f"Preparing text input for hwnd={hwnd}, chars={len(text)}")
                release_modifier_keys()
                focus_window(hwnd)
                time.sleep(0.12)
                keyboard.press_and_release("esc")
                time.sleep(0.12)
                send_unicode_text(text)
                print("Unicode text sent")
            except Exception as exc:
                print(f"Text input failed: {exc}")

        self.paste_finished.emit()

    def reset_after_paste(self) -> None:
        self.target_hwnd = 0
        self.current_buffers = []
        self.transcript_changed.emit("")
        self.state_changed.emit("idle")


def main() -> int:
    application = QApplication(sys.argv)
    application.setQuitOnLastWindowClosed(False)

    overlay = OverlayWindow()
    controller = DictationController()

    controller.state_changed.connect(
        lambda state: (
            overlay.show_overlay() if state != "idle" else overlay.hide_overlay(),
            overlay.set_state(state),
        )
    )
    controller.transcript_changed.connect(overlay.set_transcript)
    controller.level_changed.connect(overlay.set_level)
    controller.error_changed.connect(overlay.set_transcript)

    exit_code = 0
    try:
        exit_code = application.exec()
    finally:
        controller.shutdown()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
