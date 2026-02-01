import logging
import asyncio
import json
import base64
import requests
import speech_recognition as sr
import threading
import queue
import websockets
import os
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class MicTTSBot:
    def __init__(self, device_index: int = None, recognition_backend: str = None):
        """
        Initialize the Mic TTS bot that listens to microphone and sends TTS to overlay.
        
        Args:
            device_index: Optional microphone device index. If None, uses system default.
                         Use list_microphones() to see available devices.
            recognition_backend: Speech recognition backend to use. Options:
                                - "whisper" (recommended - best accuracy, local)
                                - "deepgram" (excellent real-time, requires API key)
                                - "google" (default - free but less accurate)
        """
        # WebSocket settings
        self.overlay_ws_url = "ws://localhost:6790"
        self.ws = None
        self.websocket_task = None
        
        # TTS configuration
        self.tts_api_url = "https://api.console.tts.monster/generate"
        self.tts_api_token = os.getenv("TTS_MONSTER_API_TOKEN")
        self.default_voice_id = "67dbd94d-a097-4676-af2f-1db67c1eb8dd"  # Default voice ID
        
        # Speech recognition backend selection
        self.recognition_backend = recognition_backend or os.getenv("SPEECH_BACKEND", "google").lower()
        
        # Speech recognition setup
        self.recognizer = sr.Recognizer()
        self.microphone = None
        self.device_index = device_index
        self.is_listening = False
        self.listen_thread = None
        self.audio_queue = queue.Queue()
        self.event_loop = None  # Will be set when run() is called
        
        # Whisper setup (if using)
        self.whisper_model = None
        if self.recognition_backend == "whisper":
            try:
                import whisper
                logger.info("Loading Whisper model (this may take a moment on first run)...")
                # Use base model for good balance of speed and accuracy
                # Options: tiny, base, small, medium, large
                model_size = os.getenv("WHISPER_MODEL", "base")
                self.whisper_model = whisper.load_model(model_size)
                logger.info(f"Whisper model '{model_size}' loaded successfully")
            except ImportError:
                logger.error("Whisper not installed. Install with: pip install openai-whisper")
                logger.info("Falling back to Google speech recognition")
                self.recognition_backend = "google"
            except Exception as e:
                logger.error(f"Error loading Whisper model: {e}")
                logger.info("Falling back to Google speech recognition")
                self.recognition_backend = "google"
        
        # Deepgram setup (if using)
        self.deepgram_api_key = None
        if self.recognition_backend == "deepgram":
            self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
            if not self.deepgram_api_key:
                logger.error("DEEPGRAM_API_KEY not set. Falling back to Google speech recognition")
                self.recognition_backend = "google"
        
        # Adjust for ambient noise
        self.energy_threshold = 4000  # Adjust based on your microphone sensitivity
        self.pause_threshold = 0.8  # Seconds of silence to consider end of phrase
        self.phrase_threshold = 0.3  # Minimum seconds of speaking audio
        
        logger.info(f"Mic TTS Bot initialized with recognition backend: {self.recognition_backend}")
        if device_index is not None:
            logger.info(f"Using microphone device index: {device_index}")
        else:
            logger.info("Using system default microphone")

    async def connect_websocket(self):
        """Maintains a persistent WebSocket connection with exponential backoff."""
        while True:
            logger.info("Attempting to connect to WebSocket server...")
            try:
                async with websockets.connect(self.overlay_ws_url) as websocket:
                    logger.info("Connected to WebSocket server")
                    self.ws = websocket
                    await self.upon_connection()
                    # Keep connection alive and handle messages
                    await websocket.wait_closed()
                    
                    self.ws = None
                    logger.info("WebSocket connection closed")
            except websockets.exceptions.ConnectionClosed:
                self.ws = None
                logger.info("Connection closed by server")
            except Exception as e:
                self.ws = None
                logger.error(f"Error: {e}")
                
            await asyncio.sleep(2)

    async def send_to_overlay(self, text: str):
        """Send message using existing WebSocket connection."""
        if self.ws is None or self.ws.state != websockets.State.OPEN:
            logger.warning("WebSocket not connected. Message not sent.")
            return
            
        try:
            await self.ws.send(text)
            logger.debug(f"Successfully sent message to overlay: {text}")
        except Exception as e:
            logger.error(f"Error sending message to overlay: {e}")
            self.ws = None

    async def upon_connection(self):
        """Called when WebSocket connection is established."""
        logger.info("Mic TTS Bot connected to overlay")
        logger.info("Starting microphone listener...")
        # Start listening in a separate thread
        self.start_listening()

    def start_listening(self):
        """Start the microphone listening thread."""
        if self.is_listening:
            logger.warning("Already listening to microphone")
            return
        
        self.is_listening = True
        self.listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.listen_thread.start()
        logger.info("Microphone listener started")

    def stop_listening(self):
        """Stop the microphone listener."""
        self.is_listening = False
        if self.listen_thread:
            self.listen_thread.join(timeout=2)
        logger.info("Microphone listener stopped")

    def _recognize_speech(self, audio):
        """
        Recognize speech from audio using the configured backend.
        
        Args:
            audio: AudioData object from speech_recognition
            
        Returns:
            str: Recognized text, or None if recognition failed
        """
        if self.recognition_backend == "whisper":
            return self._recognize_whisper(audio)
        elif self.recognition_backend == "deepgram":
            return self._recognize_deepgram(audio)
        else:  # google (default)
            return self.recognizer.recognize_google(audio)

    def _recognize_whisper(self, audio):
        """Recognize speech using Whisper (local)."""
        try:
            import whisper
            import numpy as np
            import io
            import wave
            
            # Get WAV data from AudioData
            wav_data = audio.get_wav_data()
            
            # Read WAV file from bytes
            wav_file = io.BytesIO(wav_data)
            with wave.open(wav_file, 'rb') as wf:
                # Read audio frames
                frames = wf.readframes(wf.getnframes())
                # Convert to numpy array
                audio_array = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                # Resample to 16kHz if needed (Whisper expects 16kHz)
                sample_rate = wf.getframerate()
                if sample_rate != 16000:
                    from scipy import signal
                    num_samples = int(len(audio_array) * 16000 / sample_rate)
                    audio_array = signal.resample(audio_array, num_samples)
            
            # Run Whisper transcription
            result = self.whisper_model.transcribe(audio_array, language="en", fp16=False)
            text = result["text"].strip()
            return text if text else None
        except ImportError as e:
            logger.error(f"Whisper dependencies missing: {e}. Install with: pip install openai-whisper scipy")
            raise
        except Exception as e:
            logger.error(f"Whisper recognition error: {e}")
            raise

    def _recognize_deepgram(self, audio):
        """Recognize speech using Deepgram API."""
        try:
            import aiohttp
            import io
            
            # Convert AudioData to bytes
            wav_data = audio.get_wav_data()
            
            # Make synchronous request (we're in a thread)
            url = "https://api.deepgram.com/v1/listen"
            headers = {
                "Authorization": f"Token {self.deepgram_api_key}",
                "Content-Type": "audio/wav"
            }
            
            response = requests.post(
                url,
                headers=headers,
                data=wav_data,
                params={"model": "nova-2", "language": "en-US", "punctuate": "true"}
            )
            
            if response.status_code == 200:
                result = response.json()
                text = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "").strip()
                return text if text else None
            else:
                logger.error(f"Deepgram API error: {response.status_code} - {response.text}")
                raise sr.RequestError(f"Deepgram API returned {response.status_code}")
        except Exception as e:
            logger.error(f"Deepgram recognition error: {e}")
            raise

    @staticmethod
    def list_microphones():
        """
        List all available microphone devices.
        
        Returns:
            list: List of tuples (index, name) for each microphone
        """
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            microphones = []
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info['maxInputChannels'] > 0:  # It's an input device
                    microphones.append((i, info['name']))
            p.terminate()
            return microphones
        except Exception as e:
            logger.error(f"Error listing microphones: {e}")
            return []

    def _listen_loop(self):
        """Main loop for listening to microphone (runs in separate thread)."""
        try:
            # Initialize microphone with specified device index (or default)
            if self.device_index is not None:
                self.microphone = sr.Microphone(device_index=self.device_index)
                logger.info(f"Using microphone device index: {self.device_index}")
            else:
                self.microphone = sr.Microphone()
                logger.info("Using system default microphone")
            
            # Adjust for ambient noise
            with self.microphone as source:
                logger.info("Adjusting for ambient noise... Please wait.")
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
                logger.info(f"Energy threshold set to: {self.recognizer.energy_threshold}")
            
            # Set recognition parameters
            self.recognizer.energy_threshold = self.energy_threshold
            self.recognizer.pause_threshold = self.pause_threshold
            self.recognizer.phrase_threshold = self.phrase_threshold
            
            logger.info("Microphone ready. Listening for speech...")
            
            while self.is_listening:
                try:
                    with self.microphone as source:
                        # Listen for audio with timeout
                        audio = self.recognizer.listen(source, timeout=1, phrase_time_limit=10)
                    
                    # Recognize speech using selected backend
                    try:
                        text = self._recognize_speech(audio)
                        if text:
                            logger.info(f"Recognized: {text}")
                            # Send directly to TTS overlay
                            if self.event_loop:
                                asyncio.run_coroutine_threadsafe(
                                    self.send_to_tts_overlay(text),
                                    self.event_loop
                                )
                            else:
                                logger.warning("Event loop not set, cannot send to overlay")
                    except sr.UnknownValueError:
                        # Speech was unintelligible
                        logger.debug("Could not understand audio")
                    except sr.RequestError as e:
                        logger.error(f"Could not request results from speech recognition service: {e}")
                    except Exception as e:
                        logger.error(f"Error in speech recognition: {e}")
                        
                except sr.WaitTimeoutError:
                    # Timeout is expected, continue listening
                    continue
                except Exception as e:
                    logger.error(f"Error in listen loop: {e}")
                    if self.is_listening:
                        time.sleep(0.5)  # Brief pause before retrying
                        
        except Exception as e:
            logger.error(f"Fatal error in microphone listener: {e}", exc_info=True)
            self.is_listening = False

    async def send_to_tts_overlay(self, message: str):
        """
        Send transcribed text to TTS overlay (similar to declare functionality).
        
        Args:
            message: The text message to send
        """
        if not message or not message.strip():
            return
        
        logger.info(f"Sending to TTS overlay: {message}")
        
        # Generate TTS on server side and send audio data to overlay (avoids CORS issues)
        audio_data_url = None
        if self.tts_api_token:
            try:
                # Generate TTS using TTS Monster API
                payload = {
                    "voice_id": self.default_voice_id,
                    "message": message[:500]  # Limit to 500 characters
                }
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": self.tts_api_token
                }
                
                response = requests.post(
                    self.tts_api_url,
                    headers=headers,
                    data=json.dumps(payload)
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get("status") == 200:
                        audio_url = result.get("url")
                        # Download the audio file
                        audio_response = requests.get(audio_url)
                        if audio_response.status_code == 200:
                            # Convert to base64 data URL
                            audio_base64 = base64.b64encode(audio_response.content).decode('utf-8')
                            # Determine MIME type (TTS Monster typically returns WAV)
                            mime_type = "audio/wav"  # Default, could detect from URL or content
                            audio_data_url = f"data:{mime_type};base64,{audio_base64}"
                            logger.debug("TTS audio generated and converted to base64")
                        else:
                            logger.error(f"Failed to download audio: {audio_response.status_code}")
                    else:
                        logger.error(f"TTS API error: {result}")
                else:
                    logger.error(f"TTS API request failed: {response.status_code} - {response.text}")
            except Exception as e:
                logger.error(f"Error generating TTS for overlay: {e}")
        else:
            logger.warning("TTS_MONSTER_API_TOKEN not set - TTS will not work in overlay")
        
        overlay_data = {
            "type": "mic_tts",
            "message": message,
            "audio_data_url": audio_data_url  # Send base64 data URL instead of credentials
        }
        logger.debug(f"Sending mic TTS overlay data: type={overlay_data['type']}, has_audio={bool(audio_data_url)}")
        await self.send_to_overlay(json.dumps(overlay_data))

    def __del__(self):
        """Cleanup when bot is destroyed."""
        self.stop_listening()

    async def run(self):
        """Run the bot (async version)."""
        # Store event loop for use in threads
        self.event_loop = asyncio.get_event_loop()
        
        # Start WebSocket connection
        self.websocket_task = asyncio.create_task(self.connect_websocket())
        
        # Keep the event loop running
        try:
            await asyncio.Event().wait()  # Wait indefinitely
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.stop_listening()
            if self.websocket_task:
                self.websocket_task.cancel()

if __name__ == "__main__":
    import argparse
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Mic TTS Bot - Listen to microphone and send TTS to overlay')
    parser.add_argument('--list-mics', action='store_true', help='List all available microphone devices and exit')
    parser.add_argument('--device', type=int, default=2, 
                       help='Microphone device index to use (use --list-mics to see available devices)')
    parser.add_argument('--device-env', action='store_true',
                       help='Read device index from MIC_DEVICE_INDEX environment variable')
    parser.add_argument('--backend', type=str, choices=['whisper', 'deepgram', 'google'], default='whisper',
                       help='Speech recognition backend: whisper (best accuracy, local), deepgram (excellent real-time, requires API key), google (default, free but less accurate)')
    
    args = parser.parse_args()
    
    # List microphones if requested
    if args.list_mics:
        print("\nAvailable Microphone Devices:")
        print("-" * 60)
        mics = MicTTSBot.list_microphones()
        if mics:
            for index, name in mics:
                print(f"  [{index}] {name}")
        else:
            print("  No microphones found or error listing devices")
        print("-" * 60)
        print("\nUsage: python micTTSBot.py --device <index>")
        exit(0)
    
    # Get device index from command line or environment variable
    device_index = args.device
    if args.device_env:
        device_env = os.getenv("MIC_DEVICE_INDEX")
        if device_env:
            try:
                device_index = int(device_env)
            except ValueError:
                logger.error(f"Invalid MIC_DEVICE_INDEX value: {device_env}")
                exit(1)
    
    # Get backend from command line or environment variable
    backend = args.backend or os.getenv("SPEECH_BACKEND")
    
    bot = MicTTSBot(device_index=device_index, recognition_backend=backend)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        bot.stop_listening()
    except Exception as e:
        logger.error(f"Error running bot: {e}", exc_info=True)
        bot.stop_listening()
