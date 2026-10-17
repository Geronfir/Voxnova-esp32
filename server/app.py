import asyncio
import json
import os

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from google import genai
from google.genai import types


def load_env_file(env_path=None):
    env_path = env_path or os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_env_file()

MEMORY_FILE = "memory.json"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HOST = os.getenv("NOVA_HOST", "0.0.0.0")
PORT = int(os.getenv("NOVA_PORT", "8000"))
MODEL_NAME = os.getenv("NOVA_MODEL", "gemini-3.8-live")
VOICE_NAME = os.getenv("NOVA_VOICE", "Kore")

SYSTEM_INSTRUCTION = (
    "Kamu asisten suara berbahasa Indonesia. "
    "Jawab MAKSIMAL 1 kalimat pendek. "
    "Matematika: jawab ANGKA saja. "
    "Fakta: jawab langsung, tanpa pembuka/penutup. "
    "DILARANG: 'tentu', 'baik', 'saya akan', 'ada yang bisa dibantu'.")

if not GEMINI_API_KEY:
    raise RuntimeError("Missing GEMINI_API_KEY. Set it in .env or your environment.")

client = genai.Client(api_key=GEMINI_API_KEY)
app = FastAPI()

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {"memories": []}

    with open(MEMORY_FILE, "r") as f:
        return json.load(f)

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)
        
