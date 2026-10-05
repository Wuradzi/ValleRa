"""Shared System.Speech voice resolution and synchronous WAV lifecycle."""
import json
import subprocess

from core.security import sanitized_environment


SPEECH_FUNCTIONS = r'''
function Resolve-ValeraVoice($speaker, [string]$hint) {
  $script:valeraStage = 'voice_enumeration'
  $voices = @($speaker.GetInstalledVoices() | Where-Object { $_.Enabled })
  if ($voices.Count -eq 0) { throw 'No enabled installed speech voices' }
  $script:valeraStage = 'voice_resolution'
  $voice = $null
  if ($hint) {
    $voice = $voices | Where-Object { $_.VoiceInfo.Name.IndexOf($hint, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | Select-Object -First 1
  }
  $script:valeraFallback = [bool]($hint -and $null -eq $voice)
  $script:valeraStage = 'voice_selection'
  if ($null -ne $voice) { $speaker.SelectVoice($voice.VoiceInfo.Name) }
  $script:valeraVoice = $speaker.Voice
}
function Invoke-ValeraWave($speaker, [string]$text, [string]$path, [bool]$notify = $false) {
  $script:valeraStage = 'temp_file_creation'
  $speaker.SetOutputToWaveFile($path)
  try {
    $script:valeraStage = 'synthesis_start'
    if ($notify) { [Console]::Out.WriteLine('{"event":"speak_call"}') }
    $speaker.Speak($text)
    $script:valeraStage = 'synthesis_finalize'
  } finally { $speaker.SetOutputToNull() }
}
'''

WINDOWS_SYNTH = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$speaker = $null
$script:valeraStage = 'backend_initialization'
$script:valeraVoice = $null
$script:valeraFallback = $false
''' + SPEECH_FUNCTIONS + r'''
try {
  Add-Type -AssemblyName System.Speech
  $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
  Resolve-ValeraVoice $speaker ([string]$request.hint)
  Invoke-ValeraWave $speaker 'Перевірка голосу.' ([string]$request.path)
  $speaker.Dispose()
  $speaker = $null
  $result = @{status='ok'; stage='synthesis_finalize'}
} catch {
  $result = @{status='synthesis_failed'; stage=$script:valeraStage;
    error_type=$_.Exception.GetType().FullName; exception_message=$_.Exception.Message}
} finally { if ($null -ne $speaker) { $speaker.Dispose() } }
$result.voice = if ($null -ne $script:valeraVoice) { $script:valeraVoice.Name } else { $null }
$result.culture = if ($null -ne $script:valeraVoice) { $script:valeraVoice.Culture.Name } else { $null }
$result.fallback = $script:valeraFallback
$result | ConvertTo-Json -Compress
'''


def synthesize_windows(path, hint):
    # The fixed script has no unbounded output; stdin carries data, never code.
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', WINDOWS_SYNTH],
                            input=json.dumps(dict(path=str(path), hint=hint)), capture_output=True, text=True,
                            encoding='utf-8', timeout=25, env=sanitized_environment(),
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        return dict(status='synthesis_failed', stage='backend_initialization', exit_code=result.returncode,
                    error_type='ChildProcessError', exception_message=result.stderr[:2048])
    data = json.loads(result.stdout[:8192].strip())
    data['exit_code'] = result.returncode
    if 'exception_message' in data:
        data['exception_message'] = data['exception_message'][:2048]
    return data
