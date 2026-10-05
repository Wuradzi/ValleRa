"""Windows resident speech transport. Speaker retains queue/generation ownership."""
import json
import logging
from pathlib import Path
import queue
import subprocess
import threading
import time
from core.security import sanitized_environment

logger = logging.getLogger(__name__)

def generate(self, text: str, path: Path | None) -> None:
    started = time.perf_counter()
    # Requests are data-only JSON; neither user text nor paths are executable
    # PowerShell. Keep one voice instance for the entire application session.
    with self._lock:
        with self._process_lock:
            if self._closing or self._stop_requested.is_set():
                return
            if self._tts_process is None or self._tts_process.poll() is not None:
                self._start_windows_speech()
            process, responses = self._tts_process, self._tts_responses
        request = {"text": text, "path": str(path.resolve()) if path else ""}
        process.stdin.write(json.dumps(request, ensure_ascii=True) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + max(30, len(text) // 8)
        while not self._stop_requested.is_set() and not self._closing:
            # A stuck adapter may keep emitting telemetry without completing.
            if time.monotonic() >= deadline:
                self._stop_sync()
                raise RuntimeError("Тайм-аут Windows Speech")
            try:
                result = responses.get(timeout=0.1)
            except queue.Empty:
                # stop() can terminate the child while this thread waits.
                if self._stop_requested.is_set() or self._closing:
                    return
                if process.poll() is not None:
                    raise RuntimeError("Windows Speech завершився без відповіді")
                if time.monotonic() >= deadline:
                    self._stop_sync()
                    raise RuntimeError("Тайм-аут Windows Speech")
                continue
            if result.get("event") == "speak_call":
                if path is None and text:
                    self._set_playback(True)
                if self._current_timing is not None:
                    self._current_timing.mark("tts_submit" if path is None else "tts_file_synthesis")
                self.performance.record("tts.request_to_speak_call", (time.perf_counter() - started) * 1000)
                if self._current_queued_at is not None:
                    self.performance.record("tts.queue_to_speak_call", (time.perf_counter() - self._current_queued_at) * 1000)
                continue
            if result.get("event") == "speech_audio":
                if path is None:
                    self._speech_audio(result.get("level"), result.get("samples"))
                continue
            if result.get("event") == "pulse_unavailable":
                logger.warning("Speech pulse telemetry unavailable; using steady UI glow")
                continue
            if not result.get("ok"):
                raise RuntimeError("Windows Speech: " + result.get("error", "помилка голосу"))
            return

def start(self) -> None:
    from services.audio.windows_speech import SPEECH_FUNCTIONS
    rate = max(-10, min(10, round((self.settings.tts_rate - 180) / 20)))
    volume = max(0, min(100, round(self.settings.tts_volume * 100)))
    # Compile the optional streaming PCM adapter once per resident TTS process.
    # A missing compiler/adapter must never make the voice unavailable.
    pulse_setup = (
        "$pulseReady = $false; "
        "try { Add-Type -Path $env:VALLERA_PULSE_SOURCE "
        "-ReferencedAssemblies ([System.Speech.Synthesis.SpeechSynthesizer].Assembly.Location) "
        "-ErrorAction Stop -WarningAction SilentlyContinue; "
        "$pulseReady = $true "
        "} catch { [Console]::Out.WriteLine('{\"event\":\"pulse_unavailable\"}') }; "
    ) if self.on_glow is not None else "$pulseReady = $false; "
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
        + SPEECH_FUNCTIONS +
        "Add-Type -AssemblyName System.Speech; "
        "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        + pulse_setup +
        "$hint = $env:VALLERA_TTS_HINT; "
        "Resolve-ValeraVoice $speaker $hint; "
        f"$speaker.Rate = {rate}; $speaker.Volume = {volume}; "
        "try { while ($null -ne ($line = [Console]::In.ReadLine())) { "
        "try { $request = ConvertFrom-Json -InputObject $line; "
        "if ($request.text) { "
        "$pcmOutput = $null; "
        "if ($request.path) { Invoke-ValeraWave $speaker ([string]$request.text) ([string]$request.path) $true } else { "
        "if ($pulseReady) { try { "
        "$pcmOutput = New-Object ValeraSpeechAudio; "
        "$speaker.SetOutputToAudioStream($pcmOutput, [ValeraSpeechAudio]::Format) "
        "} catch { if ($null -ne $pcmOutput) { $pcmOutput.Dispose(); $pcmOutput = $null }; "
        "$pulseReady = $false; "
        "[Console]::Out.WriteLine('{\"event\":\"pulse_unavailable\"}'); "
        "$speaker.SetOutputToDefaultAudioDevice() } } "
        "else { $speaker.SetOutputToDefaultAudioDevice() }; "
        "[Console]::Out.WriteLine('{\"event\":\"speak_call\"}'); "
        "try { $speaker.Speak([string]$request.text); "
        "if ($null -ne $pcmOutput) { $pcmOutput.Flush() } "
        "} catch { if ($null -ne $pcmOutput) { $pulseReady = $false }; throw "
        "} finally { try { $speaker.SetOutputToNull() } finally { "
        "if ($null -ne $pcmOutput) { $pcmOutput.Dispose() } } } } }; "
        "[Console]::Out.WriteLine('{\"ok\":true}'); "
        "} catch { "
        "$result = @{ok=$false; error=$_.Exception.Message} | ConvertTo-Json -Compress; "
        "[Console]::Out.WriteLine($result) "
        "} } } finally { $speaker.Dispose() }"
    )
    # Reap a worker that failed before its next request.
    previous = self._tts_process
    if previous is not None:
        previous.wait()
        if previous.stdin:
            previous.stdin.close()
    process = subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=sanitized_environment({
            "VALLERA_TTS_HINT": self.settings.tts_voice_hint,
            "VALLERA_PULSE_SOURCE": str(Path(__file__).resolve().parents[1] / "audio" / "speech_pulse.cs"),
        }),
    )
    responses = queue.Queue()
    self._tts_process, self._tts_responses = process, responses

    def read_responses():
        try:
            for line in process.stdout:
                try:
                    responses.put(json.loads(line))
                except ValueError:
                    responses.put({"ok": False, "error": "Некоректна відповідь TTS"})
        finally:
            process.stdout.close()

    self._tts_reader = threading.Thread(target=read_responses, name="sapi-output", daemon=True)
    self._tts_reader.start()