# ---------------------------------------------------------
# FRONTEND
# ---------------------------------------------------------
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>AI Voice Desktop Hub</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: 40px auto; text-align: center; background-color: #121212; color: #ffffff;}
        #status { font-size: 18px; margin: 20px; padding: 15px; border-radius: 8px; background-color: #1e1e1e; border: 1px solid #333;}
        .active { color: #00ffcc; font-weight: bold; }
        button { padding: 12px 24px; font-size: 16px; margin: 10px; cursor: pointer; border: none; border-radius: 5px; font-weight: bold; transition: 0.2s;}
        #startBtn { background-color: #007bff; color: white; }
        #startBtn:hover { background-color: #0056b3; }
        #stopBtn { background-color: #dc3545; color: white; }
        #stopBtn:disabled { background-color: #555; cursor: not-allowed; }
    </style>
</head>
<body>
    <h1>Desk AI Hub</h1>
    <div id="status">Status: Offline</div>
    <button id="startBtn">Connect & Listen</button>
    <button id="stopBtn" disabled>Disconnect</button>

    <script>
        let ws;
        let audioContext;
        let mediaStream;
        let pcmNode; 
        let playContext;
        let nextPlayTime = 0;
        
        let audioQueue = []; 
        let activeSources = [];
        let isPlaying = false; // <-- FIXED: Declared isPlaying here

        document.getElementById('startBtn').onclick = async () => {
            document.getElementById('status').innerText = "Status: Connecting to Server...";
            
            ws = new WebSocket(`ws://${location.host}/ws`);
            
            ws.onopen = async () => {
                document.getElementById('status').innerHTML = "Status: <span class='active'>Live! Speak now. Say 'Goodbye' to hang up.</span>";
                document.getElementById('startBtn').disabled = true;
                document.getElementById('stopBtn').disabled = false;

                playContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
                nextPlayTime = playContext.currentTime;

                mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                audioContext = new AudioContext({ sampleRate: 16000 });
                const source = audioContext.createMediaStreamSource(mediaStream);
                
                const workletCode = `
                class PCMProcessor extends AudioWorkletProcessor {
                    constructor() {
                        super();
                        this.buffer = new Int16Array(4096); 
                        this.offset = 0;
                    }
                    process(inputs, outputs, parameters) {
                        const input = inputs[0];
                        if (input && input.length > 0) {
                            const channelData = input[0];
                            for (let i = 0; i < channelData.length; i++) {
                                this.buffer[this.offset++] = Math.max(-1, Math.min(1, channelData[i])) * 0x7FFF;
                                if (this.offset >= this.buffer.length) {
                                    const sendBuffer = new Int16Array(this.buffer);
                                    this.port.postMessage(sendBuffer.buffer, [sendBuffer.buffer]);
                                    this.offset = 0; 
                                }
                            }
                        }
                        return true;
                    }
                }
                registerProcessor('pcm-processor', PCMProcessor);
                `;
                
                const blob = new Blob([workletCode], { type: 'application/javascript' });
                const workletUrl = URL.createObjectURL(blob);
                
                await audioContext.audioWorklet.addModule(workletUrl);
                pcmNode = new AudioWorkletNode(audioContext, 'pcm-processor');
                
                pcmNode.port.onmessage = (e) => {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(e.data);
                    }
                };
                
                source.connect(pcmNode);
                pcmNode.connect(audioContext.destination);
            };

            ws.onmessage = async (event) => {
                if (typeof event.data === 'string') {
                    if (event.data === "CLOSE_SESSION") {
                        document.getElementById('status').innerHTML = "<span style='color: #fbbf24;'>AI gracefully ended chat.</span>";
                        document.getElementById('stopBtn').click(); 
                        return;
                    }
                    
                    if (event.data === "CLEAR_AUDIO") {
                        activeSources.forEach(source => { try { source.stop(); } catch(e) {} });
                        activeSources = [];
                        audioQueue = []; 
                        isPlaying = false;
                        if (playContext) nextPlayTime = playContext.currentTime; 
                        return;
                    }
                }
                
                if (event.data instanceof Blob) {
                    if (!playContext) return; 

                    const arrayBuffer = await event.data.arrayBuffer();
                    if (arrayBuffer.byteLength === 0) return; 

                    const audioData = new Int16Array(arrayBuffer);
                    const float32Data = new Float32Array(audioData.length);
                    for (let i = 0; i < audioData.length; i++) {
                        float32Data[i] = audioData[i] / 0x7FFF;
                    }

                    const audioBuffer = playContext.createBuffer(1, float32Data.length, 24000);
                    audioBuffer.getChannelData(0).set(float32Data);

                    audioQueue.push(audioBuffer);

                    if (!isPlaying) {
                        let bufferedTime = audioQueue.reduce((total, buf) => total + buf.duration, 0);
                        if (bufferedTime >= 0.3) {
                            isPlaying = true;
                            nextPlayTime = playContext.currentTime + 0.05; 
                            scheduleQueue();
                        }
                    } else {
                        scheduleQueue();
                    }
                }
            };

            function scheduleQueue() {
                while (audioQueue.length > 0) {
                    const audioBuffer = audioQueue.shift(); 
                    const playbackSource = playContext.createBufferSource();
                    playbackSource.buffer = audioBuffer;
                    playbackSource.connect(playContext.destination);
                    
                    // If the network dropped entirely and we fell behind, reset the playhead
                    if (nextPlayTime < playContext.currentTime) {
                        nextPlayTime = playContext.currentTime + 0.05; 
                    }

                    playbackSource.start(nextPlayTime);
                    nextPlayTime += audioBuffer.duration;
                    
                    activeSources.push(playbackSource);
                    
                    playbackSource.onended = () => {
                        activeSources = activeSources.filter(s => s !== playbackSource);
                        if (activeSources.length === 0) {
                            isPlaying = false;
                        }
                    };
                }
            }

            ws.onclose = () => {
                if (!document.getElementById('status').innerHTML.includes('AI gracefully ended chat')) {
                    document.getElementById('status').innerText = "Status: Disconnected";
                }
                document.getElementById('startBtn').disabled = false;
                document.getElementById('stopBtn').disabled = true;
                stopAudio();
            };
        };

        document.getElementById('stopBtn').onclick = () => {
            if (ws) ws.close();
            stopAudio();
        };

        function stopAudio() {
            if (pcmNode) { pcmNode.disconnect(); pcmNode = null; }
            if (mediaStream) { mediaStream.getTracks().forEach(track => track.stop()); mediaStream = null; }
            if (audioContext) { audioContext.close(); audioContext = null; }
            if (playContext) { playContext.close(); playContext = null; }
            activeSources = [];
            audioQueue = [];
            isPlaying = false;
        }
    </script>
