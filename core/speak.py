from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from contextvars import ContextVar
from pathlib import Path

from core.tts_cache import TTSCache
from core.console import console_print
from core.security import sanitized_environment
from core.speech_text import SpeechBuffer
from core.performance import CURRENT_TURN, DISABLED_PERFORMANCE, TurnTiming

logger = logging.getLogger(__name__)
SPEECH_SCOPE = ContextVar("speech_scope", default=None)


@dataclass(slots=True)
class SpeechRequest:
    text: str
    queued_at: float
    timing: TurnTiming | None = None
    generation: int = 0


class Speaker:
    def __init__(self, settings):
        self.settings = settings
        self.cache = TTSCache(settings.paths.cache_dir / "tts")
        self._engine = None
        self._lock = threading.RLock()
        self._queue: asyncio.Queue[SpeechRequest | None] = asyncio.Queue(maxsize=8)
        self.performance = DISABLED_PERFORMANCE
        self._current_queued_at = None
        self._current_timing = None
        self._worker_task: asyncio.Task | None = None
        self._speaking = asyncio.Event()
        self._closing = False
        self._stop_requested = threading.Event()
        self._process_lock = threading.Lock()
        self._tts_process: subprocess.Popen | None = None
        self._tts_responses = None
        self._tts_reader = None
        self.generation = 0
        self._ready = asyncio.Event()
        self._ready.set()
        self._stop_lock = asyncio.Lock()
        self._stops_pending = 0
        self.on_text = None
        self.on_playback = None
        self.on_glow = None
        self._glow_lock = threading.Lock()
        self._glow = (0, 0, 0.0)
        self._waveform = []
        self._playback = threading.Event()
        self._event_loop = None

    @property
    def playback_active(self) -> bool:
        """Audio submitted to output, not queue/synthesis or acoustic proof."""
        return self._playback.is_set()

    def glow_state(self) -> dict:
        with self._glow_lock:
            sequence, strength, timestamp = self._glow
            samples = self._waveform[:]
        age_ms = max(0, int((time.monotonic() - timestamp) * 1000))
        fresh = self.playback_active and age_ms < 300
        return {"sequence": sequence,
                "strength": strength if fresh else 0,
                "samples": samples if fresh else [],
                "source": "pcm" if fresh and samples else "unavailable",
                "age_ms": min(450, age_ms)}

    def _speech_audio(self, level, samples) -> None:
        if (self.on_glow is None or not self.playback_active
                or self._stop_requested.is_set() or self._closing):
            return
        if (type(level) is not int or not 0 <= level <= 100
                or not isinstance(samples, list) or len(samples) != 32
                or any(type(value) is not int or not -100 <= value <= 100 for value in samples)):
            return
        with self._glow_lock:
            if not self.playback_active or self._stop_requested.is_set() or self._closing:
                return
            sequence, _, timestamp = self._glow
            now = time.monotonic()
            if now - timestamp < 0.05:
                return
            self._glow = (sequence + 1, min(5, (level + 19) // 20), now)
            self._waveform = samples[:]
        if self._event_loop is not None:
            try:
                self._event_loop.call_soon_threadsafe(self.on_glow)
            except RuntimeError:
                pass

    def _set_playback(self, active: bool) -> None:
        active = active and not self._stop_requested.is_set() and not self._closing
        if active == self._playback.is_set():
            return
        if active:
            self._playback.set()
        else:
            self._playback.clear()
            with self._glow_lock:
                sequence, _, _ = self._glow
                self._glow = (sequence, 0, 0.0)
                self._waveform = []
        if self.on_playback is not None and self._event_loop is not None:
            try:
                self._event_loop.call_soon_threadsafe(self.on_playback)
            except RuntimeError:
                pass  # The UI/event loop may already be closed during shutdown.

    @property
    def busy(self) -> bool:
        return self._speaking.is_set() or not self._queue.empty()

    @property
    def healthy(self) -> bool:
        return self._worker_task is not None and not self._worker_task.done()

    async def start(self) -> None:
        self._event_loop = asyncio.get_running_loop()
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker(), name="tts-worker")

    async def prepare(self) -> None:
        if os.name == "nt":
            await asyncio.to_thread(self._generate_with_windows_speech, "", None)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        await self.stop()
        await self._queue.put(None)
        if self._worker_task:
            await self._worker_task

    async def say(self, text: str) -> None:
        scope = SPEECH_SCOPE.get()
        generation = scope[1] if scope is not None and scope[0] is self else self.generation
        if generation != self.generation:
            return
        if text.strip() and not self._closing:
            console_print(f"ValleRa: {text}")
            if self.on_text is not None:
                self.on_text(text)
            for part in SpeechBuffer().feed(text, final=True):
                if generation != self.generation or self._closing:
                    return
                timing = CURRENT_TURN.get()
                if timing is not None:
                    timing.mark("first_phrase")
                await self._queue.put(SpeechRequest(part, time.perf_counter(), timing, generation))

    async def wait_until_idle(self) -> None:
        await self._queue.join()

    async def test_voice_generation(self) -> tuple[bool, str]:
        return await asyncio.to_thread(self._test_voice_generation_sync)

    async def stop(self) -> None:
        self.generation += 1
        self._stop_requested.set()
        self._set_playback(False)
        self._ready.clear()
        self._stops_pending += 1
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        try:
            async with self._stop_lock:
                stopping = asyncio.create_task(asyncio.to_thread(self._stop_sync))
                try:
                    await asyncio.shield(stopping)
                except asyncio.CancelledError:
                    await stopping
                    raise
        finally:
            self._stops_pending -= 1
            if not self._stops_pending:
                self._ready.set()

    async def _worker(self) -> None:
        while True:
            request = await self._queue.get()
            try:
                if request is None:
                    return
                await self._ready.wait()
                if self._closing or request.generation != self.generation:
                    continue
                self._stop_requested.clear()
                self._speaking.set()
                self._current_queued_at = request.queued_at
                self._current_timing = request.timing
                if request.timing is not None:
                    request.timing.mark("tts_start")
                self.performance.record("tts.queue_wait", (time.perf_counter() - request.queued_at) * 1000)
                with self.performance.span("tts.phrase_including_playback"):
                    await asyncio.to_thread(self._speak_sync, request.text)
            except Exception:
                if self._current_timing is not None:
                    self._current_timing.mark("tts_error")
                if not self._stop_requested.is_set():
                    logger.exception("TTS failed")
                    console_print("Не вдалося відтворити голос. Подробиці — у журналі сесії.")
            finally:
                self._speaking.clear()
                self._current_queued_at = None
                self._current_timing = None
                self._queue.task_done()

    def _get_engine(self):
        if self._engine is None:
            import pyttsx3

            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.settings.tts_rate)
            self._engine.setProperty("volume", self.settings.tts_volume)
            hint = self.settings.tts_voice_hint.lower()
            for voice in self._engine.getProperty("voices"):
                if hint in f"{voice.name} {voice.id}".lower():
                    self._engine.setProperty("voice", voice.id)
                    break
        return self._engine

    def _speak_sync(self, text: str) -> None:
        try:
            self._speak_with_output(text)
        finally:
            self._set_playback(False)

    def _speak_with_output(self, text: str) -> None:
        with self._lock:
            if self._closing or self._stop_requested.is_set():
                return
            started = time.perf_counter()
            if os.name == "nt" and self.settings.output_device is None:
                # The resident SAPI process speaks as it synthesizes. No WAV or
                # new PowerShell process is needed for each streamed phrase.
                self._generate_with_windows_speech(text, None)
                return
            cache_path = self.cache.path_for(
                text,
                self.settings.tts_voice_hint,
                self.settings.tts_rate,
            )
            if self._is_valid_wav(cache_path):
                self._play_wav(cache_path)
                return
            cache_path.unlink(missing_ok=True)

            if os.name == "nt":
                self._generate_with_windows_speech(text, cache_path)
                if self._closing or self._stop_requested.is_set():
                    return
                if not self._is_valid_wav(cache_path):
                    cache_path.unlink(missing_ok=True)
                    raise RuntimeError("Windows Speech створив порожній WAV-файл")
                logger.info(
                    "TTS chunk ready in %.3f s (%s characters)",
                    time.perf_counter() - started,
                    len(text),
                )
                self._play_wav(cache_path)
                return

            engine = self._get_engine()
            try:
                engine.save_to_file(text, str(cache_path))
                engine.runAndWait()
                if self._is_valid_wav(cache_path):
                    self._play_wav(cache_path)
                    return
                cache_path.unlink(missing_ok=True)
            except Exception:
                logger.exception("TTS cache generation failed")
                cache_path.unlink(missing_ok=True)

            try:
                engine.say(text)
                self._set_playback(True)
                if self._current_timing is not None:
                    self._current_timing.mark("tts_submit")
                engine.runAndWait()
            except Exception:
                logger.exception("Direct pyttsx3 playback failed")
                raise

    def _test_voice_generation_sync(self) -> tuple[bool, str]:
        text = "Перевірка генерації голосу ValleRa"
        path = self.cache.path_for(
            text,
            self.settings.tts_voice_hint,
            self.settings.tts_rate,
        )
        try:
            if os.name == "nt":
                self._generate_with_windows_speech(text, path)
                if self._is_valid_wav(path):
                    return True, (
                        f"Windows Speech; голос {self.settings.tts_voice_hint}; WAV створено"
                    )
                return False, "Windows Speech створив порожній WAV-файл"

            with self._lock:
                engine = self._get_engine()
                engine.save_to_file(text, str(path))
                engine.runAndWait()
            if self._is_valid_wav(path):
                return True, f"голос {self.settings.tts_voice_hint}; WAV створено"
            return False, "TTS створив порожній або пошкоджений WAV-файл"
        except Exception as exc:
            return False, str(exc).strip() or type(exc).__name__
        finally:
            path.unlink(missing_ok=True)

    def _play_wav(self, path) -> None:
        if self._current_timing is not None:
            self._current_timing.mark("tts_submit")
        if self._current_queued_at is not None:
            self.performance.record("tts.queue_to_playback_submit", (time.perf_counter() - self._current_queued_at) * 1000)
        if self.settings.output_device is not None:
            try:
                import sounddevice as sd

                with wave.open(str(path), "rb") as wav_file:
                    dtype = {
                        1: "uint8",
                        2: "int16",
                        3: "int24",
                        4: "int32",
                    }.get(wav_file.getsampwidth())
                    if dtype is None:
                        raise RuntimeError("непідтримувана розрядність WAV")
                    with sd.RawOutputStream(
                        samplerate=wav_file.getframerate(),
                        channels=wav_file.getnchannels(),
                        dtype=dtype,
                        device=self.settings.output_device,
                    ) as stream:
                        self._set_playback(True)
                        while data := wav_file.readframes(4096):
                            if self._stop_requested.is_set():
                                break
                            stream.write(data)
                return
            except Exception:
                self._set_playback(False)
                logger.exception("Selected audio output failed; using system default")
        try:
            import winsound

            self._set_playback(True)
            winsound.PlaySound(str(path), winsound.SND_FILENAME)
        except ImportError:
            import subprocess

            self._set_playback(True)
            subprocess.run(["aplay", str(path)], check=False)

    @staticmethod
    def _is_valid_wav(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            with wave.open(str(path), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                frames = wav_file.getnframes()
                return frame_rate > 0 and frames >= max(1, frame_rate // 10)
        except (OSError, EOFError, wave.Error):
            return False

    def _generate_with_windows_speech(self, text: str, path: Path | None) -> None:
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

    def _start_windows_speech(self) -> None:
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
            "Add-Type -AssemblyName System.Speech; "
            "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            + pulse_setup +
            "$hint = $env:VALLERA_TTS_HINT; "
            "$voice = $speaker.GetInstalledVoices() | Where-Object { "
            "$_.Enabled -and $_.VoiceInfo.Name.IndexOf($hint, "
            "[System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
            "Select-Object -First 1; "
            "if ($null -ne $voice) { $speaker.SelectVoice($voice.VoiceInfo.Name) }; "
            f"$speaker.Rate = {rate}; $speaker.Volume = {volume}; "
            "try { while ($null -ne ($line = [Console]::In.ReadLine())) { "
            "try { $request = ConvertFrom-Json -InputObject $line; "
            "if ($request.text) { "
            "$pcmOutput = $null; "
            "if ($request.path) { $speaker.SetOutputToWaveFile([string]$request.path) } "
            "elseif ($pulseReady) { try { "
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
            "if ($null -ne $pcmOutput) { $pcmOutput.Dispose() } } } }; "
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
                "VALLERA_PULSE_SOURCE": str(Path(__file__).resolve().parent.parent
                                           / "services" / "audio" / "speech_pulse.cs"),
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

    def _stop_sync(self) -> None:
        with self._process_lock:
            process = self._tts_process
            if process is not None and process.poll() is None:
                process.terminate()
            if process is not None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
                if process.stdin:
                    process.stdin.close()
            self._tts_process = None
            if self._tts_reader is not None:
                self._tts_reader.join(timeout=1)
                self._tts_reader = None
        try:
            import winsound

            winsound.PlaySound(None, 0)
        except (ImportError, RuntimeError):
            pass
        if self._engine is not None:
            try:
                self._engine.stop()
            except RuntimeError:
                pass
