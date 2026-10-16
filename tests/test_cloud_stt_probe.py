import base64
import io
import unittest
import wave

from testing.probes.cloud_stt import payload, transcript, IDS, interaction_payload, interaction_transcript, safe_error, flash_payload


class CloudSTTProbeTests(unittest.TestCase):
    def test_flash_control_has_no_reference_or_postprocessing(self):
        body = flash_payload(bytes(3200), 16000)
        self.assertEqual(body['generationConfig']['thinkingConfig']['thinkingBudget'], 0)
        self.assertEqual(len(body['contents'][0]['parts']), 2)
        self.assertIn('Do not answer or execute', body['contents'][0]['parts'][0]['text'])
        self.assertIn('inlineData', body['contents'][0]['parts'][1])

    def test_error_redaction(self):
        message = safe_error({'error': {'message': 'Invalid model token-secret ' + 'A'*100}}, 'token-secret')
        self.assertIn('Invalid model', message)
        self.assertNotIn('token-secret', message)
        self.assertNotIn('A'*100, message)

    def test_interactions_no_storage_and_only_completed_model_text(self):
        body = interaction_payload(bytes(3200), 16000)
        self.assertFalse(body['store'])
        self.assertEqual(body['input'][0]['type'], 'audio')
        response = {'status': 'completed', 'steps': [
            {'type': 'thought', 'content': [{'type': 'text', 'text': 'ignore'}]},
            {'type': 'model_output', 'content': [{'type': 'text', 'text': 'Привіт'}]}]}
        self.assertEqual(interaction_transcript(response), 'Привіт')
        response['status'] = 'incomplete'
        self.assertEqual(interaction_transcript(response), '')

    def test_wav_payload_does_not_send_reference_or_prompt(self):
        body = payload(bytes(3200), 16000)
        part = body['contents'][0]['parts'][0]
        self.assertEqual(set(part), {'inlineData'})
        with wave.open(io.BytesIO(base64.b64decode(part['inlineData']['data']))) as audio:
            self.assertEqual(audio.getframerate(), 16000)
            self.assertEqual(audio.getnframes(), 1600)
        self.assertEqual(body['generationConfig']['audioTranscriptionConfig']['languageCodes'], ['uk-UA'])
        self.assertEqual(len(set(IDS)), 8)

    def test_empty_blocked_and_thought_are_not_transcripts(self):
        self.assertEqual(transcript({}), '')
        self.assertEqual(transcript({'candidates': [{'finishReason': 'SAFETY'}]}), '')
        body = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'thought': True, 'text': 'ignore'}, {'text': 'ні'}]}}]}
        self.assertEqual(transcript(body), 'ні')
