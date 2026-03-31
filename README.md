# Voice dictation overlay

Small Windows app that records speech while you hold `Left Alt`, sends audio to
Groq Whisper, and types the recognized text into the active window.

## What it does

- Hold `Left Alt` to start recording.
- Release `Left Alt` to stop recording and transcribe.
- The app shows a small floating microphone indicator that reacts to your voice.
- The recognized text is typed into the currently active window.

## Requirements

- Windows
- Python 3.10+
- A Groq API key

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env`.
4. Put your Groq API key into `.env`:

```env
GROQ_API_KEY=your_groq_api_key_here
```

## Run

Start the app with:

```bash
python app.py
```

Or double-click `run.bat`.

## Usage

1. Focus any text field.
2. Hold `Left Alt`.
3. Speak.
4. Release `Left Alt`.
5. Wait for the text to be inserted.

## Notes

- The repository does not include any API keys.
- Each user must add their own `GROQ_API_KEY`.
- The app currently uses the `whisper-large-v3` Groq model.