</body>
</html>
"""

full_response_buffer = ""

# ---------------------------------------------------------
# BACKEND
# ---------------------------------------------------------
@app.get("/")
async def get_index():
    return HTMLResponse(HTML_PAGE)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("\n>>> Browser connected to local server.")

    end_chat_tool = {
        "function_declarations": [
            {
                "name": "end_chat_session",
                "description": "ONLY call this function AFTER you have spoken a short farewell message such as 'Goodbye', 'See you later', or 'Take care'."
            }
        ]
    }

    model = MODEL_NAME
    
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        tools=[end_chat_tool],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=VOICE_NAME
                )
            )
        ),
        system_instruction=types.Content(parts=[types.Part(text=SYSTEM_INSTRUCTION)])
    )

    try:
        async with client.aio.live.connect(model=model, config=config) as session:
            print(">>> Successfully bridged to Gemini Live API.")
            browser_active = True
            session_closing = False
            full_response_buffer = ""
            async def receive_from_browser_and_send_to_gemini():
                nonlocal browser_active
                try:
                    while browser_active:
                        data = await websocket.receive_bytes()
                        await session.send_realtime_input(
                            audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                        )
                except WebSocketDisconnect:
                    print("\n>>> Browser disconnected normally.")
                    browser_active = False  
                except Exception as e:
                    print(f"\n!!! Error reading from browser: {e}")
                    browser_active = False

            async def receive_from_gemini_and_send_to_browser():
                nonlocal browser_active, session_closing, full_response_buffer

                try:
                    while browser_active:
                        async for response in session.receive():

                            # Handle interruption / barge-in
                            if response.server_content:
                                if response.server_content.interrupted:
                                    print("\n>>> You interrupted! Silencing the browser audio queue...")
                                    try:
                                        await websocket.send_text("CLEAR_AUDIO")
                                    except Exception:
                                        pass

                            # Standard Model Processing
                            if response.server_content and response.server_content.model_turn:
                                for part in response.server_content.model_turn.parts:

                                    # Print transcript and accumulate
                                    if part.text:
                                        # Do NOT use .strip() when accumulating, or words might merge (e.g., "take ""care")
                                        full_response_buffer += part.text 
                                        print(f"\n[AI Partial Transcript]: {part.text.strip()}")
                                        print("BUFFER:", repr(full_response_buffer))

                                        # REAL-TIME FALLBACK DETECTOR
                                        buffer_lower = full_response_buffer.lower()

                                        farewell_phrases = [
                                            "goodbye",
                                            "see you later",
                                            "take care",
                                            "talk to you later",
                                            "catch you later",
                                            "bye for now"
                                        ]
                                        
                                        # Safe checks for "bye" to avoid triggering on words like "byte"
                                        is_bye = (
                                            " bye " in buffer_lower or 
                                            buffer_lower.endswith(" bye.") or 
                                            buffer_lower.endswith(" bye!") or 
                                            buffer_lower.strip() == "bye"
                                        )

                                        # Trigger immediately upon detection in the text stream
                                        if (
                                            not session_closing
                                            and len(buffer_lower.split()) <= 15
                                            and (any(phrase in buffer_lower for phrase in farewell_phrases) or is_bye)
                                        ):
                                            print("\n>>> Fallback farewell detector triggered.")
                                            session_closing = True

                                            # Give the frontend a moment to play the final audio buffer
                                            await asyncio.sleep(2)

                                            try:
                                                await websocket.send_text("CLOSE_SESSION")
                                            except Exception:
                                                pass

                                            browser_active = False
                                            return

                                    # Forward audio to browser
                                    if part.inline_data and part.inline_data.data:
                                        try:
                                            await websocket.send_bytes(part.inline_data.data)
                                        except Exception:
                                            browser_active = False
                                            return

                            # Detect end of Gemini's response turn to reset the buffer safely
                            # This MUST be outside the `for part...` loop and `if part.text:` block
                            if (
                                response.server_content 
                                and hasattr(response.server_content, "turn_complete") 
                                and response.server_content.turn_complete
                            ):
                                print("FINAL BUFFER FOR THIS TURN:", repr(full_response_buffer))
                                full_response_buffer = "" # Reset for the next time the AI speaks

                            # Tool Calls
                            if response.tool_call:

                                print("TOOL CALL RECEIVED:", response.tool_call)

                                for fc in response.tool_call.function_calls:

                                    print("FUNCTION:", fc.name)

                                    if fc.name == "end_chat_session":

                                        if session_closing:
                                            return

                                        session_closing = True

                                        print("\n>>> Gemini hit the kill switch! Ending session gracefully...")

                                        await asyncio.sleep(2)

                                        try:
                                            await websocket.send_text("CLOSE_SESSION")
                                        except Exception:
                                            pass

                                        browser_active = False
                                        return

                except asyncio.CancelledError:
                    pass

                except Exception as e:
                    print(f"\n!!! Gemini API Receive Loop Crashed: {e}")
                    browser_active = False

            task1 = asyncio.create_task(receive_from_browser_and_send_to_gemini())
            task2 = asyncio.create_task(receive_from_gemini_and_send_to_browser())

            await asyncio.wait(
                [task1, task2],
                return_when=asyncio.FIRST_COMPLETED
            )

            for p in [task1, task2]:
                p.cancel()

    except Exception as e:
        print(f"\n!!! Connection to Gemini failed: {e}")
    finally:
        try:
            await websocket.close()
            print(">>> WebSocket closed cleanly.\n")
        except Exception:
            pass

# ---------------------------------------------------------
# THE HARDWARE GATEWAY (For ESP32 & MAX98357A)
# ---------------------------------------------------------
@app.websocket("/esp32")
async def esp32_hardware_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("\n>>> [ESP32] Hardware connected to Nova Server.")

    # 1. Give the hardware the ability to hang up
    tools = {
        "function_declarations": [

            {
                "name": "end_chat_session",
                "description": "End the conversation after saying goodbye."
            },

            {
                "name": "rgb_red",
                "description": "Turn the room lights red when the user asks for red lighting."
            },
            {
                "name": "rgb_blue",
                "description": "Turn the room lights blue when the user asks for blue lighting."
            },
            {
                "name": "rgb_green",
                "description": "Turn the room lights green when the user asks for green lighting."
            },
            {
                "name": "rgb_on",
                "description": "Turn the RGB room lights on."
            },
            {
                "name": "rgb_off",
                "description": "Turn the RGB room lights off."
            }
        ]
    }

    model = MODEL_NAME
    
    # 2. Give the hardware the full context (Name, Timezone, Rules)
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        tools=[tools],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=VOICE_NAME
                )
            )
        ),
        system_instruction=types.Content(parts=[types.Part(text=SYSTEM_INSTRUCTION)]),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        )
    )

    try:
        async with client.aio.live.connect(model=model, config=config) as session:
            print(">>> [ESP32] Bridged to Gemini Live API. Speak into the mic!")
            hardware_active = True
            session_closing = False
            full_response_buffer = ""
            heard_speech = False

            # --- AUDIO QUEUE SYSTEM FOR BARGE-IN ---
            audio_queue = asyncio.Queue()
            clear_audio_flag = asyncio.Event()

            async def esp32_audio_pacer():
                """Reads from the queue and spoon-feeds the ESP32. Can be instantly interrupted."""
                nonlocal hardware_active
                chunk_size = 2048 
                
                try:
                    while hardware_active:
                        # If an interrupt happened, flush the queue
                        if clear_audio_flag.is_set():
                            while not audio_queue.empty():
                                audio_queue.get_nowait()
                            clear_audio_flag.clear()

                        try:
                            # Pull the next chunk of audio from Gemini
                            audio_bytes = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                            
                            for i in range(0, len(audio_bytes), chunk_size):
                                # Check between every single tiny chunk if we got interrupted
                                if clear_audio_flag.is_set():
                                    break 

                                chunk = audio_bytes[i:i+chunk_size]
                                await websocket.send_bytes(chunk)
                                
                                # Pace the stream (~1.5x real-time agar ring buffer ESP tak kosong)
                                chunk_duration = len(chunk) / 72000.0
                                await asyncio.sleep(chunk_duration)
                                
                        except asyncio.TimeoutError:
                            continue # Queue is empty, just loop and check flags again
                except Exception as e:
                    print(f"\n!!! [ESP32] Pacer stopped: {e}")
                    hardware_active = False

            async def receive_from_esp32():
                nonlocal hardware_active
                import struct as _struct
                _rx_total = 0
                _rx_n = 0
                _speech_open = False
                try:
                    while hardware_active:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        _text = msg.get("text")
                        data = msg.get("bytes")
                        if _text is not None:
                            if _text == "PTT_END":
                                if _speech_open:
                                    _speech_open = False
                                    await session.send_realtime_input(activity_end=types.ActivityEnd())
                                    print(">>> [ESP32] PTT_END -> activity_end (akhir giliran)")
                            continue
                        if not data:
                            continue
                        _rx_total += len(data)
                        _rx_n += 1
                        if _rx_n % 50 == 1:
                            _peak = 0
                            if len(data) >= 2:
                                _fmt = "<" + "h" * (len(data) // 2)
                                for _s in _struct.unpack(_fmt, data[:len(data)//2*2]):
                                    _a = abs(_s)
                                    if _a > _peak:
                                        _peak = _a
                            print(f">>> [ESP32] audio rx: {_rx_total} bytes total, peak={_peak} (kirim>600 = suara masuk)")
                        if not _speech_open:
                            _speech_open = True
                            await session.send_realtime_input(activity_start=types.ActivityStart())
                            print(">>> [ESP32] audio pertama -> activity_start (mulai giliran)")
                        await session.send_realtime_input(
                            audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                        )
                except Exception:
                    hardware_active = False

            async def receive_from_gemini():
                nonlocal hardware_active, session_closing, full_response_buffer, heard_speech
                try:
                    while hardware_active:
                        async for response in session.receive():
                            
                            # --- 3. BARGE-IN DETECTION ---
                            if response.server_content and response.server_content.interrupted:
                                print("\n>>> [ESP32] You interrupted! Silencing the speaker...")
                                clear_audio_flag.set()

                            # --- 3b. INPUT TRANSCRIPTION: ada suara yang bisa ditranskrip? ---
                            if response.server_content:
                                for _t in (getattr(response.server_content, "input_transcription", None),
                                           getattr(response.server_content, "interim_input_transcription", None)):
                                    if _t and getattr(_t, "text", None) and _t.text.strip():
                                        if not heard_speech:
                                            print(f"\n>>> [ESP32] suara ditranskrip: '{_t.text.strip()}'")
                                        heard_speech = True

                            if response.server_content and response.server_content.model_turn:
                                for part in response.server_content.model_turn.parts:
                                    if getattr(part, "inline_data", None):
                                        _d = part.inline_data.data or b""
                                        print(f">>> [ESP32] gemini audio out: {len(_d)} bytes")
                                    if part.text:
                                        full_response_buffer += part.text
                                        print(f"[Nova Hardware]: {part.text.strip()}")
                                        
                                        # Text-based fallback detector
                                        buffer_lower = full_response_buffer.lower()
                                        # --- BLOCK 1: Text-based fallback detector ---
                                        if not session_closing and (" bye" in buffer_lower or buffer_lower.strip() == "bye" or "goodbye" in buffer_lower):
                                            print("\n>>> [ESP32] Fallback farewell detector triggered.")
                                            session_closing = True
                                            await asyncio.sleep(2) # Let the goodbye audio play out
                                            try:
                                                await websocket.send_text("SLEEP") # <--- ADD THIS
                                            except Exception:
                                                pass
                                            hardware_active = False
                                            return

                                    if part.inline_data and part.inline_data.data:
                                        # Put audio in the queue instead of blocking the network loop
                                        if heard_speech:
                                            await audio_queue.put(part.inline_data.data)

                            if response.server_content and hasattr(response.server_content, "turn_complete") and response.server_content.turn_complete:
                                if not heard_speech:
                                    print("\n>>> [ESP32] Tak ada suara ditranskrip -> kirim IDLE (batal berpikir).")
                                    clear_audio_flag.set()
                                    try:
                                        await websocket.send_text("IDLE")
                                    except Exception:
                                        pass
                                heard_speech = False
                                full_response_buffer = ""

                            # --- BLOCK 2: Tool Call Detector ---
                            if response.tool_call:
                                for fc in response.tool_call.function_calls:
                                    if fc.name == "rgb_on":

                                        print("RGB ON")
                                        await websocket.send_text("RGB_ON")
                                        continue

                                    elif fc.name == "rgb_off":

                                        print("RGB OFF")
                                        await websocket.send_text("RGB_OFF")
                                        continue

                                    elif fc.name == "rgb_red":

                                        print("RGB RED")
                                        await websocket.send_text("RGB_RED")
                                        continue

                                    elif fc.name == "rgb_green":

                                        print("RGB GREEN")
                                        await websocket.send_text("RGB_GREEN")
                                        continue

                                    elif fc.name == "rgb_blue":

                                        print("RGB BLUE")
                                        await websocket.send_text("RGB_BLUE")
                                        continue
                                    if fc.name == "end_chat_session":
                                        if session_closing: return
                                        session_closing = True
                                        print("\n>>> [ESP32] Gemini hit the kill switch! Ending session...")
                                        await asyncio.sleep(2) # Give the pacer time to play the final goodbye
                                        try:
                                            await websocket.send_text("SLEEP")
                                            await asyncio.sleep(0.5) # <--- ADD THIS: Let the message travel!
                                        except Exception:
                                            pass
                                        
                                        hardware_active = False
                                        return

                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    print(f"\n!!! [ESP32] Gemini Receive Crashed: {e}")
                    hardware_active = False
                    try:
                        err_str = str(e).lower()
                        if "429" in err_str or "quota" in err_str or "resourceexhausted" in err_str:
                            await websocket.send_text("ERROR_QUOTA")
                        else:
                            await websocket.send_text("ERROR")
                        await asyncio.sleep(0.2)
                    except Exception:
                        pass

            # Run all three tasks simultaneously
            t1 = asyncio.create_task(receive_from_esp32())
            t2 = asyncio.create_task(receive_from_gemini())
            t3 = asyncio.create_task(esp32_audio_pacer())

            await asyncio.wait([t1, t2, t3], return_when=asyncio.FIRST_COMPLETED)
            
            for p in [t1, t2, t3]:
                p.cancel()

    except Exception as e:
        print(f"\n!!! [ESP32] Connection failed: {e}")
        try:
            err_str = str(e).lower()
            if "429" in err_str or "quota" in err_str or "resourceexhausted" in err_str:
                await websocket.send_text("ERROR_QUOTA")
            else:
                await websocket.send_text("ERROR")
            await asyncio.sleep(0.2)
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
            print(">>> [ESP32] Socket closed cleanly.\n")
        except:
            pass
        
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)