"""
Parashurama audio generation skill — code-only, no AI models.

Parashurama writes Python code using scipy/numpy for waveform synthesis
or midiutil + pydub for MIDI-based music. The executor runs it and
returns the .wav path.

Supported types:
  music    — MIDI-based melody (midiutil → pydub tone synthesis)
  ambience — layered sine/noise waveforms (scipy + numpy)
  beeps    — simple tone sequences (scipy)

Limitations:
  - No AI-generated audio — purely algorithmic synthesis
  - MIDI sounds mechanical without a sample library (FluidSynth not assumed)
  - Complex harmonics and instrument timbre require real sample packs
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_PHASE7 = Path(__file__).parent.parent
sys.path.insert(0, str(_PHASE7))
from executor import execute_code

_SERVER_MEDIA_BASE = os.environ.get("MEDIA_URL_BASE", "http://localhost:8000/media")


def create_audio(
    code: str,
    type: str = "music",
    output_filename: str = "",
) -> dict:
    """Generate an audio file by executing Python code using scipy, numpy, and pydub.

    Parashurama must pass complete, working Python code that:
    - Uses numpy/scipy for waveform synthesis OR midiutil + pydub for MIDI-based music
    - Writes the final audio to: os.path.join(OUTPUT_DIR, "audio.wav")
    - Keeps sample rate at 44100 Hz, mono or stereo
    - Does NOT import subprocess, requests, socket, or any network library

    Type guide:
      music    — tonal melody using sine waves per note (C4=261.63Hz, D4=293.66Hz, etc.)
                 Build note arrays with numpy, concatenate, write with scipy.io.wavfile
      ambience — layered noise + drone using numpy random + sine waves
      beeps    — simple tone sequence (useful for notifications or retro sounds)

    Synthesis pattern for a note (440 Hz, 0.5s, 44100 sample rate):
      t = numpy.linspace(0, duration, int(44100 * duration), endpoint=False)
      wave = numpy.sin(2 * numpy.pi * frequency * t)

    Write pattern:
      scipy.io.wavfile.write(
          os.path.join(OUTPUT_DIR, "audio.wav"),
          44100,
          (wave * 32767).astype(numpy.int16)
      )

    All output files must be written to OUTPUT_DIR (pre-set by executor).

    Returns a dict with status, url (playable in the frontend), and any error details.

    IMPORTANT: This produces algorithmic audio — no AI models involved.
    Always inform the user: "This is synthesized audio (sine-wave / waveform only —
    no sampled instruments unless a soundfont is configured)."
    """
    if not code or not code.strip():
        return {
            "status":  "error",
            "message": "code parameter is required — pass complete Python code to execute",
            "url":     "",
        }

    fname = output_filename or f"audio_{uuid.uuid4().hex[:8]}.wav"
    if not fname.endswith((".wav", ".mp3")):
        fname += ".wav"

    result = execute_code(code)

    audio_files = [
        f for f in result.get("output_files", [])
        if f.endswith((".wav", ".mp3"))
    ]

    if result["status"] == "ok" and audio_files:
        out_path = audio_files[0]
        rel = Path(out_path).name
        run_id = result["run_id"]
        url = f"{_SERVER_MEDIA_BASE}/{run_id}/{rel}"
        return {
            "status":    "ok",
            "url":       url,
            "file_path": out_path,
            "message":   f"Audio synthesized in {result['duration_s']}s. Waveform/MIDI synthesis — no sampled instruments.",
        }

    return {
        "status":  result["status"],
        "url":     "",
        "message": (
            f"Execution failed ({result['status']}).\n"
            f"stderr: {result['stderr'][:600]}\n"
            f"stdout: {result['stdout'][:200]}\n"
            "Rewrite the code and try again."
        ),
    }
